"""
REST API for managing org-level integrations.

Provides connect/disconnect/list endpoints that store encrypted API keys
in MongoDB and trigger MCP pool reload when integrations change.
"""

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp as _aiohttp
from aiohttp import web

from api.auth_helpers import require_admin, get_user_email
from api.oauth_helpers import encrypt_token, decrypt_token, register_oauth_client
from integrations.registry import PROVIDER_CATALOG, list_providers, get_provider
from observability.db import get_db

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Derive a stable MCP server key from a connector name.

    Lowercased, non-alphanumerics collapsed to underscores. The slug becomes
    the MCP server name, so the agent exposes its tools as ``mcp__<slug>``.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug


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

    # Append admin-added custom MCP connectors (not in the static catalog).
    if db is not None:
        user_email = get_user_email(request)
        async for doc in db.integrations.find({"is_custom": True, "status": "active"}):
            item = {
                "provider": doc["provider"],
                "display_name": doc.get("display_name", doc["provider"]),
                "description": "Custom MCP server",
                "auth_type": "custom",
                "auth_label": "Access token",
                "auth_help_url": "",
                "has_webhook": False,
                "webhook_secret_label": None,
                "extra_fields": [],
                "status": "connected",
                "is_custom": True,
                "url": doc.get("mcp_url", ""),
                "has_token": bool(doc.get("api_key_encrypted")),
                "auth_mode": doc.get("auth_mode", "none"),
                "connected_by": doc.get("connected_by"),
                "connected_at": doc.get("connected_at").isoformat() if doc.get("connected_at") else None,
            }
            if doc.get("auth_mode") == "oauth" and user_email:
                user_token = await db.oauth_tokens.find_one({
                    "user_email": user_email,
                    "provider": doc["provider"],
                    "provider_type": "custom_mcp",
                })
                item["user_oauth_status"] = "connected" if user_token else "not_connected"
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


async def _probe_custom_connector(request: web.Request) -> web.Response:
    """POST /api/integrations/custom/probe — check if a URL requires OAuth."""
    require_admin(request)

    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url or not re.match(r"^https?://", url):
        return web.json_response({"error": "Valid URL required"}, status=400)

    result: dict = {"requires_oauth": False, "reachable": False, "oauth_metadata": None}

    async with _aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=_aiohttp.ClientTimeout(total=10), allow_redirects=True) as resp:
                result["reachable"] = True
                if resp.status == 401:
                    result["requires_oauth"] = True
        except Exception:
            pass

    parsed = urlparse(url)
    well_known_url = f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server"

    async with _aiohttp.ClientSession() as session:
        try:
            async with session.get(well_known_url, timeout=_aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    metadata = await resp.json()
                    result["requires_oauth"] = True
                    result["oauth_metadata"] = {
                        "authorization_endpoint": metadata.get("authorization_endpoint"),
                        "token_endpoint": metadata.get("token_endpoint"),
                        "registration_endpoint": metadata.get("registration_endpoint"),
                        "scopes_supported": metadata.get("scopes_supported", []),
                    }
        except Exception:
            pass

    return web.json_response(result)


async def _add_custom_connector(request: web.Request) -> web.Response:
    """POST /api/integrations/custom — register a custom remote MCP server.

    Admin-only. Supports static token, per-user OAuth, or no auth.
    """
    require_admin(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Database not available"}, status=503)

    body = await request.json()
    name = (body.get("name") or "").strip()
    url = (body.get("url") or "").strip()
    token = (body.get("token") or "").strip()
    auth_header = (body.get("auth_header") or "Authorization").strip() or "Authorization"
    auth_mode = (body.get("auth_mode") or "").strip()

    if not name or not url:
        return web.json_response({"error": "name and url are required"}, status=400)
    if not re.match(r"^https?://", url):
        return web.json_response({"error": "url must start with http:// or https://"}, status=400)

    # Infer auth_mode if not explicitly provided
    if not auth_mode:
        auth_mode = "static" if token else "none"

    slug = _slugify(name)
    if not slug:
        return web.json_response(
            {"error": "name must contain letters or numbers"}, status=400,
        )
    if slug in PROVIDER_CATALOG:
        return web.json_response(
            {"error": f"'{slug}' collides with a built-in integration; choose another name"},
            status=409,
        )
    existing = await db.integrations.find_one({"provider": slug})
    if existing is not None:
        return web.json_response(
            {"error": f"A connector named '{slug}' already exists"}, status=409,
        )

    user_email = get_user_email(request) or "unknown"
    now = datetime.now(timezone.utc)
    doc: dict = {
        "integration_id": str(uuid.uuid4()),
        "provider": slug,
        "is_custom": True,
        "status": "active",
        "display_name": name,
        "mcp_url": url,
        "auth_header": auth_header,
        "auth_mode": auth_mode,
        "api_key_encrypted": encrypt_token(token) if token and auth_mode == "static" else None,
        "connected_by": user_email,
        "connected_at": now,
        "updated_at": now,
    }

    if auth_mode == "oauth":
        oauth_config = body.get("oauth_config") or {}
        if not oauth_config.get("authorization_endpoint") or not oauth_config.get("token_endpoint"):
            return web.json_response(
                {"error": "OAuth requires authorization_endpoint and token_endpoint"},
                status=400,
            )

        # Dynamic client registration if registration_endpoint provided and no client_id
        client_id = oauth_config.get("client_id", "")
        client_secret = oauth_config.get("client_secret", "")
        token_endpoint_auth_method = oauth_config.get("token_endpoint_auth_method", "client_secret_post")

        if not client_id and oauth_config.get("registration_endpoint"):
            from api.oauth_routes import _oauth_redirect_uri
            redirect_uri = f"{os.environ.get('APP_BASE_URL', '').rstrip('/')}/api/oauth/custom-mcp/{slug}/callback"
            reg_result = await register_oauth_client(
                registration_endpoint=oauth_config["registration_endpoint"],
                redirect_uri=redirect_uri,
            )
            if reg_result is None:
                return web.json_response(
                    {"error": "OAuth dynamic client registration failed. Provide client_id manually."},
                    status=400,
                )
            client_id = reg_result["client_id"]
            client_secret = reg_result.get("client_secret", "")
            token_endpoint_auth_method = reg_result.get("token_endpoint_auth_method", "client_secret_post")
            logger.info("[INTEGRATIONS] Dynamic client registration succeeded for '%s'", slug)

        if not client_id:
            return web.json_response(
                {"error": "OAuth requires client_id (or a registration_endpoint for dynamic registration)"},
                status=400,
            )

        doc["oauth_config"] = {
            "authorization_endpoint": oauth_config["authorization_endpoint"],
            "token_endpoint": oauth_config["token_endpoint"],
            "registration_endpoint": oauth_config.get("registration_endpoint"),
            "client_id_encrypted": encrypt_token(client_id),
            "client_secret_encrypted": encrypt_token(client_secret) if client_secret else None,
            "scopes": oauth_config.get("scopes", []),
            "token_endpoint_auth_method": token_endpoint_auth_method,
        }
        doc["api_key_encrypted"] = None

    await db.integrations.insert_one(doc)
    logger.info("[INTEGRATIONS] Added custom MCP connector '%s' (auth_mode=%s, by %s)", slug, auth_mode, user_email)

    await _reload_pool()

    return web.json_response({"status": "connected", "provider": slug, "auth_mode": auth_mode}, status=201)


async def _remove_custom_connector(request: web.Request) -> web.Response:
    """DELETE /api/integrations/custom/{provider} — remove a custom MCP connector."""
    require_admin(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Database not available"}, status=503)

    provider = request.match_info["provider"]
    result = await db.integrations.delete_one({"provider": provider, "is_custom": True})
    if result.deleted_count == 0:
        return web.json_response({"error": "Custom connector not found"}, status=404)

    # Clean up all per-user OAuth tokens for this connector
    deleted_tokens = await db.oauth_tokens.delete_many({
        "provider": provider, "provider_type": "custom_mcp",
    })
    if deleted_tokens.deleted_count > 0:
        logger.info(
            "[INTEGRATIONS] Cleaned up %d user OAuth tokens for removed connector '%s'",
            deleted_tokens.deleted_count, provider,
        )

    logger.info("[INTEGRATIONS] Removed custom MCP connector '%s'", provider)

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
    app.router.add_post("/api/integrations/custom/probe", _probe_custom_connector)
    app.router.add_post("/api/integrations/custom", _add_custom_connector)
    app.router.add_delete("/api/integrations/custom/{provider}", _remove_custom_connector)
    app.router.add_delete("/api/integrations/{provider}", _disconnect_integration)
    app.router.add_get("/api/integrations/{provider}/webhook-url", _get_webhook_url)
