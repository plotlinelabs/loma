"""Pylon webhook handler.

Receives webhooks from Pylon's workflow builder on:
  - New issue created
  - Issue has a new message (reply)
  - Issue status changed

Payload shape (configured in Pylon):
  {"account": "...", "author": "...", "message": "...", "id": "...", "status": "..."}
"""

import asyncio
import json
import logging

from aiohttp import web

from webhooks.pylon_ingestion import ingest_pylon_event

logger = logging.getLogger(__name__)


async def handle_pylon_webhook(request: web.Request) -> web.Response:
    """Handle POST /webhooks/pylon."""
    try:
        raw_body = await request.read()
        body = json.loads(raw_body) if raw_body else {}
    except Exception:
        logger.warning("[PYLON-WEBHOOK] invalid JSON body")
        return web.json_response({"error": "Invalid JSON"}, status=400)

    issue_id = body.get("id")
    if not issue_id:
        logger.warning("[PYLON-WEBHOOK] no issue id in body: %s", str(body)[:200])
        return web.json_response({"error": "missing id"}, status=400)

    logger.info(
        "[PYLON-WEBHOOK] received issue=%s account=%s status=%s",
        issue_id, body.get("account"), body.get("status"),
    )

    asyncio.create_task(ingest_pylon_event(body))

    return web.json_response({"status": "accepted", "issue_id": issue_id})


def setup_pylon_webhook_routes(app: web.Application) -> None:
    """Register Pylon webhook route."""
    app.router.add_post("/webhooks/pylon", handle_pylon_webhook)
