"""Owner-scoped Asset list HTTP routes.

GET /api/assets returns only the authenticated caller's Assets, newest first.
The request and response accept a query object so a later search filter can
slot in without a rewrite. v1 sends none and does not filter.
"""
from datetime import datetime, timezone

from aiohttp import web

from api.auth_helpers import get_user_email
from observability.assets import (
    has_live_serve_source,
    list_assets,
    resolve_asset_availability,
)


def _serialize_created_at(value) -> str | None:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _serialize_asset(doc: dict) -> dict:
    created_at = _serialize_created_at(doc.get("created_at"))
    return {
        "file_id": doc.get("file_id"),
        "name": doc.get("name") or "",
        "mime_type": doc.get("mime_type") or "application/octet-stream",
        "size_bytes": int(doc.get("size_bytes") or 0),
        "created_at": created_at,
        "conversation_id": doc.get("conversation_id"),
        "source": doc.get("source") or "",
    }


def _asset_query(request: web.Request) -> dict:
    q = request.query.get("q", "").strip()
    return {"q": q} if q else {}


async def handle_list_assets(request: web.Request) -> web.Response:
    """GET /api/assets — caller's Assets, newest first."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    query = _asset_query(request)
    rows = await list_assets(user_email)
    assets = []
    for row in rows:
        file_id = row.get("file_id") or ""
        if not has_live_serve_source(file_id):
            continue
        item = _serialize_asset(row)
        status, reason = await resolve_asset_availability(file_id)
        item["status"] = status
        item["reason"] = reason
        assets.append(item)
    return web.json_response({
        "assets": assets,
        "query": query,
    })


def setup_asset_routes(app: web.Application):
    app.router.add_get("/api/assets", handle_list_assets)
