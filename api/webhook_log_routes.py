"""API routes for webhook logs.

Provides read-only access to the ``webhook_logs`` collection so the
dashboard can display incoming webhook request history for debugging.
"""

import logging
from datetime import datetime

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import require_analyst_or_above

logger = logging.getLogger(__name__)


def _serialize(doc):
    """Make a MongoDB document JSON-serializable."""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [_serialize(d) for d in doc]
    if isinstance(doc, dict):
        result = {}
        for k, v in doc.items():
            if k == "_id":
                result[k] = str(v)
            elif isinstance(v, datetime):
                if v.tzinfo is None:
                    result[k] = v.isoformat() + "Z"
                else:
                    result[k] = v.isoformat()
            elif isinstance(v, dict):
                result[k] = _serialize(v)
            elif isinstance(v, list):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        if doc.tzinfo is None:
            return doc.isoformat() + "Z"
        return doc.isoformat()
    return doc


async def handle_list_webhook_logs(request: web.Request) -> web.Response:
    """GET /api/webhook-logs — list recent webhook logs."""
    require_analyst_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.query.get("flowId")
    limit = min(int(request.query.get("limit", 50)), 200)

    query: dict = {}
    if flow_id:
        query["flow_id"] = flow_id

    logs = (
        await db.webhook_logs.find(query)
        .sort("received_at", -1)
        .limit(limit)
        .to_list(limit)
    )

    return web.json_response({"logs": _serialize(logs)})


async def handle_get_webhook_log(request: web.Request) -> web.Response:
    """GET /api/webhook-logs/{log_id} — get a single webhook log with full payload."""
    require_analyst_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    log_id = request.match_info["log_id"]
    doc = await db.webhook_logs.find_one({"log_id": log_id})
    if doc is None:
        return web.json_response({"error": "Log not found"}, status=404)

    return web.json_response({"log": _serialize(doc)})


def setup_webhook_log_routes(app: web.Application):
    """Register webhook log API routes."""
    app.router.add_get("/api/webhook-logs", handle_list_webhook_logs)
    app.router.add_get("/api/webhook-logs/{log_id}", handle_get_webhook_log)
