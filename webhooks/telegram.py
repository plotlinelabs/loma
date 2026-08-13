"""Telegram bot webhook handler.

Receives updates from the Telegram Bot API on:

    POST /webhooks/telegram

The single shared Loma bot (token in ``TELEGRAM_BOT_TOKEN``) serves every
Loma user. Users link their Telegram account from the dashboard
(Integrations > Personal > Telegram), which generates a one-time code and
a ``https://t.me/<bot>?start=<code>`` deep link. The resulting
``/start <code>`` message is handled here and saved as a mapping in the
``telegram_links`` collection. After linking, any DM from that Telegram
user runs the agent as the linked Loma user.

Security:
  - If ``TELEGRAM_WEBHOOK_SECRET`` is set, the
    ``X-Telegram-Bot-Api-Secret-Token`` header must match (constant-time
    comparison). Configure the same value via Telegram's setWebhook
    ``secret_token`` parameter (see POST /api/telegram/setup-webhook).
  - Only private (DM) chats are processed; group messages are ignored.
  - Updates are deduplicated on ``update_id``.
"""

import asyncio
import hmac
import json
import logging
import os

from aiohttp import web

from webhooks.telegram_ingestion import process_telegram_update

logger = logging.getLogger(__name__)


def _verify_secret(request: web.Request) -> bool:
    """Verify X-Telegram-Bot-Api-Secret-Token if a secret is configured."""
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not secret:
        return True
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(header, secret)


async def handle_telegram_webhook(request: web.Request) -> web.Response:
    """Handle POST /webhooks/telegram."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        return web.json_response({"error": "Telegram not configured"}, status=503)

    if not _verify_secret(request):
        logger.warning("[TELEGRAM-WEBHOOK] secret token mismatch")
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        raw_body = await request.read()
        update = json.loads(raw_body) if raw_body else {}
    except Exception:
        logger.warning("[TELEGRAM-WEBHOOK] invalid JSON body")
        return web.json_response({"error": "Invalid JSON"}, status=400)

    update_id = update.get("update_id")
    if update_id is None:
        return web.json_response({"error": "missing update_id"}, status=400)

    logger.info("[TELEGRAM-WEBHOOK] received update_id=%s", update_id)

    # Ack immediately — Telegram retries on slow/failed responses. Processing
    # (dedup, linking, agent run) happens in the background.
    asyncio.create_task(process_telegram_update(update))

    return web.json_response({"status": "accepted", "update_id": update_id})


def setup_telegram_webhook_routes(app: web.Application) -> None:
    """Register Telegram webhook route."""
    app.router.add_post("/webhooks/telegram", handle_telegram_webhook)
