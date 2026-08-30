"""Personal API key routes — bearer keys for external MCP/API access.

Keys let external agents (e.g. the loma-tasks MCP server) act as a specific
user without a dashboard session. The full key (`loma_sk_...`) is returned
exactly once at creation; only its SHA-256 hash is stored. Keys never expire —
revocation (DELETE) is the kill switch and takes effect immediately.

Collection: `api_keys`
  { key_id, user_email, name, key_hash, key_prefix,
    created_at, last_used_at, revoked, revoked_at }

All routes are session-authed (nginx-injected X-User-Email), and every user
can only see / mint / revoke their own keys.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone

from aiohttp import web

from api.auth_helpers import get_user_email
from observability.db import get_db

logger = logging.getLogger(__name__)

KEY_PREFIX = "loma_sk_"
MAX_KEYS_PER_USER = 20
MAX_KEY_NAME_LEN = 60


def hash_api_key(key: str) -> str:
    """SHA-256 hex digest of a full API key (shared with the MCP server)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _key_view(doc: dict) -> dict:
    """Public shape of a key record — never includes the hash."""
    return {
        "key_id": doc.get("key_id"),
        "name": doc.get("name"),
        "key_prefix": doc.get("key_prefix"),
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
        "last_used_at": doc["last_used_at"].isoformat() if doc.get("last_used_at") else None,
        "revoked": bool(doc.get("revoked")),
    }


async def handle_create_api_key(request: web.Request) -> web.Response:
    """POST /api/api-keys — mint a new key for the caller. Returns the full key ONCE."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        body = {}

    name = (body.get("name") or "").strip() or "Unnamed key"
    if len(name) > MAX_KEY_NAME_LEN:
        return web.json_response(
            {"error": f"Name too long (max {MAX_KEY_NAME_LEN} chars)"}, status=400)

    active = await db.api_keys.count_documents(
        {"user_email": user_email, "revoked": {"$ne": True}})
    if active >= MAX_KEYS_PER_USER:
        return web.json_response(
            {"error": f"Key limit reached ({MAX_KEYS_PER_USER}). Revoke an unused key first."},
            status=400)

    full_key = KEY_PREFIX + secrets.token_hex(24)  # loma_sk_ + 48 hex chars
    now = datetime.now(timezone.utc)
    doc = {
        "key_id": str(uuid.uuid4()),
        "user_email": user_email,
        "name": name,
        "key_hash": hash_api_key(full_key),
        # First 12 chars ("loma_sk_" + 4) — enough to recognize, useless to guess.
        "key_prefix": full_key[:12],
        "created_at": now,
        "last_used_at": None,
        "revoked": False,
    }
    await db.api_keys.insert_one(doc)
    logger.info("API key created for %s (key_id=%s)", user_email, doc["key_id"])

    return web.json_response({"key": full_key, "record": _key_view(doc)}, status=201)


async def handle_list_api_keys(request: web.Request) -> web.Response:
    """GET /api/api-keys — the caller's active keys (revoked ones are hidden)."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    docs = await db.api_keys.find(
        {"user_email": user_email, "revoked": {"$ne": True}},
    ).sort("created_at", -1).to_list(MAX_KEYS_PER_USER)
    return web.json_response({"keys": [_key_view(d) for d in docs]})


async def handle_revoke_api_key(request: web.Request) -> web.Response:
    """DELETE /api/api-keys/{key_id} — revoke one of the caller's keys."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    key_id = request.match_info["key_id"]
    result = await db.api_keys.update_one(
        # Scoped to the caller — nobody can revoke (or probe) another user's keys.
        {"key_id": key_id, "user_email": user_email, "revoked": {"$ne": True}},
        {"$set": {"revoked": True, "revoked_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        return web.json_response({"error": "Not found"}, status=404)
    logger.info("API key revoked for %s (key_id=%s)", user_email, key_id)
    return web.json_response({"revoked": True})


def setup_api_key_routes(app: web.Application):
    app.router.add_post("/api/api-keys", handle_create_api_key)
    app.router.add_get("/api/api-keys", handle_list_api_keys)
    app.router.add_delete("/api/api-keys/{key_id}", handle_revoke_api_key)
