"""Routes for per-user Claude Code authentication (individual subscriptions)."""

import asyncio
import json
import logging
import os
import secrets
import shutil
import time
from pathlib import Path

from aiohttp import web

from api.auth_helpers import get_user_email
from agent.pool import get_pool

logger = logging.getLogger(__name__)

def _get_claude_users_dir() -> Path:
    """Get CLAUDE_USERS_DIR lazily so .env is loaded before first access."""
    return Path(os.environ.get("CLAUDE_USERS_DIR", "/opt/claude-users"))

# One-time tokens for claude-login terminal sessions: token -> {expiry, auto_command}
_claude_terminal_tokens: dict[str, dict] = {}
TOKEN_TTL = 30  # seconds


async def handle_claude_auth_status(request: web.Request) -> web.Response:
    """GET /api/claude-auth/status — check if user has Claude credentials."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)

    config_dir = _get_claude_users_dir() / user_email
    connected = False
    result: dict = {"connected": False}

    # Only check auth if config dir exists (created when terminal-token is issued)
    if config_dir.exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "auth", "status", "--json",
                env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0 and stdout:
                info = json.loads(stdout.decode())
                auth_email = info.get("email", "")
                # Only consider connected if there's an actual authenticated account
                if auth_email:
                    connected = True
                    result["connected"] = True
                    result["email"] = auth_email
                    result["authMethod"] = info.get("authMethod", "")
        except Exception as e:
            logger.warning("Failed to read claude auth status for %s: %s", user_email, e)

    # Check pool status for connected account info
    try:
        pool = get_pool()
        status = pool.status()
        result["pool_accounts"] = len(status.get("accounts", []))
        result["pool_available"] = status.get("available", 0)
        # If user just connected, refresh pool accounts to include them
        if connected and user_email not in status.get("accounts", []):
            pool.refresh_accounts()
    except RuntimeError:
        pass

    return web.json_response(result)


async def handle_claude_terminal_token(request: web.Request) -> web.Response:
    """POST /api/claude-auth/terminal-token — issue a one-time token for login terminal."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)

    # Ensure user dir exists
    config_dir = _get_claude_users_dir() / user_email
    config_dir.mkdir(parents=True, exist_ok=True)

    # Clean expired tokens
    now = time.time()
    expired = [t for t, v in _claude_terminal_tokens.items() if v["expiry"] < now]
    for t in expired:
        _claude_terminal_tokens.pop(t, None)

    token = secrets.token_urlsafe(32)
    auto_command = f"CLAUDE_CONFIG_DIR={config_dir} claude login"
    _claude_terminal_tokens[token] = {"expiry": now + TOKEN_TTL, "auto_command": auto_command}

    return web.json_response({"token": token, "autoCommand": auto_command})


async def handle_claude_disconnect(request: web.Request) -> web.Response:
    """POST /api/claude-auth/disconnect — remove user's Claude credentials."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)

    config_dir = _get_claude_users_dir() / user_email

    # Refresh pool accounts after disconnect
    try:
        pool = get_pool()
    except RuntimeError:
        pool = None

    # Try graceful logout first
    if config_dir.exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "auth", "logout",
                env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception as e:
            logger.warning("Claude logout failed for %s: %s", user_email, e)

        # Remove the config directory
        try:
            shutil.rmtree(config_dir)
            logger.info("Removed Claude config dir for %s", user_email)
        except OSError as e:
            logger.error("Failed to remove config dir for %s: %s", user_email, e)
            return web.json_response({"error": "Failed to remove credentials"}, status=500)

    # Re-scan accounts so the pool drops this user
    if pool is not None:
        pool.refresh_accounts()

    return web.json_response({"ok": True})


def get_claude_terminal_token(token: str) -> dict | None:
    """Validate and consume a claude terminal token. Returns token info or None."""
    info = _claude_terminal_tokens.pop(token, None)
    if not info or info["expiry"] < time.time():
        return None
    return info


def setup_claude_auth_routes(app: web.Application):
    app.router.add_get("/api/claude-auth/status", handle_claude_auth_status)
    app.router.add_post("/api/claude-auth/terminal-token", handle_claude_terminal_token)
    app.router.add_post("/api/claude-auth/disconnect", handle_claude_disconnect)
