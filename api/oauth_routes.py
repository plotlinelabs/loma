"""OAuth API routes — Google & Slack OAuth flows for personal tool integrations."""

import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from observability.db import get_db
from api.auth_helpers import get_user_email
from api.oauth_helpers import (
    GOOGLE_SCOPES,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    SLACK_AUTH_URL,
    SLACK_TOKEN_URL,
    SLACK_USER_SCOPES,
    create_oauth_state,
    verify_oauth_state,
    store_google_tokens,
    revoke_google_tokens,
    store_slack_tokens,
    revoke_slack_tokens,
)

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _serialize(doc):
    """Make a MongoDB document JSON-serializable."""
    if doc is None:
        return None
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
                result[k] = [_serialize(i) for i in v]
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        return doc.isoformat() + ("Z" if doc.tzinfo is None else "")
    return doc


# ── Google OAuth flow ─────────────────────────────────────────────────────


async def handle_google_authorize(request: web.Request) -> web.Response:
    """GET /api/oauth/google/authorize — return the Google OAuth consent URL."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")

    if not client_id or not redirect_uri:
        return web.json_response(
            {"error": "Google OAuth not configured (missing client ID or redirect URI)"},
            status=503,
        )

    state = create_oauth_state(email)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    authorize_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return web.json_response({"authorize_url": authorize_url})


async def handle_google_callback(request: web.Request) -> web.Response:
    """GET /api/oauth/google/callback — handle Google OAuth redirect.

    This route is public (no X-User-Email header) because Google redirects
    directly here. Authentication is via the HMAC-signed state parameter.
    """
    db = get_db()
    if db is None:
        return _callback_error("Database not available")

    # Check for error from Google
    error = request.query.get("error")
    if error:
        logger.warning("Google OAuth error: %s", error)
        return _callback_error(f"Google authorization failed: {error}")

    code = request.query.get("code")
    state = request.query.get("state")

    if not code or not state:
        return _callback_error("Missing authorization code or state")

    # Verify state (CSRF protection + user identification)
    email = verify_oauth_state(state)
    if email is None:
        return _callback_error("Invalid or expired authorization state")

    # Exchange code for tokens
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Token exchange failed (%d): %s", resp.status, text[:300])
                    return _callback_error("Failed to exchange authorization code")
                token_data = await resp.json()
    except Exception as e:
        logger.error("Token exchange request failed: %s", e)
        return _callback_error("Failed to connect to Google")

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token or not refresh_token:
        return _callback_error("Google did not return required tokens")

    # Verify the token belongs to the expected user
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return _callback_error("Failed to verify Google identity")
                userinfo = await resp.json()
    except Exception as e:
        logger.error("Userinfo request failed: %s", e)
        return _callback_error("Failed to verify Google identity")

    google_email = userinfo.get("email", "")
    if not google_email:
        return _callback_error("Could not determine Google account email")

    # Store tokens
    scopes = token_data.get("scope", "").split()
    await store_google_tokens(
        db=db,
        user_email=email,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        scopes=scopes,
    )

    logger.info("Google OAuth completed for %s (Google account: %s)", email, google_email)

    # Return HTML that closes the popup and notifies the parent window
    return _callback_success(google_email)


def _callback_success(google_email: str) -> web.Response:
    """Return HTML page that signals OAuth success to the opener and closes."""
    html = f"""<!DOCTYPE html>
<html>
<head><title>Connected</title></head>
<body>
<p>Google account ({google_email}) connected successfully. This window will close.</p>
<script>
  if (window.opener) {{
    window.opener.postMessage({{
      type: 'oauth-complete',
      provider: 'google',
      email: '{google_email}'
    }}, '*');
  }}
  setTimeout(function() {{ window.close(); }}, 1500);
</script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


def _callback_error(message: str, provider: str = "google") -> web.Response:
    """Return HTML page that signals OAuth error to the opener and closes."""
    html = f"""<!DOCTYPE html>
<html>
<head><title>Error</title></head>
<body>
<p>Error: {message}</p>
<p>You can close this window and try again.</p>
<script>
  if (window.opener) {{
    window.opener.postMessage({{
      type: 'oauth-error',
      provider: '{provider}',
      error: '{message}'
    }}, '*');
  }}
</script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


# ── Slack OAuth flow ──────────────────────────────────────────────────────


async def handle_slack_authorize(request: web.Request) -> web.Response:
    """GET /api/oauth/slack/authorize — return the Slack OAuth consent URL."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    client_id = os.environ.get("SLACK_OAUTH_CLIENT_ID", "")
    redirect_uri = os.environ.get("SLACK_OAUTH_REDIRECT_URI", "")

    if not client_id or not redirect_uri:
        return web.json_response(
            {"error": "Slack OAuth not configured (missing client ID or redirect URI)"},
            status=503,
        )

    state = create_oauth_state(email)

    # Slack v2 OAuth uses "user_scope" (not "scope") for user token scopes
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "user_scope": ",".join(SLACK_USER_SCOPES),
        "state": state,
    }

    authorize_url = f"{SLACK_AUTH_URL}?{urlencode(params)}"
    return web.json_response({"authorize_url": authorize_url})


async def handle_slack_callback(request: web.Request) -> web.Response:
    """GET /api/oauth/slack/callback — handle Slack OAuth redirect.

    This route is public (no X-User-Email header) because Slack redirects
    directly here. Authentication is via the HMAC-signed state parameter.
    """
    db = get_db()
    if db is None:
        return _callback_error("Database not available", provider="slack")

    # Check for error from Slack
    error = request.query.get("error")
    if error:
        logger.warning("Slack OAuth error: %s", error)
        return _callback_error(f"Slack authorization failed: {error}", provider="slack")

    code = request.query.get("code")
    state = request.query.get("state")

    if not code or not state:
        return _callback_error("Missing authorization code or state", provider="slack")

    # Verify state (CSRF protection + user identification)
    email = verify_oauth_state(state)
    if email is None:
        return _callback_error("Invalid or expired authorization state", provider="slack")

    # Exchange code for tokens
    client_id = os.environ.get("SLACK_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("SLACK_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("SLACK_OAUTH_REDIRECT_URI", "")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SLACK_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                token_data = await resp.json()
                if not token_data.get("ok"):
                    slack_error = token_data.get("error", "unknown")
                    logger.error("Slack token exchange failed: %s", slack_error)
                    return _callback_error(
                        f"Failed to exchange authorization code: {slack_error}",
                        provider="slack",
                    )
    except Exception as e:
        logger.error("Slack token exchange request failed: %s", e)
        return _callback_error("Failed to connect to Slack", provider="slack")

    # Slack v2 OAuth nests user tokens under authed_user
    authed_user = token_data.get("authed_user", {})
    access_token = authed_user.get("access_token", "")
    slack_user_id = authed_user.get("id", "")
    scopes = authed_user.get("scope", "").split(",")
    slack_team_id = token_data.get("team", {}).get("id", "")

    if not access_token:
        return _callback_error("Slack did not return a user access token", provider="slack")

    # Store tokens
    try:
        await store_slack_tokens(
            db=db,
            user_email=email,
            access_token=access_token,
            scopes=scopes,
            slack_user_id=slack_user_id,
            slack_team_id=slack_team_id,
        )
    except Exception as e:
        logger.error("Failed to store Slack tokens for %s: %s", email, e)
        return _callback_error("Failed to save connection. Please try again.", provider="slack")

    slack_team_name = token_data.get("team", {}).get("name", "Slack")
    logger.info(
        "Slack OAuth completed for %s (Slack user: %s, team: %s)",
        email, slack_user_id, slack_team_name,
    )

    # Return HTML that closes the popup and notifies the parent window
    return _callback_success_generic(
        provider="slack",
        display_name=f"{slack_team_name} workspace",
    )


def _callback_success_generic(provider: str, display_name: str) -> web.Response:
    """Return HTML page that signals OAuth success for any provider."""
    html = f"""<!DOCTYPE html>
<html>
<head><title>Connected</title></head>
<body>
<p>{display_name} connected successfully. This window will close.</p>
<script>
  if (window.opener) {{
    window.opener.postMessage({{
      type: 'oauth-complete',
      provider: '{provider}'
    }}, '*');
  }}
  setTimeout(function() {{ window.close(); }}, 1500);
</script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


# ── Connections management ────────────────────────────────────────────────


async def handle_list_connections(request: web.Request) -> web.Response:
    """GET /api/oauth/connections — list current user's connected OAuth accounts."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    # Look up token documents (never return actual tokens)
    docs = await db.oauth_tokens.find(
        {"user_email": email},
        {"access_token": 0, "refresh_token": 0},  # exclude tokens
    ).to_list(10)

    connections = []
    for doc in docs:
        connections.append({
            "provider": doc.get("provider", "unknown"),
            "status": "connected",
            "scopes": doc.get("scopes", []),
            "connected_at": _serialize(doc.get("connected_at")),
            "updated_at": _serialize(doc.get("updated_at")),
        })

    # Check for expired statuses for providers without active connections
    user = await db.users.find_one({"email": email})
    if user is not None:
        tool_assignments = user.get("tool_assignments") or {}
        for provider, assignment_key in [("google", "google-personal"), ("slack", "slack-personal")]:
            if not any(c["provider"] == provider for c in connections):
                status = tool_assignments.get(assignment_key, {}).get("oauth_status")
                if status == "expired":
                    connections.append({
                        "provider": provider,
                        "status": "expired",
                        "scopes": [],
                        "connected_at": None,
                        "updated_at": None,
                    })

    return web.json_response({"connections": connections})


async def handle_disconnect_google(request: web.Request) -> web.Response:
    """DELETE /api/oauth/connections/google — disconnect Google account."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    revoked = await revoke_google_tokens(db, email)
    if not revoked:
        return web.json_response({"error": "No Google connection found"}, status=404)

    return web.json_response({"disconnected": True})


async def handle_disconnect_slack(request: web.Request) -> web.Response:
    """DELETE /api/oauth/connections/slack — disconnect Slack account."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "User not authenticated"}, status=401)

    revoked = await revoke_slack_tokens(db, email)
    if not revoked:
        return web.json_response({"error": "No Slack connection found"}, status=404)

    return web.json_response({"disconnected": True})


# ── Route registration ────────────────────────────────────────────────────


def setup_oauth_routes(app: web.Application):
    """Register OAuth routes."""
    # Google
    app.router.add_get("/api/oauth/google/authorize", handle_google_authorize)
    app.router.add_get("/api/oauth/google/callback", handle_google_callback)
    app.router.add_delete("/api/oauth/connections/google", handle_disconnect_google)
    # Slack
    app.router.add_get("/api/oauth/slack/authorize", handle_slack_authorize)
    app.router.add_get("/api/oauth/slack/callback", handle_slack_callback)
    app.router.add_delete("/api/oauth/connections/slack", handle_disconnect_slack)
    # Shared
    app.router.add_get("/api/oauth/connections", handle_list_connections)
