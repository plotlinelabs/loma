"""Claude MAX subscription usage monitoring — view rate limits, auth status, and manage login."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp as aiohttp_client
from aiohttp import web

from api.auth_helpers import require_maintainer_or_above, require_analyst_or_above

logger = logging.getLogger(__name__)

CREDENTIALS_PATH = os.path.expanduser("~/.claude/.credentials.json")
KEYCHAIN_SERVICE = "Claude Code-credentials"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def _read_credentials() -> dict | None:
    """Read Claude CLI credentials from file or macOS Keychain."""
    # 1. Try legacy file-based credentials
    try:
        with open(CREDENTIALS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    # 2. Try macOS Keychain (newer Claude CLI versions store credentials here)
    import subprocess
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return None


def _clear_credentials():
    """Remove Claude CLI credentials from file and macOS Keychain."""
    # 1. Remove file-based credentials
    try:
        if os.path.exists(CREDENTIALS_PATH):
            os.remove(CREDENTIALS_PATH)
            logger.info("Removed credentials file: %s", CREDENTIALS_PATH)
    except OSError as e:
        logger.warning("Failed to remove credentials file: %s", e)

    # 2. Remove macOS Keychain entry
    import subprocess
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True, timeout=5,
        )
        logger.info("Removed Keychain entry: %s", KEYCHAIN_SERVICE)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Not on macOS or security command not available


def _read_oauth_token() -> str | None:
    """Read the OAuth access token from Claude CLI credentials."""
    creds = _read_credentials()
    if creds is None:
        return None
    return creds.get("claudeAiOauth", {}).get("accessToken")


def _read_token_expiry() -> int | None:
    """Read the token expiry timestamp from credentials."""
    creds = _read_credentials()
    if creds is None:
        return None
    return creds.get("claudeAiOauth", {}).get("expiresAt")


async def _fetch_rate_limit_headers() -> dict:
    """Make a minimal API call to get rate limit headers from Anthropic."""
    token = _read_oauth_token()
    if token is None:
        return {"error": "No OAuth token found. Run `claude auth login` to authenticate."}

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }

    try:
        async with aiohttp_client.ClientSession() as session:
            async with session.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": token,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=aiohttp_client.ClientTimeout(total=15),
            ) as resp:
                headers = dict(resp.headers)
                # Parse all anthropic-ratelimit-unified-* headers
                result = {}
                prefix = "anthropic-ratelimit-unified-"
                for key, value in headers.items():
                    lower_key = key.lower()
                    if lower_key.startswith(prefix):
                        suffix = lower_key[len(prefix):]
                        result[suffix] = value
                return result
    except Exception as e:
        logger.exception("Failed to fetch rate limit headers")
        return {"error": str(e)}


def _parse_usage_stats(headers: dict) -> dict:
    """Parse rate limit headers into structured usage data."""
    if "error" in headers:
        return headers

    def _bucket(prefix: str, label: str) -> dict:
        return {
            "label": label,
            "utilization": float(headers.get(f"{prefix}-utilization", 0)),
            "reset": int(headers.get(f"{prefix}-reset", 0)),
            "status": headers.get(f"{prefix}-status", "unknown"),
        }

    return {
        "session": _bucket("5h", "Current Session (5h)"),
        "weekly": _bucket("7d", "Current Week (All Models)"),
        "weekly_sonnet": _bucket("7d_sonnet", "Current Week (Sonnet Only)"),
        "overage": _bucket("overage", "Extra Usage"),
        "representative_claim": headers.get("representative-claim", ""),
        "fallback_percentage": float(headers.get("fallback-percentage", 0)),
        "overall_status": headers.get("status", "unknown"),
        "overall_reset": int(headers.get("reset", 0)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Routes ────────────────────────────────────────────────────────────────


async def handle_usage_stats(request: web.Request) -> web.Response:
    """GET /api/usage/stats — fetch current rate limit utilization."""
    require_analyst_or_above(request)
    raw = await _fetch_rate_limit_headers()
    stats = _parse_usage_stats(raw)
    if "error" in stats:
        return web.json_response(stats, status=503)
    return web.json_response(stats)


async def handle_auth_info(request: web.Request) -> web.Response:
    """GET /api/usage/auth-info — get current Claude auth status.

    Reads credentials directly from file/Keychain instead of spawning
    `claude auth status` (which can hang on headless servers).
    """
    require_analyst_or_above(request)
    try:
        creds = _read_credentials()
        if creds is None:
            return web.json_response({"loggedIn": False}, status=200)

        oauth_data = creds.get("claudeAiOauth", {})
        access_token = (oauth_data.get("accessToken") or "").strip()
        if not access_token:
            return web.json_response({"loggedIn": False}, status=200)

        info = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
        }

        # Add optional fields from credentials
        if oauth_data.get("expiresAt"):
            info["tokenExpiresAt"] = oauth_data["expiresAt"]

        # Try to get org/email info from a quick API call (best-effort, non-blocking)
        # Fall back to just showing "Authenticated" if it fails
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "auth", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "CLAUDECODE": ""},
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                cli_info = json.loads(stdout.decode())
                info.update({k: v for k, v in cli_info.items() if v is not None})
        except (asyncio.TimeoutError, Exception):
            pass  # Fine — we already know they're logged in from the credential file

        return web.json_response(info)
    except Exception as e:
        logger.exception("Failed to get auth status")
        return web.json_response({"error": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    """GET /api/usage/health — combined auth + usage health check."""
    require_analyst_or_above(request)

    # Auth status
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "auth", "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CLAUDECODE": ""},
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        auth_info = json.loads(stdout.decode()) if proc.returncode == 0 else {"loggedIn": False}
    except Exception:
        auth_info = {"loggedIn": False, "error": "Failed to check auth"}

    # Token expiry
    expiry = _read_token_expiry()
    token_valid = expiry is not None and expiry > int(datetime.now(timezone.utc).timestamp() * 1000)

    # Usage stats
    raw = await _fetch_rate_limit_headers()
    stats = _parse_usage_stats(raw)
    usage_ok = "error" not in stats and stats.get("overall_status") == "allowed"

    return web.json_response({
        "auth": auth_info,
        "token_valid": token_valid,
        "token_expires_at": expiry,
        "usage": stats if "error" not in stats else None,
        "usage_ok": usage_ok,
        "healthy": auth_info.get("loggedIn", False) and token_valid and usage_ok,
    })



async def handle_logout(request: web.Request) -> web.Response:
    """POST /api/usage/logout — log out of Claude."""
    require_maintainer_or_above(request)

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "auth", "logout",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CLAUDECODE": ""},
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)

        # Ensure credentials are fully cleared — claude auth logout may leave
        # stale files or Keychain entries on some platforms.
        _clear_credentials()

        return web.json_response({"success": True})
    except Exception as e:
        logger.exception("Logout failed")
        return web.json_response({"error": str(e)}, status=500)


# ── Setup ─────────────────────────────────────────────────────────────────


def setup_usage_routes(app: web.Application):
    app.router.add_get("/api/usage/stats", handle_usage_stats)
    app.router.add_get("/api/usage/auth-info", handle_auth_info)
    app.router.add_get("/api/usage/health", handle_health)
    app.router.add_post("/api/usage/logout", handle_logout)
