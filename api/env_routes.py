"""Environment variable management API — view, edit, and audit .env file changes."""

import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from dotenv import dotenv_values, load_dotenv

from observability.db import get_db
from api.auth_helpers import require_maintainer_or_above, get_user_email

logger = logging.getLogger(__name__)

# Path to the .env file (project root, one level up from api/)
DOTENV_PATH = str(Path(__file__).resolve().parent.parent / ".env")

# Substrings in key names that indicate a sensitive value
SENSITIVE_PATTERNS = ("SECRET", "KEY", "TOKEN", "PASSWORD", "COOKIE", "ENCRYPTION")

# Keys that cannot be edited or deleted via the dashboard
READONLY_KEYS = frozenset({"OBSERVABILITY_MONGODB_URI", "WEBHOOK_PORT", "ENV"})

# Keys whose changes require a process restart (pre-initialized clients)
CONNECTION_VARS = frozenset({
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "OBSERVABILITY_MONGODB_URI",
    "ANTHROPIC_API_KEY",
    "OPENCODE_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "OAUTH_ENCRYPTION_KEY",
})

OPENCODE_PROVIDER_VARS = frozenset({"OPENCODE_API_KEY", "OPENAI_API_KEY"})

# Valid env var key pattern
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _write_env_in_place(path: str, key: str, value: str | None) -> None:
    """Set (value given) or delete (value None) a key in the .env file by
    rewriting it in place.

    python-dotenv's set_key/unset_key write a temp file and os.rename() it over
    the target, which fails with EBUSY when .env is a bind-mounted single file in
    Docker (you cannot rename over a mount point). Truncating and rewriting the
    same inode avoids the rename and preserves comments/ordering of other lines.
    """
    p = Path(path)
    try:
        lines = p.read_text().splitlines()
    except FileNotFoundError:
        lines = []
    key_re = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    out: list[str] = []
    found = False
    for line in lines:
        if key_re.match(line):
            found = True
            if value is not None:
                out.append(f"{key}={value}")
            # value is None -> delete: drop the line
        else:
            out.append(line)
    if value is not None and not found:
        out.append(f"{key}={value}")
    p.write_text("\n".join(out) + ("\n" if out else ""))


def _is_sensitive(key: str) -> bool:
    upper = key.upper()
    return any(p in upper for p in SENSITIVE_PATTERNS)


def _mask_value(key: str, value: str | None) -> str:
    if value is None:
        return ""
    if _is_sensitive(key):
        return "\u2022\u2022\u2022"
    return value


def _mask_for_audit(key: str, value: str | None, custom_keys: set[str] | None = None) -> str | None:
    """Mask sensitive values for audit storage — show first 4 + last 4 chars."""
    if value is None:
        return None
    if not _is_sensitive_with_custom(key, custom_keys or set()):
        return value
    if len(value) <= 8:
        return "\u2022" * len(value)
    return value[:4] + "\u2022\u2022\u2022" + value[-4:]


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
                result[k] = v.isoformat() + ("Z" if v.tzinfo is None else "")
            elif isinstance(v, dict):
                result[k] = _serialize(v)
            elif isinstance(v, list):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        return doc.isoformat() + ("Z" if doc.tzinfo is None else "")
    return doc


# ── Helpers for custom sensitive keys ──────────────────────────────────────


async def _get_custom_sensitive_keys() -> set[str]:
    """Load user-marked sensitive keys from MongoDB."""
    db = get_db()
    if db is None:
        return set()
    doc = await db.env_settings.find_one({"_id": "sensitive_keys"})
    if doc is None:
        return set()
    return set(doc.get("keys", []))


def _is_sensitive_with_custom(key: str, custom_keys: set[str]) -> bool:
    return _is_sensitive(key) or key in custom_keys


def _mask_value_with_custom(key: str, value: str | None, custom_keys: set[str]) -> str:
    if value is None:
        return ""
    if _is_sensitive_with_custom(key, custom_keys):
        return "\u2022\u2022\u2022"
    return value


# ── List env vars ──────────────────────────────────────────────────────────


async def handle_list_env(request: web.Request) -> web.Response:
    """GET /api/env — list all env vars from the .env file (admin only)."""
    require_maintainer_or_above(request)

    try:
        current = dotenv_values(DOTENV_PATH)
    except Exception as e:
        logger.error("Failed to read .env file: %s", e)
        return web.json_response({"error": f"Failed to read .env file: {e}"}, status=500)

    custom_sensitive = await _get_custom_sensitive_keys()

    variables = []
    for key, value in current.items():
        sensitive = _is_sensitive_with_custom(key, custom_sensitive)
        variables.append({
            "key": key,
            "value": _mask_value_with_custom(key, value, custom_sensitive),
            "is_sensitive": sensitive,
            "is_readonly": key in READONLY_KEYS,
            "masked": sensitive,
        })

    return web.json_response({"variables": variables})


# ── Reveal a single value ──────────────────────────────────────────────────


async def handle_reveal_env(request: web.Request) -> web.Response:
    """POST /api/env/reveal — reveal a single sensitive env var value (admin only)."""
    require_maintainer_or_above(request)

    body = await request.json()
    key = body.get("key", "").strip()
    if not key:
        return web.json_response({"error": "key is required"}, status=400)

    try:
        current = dotenv_values(DOTENV_PATH)
    except Exception as e:
        return web.json_response({"error": f"Failed to read .env file: {e}"}, status=500)

    if key not in current:
        return web.json_response({"error": f"Key '{key}' not found"}, status=404)

    # Log reveal to audit
    db = get_db()
    if db is not None:
        await db.env_audit_log.insert_one({
            "action": "reveal",
            "user_email": get_user_email(request),
            "timestamp": datetime.now(timezone.utc),
            "revealed_key": key,
        })

    return web.json_response({"key": key, "value": current[key] or ""})


# ── Bulk update env vars ───────────────────────────────────────────────────


async def handle_update_env(request: web.Request) -> web.Response:
    """PUT /api/env — bulk update env vars (admin only)."""
    require_maintainer_or_above(request)

    body = await request.json()
    variables = body.get("variables", [])
    if not isinstance(variables, list) or len(variables) == 0:
        return web.json_response({"error": "variables array is required"}, status=400)

    # Validation
    seen_keys: set[str] = set()
    errors: list[str] = []
    for var in variables:
        key = var.get("key", "").strip()
        action = var.get("action", "set")

        if not key:
            errors.append("Empty key is not allowed")
            continue
        if not KEY_PATTERN.match(key):
            errors.append(f"Invalid key name: '{key}' (must match [A-Za-z_][A-Za-z0-9_]*)")
            continue
        if key in READONLY_KEYS:
            errors.append(f"Key '{key}' is read-only and cannot be modified via the dashboard")
            continue
        if key in seen_keys:
            errors.append(f"Duplicate key: '{key}'")
            continue
        if action not in ("set", "delete"):
            errors.append(f"Invalid action '{action}' for key '{key}'")
            continue
        seen_keys.add(key)

    if errors:
        return web.json_response({"error": "; ".join(errors)}, status=400)

    # Read current values for diff
    try:
        current = dotenv_values(DOTENV_PATH)
    except Exception as e:
        return web.json_response({"error": f"Failed to read .env file: {e}"}, status=500)

    custom_sensitive = await _get_custom_sensitive_keys()

    # Compute changes and apply
    changes: list[dict] = []
    for var in variables:
        key = var["key"].strip()
        action = var.get("action", "set")

        if action == "set":
            new_value = var.get("value", "")
            old_value = current.get(key)
            if old_value != new_value:
                change_type = "added" if old_value is None else "modified"
                changes.append({
                    "key": key,
                    "type": change_type,
                    "old_preview": _mask_for_audit(key, old_value, custom_sensitive),
                    "new_preview": _mask_for_audit(key, new_value, custom_sensitive),
                })
                try:
                    _write_env_in_place(DOTENV_PATH, key, new_value)
                except Exception as e:
                    return web.json_response(
                        {"error": f"Failed to set key '{key}': {e}"}, status=500
                    )

        elif action == "delete":
            if key in current:
                changes.append({
                    "key": key,
                    "type": "deleted",
                    "old_preview": _mask_for_audit(key, current[key], custom_sensitive),
                    "new_preview": None,
                })
                try:
                    _write_env_in_place(DOTENV_PATH, key, None)
                except Exception as e:
                    return web.json_response(
                        {"error": f"Failed to delete key '{key}': {e}"}, status=500
                    )

    if not changes:
        return web.json_response({"success": True, "changes_applied": 0, "changes": [], "restart_recommended": False})

    # Hot-reload env vars into os.environ
    load_dotenv(DOTENV_PATH, override=True)

    if any(c["key"] in OPENCODE_PROVIDER_VARS for c in changes):
        try:
            from agent.opencode_runtime import reset_opencode_runtime
            await reset_opencode_runtime("provider API key changed")
        except Exception:
            logger.warning("Failed to reset OpenCode runtime after provider key update", exc_info=True)

    # Write audit log
    db = get_db()
    if db is not None:
        await db.env_audit_log.insert_one({
            "action": "update",
            "user_email": get_user_email(request),
            "timestamp": datetime.now(timezone.utc),
            "changes": changes,
        })

    restart_recommended = any(c["key"] in CONNECTION_VARS for c in changes)

    return web.json_response({
        "success": True,
        "changes_applied": len(changes),
        "changes": changes,
        "restart_recommended": restart_recommended,
    })


# ── Audit log ──────────────────────────────────────────────────────────────


async def handle_list_env_audit(request: web.Request) -> web.Response:
    """GET /api/env/audit — paginated audit log (admin only)."""
    require_maintainer_or_above(request)

    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    limit = min(int(request.query.get("limit", "50")), 200)
    cursor = db.env_audit_log.find().sort("timestamp", -1).limit(limit)
    logs = await cursor.to_list(length=limit)

    return web.json_response({"logs": _serialize(logs)})


# ── Toggle sensitive ────────────────────────────────────────────────────────


async def handle_toggle_sensitive(request: web.Request) -> web.Response:
    """PATCH /api/env/sensitive — add or remove a key from the custom sensitive list (admin only)."""
    require_maintainer_or_above(request)

    body = await request.json()
    key = body.get("key", "").strip()
    sensitive = body.get("sensitive", True)

    if not key:
        return web.json_response({"error": "key is required"}, status=400)

    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    if sensitive:
        await db.env_settings.update_one(
            {"_id": "sensitive_keys"},
            {"$addToSet": {"keys": key}},
            upsert=True,
        )
    else:
        await db.env_settings.update_one(
            {"_id": "sensitive_keys"},
            {"$pull": {"keys": key}},
        )

    return web.json_response({"key": key, "sensitive": sensitive})


# ── Restart service ────────────────────────────────────────────────────────


async def handle_restart_service(request: web.Request) -> web.Response:
    """POST /api/env/restart — restart the Python service (admin only)."""
    require_maintainer_or_above(request)

    user_email = get_user_email(request)
    logger.warning("Service restart requested by %s", user_email)

    # Log to audit
    db = get_db()
    if db is not None:
        await db.env_audit_log.insert_one({
            "action": "restart",
            "user_email": user_email,
            "timestamp": datetime.now(timezone.utc),
        })

    # Send response before restarting
    resp = web.json_response({"success": True, "message": "Service is restarting..."})
    await resp.prepare(request)
    await resp.write_eof()

    # Replace the current process with a fresh Python invocation
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ── Route registration ─────────────────────────────────────────────────────


def setup_env_routes(app: web.Application):
    app.router.add_get("/api/env", handle_list_env)
    app.router.add_post("/api/env/reveal", handle_reveal_env)
    app.router.add_put("/api/env", handle_update_env)
    app.router.add_get("/api/env/audit", handle_list_env_audit)
    app.router.add_patch("/api/env/sensitive", handle_toggle_sensitive)
    app.router.add_post("/api/env/restart", handle_restart_service)
