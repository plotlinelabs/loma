"""Telegram personal-channel API routes.

Backs the Telegram card on Integrations > Personal:

  GET    /api/telegram/status         — bot configured? current user linked?
  POST   /api/telegram/link           — mint a one-time code + t.me deep link
  DELETE /api/telegram/link           — unlink the current user's Telegram
  POST   /api/telegram/setup-webhook  — (admin) register the webhook with Telegram

Linking flow: the dashboard calls POST /link, shows the returned
``https://t.me/<bot>?start=<code>`` deep link, and polls GET /status until
``linked`` flips to true (the webhook consumes the code — see
``webhooks/telegram_ingestion.py``).
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web

from api.auth_helpers import get_user_email, require_admin
from observability.db import get_db
from webhooks.telegram_ingestion import _telegram_api

logger = logging.getLogger(__name__)

_LINK_CODE_TTL_MINUTES = 10

# getMe result cached per-process — the bot identity never changes at runtime.
_cached_bot_username: str | None = None


def _serialize_dt(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def _get_bot_username() -> str | None:
    """Fetch (and cache) the bot's username via getMe."""
    global _cached_bot_username
    if _cached_bot_username:
        return _cached_bot_username
    data = await _telegram_api("getMe", {})
    if data.get("ok"):
        _cached_bot_username = data.get("result", {}).get("username")
    return _cached_bot_username


async def handle_telegram_status(request: web.Request) -> web.Response:
    """GET /api/telegram/status — configuration + link state for current user."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    configured = bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())
    payload = {
        "configured": configured,
        "linked": False,
        "bot_username": None,
        "telegram_username": None,
        "linked_at": None,
    }
    if configured:
        payload["bot_username"] = await _get_bot_username()
        link = await db.telegram_links.find_one({"user_email": email})
        if link:
            payload["linked"] = True
            payload["telegram_username"] = link.get("telegram_username") or link.get("telegram_first_name")
            payload["linked_at"] = _serialize_dt(link.get("linked_at"))

    return web.json_response(payload)


async def handle_telegram_create_link(request: web.Request) -> web.Response:
    """POST /api/telegram/link — mint a one-time code and deep link."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        return web.json_response({"error": "Telegram bot is not configured on this deployment"}, status=503)

    bot_username = await _get_bot_username()
    if not bot_username:
        return web.json_response({"error": "Could not reach the Telegram Bot API"}, status=502)

    now = datetime.now(timezone.utc)
    # start payload charset is [A-Za-z0-9_-], max 64 chars — token_urlsafe fits.
    code = secrets.token_urlsafe(24)
    await db.telegram_link_codes.insert_one({
        "code": code,
        "user_email": email,
        "created_at": now,
        "expires_at": now + timedelta(minutes=_LINK_CODE_TTL_MINUTES),
        "used": False,
    })

    return web.json_response({
        "deep_link": f"https://t.me/{bot_username}?start={code}",
        "bot_username": bot_username,
        "expires_in_minutes": _LINK_CODE_TTL_MINUTES,
    })


async def handle_telegram_unlink(request: web.Request) -> web.Response:
    """DELETE /api/telegram/link — remove the current user's mapping."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    result = await db.telegram_links.delete_many({"user_email": email})
    if not result.deleted_count:
        return web.json_response({"error": "No Telegram connection found"}, status=404)

    return web.json_response({"disconnected": True})


async def handle_telegram_setup_webhook(request: web.Request) -> web.Response:
    """POST /api/telegram/setup-webhook — (admin) call Telegram setWebhook.

    Body: {"url": "https://<public-backend-host>/webhooks/telegram"} — falls
    back to the TELEGRAM_WEBHOOK_URL env var. If TELEGRAM_WEBHOOK_SECRET is
    set, it is registered as the webhook secret_token.
    """
    require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}

    url = (body.get("url") or os.environ.get("TELEGRAM_WEBHOOK_URL", "")).strip()
    if not url:
        return web.json_response(
            {"error": "Provide a webhook url in the body or set TELEGRAM_WEBHOOK_URL"},
            status=400,
        )

    payload = {"url": url, "allowed_updates": ["message"]}
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if secret:
        payload["secret_token"] = secret

    data = await _telegram_api("setWebhook", payload)
    if not data.get("ok"):
        return web.json_response(
            {"error": data.get("description", "setWebhook failed")}, status=502,
        )

    logger.info("[TELEGRAM] Webhook registered at %s", url)
    return web.json_response({"ok": True, "url": url})


def setup_telegram_routes(app: web.Application):
    """Register Telegram API routes."""
    app.router.add_get("/api/telegram/status", handle_telegram_status)
    app.router.add_post("/api/telegram/link", handle_telegram_create_link)
    app.router.add_delete("/api/telegram/link", handle_telegram_unlink)
    app.router.add_post("/api/telegram/setup-webhook", handle_telegram_setup_webhook)
