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

from api.auth_helpers import get_user_email, require_admin
from agent.pool import get_pool

logger = logging.getLogger(__name__)

# Substrings (lowercased) that indicate a failure is an auth problem
# (expired/revoked OAuth token, logged out) rather than a transient
# error like a rate limit or network issue.
_AUTH_ERROR_MARKERS = (
    "authentication_error",
    "authentication failed",
    "not logged in",
    "please run /login",
    "please log in",
    "oauth token has expired",
    "token has expired",
    "invalid api key",
    "invalid bearer token",
    "unauthorized",
    "401",
    "credential",
    "revoked",
)

TEST_TIMEOUT_SECONDS = 90

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


async def remove_claude_credentials(user_email: str) -> bool:
    """Gracefully log out and delete a user's Claude credential dir.

    Shared by the disconnect route and user deletion (governance) so a removed
    user's OAuth file cannot be re-discovered by the pool's disk rescan.
    Returns True if the dir is gone (or never existed), False on removal failure.
    """
    config_dir = _get_claude_users_dir() / user_email
    if not config_dir.exists():
        return True

    # Try graceful logout first
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
        return True
    except OSError as e:
        logger.error("Failed to remove config dir for %s: %s", user_email, e)
        return False


async def handle_claude_disconnect(request: web.Request) -> web.Response:
    """POST /api/claude-auth/disconnect — remove user's Claude credentials."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)

    # Refresh pool accounts after disconnect
    try:
        pool = get_pool()
    except RuntimeError:
        pool = None

    if not await remove_claude_credentials(user_email):
        return web.json_response({"error": "Failed to remove credentials"}, status=500)

    # Re-scan accounts so the pool drops this user
    if pool is not None:
        pool.refresh_accounts()

    return web.json_response({"ok": True})


async def handle_claude_auth_test(request: web.Request) -> web.Response:
    """POST /api/claude-auth/test — run a tiny test chat against a user's Claude account.

    Admin-only. Body: {"email": "<user email>"} (defaults to the requester).
    Runs `claude -p` with the target user's CLAUDE_CONFIG_DIR so an admin can
    verify whether that account works or is failing with an auth error
    (e.g. someone connected the wrong Claude account).
    """
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)
    require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    target_email = (body.get("email") or user_email).strip()

    config_dir = _get_claude_users_dir() / target_email
    if not (config_dir / ".claude.json").exists():
        return web.json_response(
            {"ok": False, "email": target_email, "error": "This user has no Claude account connected."},
            status=404,
        )

    prompt = "Reply with exactly one word: OK"
    model = os.environ.get("CLAUDE_TEST_MODEL", "haiku")
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--model", model,
            env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)},
            cwd=str(config_dir),  # avoid loading the repo's project settings
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return web.json_response({
            "ok": False,
            "email": target_email,
            "error": f"Test timed out after {TEST_TIMEOUT_SECONDS}s.",
            "auth_error": False,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
    except FileNotFoundError:
        return web.json_response(
            {"ok": False, "email": target_email, "error": "claude CLI not found on server."},
            status=500,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    out = (stdout or b"").decode(errors="replace").strip()
    err = (stderr or b"").decode(errors="replace").strip()

    if proc.returncode == 0 and out:
        logger.info("Claude test OK for %s (%dms)", target_email, duration_ms)
        return web.json_response({
            "ok": True,
            "email": target_email,
            "response": out[:2000],
            "auth_error": False,
            "duration_ms": duration_ms,
        })

    combined = f"{out}\n{err}".lower()
    auth_error = any(marker in combined for marker in _AUTH_ERROR_MARKERS)
    error_text = (err or out or f"claude exited with code {proc.returncode}")[:2000]
    logger.warning(
        "Claude test FAILED for %s (auth_error=%s, code=%s): %s",
        target_email, auth_error, proc.returncode, error_text[:300],
    )
    return web.json_response({
        "ok": False,
        "email": target_email,
        "error": error_text,
        "auth_error": auth_error,
        "duration_ms": duration_ms,
    })


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
    app.router.add_post("/api/claude-auth/test", handle_claude_auth_test)
