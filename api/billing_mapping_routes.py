"""Operator organization to MonetizeNow billing mapping API."""

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import logging
import os
import time
from typing import Any
from uuid import uuid4

from aiohttp import web
from bson import ObjectId
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError

from api.auth_helpers import get_user_email, require_operator_or_above
from api.plotline_db import get_plotline_db, get_plotline_db_status
from observability.db import get_db
from tools.monetize_now import account_contracts, get_account, get_contract

ACTIVE = "ACTIVE"
logger = logging.getLogger(__name__)
_CONTRACT_CACHE_TTL_SECONDS = 60
_CONTRACT_CACHE_MAX_ENTRIES = 2048
_contract_cache: OrderedDict[str, tuple[float, dict[str, Any] | None]] = OrderedDict()
_RECONCILIATION_INTERVAL_SECONDS = 15 * 60
_RECONCILIATION_BATCH_SIZE = 100
_RECONCILIATION_LEASE_MINUTES = 30
_RECONCILIATION_OWNER = str(uuid4())
# The classifications a snapshot row can hold. "unknown" is only ever seeded when an
# upstream lookup failed during reconciliation, so operators need a way to filter for
# it: a genuinely broken product must never silently vanish from a worklist just
# because MonetizeNow was flapping the last time it was checked.
_WORKLIST_STATUSES = (
    "correctly_linked", "contract_missing", "invalid_contract", "inactive_contract",
    "account_mismatch", "account_not_linked", "unknown",
)


def _id(value: Any) -> str:
    return str(value) if value is not None else ""


def _payload(result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("error"):
        return None
    data = result.get("data", result)
    return data if isinstance(data, dict) else None


def _upstream_payload(result: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Keep upstream failures distinct from valid empty/not-found responses."""
    if not isinstance(result, dict):
        return None, "Invalid response from MonetizeNow"
    if result.get("error"):
        return None, str(result["error"])
    return _payload(result), None


async def _json_body(request: web.Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except (ValueError, TypeError, web.HTTPBadRequest):
        return None
    return body if isinstance(body, dict) else None


def _items(result: dict[str, Any]) -> list[dict[str, Any]]:
    data: Any = result.get("data", result) if isinstance(result, dict) else {}
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("content", "items", "results", "contracts"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _contract_account_id(contract: dict[str, Any]) -> str:
    account = contract.get("account")
    if isinstance(account, dict):
        return _id(account.get("id") or account.get("accountId"))
    return _id(contract.get("accountId") or contract.get("account_id"))


def _contract_view(contract: dict[str, Any]) -> dict[str, Any]:
    contract_id = _id(contract.get("id") or contract.get("contractId"))
    return {
        "id": contract_id,
        "name": contract.get("name") or contract.get("displayName") or contract_id,
        "status": str(contract.get("status") or "UNKNOWN").upper(),
        "accountId": _contract_account_id(contract),
        "legalEntityId": _id(contract.get("legalEntityId")),
        "startDate": contract.get("startDate") or contract.get("effectiveFrom"),
        "url": f"https://app.monetizenow.io/contracts/{contract_id}" if contract_id else None,
    }


def classify_product(billing_id: str | None, contract: dict[str, Any] | None, account_id: str | None) -> str:
    if not billing_id:
        return "contract_missing"
    if not contract:
        return "invalid_contract"
    if str(contract.get("status") or "").upper() != ACTIVE:
        return "inactive_contract"
    if not account_id:
        return "account_not_linked"
    if _contract_account_id(contract) != account_id:
        return "account_mismatch"
    return "correctly_linked"


async def _load_contract(
    billing_id: str | None,
    semaphore: asyncio.Semaphore,
    *,
    errored: set[str] | None = None,
):
    if not billing_id:
        return None
    cached = _contract_cache.get(billing_id)
    now = time.monotonic()
    if cached and cached[0] > now:
        _contract_cache.move_to_end(billing_id)
        return cached[1]
    if cached:
        del _contract_cache[billing_id]
    async with semaphore:
        try:
            payload, error = _upstream_payload(await get_contract(billing_id))
        except Exception as exc:  # noqa: BLE001 - upstream/tool failure must never 500 the page
            payload, error = None, str(exc)
    if error:
        logger.warning("MonetizeNow contract lookup failed for %s: %s", billing_id, error)
        # Record the failure so callers can preserve last-known state instead of
        # misclassifying (or aborting a whole sweep on) a transient upstream error.
        if errored is not None:
            errored.add(billing_id)
        return None
    _contract_cache[billing_id] = (now + _CONTRACT_CACHE_TTL_SECONDS, payload)
    _contract_cache.move_to_end(billing_id)
    while len(_contract_cache) > _CONTRACT_CACHE_MAX_ENTRIES:
        _contract_cache.popitem(last=False)
    return payload


async def _load_contracts(billing_ids: list[str]) -> tuple[dict[str, Any], set[str]]:
    """Fetch many contracts tolerantly.

    A single upstream failure never aborts the batch; the failed billing ids are
    returned in the second element so the caller can preserve their last-known
    classification rather than falsely marking them invalid or poisoning a sweep.
    """
    semaphore = asyncio.Semaphore(12)
    errored: set[str] = set()
    contracts = await asyncio.gather(
        *[_load_contract(value, semaphore, errored=errored) for value in billing_ids]
    )
    return dict(zip(billing_ids, contracts)), errored


def _snapshot_operation(
    product_id: str,
    org_id: str,
    checked_at: datetime,
    *,
    status: str | None,
    run_id: str | None = None,
) -> UpdateOne:
    """Build an idempotent snapshot upsert.

    When ``status`` is None the upstream lookup failed for this product, so we
    keep any existing status untouched (only ``$setOnInsert`` a neutral
    ``unknown`` that no filter matches) while still stamping ``checked_at``/
    ``run_id`` so a background sweep does not prune the row as an orphan.
    """
    fields: dict[str, Any] = {
        "product_id": product_id,
        "organization_id": org_id,
        "checked_at": checked_at,
    }
    if run_id is not None:
        fields["run_id"] = run_id
    update: dict[str, Any] = {"$set": fields}
    if status is None:
        update["$setOnInsert"] = {"status": "unknown"}
    else:
        fields["status"] = status
    return UpdateOne({"product_id": product_id}, update, upsert=True)


async def _store_status_snapshots(loma_db: Any, organizations: list[dict[str, Any]]) -> None:
    """Persist last-known classifications in one database round trip."""
    checked_at = datetime.now(timezone.utc)
    operations = [
        UpdateOne(
                {"product_id": product["id"]},
                {"$set": {
                    "product_id": product["id"],
                    "organization_id": organization["id"],
                    "status": product["status"],
                    "checked_at": checked_at,
                }},
                upsert=True,
            )
        for organization in organizations
        for product in organization["products"]
    ]
    if operations:
        await loma_db.billing_product_statuses.bulk_write(operations, ordered=False)


async def _ensure_snapshot_indexes(loma_db: Any) -> None:
    await loma_db.billing_product_statuses.create_index("product_id", unique=True)
    await loma_db.billing_product_statuses.create_index([("status", 1), ("organization_id", 1)])


async def _acquire_reconciliation_lease(loma_db: Any, now: datetime) -> bool:
    """Atomically take the reconciliation lease so only one instance sweeps.

    Relies on MongoDB per-document atomicity: the update either takes over an
    expired/absent lease (matched->modified, or upserted) or, when another
    instance already holds an unexpired lease, the unique ``_id`` forces a
    DuplicateKeyError. Acquisition is decided by ``modified_count``/``upserted_id``
    rather than reading back ``owner``, so the expiry-takeover path cannot let two
    instances believe they both won.
    """
    try:
        result = await loma_db.billing_reconciliation_leases.update_one(
            {"_id": "status_snapshot", "$or": [
                {"lease_expires_at": {"$lte": now}},
                {"lease_expires_at": {"$exists": False}},
            ]},
            {"$set": {
                "owner": _RECONCILIATION_OWNER,
                "lease_expires_at": now + timedelta(minutes=_RECONCILIATION_LEASE_MINUTES),
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        return False
    return bool(result.modified_count or result.upserted_id)


async def _renew_reconciliation_lease(loma_db: Any, now: datetime) -> bool:
    """Extend our own lease; returns False if another instance has taken it over.

    Scoped to ``owner`` so a sweep that outlives the lease TTL keeps the lease alive
    batch by batch. If we no longer hold it (matched_count == 0), the caller aborts
    instead of racing the new owner, whose cleanup would delete rows we just wrote.
    """
    result = await loma_db.billing_reconciliation_leases.update_one(
        {"_id": "status_snapshot", "owner": _RECONCILIATION_OWNER},
        {"$set": {"lease_expires_at": now + timedelta(minutes=_RECONCILIATION_LEASE_MINUTES)}},
    )
    return result.matched_count == 1


async def _reconcile_org_batch(
    plotline_db: Any,
    loma_db: Any,
    org_docs: list[dict[str, Any]],
    run_id: str,
    checked_at: datetime,
) -> None:
    org_ids = [org["_id"] for org in org_docs]
    product_docs = await plotline_db.products.find(
        {"orgId": {"$in": org_ids}}, {"orgId": 1, "billingId": 1}
    ).to_list(None)
    mappings = await loma_db.billing_account_mappings.find(
        {"organization_id": {"$in": [_id(value) for value in org_ids]}}
    ).to_list(None)
    mapping_by_org = {item["organization_id"]: item for item in mappings}
    billing_ids = list(dict.fromkeys(
        product.get("billingId") for product in product_docs if product.get("billingId")
    ))
    contract_by_id, errored_billing_ids = await _load_contracts(billing_ids)
    operations = []
    for product in product_docs:
        org_id = _id(product.get("orgId"))
        billing_id = product.get("billingId")
        product_id = _id(product.get("_id"))
        if billing_id and billing_id in errored_billing_ids:
            operations.append(_snapshot_operation(
                product_id, org_id, checked_at, status=None, run_id=run_id
            ))
            continue
        status = classify_product(
            billing_id,
            contract_by_id.get(billing_id),
            (mapping_by_org.get(org_id) or {}).get("monetize_now_account_id"),
        )
        operations.append(_snapshot_operation(
            product_id, org_id, checked_at, status=status, run_id=run_id
        ))
    if operations:
        await loma_db.billing_product_statuses.bulk_write(operations, ordered=False)


async def _reconcile_all_statuses() -> None:
    """Build a complete status snapshot in bounded batches for filter worklists."""
    plotline_db, loma_db = get_plotline_db(), get_db()
    if plotline_db is None or loma_db is None:
        return
    run_id = str(uuid4())
    run_start = datetime.now(timezone.utc)
    if not await _acquire_reconciliation_lease(loma_db, run_start):
        return
    # Everything after the lease is taken runs under try/finally so the lease is always
    # released -- even if the initial state write throws -- and a single failure can
    # never leave the lease held for its full TTL or kill the reconciliation task.
    was_ready = False
    try:
        existing_state = await loma_db.billing_reconciliation_state.find_one({"_id": "status_snapshot"})
        was_ready = (existing_state or {}).get("state") == "ready"
        await loma_db.billing_reconciliation_state.update_one(
            {"_id": "status_snapshot"},
            {"$set": {
                "state": "ready" if was_ready else "running",
                "started_at": run_start,
                "run_id": run_id,
            }},
            upsert=True,
        )
        cursor = plotline_db.orgs.find({}, {"_id": 1}).sort("_id", 1)
        while True:
            org_docs = await cursor.to_list(_RECONCILIATION_BATCH_SIZE)
            if not org_docs:
                break
            # Renew before each batch so a sweep longer than the lease TTL is not
            # taken over mid-run; if we have lost the lease, abort rather than race a
            # second run whose cleanup would delete rows this run already wrote.
            if not await _renew_reconciliation_lease(loma_db, datetime.now(timezone.utc)):
                logger.warning("Lost billing reconciliation lease mid-sweep; aborting run %s", run_id)
                return
            # Stamp each batch with the real check time so "last reconciled" only ever
            # moves forward, never backwards past an interactive write made mid-run.
            await _reconcile_org_batch(
                plotline_db, loma_db, org_docs, run_id, datetime.now(timezone.utc)
            )
        # Rows untouched by this complete run refer to products that no longer exist.
        # Interactive writes during the run carry checked_at >= run_start, so keying
        # cleanup on run_start (not the per-batch time) preserves them.
        await loma_db.billing_product_statuses.delete_many({"checked_at": {"$lt": run_start}})
        await loma_db.billing_reconciliation_state.update_one(
            {"_id": "status_snapshot"},
            {"$set": {"state": "ready", "completed_at": datetime.now(timezone.utc), "run_id": run_id}},
        )
    except Exception:
        logger.exception("Billing status reconciliation failed")
        try:
            await loma_db.billing_reconciliation_state.update_one(
                {"_id": "status_snapshot"},
                {"$set": {
                    "state": "ready" if was_ready else "failed",
                    "failed_at": datetime.now(timezone.utc),
                    "run_id": run_id,
                }},
            )
        except Exception:
            logger.exception("Could not record billing reconciliation failure state")
    finally:
        await loma_db.billing_reconciliation_leases.delete_one({
            "_id": "status_snapshot", "owner": _RECONCILIATION_OWNER
        })


async def _reconciliation_context(_app: web.Application):
    """Continuously refresh the complete worklist without blocking app startup."""
    loma_db = get_db()
    if loma_db is not None:
        # Create indexes once at startup rather than on every 15-min cycle, and never
        # let an index-creation blip abort app startup.
        try:
            await _ensure_snapshot_indexes(loma_db)
        except Exception:
            logger.exception("Could not ensure billing snapshot indexes")

    async def loop():
        while True:
            try:
                await _reconcile_all_statuses()
            except Exception:
                # A single failed sweep must never terminate the reconciliation task;
                # otherwise filtered worklists stay stuck at 503 until the app restarts.
                logger.exception("Billing reconciliation cycle crashed; continuing")
            await asyncio.sleep(_RECONCILIATION_INTERVAL_SECONDS)

    task = asyncio.create_task(loop(), name="billing-status-reconciliation")
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _reconcile_organization(plotline_db: Any, loma_db: Any, org_id: str) -> None:
    """Refresh one organization immediately after an operator changes its mapping."""
    object_id = ObjectId(org_id)
    products = await plotline_db.products.find(
        {"orgId": object_id}, {"orgId": 1, "billingId": 1}
    ).to_list(None)
    mapping = await loma_db.billing_account_mappings.find_one({"organization_id": org_id})
    account_id = (mapping or {}).get("monetize_now_account_id")
    billing_ids = list(dict.fromkeys(
        product.get("billingId") for product in products if product.get("billingId")
    ))
    contract_by_id, errored_billing_ids = await _load_contracts(billing_ids)
    checked_at = datetime.now(timezone.utc)
    operations = []
    for product in products:
        product_id = _id(product.get("_id"))
        billing_id = product.get("billingId")
        if billing_id and billing_id in errored_billing_ids:
            operations.append(_snapshot_operation(product_id, org_id, checked_at, status=None))
            continue
        operations.append(_snapshot_operation(
            product_id, org_id, checked_at,
            status=classify_product(billing_id, contract_by_id.get(billing_id), account_id),
        ))
    if operations:
        await loma_db.billing_product_statuses.bulk_write(operations, ordered=False)
    await loma_db.billing_product_statuses.delete_many({
        "organization_id": org_id,
        "product_id": {"$nin": [_id(product.get("_id")) for product in products]},
    })


def _monetizenow_configured() -> bool:
    return bool(os.environ.get("MONETIZE_NOW_API_KEY", "").strip()) and bool(
        os.environ.get("MONETIZE_NOW_BASE_URL", "").strip()
    )


def _billing_unavailable_response() -> web.Response:
    """Explain *why* billing mapping is unavailable without leaking any secret value.

    Turns the opaque 503 operators hit on a misconfigured preview into an
    actionable message naming the missing/unreachable dependency.
    """
    reason = get_plotline_db_status()
    detail = {
        "env-missing": "Set PLOTLINE_MONGODB_URI (or MONGODB_DASHBOARD_URI) on the backend.",
        "connect-failed": "The dashboard MongoDB URI is set but unreachable; check the value, network access, and credentials.",
    }.get(reason, "Billing mapping databases are not configured.")
    return web.json_response(
        {"error": "Billing mapping is unavailable", "reason": reason, "detail": detail},
        status=503,
    )


async def handle_billing_health(request: web.Request) -> web.Response:
    """Operator-facing config diagnostics: booleans/status strings only, never secret
    values, so a misconfigured preview is diagnosable from the dashboard or a curl."""
    require_operator_or_above(request)
    return web.json_response({
        "dashboardDb": get_plotline_db_status(),
        "observabilityDb": "connected" if get_db() is not None else "unavailable",
        "monetizeNowConfigured": _monetizenow_configured(),
    })


async def handle_list_billing_mappings(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    plotline_db, loma_db = get_plotline_db(), get_db()
    if plotline_db is None or loma_db is None:
        return _billing_unavailable_response()

    try:
        page = max(1, int(request.query.get("page", "1")))
        page_size = min(100, max(1, int(request.query.get("pageSize", "25"))))
    except ValueError:
        return web.json_response({"error": "page and pageSize must be integers"}, status=400)
    status_filter = request.query.get("status", "all")
    valid_statuses = set(_WORKLIST_STATUSES)
    if status_filter != "all" and status_filter not in valid_statuses:
        return web.json_response({"error": "Invalid status filter"}, status=400)

    # Filtered reads use the persisted last-known reconciliation state. Live
    # MonetizeNow lookups are limited to the visible page and refresh snapshots.
    if status_filter == "all":
        total = await plotline_db.orgs.count_documents({})
        org_docs = await (
            plotline_db.orgs.find({}, {"name": 1, "isBlocked": 1})
            .sort("name", 1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
        )
    else:
        reconciliation = await loma_db.billing_reconciliation_state.find_one(
            {"_id": "status_snapshot"}
        )
        if not reconciliation or reconciliation.get("state") != "ready":
            return web.json_response(
                {"error": "Billing status reconciliation is still preparing the complete worklist"},
                status=503,
            )
        matching_org_ids = await loma_db.billing_product_statuses.distinct(
            "organization_id", {"status": status_filter}
        )
        object_ids = [ObjectId(value) for value in matching_org_ids if ObjectId.is_valid(value)]
        total = await plotline_db.orgs.count_documents({"_id": {"$in": object_ids}})
        org_docs = await (
            plotline_db.orgs.find(
                {"_id": {"$in": object_ids}}, {"name": 1, "isBlocked": 1}
            ).sort("name", 1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
        )
    org_ids = [org["_id"] for org in org_docs]
    product_docs = await plotline_db.products.find(
        {"orgId": {"$in": org_ids}}, {"name": 1, "orgId": 1, "billingId": 1}
    ).to_list(None)
    mappings = await loma_db.billing_account_mappings.find(
        {"organization_id": {"$in": [_id(value) for value in org_ids]}}
    ).to_list(None)
    mapping_by_org = {item["organization_id"]: item for item in mappings}

    # On filtered reads the snapshot is the source of truth for which orgs appear and
    # for the header total, so display the snapshot status too. Otherwise a live
    # re-classification can list an org under, say, "invalid contract" while every row
    # renders a different (already-fixed) status and the total disagrees with the page.
    snapshot_by_product: dict[str, dict[str, Any]] = {}
    if status_filter != "all":
        snapshots = await loma_db.billing_product_statuses.find(
            {"product_id": {"$in": [_id(product.get("_id")) for product in product_docs]}}
        ).to_list(None)
        snapshot_by_product = {row["product_id"]: row for row in snapshots}

    semaphore = asyncio.Semaphore(12)
    unique_billing_ids = list(dict.fromkeys(
        product.get("billingId") for product in product_docs if product.get("billingId")
    ))
    contracts = await asyncio.gather(*[_load_contract(value, semaphore) for value in unique_billing_ids])
    contract_by_id = dict(zip(unique_billing_ids, contracts))
    products_by_org: dict[str, list[dict[str, Any]]] = {}
    for product in product_docs:
        org_id = _id(product.get("orgId"))
        billing_id = product.get("billingId")
        contract = contract_by_id.get(billing_id)
        account_id = (mapping_by_org.get(org_id) or {}).get("monetize_now_account_id")
        product_id = _id(product.get("_id"))
        live_status = classify_product(billing_id, contract, account_id)
        snapshot = snapshot_by_product.get(product_id)
        # Filtered reads show the snapshot status (consistent with membership + total);
        # "recheckPending" marks a row whose live contract now disagrees with that
        # last-reconciled status, so the status label and contract card are never read
        # as a hard contradiction. The unfiltered view always shows the live status.
        display_status = snapshot.get("status", live_status) if snapshot else live_status
        checked_at = snapshot.get("checked_at") if snapshot else None
        products_by_org.setdefault(org_id, []).append({
            "id": product_id,
            "name": product.get("name") or "Unnamed product",
            "billingId": billing_id,
            "status": display_status,
            "recheckPending": status_filter != "all" and display_status != live_status,
            "statusAsOf": checked_at.isoformat() if hasattr(checked_at, "isoformat") else None,
            "contract": _contract_view(contract) if contract else None,
        })

    organizations = []
    for org in org_docs:
        org_id = _id(org["_id"])
        mapping = mapping_by_org.get(org_id) or {}
        products = products_by_org.get(org_id, [])
        organizations.append({
            "id": org_id,
            "name": org.get("name") or "Unnamed organization",
            "isBlocked": bool(org.get("isBlocked")),
            "monetizeNowAccountId": mapping.get("monetize_now_account_id"),
            "products": products,
            "summary": {status: sum(p["status"] == status for p in products) for status in _WORKLIST_STATUSES},
        })
    if status_filter != "all":
        # Drop orgs whose visible products no longer contain a match: the only matching
        # product may have been deleted upstream, or fixed and re-linked since the last
        # reconcile. Otherwise an orphan snapshot keeps an org in the worklist with no
        # actionable row. (Membership + total stay snapshot-derived; recheckPending and
        # the ~15-min reconcile interval account for the lag.)
        organizations = [
            org for org in organizations
            if any(product["status"] == status_filter for product in org["products"])
        ]
    if status_filter == "all":
        await _store_status_snapshots(loma_db, organizations)
    return web.json_response({
        "organizations": organizations,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "hasNext": page * page_size < total},
    })


async def handle_set_account_mapping(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    plotline_db, loma_db = get_plotline_db(), get_db()
    if plotline_db is None or loma_db is None:
        return _billing_unavailable_response()
    org_id = request.match_info["organization_id"]
    if not ObjectId.is_valid(org_id) or not await plotline_db.orgs.find_one({"_id": ObjectId(org_id)}):
        return web.json_response({"error": "Organization not found"}, status=404)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "A valid JSON object is required"}, status=400)
    account_id = str(body.get("accountId") or "").strip()
    account_result = await get_account(account_id) if account_id else {}
    account, upstream_error = _upstream_payload(account_result)
    if upstream_error:
        return web.json_response({"error": f"MonetizeNow account lookup failed: {upstream_error}"}, status=502)
    if not account:
        return web.json_response({"error": "MonetizeNow account not found"}, status=404)
    canonical_account_id = _id(account.get("id") or account.get("accountId"))
    if not canonical_account_id:
        return web.json_response({"error": "MonetizeNow returned an account without an ID"}, status=502)
    now, actor = datetime.now(timezone.utc), get_user_email(request)
    old = await loma_db.billing_account_mappings.find_one({"organization_id": org_id})
    await loma_db.billing_account_mappings.update_one(
        {"organization_id": org_id},
        {
            "$set": {"organization_id": org_id, "monetize_now_account_id": canonical_account_id, "updated_at": now, "updated_by": actor},
            "$inc": {"mapping_revision": 1},
        },
        upsert=True,
    )
    try:
        await _reconcile_organization(plotline_db, loma_db, org_id)
    except Exception:
        # Keep the prior worklist entry rather than evicting it during an
        # upstream outage. The scheduled reconciler will refresh it later.
        logger.exception("Could not refresh billing statuses for organization %s", org_id)
    await loma_db.billing_mapping_audit.insert_one({
        "type": "account_mapping", "organization_id": org_id,
        "old_value": (old or {}).get("monetize_now_account_id"), "new_value": canonical_account_id,
        "updated_at": now, "updated_by": actor,
    })
    return web.json_response({"organizationId": org_id, "accountId": canonical_account_id})


async def handle_active_contracts(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    loma_db = get_db()
    org_id = request.match_info["organization_id"]
    mapping = await loma_db.billing_account_mappings.find_one({"organization_id": org_id}) if loma_db is not None else None
    account_id = (mapping or {}).get("monetize_now_account_id")
    if not account_id:
        return web.json_response({"error": "Link a MonetizeNow account first"}, status=400)
    contracts: list[dict[str, Any]] = []
    page, page_size = 0, 100
    while True:
        result = await account_contracts(account_id, status=ACTIVE, page=page, page_size=page_size)
        if not isinstance(result, dict) or result.get("error"):
            error = result.get("error") if isinstance(result, dict) else "Invalid response"
            return web.json_response({"error": f"MonetizeNow contract lookup failed: {error}"}, status=502)
        items = _items(result)
        contracts.extend(
            _contract_view(item) for item in items
            if str(item.get("status") or "").upper() == ACTIVE
        )
        if len(items) < page_size:
            break
        page += 1
    return web.json_response({"contracts": contracts})


async def handle_set_product_contract(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    plotline_db, loma_db = get_plotline_db(), get_db()
    if plotline_db is None or loma_db is None:
        return _billing_unavailable_response()
    product_id = request.match_info["product_id"]
    if not ObjectId.is_valid(product_id):
        return web.json_response({"error": "Product not found"}, status=404)
    product = await plotline_db.products.find_one({"_id": ObjectId(product_id)})
    if not product:
        return web.json_response({"error": "Product not found"}, status=404)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "A valid JSON object is required"}, status=400)
    contract_id = str(body.get("contractId") or "").strip()
    if not contract_id:
        return web.json_response({"error": "contractId is required"}, status=400)
    contract, upstream_error = _upstream_payload(await get_contract(contract_id))
    if upstream_error:
        return web.json_response({"error": f"MonetizeNow contract lookup failed: {upstream_error}"}, status=502)
    mapping = await loma_db.billing_account_mappings.find_one({"organization_id": _id(product.get("orgId"))})
    account_id = (mapping or {}).get("monetize_now_account_id")
    mapping_revision = (mapping or {}).get("mapping_revision", 0)
    if not contract:
        return web.json_response({"error": "MonetizeNow contract not found"}, status=404)
    if str(contract.get("status") or "").upper() != ACTIVE:
        return web.json_response({"error": "Only an ACTIVE contract can be linked"}, status=400)
    if not account_id or _contract_account_id(contract) != account_id:
        return web.json_response({"error": "Contract does not belong to the mapped account"}, status=400)
    org_id = _id(product.get("orgId"))
    now, actor, old = datetime.now(timezone.utc), get_user_email(request), product.get("billingId")
    if old == contract_id:
        return web.json_response({"productId": product_id, "contractId": contract_id})
    old_updated_at = product.get("updatedAt")
    revision_match: Any = mapping_revision if mapping_revision else {"$in": [0, None]}
    current_mapping = await loma_db.billing_account_mappings.find_one({
        "organization_id": org_id,
        "monetize_now_account_id": account_id,
        "mapping_revision": revision_match,
    })
    if not current_mapping:
        return web.json_response({"error": "Account mapping changed; retry with the latest mapping"}, status=409)
    update_result = await plotline_db.products.update_one(
        {"_id": ObjectId(product_id), "orgId": product.get("orgId"), "billingId": old},
        {"$set": {"billingId": contract_id, "updatedAt": now}},
    )
    if update_result.modified_count != 1 and old != contract_id:
        return web.json_response({"error": "Product mapping changed; refresh and retry"}, status=409)

    mapping_after_write = await loma_db.billing_account_mappings.find_one({
        "organization_id": org_id,
        "monetize_now_account_id": account_id,
    })
    if not mapping_after_write:
        # These records live in separate MongoDB deployments, so no distributed
        # transaction can make the two writes atomic. The monotonic revision
        # detects cross-process remaps; this conditional rollback is best-effort.
        rollback_update: dict[str, Any] = {"$set": {"billingId": old}}
        if old_updated_at is None:
            rollback_update["$unset"] = {"updatedAt": ""}
        else:
            rollback_update["$set"]["updatedAt"] = old_updated_at
        try:
            rollback = await plotline_db.products.update_one(
                {"_id": ObjectId(product_id), "orgId": product.get("orgId"), "billingId": contract_id, "updatedAt": now},
                rollback_update,
            )
        except Exception:
            logger.exception("Failed to roll back stale contract mapping for product %s", product_id)
            return web.json_response({"error": "Account mapping changed and automatic recovery failed"}, status=500)
        if rollback.modified_count != 1:
            logger.error("Could not roll back stale contract mapping for product %s because it changed again", product_id)
            return web.json_response({"error": "Account mapping changed and product was concurrently modified"}, status=409)
        return web.json_response({"error": "Account mapping changed; product update was reverted"}, status=409)
    await loma_db.billing_mapping_audit.insert_one({
        "type": "product_contract", "organization_id": org_id, "product_id": product_id,
        "old_value": old, "new_value": contract_id, "updated_at": now, "updated_by": actor,
    })
    await loma_db.billing_product_statuses.update_one(
        {"product_id": product_id},
        {"$set": {
            "product_id": product_id,
            "organization_id": org_id,
            "status": "correctly_linked",
            "checked_at": now,
        }},
        upsert=True,
    )
    return web.json_response({"productId": product_id, "contractId": contract_id})


def setup_billing_mapping_routes(app: web.Application):
    app.router.add_get("/api/billing-mappings", handle_list_billing_mappings)
    app.router.add_get("/api/billing-mappings/health", handle_billing_health)
    app.router.add_put("/api/billing-mappings/organizations/{organization_id}/account", handle_set_account_mapping)
    app.router.add_get("/api/billing-mappings/organizations/{organization_id}/contracts", handle_active_contracts)
    app.router.add_put("/api/billing-mappings/products/{product_id}/contract", handle_set_product_contract)
    app.cleanup_ctx.append(_reconciliation_context)
