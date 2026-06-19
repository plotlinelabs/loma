"""Unified webhook endpoint.

All external webhook integrations (Pylon, Linear, GitHub, etc.) can be
configured to POST to:

    /webhook?flowId=<flow_id>

Each webhook flow defines its own auth method (bearer_token, hmac_sha256,
or none).  Every request is logged to the ``webhook_logs`` collection for
debugging, regardless of auth or execution outcome.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

from aiohttp import web

from observability.db import get_db
from scheduler.models import get_flow

logger = logging.getLogger(__name__)

# Header keys whose values are redacted in stored logs
_SENSITIVE_HEADERS = {"authorization", "x-hub-signature-256", "linear-signature"}


# ---------------------------------------------------------------------------
# Webhook log helpers
# ---------------------------------------------------------------------------

def _sanitize_headers(headers: dict) -> dict:
    """Redact sensitive header values for storage."""
    sanitized = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADERS:
            sanitized[k] = "***REDACTED***"
        else:
            sanitized[k] = v
    return sanitized


def _parse_body(raw_body: bytes) -> dict | str:
    """Parse the body as JSON; fall back to a truncated string."""
    try:
        parsed = json.loads(raw_body)
        # Truncate large payloads (keep first 100KB worth of JSON text)
        text = json.dumps(parsed, ensure_ascii=False)
        if len(text) > 100_000:
            return text[:100_000] + "...(truncated)"
        return parsed
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = raw_body.decode("utf-8", errors="replace")
        return text[:100_000]


async def create_webhook_log(
    db, flow_id: str | None, flow_name: str, headers: dict, raw_body: bytes,
    auth_method: str = "",
) -> str:
    """Create an initial webhook log entry.  Returns log_id."""
    log_id = str(uuid.uuid4())
    doc = {
        "log_id": log_id,
        "flow_id": flow_id or "",
        "flow_name": flow_name,
        "received_at": datetime.now(timezone.utc),
        "headers": _sanitize_headers(headers),
        "body": _parse_body(raw_body),
        "auth_method": auth_method,
        "auth_result": "pending",
        "execution_status": "pending",
        "conversation_id": None,
        "response_status_code": None,
        "error": None,
        "duration_ms": None,
    }
    await db.webhook_logs.insert_one(doc)
    return log_id


async def update_webhook_log(db, log_id: str, **fields):
    """Update fields on an existing webhook log."""
    if not log_id:
        return
    await db.webhook_logs.update_one({"log_id": log_id}, {"$set": fields})


# ---------------------------------------------------------------------------
# Auth verification
# ---------------------------------------------------------------------------

def verify_webhook_auth(
    webhook_config: dict, request: web.Request, raw_body: bytes,
) -> bool:
    """Verify the request against the flow's auth configuration.

    Returns True if auth passes (or method is ``none``).
    """
    method = webhook_config.get("auth_method", "none")
    secret = webhook_config.get("auth_secret", "")

    if method == "none":
        return True

    if method == "bearer_token":
        auth_header = request.headers.get("Authorization", "")
        expected = f"Bearer {secret}"
        return hmac.compare_digest(auth_header, expected)

    if method == "hmac_sha256":
        sig_header = webhook_config.get("signature_header", "")
        signature = request.headers.get(sig_header, "")
        if not signature or not secret:
            return False
        try:
            header_sig = bytes.fromhex(signature)
        except ValueError:
            return False
        computed = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
        return hmac.compare_digest(computed, header_sig)

    logger.warning("[WEBHOOK] Unknown auth method: %s", method)
    return False


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_incoming_webhook(request: web.Request) -> web.Response:
    """Handle POST /webhook?flowId=<flow_id>."""
    flow_id = request.query.get("flowId")
    raw_body = await request.read()
    headers = dict(request.headers)

    db = get_db()
    if db is None:
        return web.json_response({"error": "Service unavailable"}, status=503)

    # 1. Look up flow
    flow = None
    flow_name = ""
    auth_method = ""
    if flow_id:
        flow = await get_flow(db, flow_id)

    if flow is not None:
        flow_name = flow.get("name", "")
        auth_method = flow.get("webhook_config", {}).get("auth_method", "none")

    # 2. Create webhook log entry
    log_id = await create_webhook_log(
        db, flow_id, flow_name, headers, raw_body, auth_method=auth_method,
    )

    # 3. Validate flow exists and is a webhook flow
    if not flow_id:
        await update_webhook_log(
            db, log_id, response_status_code=400, error="Missing flowId query parameter",
        )
        return web.json_response({"error": "Missing flowId query parameter"}, status=400)

    if flow is None or flow.get("trigger_type") != "webhook":
        await update_webhook_log(
            db, log_id, response_status_code=404, error="Webhook flow not found",
        )
        return web.json_response({"error": "Webhook flow not found"}, status=404)

    if flow["status"] != "active":
        await update_webhook_log(
            db, log_id,
            response_status_code=200,
            execution_status="skipped",
            auth_result="skipped",
            error=f"Flow is {flow['status']}",
        )
        return web.json_response(
            {"status": "skipped", "reason": f"flow_{flow['status']}"},
        )

    # 4. Validate auth
    if not verify_webhook_auth(flow.get("webhook_config", {}), request, raw_body):
        await update_webhook_log(
            db, log_id, response_status_code=401, auth_result="failed",
        )
        logger.warning("[WEBHOOK] Auth failed for flow %s (%s)", flow_id, flow_name)
        return web.json_response({"error": "Unauthorized"}, status=401)

    await update_webhook_log(db, log_id, auth_result="success")

    # 5. Fire-and-forget execution
    from scheduler.webhook_executor import execute_webhook_flow

    asyncio.create_task(
        execute_webhook_flow(flow, raw_body, headers, log_id)
    )

    logger.info("[WEBHOOK] Accepted webhook for flow %s (%s), log=%s", flow_id, flow_name, log_id)
    return web.json_response({"status": "accepted", "log_id": log_id}, status=202)


def setup_incoming_webhook_routes(app: web.Application):
    """Register the unified webhook endpoint."""
    app.router.add_post("/webhook", handle_incoming_webhook)
