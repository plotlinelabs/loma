"""Routes for per-user Codex (ChatGPT subscription) authentication.

Mirrors api/claude_auth_routes.py: each user connects their own Codex
subscription from the Integrations page; credentials land in an isolated
``CODEX_HOME`` dir (``$CODEX_USERS_DIR/<email>/auth.json``) via a device-auth
login run inside the dashboard web terminal. Connected accounts join the
round-robin Codex pool (agent/codex_pool.py).

The browser-redirect OAuth flow (localhost:1455) cannot work through the
server-side PTY, so the auto-command uses the device-auth flow: the CLI prints
a code the user approves at chatgpt.com from their own browser. The exact flag
is configurable via CODEX_LOGIN_ARGS to track CLI changes without a deploy.
"""

import asyncio
import logging
import os
import secrets
import shutil
import time
from pathlib import Path

from aiohttp import web

from api.auth_helpers import get_user_email
from agent.codex_pool import get_codex_pool
from agent.codex_runtime import read_codex_auth

logger = logging.getLogger(__name__)


def _get_codex_users_dir() -> Path:
    """Get CODEX_USERS_DIR lazily so .env is loaded before first access."""
    return Path(os.environ.get("CODEX_USERS_DIR", "/opt/codex-users"))


def _codex_login_args() -> str:
    """Extra args for `codex login` (device-auth flow for headless PTY)."""
    return os.environ.get("CODEX_LOGIN_ARGS", "--device-auth")


# One-time tokens for codex-login terminal sessions: token -> {expiry, auto_command}
_codex_terminal_tokens: dict[str, dict] = {}
TOKEN_TTL = 30  # seconds


async def handle_codex_auth_status(request: web.Request) -> web.Response:
    """GET /api/codex-auth/status — check if user has Codex credentials."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)

    config_dir = _get_codex_users_dir() / user_email
    result: dict = {"connected": False}

    connected = False
    if config_dir.exists():
        auth = read_codex_auth(config_dir)
        if auth is not None:
            connected = True
            result["connected"] = True
            result["email"] = auth.get("email") or user_email
            result["authMethod"] = auth.get("auth_method", "")
            if auth.get("plan"):
                result["plan"] = auth["plan"]

    try:
        pool = get_codex_pool()
        status = pool.status()
        result["pool_accounts"] = len(status.get("accounts", []))
        result["pool_available"] = status.get("available", 0)
        # If user just connected, refresh pool accounts to include them
        if connected and user_email not in status.get("accounts", []):
            pool.refresh_accounts()
    except RuntimeError:
        # Pool not initialized (CODEX_POOL_ENABLED off) — credentials are
        # still stored so the pool picks them up when enabled.
        result["pool_enabled"] = False

    return web.json_response(result)


async def handle_codex_terminal_token(request: web.Request) -> web.Response:
    """POST /api/codex-auth/terminal-token — issue a one-time token for login terminal."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)

    config_dir = _get_codex_users_dir() / user_email
    config_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    expired = [t for t, v in _codex_terminal_tokens.items() if v["expiry"] < now]
    for t in expired:
        _codex_terminal_tokens.pop(t, None)

    token = secrets.token_urlsafe(32)
    auto_command = f"CODEX_HOME={config_dir} codex login {_codex_login_args()}".rstrip()
    _codex_terminal_tokens[token] = {"expiry": now + TOKEN_TTL, "auto_command": auto_command}

    return web.json_response({"token": token, "autoCommand": auto_command})


async def remove_codex_credentials(user_email: str) -> bool:
    """Gracefully log out and delete a user's Codex credential dir.

    Shared by the disconnect route and user deletion (governance) so a removed
    user's auth file cannot be re-discovered by the pool's disk rescan.
    Returns True if the dir is gone (or never existed), False on removal failure.
    """
    config_dir = _get_codex_users_dir() / user_email
    if not config_dir.exists():
        return True

    # Try graceful logout first (revokes the token server-side)
    try:
        proc = await asyncio.create_subprocess_exec(
            "codex", "logout",
            env={**os.environ, "CODEX_HOME": str(config_dir)},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception as e:
        logger.warning("Codex logout failed for %s: %s", user_email, e)

    try:
        shutil.rmtree(config_dir)
        logger.info("Removed Codex config dir for %s", user_email)
        return True
    except OSError as e:
        logger.error("Failed to remove Codex config dir for %s: %s", user_email, e)
        return False


async def handle_codex_disconnect(request: web.Request) -> web.Response:
    """POST /api/codex-auth/disconnect — remove user's Codex credentials."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)

    try:
        pool = get_codex_pool()
    except RuntimeError:
        pool = None

    if not await remove_codex_credentials(user_email):
        return web.json_response({"error": "Failed to remove credentials"}, status=500)

    if pool is not None:
        pool.refresh_accounts()

    return web.json_response({"ok": True})


def get_codex_terminal_token(token: str) -> dict | None:
    """Validate and consume a codex terminal token. Returns token info or None."""
    info = _codex_terminal_tokens.pop(token, None)
    if not info or info["expiry"] < time.time():
        return None
    return info


def setup_codex_auth_routes(app: web.Application):
    app.router.add_get("/api/codex-auth/status", handle_codex_auth_status)
    app.router.add_post("/api/codex-auth/terminal-token", handle_codex_terminal_token)
    app.router.add_post("/api/codex-auth/disconnect", handle_codex_disconnect)
