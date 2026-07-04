"""Web push subscription routes (tasks-board attention notifications).

One doc per browser in the `push_subscriptions` collection, unique by
endpoint. See observability/push.py for sending.
"""

import logging
from datetime import datetime, timezone

from aiohttp import web

from observability.db import get_db
from observability.push import vapid_public_key
from api.auth_helpers import get_user_email

logger = logging.getLogger(__name__)


async def handle_vapid_public_key(request: web.Request) -> web.Response:
    """GET /api/push/vapid-public-key — 404 when push isn't configured."""
    key = vapid_public_key()
    if not key:
        return web.json_response({"error": "Push not configured"}, status=404)
    return web.json_response({"key": key})


async def handle_subscribe(request: web.Request) -> web.Response:
    """POST /api/push/subscriptions — upsert this browser's subscription."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    subscription = body.get("subscription") or {}
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return web.json_response({"error": "Invalid subscription"}, status=400)

    # Upsert by endpoint — a browser re-subscribing (or a different user on
    # the same browser) replaces the existing doc for that endpoint.
    await db.push_subscriptions.update_one(
        {"subscription.endpoint": endpoint},
        {"$set": {
            "user_email": user_email,
            "subscription": {
                "endpoint": endpoint,
                "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
            },
            "user_agent": request.headers.get("User-Agent", "")[:200],
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return web.json_response({"subscribed": True}, status=201)


async def handle_unsubscribe(request: web.Request) -> web.Response:
    """DELETE /api/push/subscriptions — remove this browser's subscription."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    endpoint = body.get("endpoint")
    if not endpoint:
        return web.json_response({"error": "endpoint is required"}, status=400)

    # Own subscriptions only.
    await db.push_subscriptions.delete_one({
        "subscription.endpoint": endpoint,
        "user_email": user_email,
    })
    return web.json_response({"subscribed": False})


def setup_push_routes(app: web.Application):
    """Register web-push routes on the aiohttp app."""
    app.router.add_get("/api/push/vapid-public-key", handle_vapid_public_key)
    app.router.add_post("/api/push/subscriptions", handle_subscribe)
    app.router.add_delete("/api/push/subscriptions", handle_unsubscribe)
