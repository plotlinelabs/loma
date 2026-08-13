"""Notification inbox routes.

Per-user persistent notifications (see observability/notifications.py).
`read` clears the unread badge; `dismissed` removes the card from the
inbox list. All routes are scoped to the authenticated user.
"""

import logging
from datetime import datetime, timezone

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import get_user_email

logger = logging.getLogger(__name__)

_MAX_LIMIT = 100


def _serialize(doc: dict) -> dict:
    """Stringify _id and isoformat datetimes for JSON responses."""
    out = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        if isinstance(value, datetime):
            if value.tzinfo is None:
                out[key] = value.isoformat() + "Z"
            else:
                out[key] = value.isoformat()
        else:
            out[key] = value
    return out


async def handle_list_notifications(request: web.Request) -> web.Response:
    """GET /api/notifications?include_dismissed=1&limit=50 — own inbox, newest first."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        limit = min(int(request.query.get("limit", "50")), _MAX_LIMIT)
    except ValueError:
        limit = 50
    query: dict = {"user_email": user_email}
    if request.query.get("include_dismissed") not in ("1", "true"):
        query["dismissed"] = {"$ne": True}

    docs = await db.notifications.find(query).sort("created_at", -1).to_list(limit)
    return web.json_response({"notifications": [_serialize(d) for d in docs]})


async def handle_unread_count(request: web.Request) -> web.Response:
    """GET /api/notifications/unread-count — cheap poll target for the bell badge."""
    db = get_db()
    if db is None:
        return web.json_response({"count": 0})
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    count = await db.notifications.count_documents({
        "user_email": user_email,
        "read": {"$ne": True},
        "dismissed": {"$ne": True},
    })
    return web.json_response({"count": count})


async def handle_read_all(request: web.Request) -> web.Response:
    """POST /api/notifications/read-all — mark every own notification read."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    result = await db.notifications.update_many(
        {"user_email": user_email, "read": {"$ne": True}},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc)}},
    )
    return web.json_response({"updated": result.modified_count})


async def _update_own_notification(request: web.Request, updates: dict) -> web.Response:
    """Shared guard: update a notification only if it belongs to the caller."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    notification_id = request.match_info.get("notification_id", "")
    result = await db.notifications.update_one(
        {"notification_id": notification_id, "user_email": user_email},
        {"$set": updates},
    )
    if result.matched_count == 0:
        return web.json_response({"error": "Notification not found"}, status=404)
    return web.json_response({"ok": True})


async def handle_mark_read(request: web.Request) -> web.Response:
    """POST /api/notifications/{notification_id}/read"""
    return await _update_own_notification(
        request, {"read": True, "read_at": datetime.now(timezone.utc)},
    )


async def handle_dismiss(request: web.Request) -> web.Response:
    """POST /api/notifications/{notification_id}/dismiss — also marks read."""
    return await _update_own_notification(
        request,
        {"dismissed": True, "read": True, "dismissed_at": datetime.now(timezone.utc)},
    )


def setup_notification_routes(app: web.Application):
    """Register notification inbox routes on the aiohttp app."""
    # Static paths before {notification_id} routes.
    app.router.add_get("/api/notifications", handle_list_notifications)
    app.router.add_get("/api/notifications/unread-count", handle_unread_count)
    app.router.add_post("/api/notifications/read-all", handle_read_all)
    app.router.add_post("/api/notifications/{notification_id}/read", handle_mark_read)
    app.router.add_post("/api/notifications/{notification_id}/dismiss", handle_dismiss)
