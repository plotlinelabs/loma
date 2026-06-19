"""Grain webhook handler.

Receives a Zapier-delivered webhook when a meeting completes. Zap payload:

  {"data": {"recording_id": "<uuid>"}}

Fetches the recording + transcript from the Grain API and stores a unified
change-stream event (fire-and-forget).
"""

import json
import logging

from aiohttp import web

from webhooks.grain_ingestion import ingest_grain_webhook
import asyncio

logger = logging.getLogger(__name__)


def _extract_recording_id(body: dict) -> str | None:
    """Pull the recording_id out of the webhook body.

    Handles a few shapes Zapier may send:
      {"data": {"recording_id": "..."}}      — current format (unflattened)
      {"recording_id": "..."}                — top-level
      {"data": {"id": "..."}}                — alternative naming
    """
    if not isinstance(body, dict):
        return None
    data = body.get("data") or {}
    if isinstance(data, dict):
        rid = data.get("recording_id") or data.get("id")
        if rid:
            return str(rid)
    rid = body.get("recording_id") or body.get("id")
    return str(rid) if rid else None


async def handle_grain_webhook(request: web.Request) -> web.Response:
    """Handle POST /webhooks/grain from the Zap."""
    try:
        raw_body = await request.read()
        body = json.loads(raw_body) if raw_body else {}
    except Exception:
        logger.warning("[GRAIN-WEBHOOK] invalid JSON body")
        return web.json_response({"error": "Invalid JSON"}, status=400)

    recording_id = _extract_recording_id(body)
    if not recording_id:
        logger.warning("[GRAIN-WEBHOOK] no recording_id in body: %s", str(body)[:200])
        return web.json_response({"error": "missing recording_id"}, status=400)

    logger.info("[GRAIN-WEBHOOK] received recording_id=%s", recording_id)

    # Fire-and-forget: ingestion fetches transcript + recording details from Grain API.
    asyncio.create_task(ingest_grain_webhook(body, recording_id))

    return web.json_response({"status": "accepted", "recording_id": recording_id})


def setup_grain_webhook_routes(app: web.Application) -> None:
    """Register the Grain webhook route."""
    app.router.add_post("/webhooks/grain", handle_grain_webhook)
