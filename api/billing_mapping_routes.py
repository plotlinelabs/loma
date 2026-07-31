"""Operator organization-to-billing-contract reconciliation API.

Builds a single reconciled view of every organization, its products, and the
billing contract each product points at. The billing account is *derived from the
contract itself*, so operators never have to know or type an account id; the
optional per-organization override exists only to declare the expected account
when a human knows better than the data.

The whole estate is small (hundreds of organizations and products), so one full
pass is cheap and is cached in-process for a few minutes. There is deliberately
no background reconciler, no lease, and no snapshot collection: filtering and
pagination are applied to the in-memory view, which also keeps the header total
consistent with the rows actually rendered.
"""

import asyncio
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
import logging
import os
import time
from typing import Any

from aiohttp import web
from bson import ObjectId

from api.auth_helpers import (
    get_user_email,
    require_maintainer_or_above,
    require_operator_or_above,
)
from api.dashboard_db import get_dashboard_db, get_dashboard_db_status
from observability.db import get_db
from tools.monetize_now import account_contracts, get_account, get_contract

ACTIVE = "ACTIVE"
logger = logging.getLogger(__name__)

# "unknown" is only ever produced when an upstream lookup failed, so a genuinely
# broken product never silently disappears from a worklist because the billing
# provider was flapping.
STATUSES = (
    "correctly_linked",
    "contract_missing",
    "invalid_contract",
    "inactive_contract",
    "account_mismatch",
    "unknown",
)

_UPSTREAM_CONCURRENCY = 16
_VIEW_TTL_SECONDS = 300
_CONTRACT_CACHE_TTL_SECONDS = 300
_CONTRACT_CACHE_MAX_ENTRIES = 2048
_contract_cache: OrderedDict[str, tuple[float, dict[str, Any] | None]] = OrderedDict()
# One cached full view, rebuilt at most once per TTL. The lock collapses a
# thundering herd of concurrent page loads into a single upstream sweep.
_view_cache: tuple[float, dict[str, Any]] | None = None
_view_lock = asyncio.Lock()


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
        return None, "Invalid response from the billing provider"
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


def _contract_view(
    contract: dict[str, Any], account_by_id: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    contract_id = _id(contract.get("id") or contract.get("contractId"))
    account_id = _contract_account_id(contract)
    account = (account_by_id or {}).get(account_id) or {}
    return {
        "id": contract_id,
        "name": contract.get("name") or contract.get("displayName") or contract_id,
        "status": str(contract.get("status") or "UNKNOWN").upper(),
        "accountId": account_id,
        "accountName": account.get("name") or account.get("displayName"),
        "legalEntityId": _id(contract.get("legalEntityId")),
        "startDate": contract.get("startDate") or contract.get("effectiveFrom"),
        "url": f"https://app.monetizenow.io/contracts/{contract_id}" if contract_id else None,
    }


def classify_product(
    billing_id: str | None,
    contract: dict[str, Any] | None,
    expected_account_id: str | None,
    *,
    lookup_failed: bool = False,
) -> str:
    """Classify one product's contract linkage.

    ``expected_account_id`` is the account the organization is believed to belong
    to (an operator override, else the account most of its other contracts point
    at). When it is unknown there is nothing to disagree with, so a live ACTIVE
    contract is simply correct.
    """
    if not billing_id:
        return "contract_missing"
    if lookup_failed:
        return "unknown"
    if not contract:
        return "invalid_contract"
    if str(contract.get("status") or "").upper() != ACTIVE:
        return "inactive_contract"
    if expected_account_id and _contract_account_id(contract) != expected_account_id:
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
        except Exception as exc:  # noqa: BLE001 - upstream failure must never 500 the page
            payload, error = None, str(exc)
    if error:
        logger.warning("Contract lookup failed for %s: %s", billing_id, error)
        # Record the failure so callers classify as "unknown" rather than
        # misreporting a transient upstream error as a broken link.
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
    returned so the caller can mark just those products "unknown".
    """
    semaphore = asyncio.Semaphore(_UPSTREAM_CONCURRENCY)
    errored: set[str] = set()
    contracts = await asyncio.gather(
        *[_load_contract(value, semaphore, errored=errored) for value in billing_ids]
    )
    return dict(zip(billing_ids, contracts)), errored


async def _load_accounts(account_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve account ids to account records so the UI can show real names."""
    semaphore = asyncio.Semaphore(_UPSTREAM_CONCURRENCY)

    async def one(account_id: str):
        async with semaphore:
            try:
                payload, error = _upstream_payload(await get_account(account_id))
            except Exception as exc:  # noqa: BLE001
                payload, error = None, str(exc)
        if error:
            logger.warning("Account lookup failed for %s: %s", account_id, error)
        return account_id, payload

    results = await asyncio.gather(*[one(value) for value in account_ids])
    return {key: value for key, value in results if value}


def _expected_account_id(
    override: str | None, contracts: list[dict[str, Any] | None]
) -> tuple[str | None, str]:
    """Return the organization's expected account id and where it came from.

    An explicit operator override always wins. Otherwise the organization is
    assumed to belong to whichever account most of its resolved contracts point
    at, which makes the odd contract out visible as an account_mismatch without
    anyone having to hand-enter 160+ account ids.
    """
    if override:
        return override, "override"
    counts = Counter(
        _contract_account_id(contract)
        for contract in contracts
        if contract and _contract_account_id(contract)
    )
    if not counts:
        return None, "none"
    return counts.most_common(1)[0][0], "derived"


async def _build_view(dashboard_db: Any, loma_db: Any) -> dict[str, Any]:
    """One full pass over every organization, product, contract, and account."""
    org_docs = await dashboard_db.orgs.find(
        {}, {"name": 1, "products": 1, "shouldDisableDashboard": 1}
    ).sort("name", 1).to_list(None)

    # Organizations own products via an array of ObjectId references. Some of
    # those references point at products that have since been deleted, so the
    # lookup is intentionally tolerant and the dangling count is surfaced.
    referenced_ids = [
        product_id
        for org in org_docs
        for product_id in (org.get("products") or [])
    ]
    product_docs = await dashboard_db.products.find(
        {"_id": {"$in": referenced_ids}}, {"name": 1, "billingId": 1}
    ).to_list(None)
    product_by_id = {doc["_id"]: doc for doc in product_docs}

    overrides = {
        row["organization_id"]: row.get("monetize_now_account_id")
        for row in await loma_db.billing_account_mappings.find({}).to_list(None)
        if row.get("organization_id")
    }

    billing_ids = list(dict.fromkeys(
        doc.get("billingId") for doc in product_docs if doc.get("billingId")
    ))
    contract_by_id, errored_billing_ids = await _load_contracts(billing_ids)
    account_ids = list(dict.fromkeys(
        _contract_account_id(contract)
        for contract in contract_by_id.values()
        if contract and _contract_account_id(contract)
    ))
    account_by_id = await _load_accounts(account_ids)

    # A contract legitimately covers many products inside one organization. The
    # same contract appearing under *different* organizations is the mis-linking
    # signal worth surfacing.
    orgs_by_contract: dict[str, set[str]] = defaultdict(set)
    for org in org_docs:
        for product_id in org.get("products") or []:
            product = product_by_id.get(product_id)
            if product and product.get("billingId"):
                orgs_by_contract[product["billingId"]].add(_id(org["_id"]))

    organizations = []
    for org in org_docs:
        org_id = _id(org["_id"])
        refs = org.get("products") or []
        resolved = [product_by_id[pid] for pid in refs if pid in product_by_id]
        contracts = [contract_by_id.get(doc.get("billingId")) for doc in resolved]
        expected_account, account_source = _expected_account_id(
            overrides.get(org_id), contracts
        )
        products = []
        for doc in resolved:
            billing_id = doc.get("billingId")
            contract = contract_by_id.get(billing_id)
            shared_with = sorted(orgs_by_contract.get(billing_id, set()) - {org_id})
            products.append({
                "id": _id(doc["_id"]),
                "name": doc.get("name") or "Unnamed product",
                "billingId": billing_id,
                "status": classify_product(
                    billing_id,
                    contract,
                    expected_account,
                    lookup_failed=bool(billing_id and billing_id in errored_billing_ids),
                ),
                "contract": _contract_view(contract, account_by_id) if contract else None,
                "sharedWithOrganizationIds": shared_with,
            })
        account = account_by_id.get(expected_account or "") or {}
        organizations.append({
            "id": org_id,
            "name": org.get("name") or "Unnamed organization",
            "dashboardDisabled": bool(org.get("shouldDisableDashboard")),
            "accountId": expected_account,
            "accountName": account.get("name") or account.get("displayName"),
            "accountSource": account_source,
            "danglingProductRefs": len(refs) - len(resolved),
            "products": products,
            "summary": {
                status: sum(item["status"] == status for item in products)
                for status in STATUSES
            },
        })

    org_name_by_id = {org["id"]: org["name"] for org in organizations}
    for org in organizations:
        for product in org["products"]:
            product["sharedWithOrganizations"] = [
                org_name_by_id.get(value, value)
                for value in product.pop("sharedWithOrganizationIds")
            ]

    return {
        "organizations": organizations,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "upstreamFailures": len(errored_billing_ids),
    }


async def _get_view(dashboard_db: Any, loma_db: Any, *, refresh: bool = False) -> dict[str, Any]:
    global _view_cache
    async with _view_lock:
        now = time.monotonic()
        if not refresh and _view_cache and _view_cache[0] > now:
            return _view_cache[1]
        view = await _build_view(dashboard_db, loma_db)
        _view_cache = (now + _VIEW_TTL_SECONDS, view)
        return view


def _invalidate_view() -> None:
    """Drop the cached view so the next read reflects a just-made change."""
    global _view_cache
    _view_cache = None


def _monetizenow_configured() -> bool:
    return bool(os.environ.get("MONETIZE_NOW_API_KEY", "").strip()) and bool(
        os.environ.get("MONETIZE_NOW_BASE_URL", "").strip()
    )


def _billing_unavailable_response() -> web.Response:
    """Explain *why* billing mapping is unavailable without leaking any secret value."""
    reason = get_dashboard_db_status()
    detail = {
        "env-missing": "Set DASHBOARD_MONGODB_URI (or MONGODB_DASHBOARD_URI) on the backend.",
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
        "dashboardDb": get_dashboard_db_status(),
        "observabilityDb": "connected" if get_db() is not None else "unavailable",
        "billingProviderConfigured": _monetizenow_configured(),
    })


async def handle_list_billing_mappings(request: web.Request) -> web.Response:
    require_operator_or_above(request)
    dashboard_db, loma_db = get_dashboard_db(), get_db()
    if dashboard_db is None or loma_db is None:
        return _billing_unavailable_response()

    try:
        page = max(1, int(request.query.get("page", "1")))
        page_size = min(100, max(1, int(request.query.get("pageSize", "25"))))
    except ValueError:
        return web.json_response({"error": "page and pageSize must be integers"}, status=400)
    status_filter = request.query.get("status", "all")
    if status_filter != "all" and status_filter not in set(STATUSES):
        return web.json_response({"error": "Invalid status filter"}, status=400)
    search = request.query.get("q", "").strip().lower()
    refresh = request.query.get("refresh") in ("1", "true", "yes")

    view = await _get_view(dashboard_db, loma_db, refresh=refresh)
    organizations = view["organizations"]
    if status_filter != "all":
        organizations = [
            org for org in organizations
            if any(product["status"] == status_filter for product in org["products"])
        ]
    if search:
        organizations = [
            org for org in organizations
            if search in org["name"].lower()
            or search in (org.get("accountName") or "").lower()
            or search in org["id"].lower()
            or search in (org.get("accountId") or "").lower()
        ]

    # The total is computed from the same filtered list the page is sliced out
    # of, so the header count can never disagree with the rendered rows.
    total = len(organizations)
    start = (page - 1) * page_size
    visible = organizations[start:start + page_size]

    totals = {
        status: sum(org["summary"][status] for org in view["organizations"])
        for status in STATUSES
    }
    return web.json_response({
        "organizations": visible,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "hasNext": start + page_size < total,
        },
        "totals": totals,
        "generatedAt": view["generatedAt"],
        "upstreamFailures": view["upstreamFailures"],
    })


async def handle_set_account_mapping(request: web.Request) -> web.Response:
    """Override the account an organization is expected to belong to.

    Optional: the account is normally derived from the organization's own
    contracts. An empty accountId clears the override and restores derivation.
    """
    require_maintainer_or_above(request)
    dashboard_db, loma_db = get_dashboard_db(), get_db()
    if dashboard_db is None or loma_db is None:
        return _billing_unavailable_response()
    org_id = request.match_info["organization_id"]
    if not ObjectId.is_valid(org_id) or not await dashboard_db.orgs.find_one({"_id": ObjectId(org_id)}):
        return web.json_response({"error": "Organization not found"}, status=404)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "A valid JSON object is required"}, status=400)
    account_id = str(body.get("accountId") or "").strip()
    now, actor = datetime.now(timezone.utc), get_user_email(request)
    old = await loma_db.billing_account_mappings.find_one({"organization_id": org_id})

    if not account_id:
        await loma_db.billing_account_mappings.delete_one({"organization_id": org_id})
        await loma_db.billing_mapping_audit.insert_one({
            "type": "account_mapping", "organization_id": org_id,
            "old_value": (old or {}).get("monetize_now_account_id"), "new_value": None,
            "updated_at": now, "updated_by": actor,
        })
        _invalidate_view()
        return web.json_response({"organizationId": org_id, "accountId": None})

    account, upstream_error = _upstream_payload(await get_account(account_id))
    if upstream_error:
        return web.json_response({"error": f"Account lookup failed: {upstream_error}"}, status=502)
    if not account:
        return web.json_response({"error": "Billing account not found"}, status=404)
    canonical_account_id = _id(account.get("id") or account.get("accountId"))
    if not canonical_account_id:
        return web.json_response({"error": "Billing provider returned an account without an ID"}, status=502)
    await loma_db.billing_account_mappings.update_one(
        {"organization_id": org_id},
        {"$set": {
            "organization_id": org_id,
            "monetize_now_account_id": canonical_account_id,
            "updated_at": now,
            "updated_by": actor,
        }},
        upsert=True,
    )
    await loma_db.billing_mapping_audit.insert_one({
        "type": "account_mapping", "organization_id": org_id,
        "old_value": (old or {}).get("monetize_now_account_id"),
        "new_value": canonical_account_id,
        "updated_at": now, "updated_by": actor,
    })
    _invalidate_view()
    return web.json_response({
        "organizationId": org_id,
        "accountId": canonical_account_id,
        "accountName": account.get("name") or account.get("displayName"),
    })


async def handle_active_contracts(request: web.Request) -> web.Response:
    """List the ACTIVE contracts an operator can pick from for this organization."""
    require_operator_or_above(request)
    dashboard_db, loma_db = get_dashboard_db(), get_db()
    if dashboard_db is None or loma_db is None:
        return _billing_unavailable_response()
    org_id = request.match_info["organization_id"]
    view = await _get_view(dashboard_db, loma_db)
    org = next((item for item in view["organizations"] if item["id"] == org_id), None)
    if org is None:
        return web.json_response({"error": "Organization not found"}, status=404)
    account_id = org.get("accountId")
    if not account_id:
        return web.json_response(
            {"error": "No billing account is known for this organization; set one first"},
            status=400,
        )
    contracts: list[dict[str, Any]] = []
    page, page_size = 0, 100
    while True:
        result = await account_contracts(account_id, status=ACTIVE, page=page, page_size=page_size)
        if not isinstance(result, dict) or result.get("error"):
            error = result.get("error") if isinstance(result, dict) else "Invalid response"
            return web.json_response({"error": f"Contract lookup failed: {error}"}, status=502)
        items = _items(result)
        contracts.extend(
            _contract_view(item) for item in items
            if str(item.get("status") or "").upper() == ACTIVE
        )
        if len(items) < page_size:
            break
        page += 1
    return web.json_response({"contracts": contracts, "accountId": account_id})


async def handle_set_product_contract(request: web.Request) -> web.Response:
    """Point one product at a billing contract.

    This writes into the product dashboard database, so it is gated at maintainer
    and guarded by a compare-and-set on the previous value.
    """
    require_maintainer_or_above(request)
    dashboard_db, loma_db = get_dashboard_db(), get_db()
    if dashboard_db is None or loma_db is None:
        return _billing_unavailable_response()
    product_id = request.match_info["product_id"]
    if not ObjectId.is_valid(product_id):
        return web.json_response({"error": "Product not found"}, status=404)
    product = await dashboard_db.products.find_one({"_id": ObjectId(product_id)})
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
        return web.json_response({"error": f"Contract lookup failed: {upstream_error}"}, status=502)
    if not contract:
        return web.json_response({"error": "Billing contract not found"}, status=404)
    if str(contract.get("status") or "").upper() != ACTIVE:
        return web.json_response({"error": "Only an ACTIVE contract can be linked"}, status=400)

    # Products are owned through orgs.products[], so resolve the owner that way.
    org = await dashboard_db.orgs.find_one(
        {"products": ObjectId(product_id)}, {"name": 1, "products": 1}
    )
    org_id = _id(org["_id"]) if org else ""
    if org is not None:
        mapping = await loma_db.billing_account_mappings.find_one({"organization_id": org_id})
        override = (mapping or {}).get("monetize_now_account_id")
        if override and _contract_account_id(contract) != override:
            return web.json_response(
                {"error": "Contract does not belong to the account mapped to this organization"},
                status=400,
            )

    old = product.get("billingId")
    if old == contract_id:
        return web.json_response({"productId": product_id, "contractId": contract_id})
    now, actor = datetime.now(timezone.utc), get_user_email(request)
    update_result = await dashboard_db.products.update_one(
        {"_id": ObjectId(product_id), "billingId": old},
        {"$set": {"billingId": contract_id, "updatedAt": now}},
    )
    if update_result.modified_count != 1:
        return web.json_response({"error": "Product changed; refresh and retry"}, status=409)
    await loma_db.billing_mapping_audit.insert_one({
        "type": "product_contract", "organization_id": org_id, "product_id": product_id,
        "old_value": old, "new_value": contract_id, "updated_at": now, "updated_by": actor,
    })
    _invalidate_view()
    return web.json_response({"productId": product_id, "contractId": contract_id})


def setup_billing_mapping_routes(app: web.Application):
    app.router.add_get("/api/billing-mappings", handle_list_billing_mappings)
    app.router.add_get("/api/billing-mappings/health", handle_billing_health)
    app.router.add_put("/api/billing-mappings/organizations/{organization_id}/account", handle_set_account_mapping)
    app.router.add_get("/api/billing-mappings/organizations/{organization_id}/contracts", handle_active_contracts)
    app.router.add_put("/api/billing-mappings/products/{product_id}/contract", handle_set_product_contract)
