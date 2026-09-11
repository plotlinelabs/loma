"""Billing mapping API — link Plotline client orgs to MonetizeNow accounts.

Reuses the existing connectors only:
- Plotline prod MongoDB via the `mongodb` integration stored in db.integrations
  (same connection string the Integrations page manages — no new auth config).
- MonetizeNow via tools/monetize_now.py (MONETIZE_NOW_API_KEY / _BASE_URL env).

Manual org → account links are persisted in the loma DB (`billing_mappings`).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

from observability.db import get_db
from api.auth_helpers import get_user_email
from tools.monetize_now import list_accounts, search_accounts

logger = logging.getLogger(__name__)

_ACCOUNTS_CACHE_TTL_SECONDS = 300
_ACCOUNTS_PAGE_SIZE = 100
_ACCOUNTS_MAX_PAGES = 50

_plotline_client: AsyncIOMotorClient | None = None
_plotline_uri: str | None = None
_accounts_cache: tuple[float, list[dict[str, Any]]] | None = None


# ── Existing-connector access ──────────────────────────────────────────────


async def _get_plotline_db():
    """Plotline prod DB via the `mongodb` integration from the Integrations page."""
    global _plotline_client, _plotline_uri
    db = get_db()
    if db is None:
        return None
    doc = await db.integrations.find_one(
        {"provider": "mongodb", "status": "active"}, {"api_key_encrypted": 1}
    )
    if not doc or not doc.get("api_key_encrypted"):
        return None
    from api.oauth_helpers import decrypt_token

    uri = decrypt_token(doc["api_key_encrypted"])
    if _plotline_client is None or uri != _plotline_uri:
        if _plotline_client is not None:
            _plotline_client.close()
        _plotline_client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        _plotline_uri = uri
    return _plotline_client.get_default_database(default="plotline")


def _account_items(result: Any) -> list[dict[str, Any]]:
    """Extract the account list from a MonetizeNow list/search response."""
    data = result.get("data", result) if isinstance(result, dict) else {}
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("content", "items", "results", "accounts"):
        if isinstance(data.get(key), list):
            return [item for item in data[key] if isinstance(item, dict)]
    return []


async def _fetch_mn_accounts() -> list[dict[str, Any]]:
    """All MonetizeNow accounts, paged once and cached in-memory for 5 minutes."""
    global _accounts_cache
    now = time.monotonic()
    if _accounts_cache and now - _accounts_cache[0] < _ACCOUNTS_CACHE_TTL_SECONDS:
        return _accounts_cache[1]
    accounts: list[dict[str, Any]] = []
    for page in range(_ACCOUNTS_MAX_PAGES):
        result = await list_accounts(page=page, page_size=_ACCOUNTS_PAGE_SIZE)
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError(str((result or {}).get("error") or "Invalid MonetizeNow response"))
        items = _account_items(result)
        accounts.extend(items)
        if len(items) < _ACCOUNTS_PAGE_SIZE:
            break
    _accounts_cache = (now, accounts)
    return accounts


def _serialize_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(account.get("id") or ""),
        "name": account.get("name") or "",
        "custom_id": account.get("customId") or None,
    }


def _normalize(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


# ── Handlers ───────────────────────────────────────────────────────────────


async def handle_get_mapping(request: web.Request) -> web.Response:
    """GET /api/billing/mapping — all client orgs with their MonetizeNow link status."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)
    plotline_db = await _get_plotline_db()
    if plotline_db is None:
        return web.json_response(
            {"error": "MongoDB integration not connected. Link it on the Integrations page."},
            status=503,
        )

    try:
        orgs = await plotline_db.orgs.find({}, {"name": 1, "isBlocked": 1}).sort("name", 1).to_list(None)
        products = await plotline_db.products.find({}, {"orgId": 1, "billingId": 1}).to_list(None)
    except Exception:
        logger.exception("Failed to query Plotline prod MongoDB")
        return web.json_response({"error": "Plotline MongoDB unreachable"}, status=502)

    products_by_org: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        products_by_org.setdefault(str(product.get("orgId")), []).append(product)

    mappings = await db.billing_mappings.find({}).to_list(None)
    mapping_by_org = {m["org_id"]: m for m in mappings}

    accounts_error = None
    accounts_by_name: dict[str, dict[str, Any]] = {}
    try:
        for account in await _fetch_mn_accounts():
            key = _normalize(account.get("name") or "")
            if key:
                # First match wins on duplicate normalized names.
                accounts_by_name.setdefault(key, account)
    except Exception as exc:
        logger.exception("Failed to fetch MonetizeNow accounts")
        accounts_error = str(exc)

    rows = []
    for org in orgs:
        org_id = str(org["_id"])
        org_products = products_by_org.get(org_id, [])
        mapping = mapping_by_org.get(org_id)
        auto_match = accounts_by_name.get(_normalize(org.get("name") or ""))
        if mapping:
            status, account = "linked", {
                "id": mapping["mn_account_id"],
                "name": mapping.get("mn_account_name") or "",
                "custom_id": None,
            }
        elif auto_match:
            status, account = "auto_matched", _serialize_account(auto_match)
        else:
            status, account = "not_found", None
        rows.append({
            "org_id": org_id,
            "org_name": org.get("name") or org_id,
            "is_blocked": bool(org.get("isBlocked")),
            "products_count": len(org_products),
            "has_billing_id": any(p.get("billingId") for p in org_products),
            "status": status,
            "account": account,
            "linked_by": (mapping or {}).get("linked_by"),
        })

    return web.json_response({"organisations": rows, "accounts_error": accounts_error})


async def handle_search_accounts(request: web.Request) -> web.Response:
    """GET /api/billing/accounts?q= — MonetizeNow account picker for manual linking."""
    query = (request.query.get("q") or "").strip()
    if not query:
        return web.json_response({"accounts": []})
    result = await search_accounts(query)
    if not isinstance(result, dict) or result.get("error"):
        return web.json_response(
            {"error": str((result or {}).get("error") or "MonetizeNow search failed")}, status=502
        )
    return web.json_response({"accounts": [_serialize_account(a) for a in _account_items(result)]})


async def handle_link_org(request: web.Request) -> web.Response:
    """PUT /api/billing/mapping/{org_id} — manually link an org to a MonetizeNow account."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)
    org_id = request.match_info["org_id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    account_id = str(body.get("account_id") or "").strip()
    if not account_id:
        return web.json_response({"error": "account_id is required"}, status=400)
    await db.billing_mappings.update_one(
        {"org_id": org_id},
        {"$set": {
            "org_id": org_id,
            "org_name": body.get("org_name") or "",
            "mn_account_id": account_id,
            "mn_account_name": body.get("account_name") or "",
            "linked_by": get_user_email(request),
            "linked_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return web.json_response({"ok": True})


async def handle_unlink_org(request: web.Request) -> web.Response:
    """DELETE /api/billing/mapping/{org_id} — remove a manual link."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)
    await db.billing_mappings.delete_one({"org_id": request.match_info["org_id"]})
    return web.json_response({"ok": True})


# ── Route registration ─────────────────────────────────────────────────────


def setup_billing_routes(app: web.Application):
    """Register billing mapping routes."""
    app.router.add_get("/api/billing/mapping", handle_get_mapping)
    app.router.add_get("/api/billing/accounts", handle_search_accounts)
    app.router.add_put("/api/billing/mapping/{org_id}", handle_link_org)
    app.router.add_delete("/api/billing/mapping/{org_id}", handle_unlink_org)
