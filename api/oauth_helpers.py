"""OAuth token helpers — encryption, storage, refresh, revocation.

Tokens are encrypted at rest using Fernet symmetric encryption.
The encryption key is read from OAUTH_ENCRYPTION_KEY env var.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone

import aiohttp
from cryptography.fernet import Fernet, InvalidToken

from observability.db import get_db

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/userinfo.email",
]

# ── Slack OAuth constants ─────────────────────────────────────────────────

SLACK_AUTH_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_REVOKE_URL = "https://slack.com/api/auth.revoke"
SLACK_IDENTITY_URL = "https://slack.com/api/users.identity"

SLACK_USER_SCOPES = [
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "im:write",
    "mpim:history",
    "mpim:read",
    "mpim:write",
    "chat:write",
    "search:read",
    "users:read",
    "users:read.email",
    "reactions:read",
    "reactions:write",
    "files:read",
    "files:write",
]


# ── Encryption ────────────────────────────────────────────────────────────


def _get_fernet() -> Fernet:
    key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip().strip("'\"")
    if not key:
        raise ValueError("OAUTH_ENCRYPTION_KEY environment variable is not set")
    return Fernet(key.encode())


def encrypt_token(token: str) -> str:
    """Encrypt a token string using Fernet."""
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a token string using Fernet."""
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt token — key may have changed")


# ── HMAC state for CSRF protection ───────────────────────────────────────


def create_oauth_state(email: str) -> str:
    """Create an HMAC-signed OAuth state parameter.

    Encodes user email + timestamp, signed with OAUTH_ENCRYPTION_KEY.
    """
    key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip().strip("'\"")
    ts = str(int(time.time()))
    payload = f"{email}:{ts}"
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    state_data = json.dumps({"email": email, "ts": ts, "sig": sig})
    import base64
    return base64.urlsafe_b64encode(state_data.encode()).decode()


def verify_oauth_state(state: str, max_age: int = 600) -> str | None:
    """Verify an HMAC-signed OAuth state and return the email.

    Returns None if invalid or expired (default: 10 minute max age).
    """
    import base64
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state).decode())
    except Exception:
        logger.warning("Invalid OAuth state encoding")
        return None

    email = state_data.get("email", "")
    ts = state_data.get("ts", "")
    sig = state_data.get("sig", "")

    if not email or not ts or not sig:
        logger.warning("Missing fields in OAuth state")
        return None

    # Check expiry
    try:
        if int(time.time()) - int(ts) > max_age:
            logger.warning("OAuth state expired for %s", email)
            return None
    except ValueError:
        return None

    # Verify HMAC
    key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip().strip("'\"")
    payload = f"{email}:{ts}"
    expected_sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        logger.warning("OAuth state HMAC mismatch for %s", email)
        return None

    return email


# ── Token storage ─────────────────────────────────────────────────────────


async def _ensure_tool_assignments_mapping(db, user_email: str) -> None:
    """Normalize legacy user rows before nested tool assignment updates."""
    user = await db.users.find_one({"email": user_email}, {"tool_assignments": 1})
    if user is None:
        return
    if not isinstance(user.get("tool_assignments", {}), dict):
        await db.users.update_one(
            {"email": user_email},
            {"$set": {"tool_assignments": {}}},
        )


async def store_google_tokens(
    db,
    user_email: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    scopes: list[str],
) -> None:
    """Encrypt and store Google OAuth tokens. Updates user's tool assignment status."""
    now = datetime.now(timezone.utc)
    token_expiry = datetime.fromtimestamp(
        time.time() + expires_in, tz=timezone.utc
    )

    await db.oauth_tokens.update_one(
        {"user_email": user_email, "provider": "google"},
        {"$set": {
            "provider": "google",
            "access_token": encrypt_token(access_token),
            "refresh_token": encrypt_token(refresh_token),
            "token_expiry": token_expiry,
            "scopes": scopes,
            "updated_at": now,
        }, "$setOnInsert": {
            "connected_at": now,
        }},
        upsert=True,
    )

    # Update user's tool_assignments status
    await _ensure_tool_assignments_mapping(db, user_email)
    await db.users.update_one(
        {"email": user_email},
        {"$set": {
            "tool_assignments.google-personal.oauth_status": "connected",
            "tool_assignments.google-personal.last_used": now.isoformat() + "Z",
            "updated_at": now,
        }},
    )

    logger.info("Stored Google OAuth tokens for %s", user_email)


async def get_valid_google_token(user_email: str, db=None) -> str | None:
    """Get a valid Google access token for a user.

    Refreshes automatically if expired. Returns None if no token exists
    or refresh fails (marks status as 'expired').
    """
    if db is None:
        db = get_db()
    if db is None:
        logger.error("Database not available for token lookup")
        return None

    doc = await db.oauth_tokens.find_one({"user_email": user_email, "provider": "google"})
    if doc is None:
        return None

    # Check if token is still valid (with 60s buffer)
    expiry = doc.get("token_expiry")
    if expiry is not None and expiry.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        try:
            return decrypt_token(doc["access_token"])
        except ValueError:
            logger.error("Failed to decrypt access token for %s", user_email)
            return None

    # Token expired — refresh it
    logger.info("Access token expired for %s, refreshing...", user_email)
    try:
        refresh_token = decrypt_token(doc["refresh_token"])
    except ValueError:
        logger.error("Failed to decrypt refresh token for %s", user_email)
        await _mark_expired(db, user_email)
        return None

    new_token = await _refresh_google_token(refresh_token)
    if new_token is None:
        await _mark_expired(db, user_email)
        return None

    # Store the refreshed token
    now = datetime.now(timezone.utc)
    token_expiry = datetime.fromtimestamp(
        time.time() + new_token["expires_in"], tz=timezone.utc
    )
    update: dict = {
        "access_token": encrypt_token(new_token["access_token"]),
        "token_expiry": token_expiry,
        "updated_at": now,
    }
    # Google may issue a new refresh token (rotation)
    if "refresh_token" in new_token:
        update["refresh_token"] = encrypt_token(new_token["refresh_token"])

    await db.oauth_tokens.update_one(
        {"user_email": user_email},
        {"$set": update},
    )

    logger.info("Refreshed Google access token for %s", user_email)
    return new_token["access_token"]


async def revoke_google_tokens(db, user_email: str) -> bool:
    """Revoke Google tokens, delete from DB, update user status."""
    doc = await db.oauth_tokens.find_one({"user_email": user_email, "provider": "google"})
    if doc is None:
        return False

    # Try to revoke at Google (best-effort)
    try:
        access_token = decrypt_token(doc["access_token"])
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GOOGLE_REVOKE_URL,
                params={"token": access_token},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info("Revoked Google token for %s", user_email)
                else:
                    logger.warning("Google revoke returned %d for %s", resp.status, user_email)
    except Exception as e:
        logger.warning("Failed to revoke Google token for %s: %s", user_email, e)

    # Delete from DB
    await db.oauth_tokens.delete_one({"user_email": user_email, "provider": "google"})

    # Update user status
    now = datetime.now(timezone.utc)
    await _ensure_tool_assignments_mapping(db, user_email)
    await db.users.update_one(
        {"email": user_email},
        {"$set": {
            "tool_assignments.google-personal.oauth_status": "not_connected",
            "updated_at": now,
        }},
    )

    logger.info("Disconnected Google for %s", user_email)
    return True


# ── Internal helpers ──────────────────────────────────────────────────────


async def _refresh_google_token(refresh_token: str) -> dict | None:
    """Exchange a refresh token for a new access token."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
                    "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Google token refresh failed (%d): %s", resp.status, text[:300])
                    return None
                return await resp.json()
    except Exception as e:
        logger.error("Google token refresh request failed: %s", e)
        return None


async def _mark_expired(db, user_email: str) -> None:
    """Mark a user's Google OAuth status as expired."""
    now = datetime.now(timezone.utc)
    await _ensure_tool_assignments_mapping(db, user_email)
    await db.users.update_one(
        {"email": user_email},
        {"$set": {
            "tool_assignments.google-personal.oauth_status": "expired",
            "updated_at": now,
        }},
    )
    logger.warning("Marked Google OAuth as expired for %s", user_email)


# ── Slack token storage ──────────────────────────────────────────────────


async def store_slack_tokens(
    db,
    user_email: str,
    access_token: str,
    scopes: list[str],
    slack_user_id: str = "",
    slack_team_id: str = "",
) -> None:
    """Encrypt and store Slack OAuth user token.

    Slack user tokens are long-lived (no expiry / no refresh token).
    """
    now = datetime.now(timezone.utc)

    await db.oauth_tokens.update_one(
        {"user_email": user_email, "provider": "slack"},
        {"$set": {
            "provider": "slack",
            "access_token": encrypt_token(access_token),
            "scopes": scopes,
            "slack_user_id": slack_user_id,
            "slack_team_id": slack_team_id,
            "updated_at": now,
        }, "$setOnInsert": {
            "connected_at": now,
        }},
        upsert=True,
    )

    # Update user's tool_assignments status
    await _ensure_tool_assignments_mapping(db, user_email)
    await db.users.update_one(
        {"email": user_email},
        {"$set": {
            "tool_assignments.slack-personal.oauth_status": "connected",
            "tool_assignments.slack-personal.last_used": now.isoformat() + "Z",
            "updated_at": now,
        }},
    )

    logger.info("Stored Slack OAuth token for %s", user_email)


async def revoke_slack_tokens(db, user_email: str) -> bool:
    """Revoke Slack user token, delete from DB, update user status."""
    doc = await db.oauth_tokens.find_one({"user_email": user_email, "provider": "slack"})
    if doc is None:
        return False

    # Try to revoke at Slack (best-effort)
    try:
        access_token = decrypt_token(doc["access_token"])
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SLACK_REVOKE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    logger.info("Revoked Slack token for %s", user_email)
                else:
                    logger.warning("Slack revoke returned error for %s: %s", user_email, data.get("error"))
    except Exception as e:
        logger.warning("Failed to revoke Slack token for %s: %s", user_email, e)

    # Delete from DB
    await db.oauth_tokens.delete_one({"user_email": user_email, "provider": "slack"})

    # Update user status
    now = datetime.now(timezone.utc)
    await _ensure_tool_assignments_mapping(db, user_email)
    await db.users.update_one(
        {"email": user_email},
        {"$set": {
            "tool_assignments.slack-personal.oauth_status": "not_connected",
            "updated_at": now,
        }},
    )

    logger.info("Disconnected Slack for %s", user_email)
    return True
