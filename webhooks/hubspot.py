"""HubSpot webhook handler.

Receives batched webhook subscription events from HubSpot. The payload
is a JSON **array** of event objects (HubSpot batches multiple events
per delivery). We must respond within 5 seconds, so ingestion runs
fire-and-forget.
"""

import asyncio
import json
import logging

from aiohttp import web

from webhooks.hubspot_ingestion import ingest_hubspot_events

logger = logging.getLogger(__name__)


async def handle_hubspot_webhook(request: web.Request) -> web.Response:
    """Handle POST /webhooks/hubspot."""
    try:
        raw_body = await request.read()
        body = json.loads(raw_body) if raw_body else []
    except Exception:
        logger.warning("[HUBSPOT-WEBHOOK] invalid JSON body")
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not isinstance(body, list):
        logger.warning("[HUBSPOT-WEBHOOK] expected array, got %s", type(body).__name__)
        return web.json_response({"error": "expected array"}, status=400)

    logger.info("[HUBSPOT-WEBHOOK] received batch of %d events", len(body))

    if body:
        asyncio.create_task(ingest_hubspot_events(body))

    return web.json_response({"status": "accepted", "events": len(body)})


def setup_hubspot_webhook_routes(app: web.Application) -> None:
    """Register HubSpot webhook route."""
    app.router.add_post("/webhooks/hubspot", handle_hubspot_webhook)
