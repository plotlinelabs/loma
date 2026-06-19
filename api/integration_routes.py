"""
REST API for managing org-level integrations.

Provides connect/disconnect/list endpoints that store encrypted API keys
in MongoDB and trigger MCP pool reload when integrations change.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

from aiohttp import web

from api.oauth_helpers import encrypt_token, decrypt_token
from integrations.registry import PROVIDER_CATALOG, list_providers, get_provider
from observability.db import get_db

logger = logging.getLogger(__name__)


async def _list_integrations(request: web.Request) -> web.Response:
    """GET /api/integrations — list all providers with their connection status."""
    db = get_db()

    # Build a map of connected integrations from DB
    connected = {}
    if db is not None:
        async for doc in db.integrations.find({"status": "active"}):
            connected[doc["provider"]] = {
                "connected_by": doc.get("connected_by"),
                "connected_at": doc.get("connected_at", "").isoformat() if doc.get("connected_at") else None,
                "has_webhook_secret": bool(doc.get("webhook_secret_encrypted")),
            }

    # Merge catalog with DB status
    result = []
    for entry in list_providers():
        provider = entry["provider"]
        # Determine status: DB-connected > system-managed (no auth needed) > not connected
        if provider in connected:
            status = "connected"
        elif entry["auth_type"] == "none":
            status = "system_managed"
        else:
            status = "not_connected"
        item = {
            "provider": provider,
            "display_name": entry["display_name"],
            "description": entry["description"],
            "auth_type": entry["auth_type"],
            "auth_label": entry["auth_label"],
            "auth_help_url": entry["auth_help_url"],
            "has_webhook": entry.get("webhook") is not None,
            "webhook_secret_label": entry.get("webhook", {}).get("secret_label") if entry.get("webhook") else None,
            "extra_fields": entry.get("extra_fields", []),
            "status": status,
        }
        if provider in connected:
            item.update(connected[provider])
        result.append(item)

    return web.json_response(result)


async def _connect_integration(request: web.Request) -> web.Response:
    """POST /api/integrations/connect — store encrypted API key and reload pool."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Database not available"}, status=503)

    body = await request.json()
    provider = body.get("provider", "").strip()
    api_key = body.get("api_key", "").strip()
    webhook_secret = body.get("webhook_secret", "").strip()
    extra_fields = body.get("extra_fields", {})

    if not provider or not api_key:
        return web.json_response({"error": "provider and api_key are required"}, status=400)

    catalog_entry = get_provider(provider)
    if catalog_entry is None:
        return web.json_response({"error": f"Unknown provider: {provider}"}, status=400)

    # Get user email from auth context (set by auth_middleware)
    user_email = request.get("user_email", "unknown")

    now = datetime.now(timezone.utc)
    doc = {
        "integration_id": str(uuid.uuid4()),
        "provider": provider,
        "status": "active",
        "api_key_encrypted": encrypt_token(api_key),
        "webhook_secret_encrypted": encrypt_token(webhook_secret) if webhook_secret else None,
        "extra_fields_encrypted": {k: encrypt_token(v) for k, v in extra_fields.items()} if extra_fields else None,
        "connected_by": user_email,
        "connected_at": now,
        "updated_at": now,
    }

    # Upsert — reconnecting overwrites the old key
    await db.integrations.update_one(
        {"provider": provider},
        {"$set": doc},
        upsert=True,
    )

    logger.info("[INTEGRATIONS] Connected %s (by %s)", provider, user_email)

    # Reload MCP pool with new config
    await _reload_pool()

    return web.json_response({"status": "connected", "provider": provider})


async def _disconnect_integration(request: web.Request) -> web.Response:
    """DELETE /api/integrations/{provider} — remove integration and reload pool."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Database not available"}, status=503)

    provider = request.match_info["provider"]
    catalog_entry = get_provider(provider)
    if catalog_entry is None:
        return web.json_response({"error": f"Unknown provider: {provider}"}, status=400)

    result = await db.integrations.delete_one({"provider": provider})
    if result.deleted_count == 0:
        return web.json_response({"error": "Integration not found"}, status=404)

    logger.info("[INTEGRATIONS] Disconnected %s", provider)

    # Reload MCP pool without this integration
    await _reload_pool()

    return web.json_response({"status": "disconnected", "provider": provider})


async def _get_webhook_url(request: web.Request) -> web.Response:
    """GET /api/integrations/{provider}/webhook-url — return the webhook URL."""
    provider = request.match_info["provider"]
    catalog_entry = get_provider(provider)
    if catalog_entry is None:
        return web.json_response({"error": f"Unknown provider: {provider}"}, status=400)

    if catalog_entry.get("webhook") is None:
        return web.json_response({"error": f"{provider} does not support webhooks"}, status=400)

    # Build the webhook URL from the app's base URL
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if not base_url:
        # Fallback: construct from request
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        host = request.headers.get("X-Forwarded-Host", request.host)
        base_url = f"{scheme}://{host}"

    webhook_url = f"{base_url}/webhooks/{provider}"

    return web.json_response({
        "provider": provider,
        "webhook_url": webhook_url,
    })


async def _reload_pool():
    """Reload the MCP pool with updated integration config."""
    try:
        from agent.client import load_config, merge_db_integrations
        from agent.pool import get_pool

        config = load_config()
        config = await merge_db_integrations(config)
        pool = get_pool()
        if pool is not None:
            await pool.reload_config(config)
            logger.info("[INTEGRATIONS] MCP pool reloaded")
    except Exception:
        logger.exception("[INTEGRATIONS] Failed to reload MCP pool")


def setup_integration_routes(app: web.Application):
    """Register integration API routes."""
    app.router.add_get("/api/integrations", _list_integrations)
    app.router.add_post("/api/integrations/connect", _connect_integration)
    app.router.add_delete("/api/integrations/{provider}", _disconnect_integration)
    app.router.add_get("/api/integrations/{provider}/webhook-url", _get_webhook_url)
