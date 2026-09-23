"""Claude connection status and shared-pool credential lifecycle."""

import asyncio
import logging
import os
import time

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

async def handle_claude_auth_status(request: web.Request) -> web.Response:
    """Read metadata only; never run a host CLI with ambient credentials."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)
    from isolation.claude_login import directory
    from isolation.accounts import _read, SubscriptionAccount
    result = {"connected": False}
    try:
        path = directory(user_email)
        SubscriptionAccount('claude', user_email, path).identity()
        meta = _read(path, '.claude.json')['oauthAccount']
        result.update(connected=True, email=meta['emailAddress'], authMethod='oauth')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        pass
    return web.json_response(result, headers={'Cache-Control': 'no-store'})


async def handle_claude_terminal_token(request: web.Request) -> web.Response:
    return web.json_response({"error": "Host terminals are disabled. Use isolated Claude login."}, status=403)


async def handle_claude_disconnect(request: web.Request) -> web.Response:
    from api.claude_login_routes import SESSIONS, allowed, owner
    from isolation.claude_login import disconnect
    user_email = owner(request)
    if not await allowed(user_email):
        raise web.HTTPForbidden()
    session = request.app[SESSIONS].pop(user_email, None)
    if session and session.task:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
    async with asyncio.timeout(10):
        await disconnect(user_email)
    try:
        get_pool().refresh_accounts()
    except RuntimeError:
        pass
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

    from isolation.deployment import remote_workers_enabled
    from isolation.claude_login import directory
    if remote_workers_enabled():
        return web.json_response({"ok": False, "error": "Use an isolated Claude task to test this account."}, status=409)
    try:
        config_dir = directory(target_email)
    except ValueError:
        return web.json_response({"error": "Invalid account identity"}, status=400)
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
    return None  # old terminal capabilities are never accepted


def setup_claude_auth_routes(app: web.Application):
    from api.claude_login_routes import setup_claude_login_routes
    setup_claude_login_routes(app)
    app.router.add_get("/api/claude-auth/status", handle_claude_auth_status)
    app.router.add_post("/api/claude-auth/terminal-token", handle_claude_terminal_token)
    app.router.add_post("/api/claude-auth/disconnect", handle_claude_disconnect)
    app.router.add_post("/api/claude-auth/test", handle_claude_auth_test)
