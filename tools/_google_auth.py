"""Shared Google OAuth token resolution for CLI tools.

CLI tools (gmail.py, google_drive.py) import this module to get a valid
Google access token for a specific user. The module handles:
  1. Connecting to MongoDB (OBSERVABILITY_MONGODB_URI)
  2. Looking up the user's encrypted tokens
  3. Decrypting tokens using OAUTH_ENCRYPTION_KEY
  4. Refreshing expired tokens automatically
  5. Returning a google.oauth2.credentials.Credentials object

Usage:
    from _google_auth import get_google_credentials, get_google_access_token
    creds = await get_google_credentials(user_email)
    # or just the raw access token:
    token = await get_google_access_token(user_email)
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone

import aiohttp
from cryptography.fernet import Fernet, InvalidToken
from motor.motor_asyncio import AsyncIOMotorClient
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _get_fernet() -> Fernet:
    key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip().strip("'\"")
    if not key:
        raise ValueError("OAUTH_ENCRYPTION_KEY environment variable is not set")
    return Fernet(key.encode())


def _decrypt(encrypted: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt token — encryption key may have changed")


def _encrypt(token: str) -> str:
    return _get_fernet().encrypt(token.encode()).decode()


async def _get_db():
    """Connect to the observability MongoDB and return the database."""
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("OBSERVABILITY_MONGODB_URI environment variable is not set")
    client = AsyncIOMotorClient(uri)
    return client, client.loma_observability


async def _refresh_token(refresh_token: str) -> dict | None:
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
                    logger.error("Token refresh failed (%d): %s", resp.status, text[:300])
                    return None
                return await resp.json()
    except Exception as e:
        logger.error("Token refresh request failed: %s", e)
        return None


async def get_google_access_token(user_email: str) -> str:
    """Get a valid Google access token for a user.

    Connects to MongoDB, looks up tokens, refreshes if expired.
    Raises ValueError if no connection exists or refresh fails.
    """
    client, db = await _get_db()
    try:
        doc = await db.oauth_tokens.find_one({
            "user_email": user_email,
            "provider": "google",
        })
        if doc is None:
            raise ValueError(
                f"No Google OAuth connection found for {user_email}. "
                "Please connect your Google account at the Integrations page in the Loma dashboard."
            )

        # Check if token is still valid
        expiry = doc.get("token_expiry")
        if expiry is not None and expiry.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            return _decrypt(doc["access_token"])

        # Token expired — refresh
        logger.info("Access token expired for %s, refreshing...", user_email)
        refresh_tok = _decrypt(doc["refresh_token"])
        new_token = await _refresh_token(refresh_tok)

        if new_token is None:
            # Mark as expired
            now = datetime.now(timezone.utc)
            await db.users.update_one(
                {"email": user_email},
                {"$set": {
                    "tool_assignments.google-personal.oauth_status": "expired",
                    "updated_at": now,
                }},
            )
            raise ValueError(
                f"Google OAuth token refresh failed for {user_email}. "
                "The token may have been revoked. Please reconnect at the Integrations page."
            )

        # Store refreshed token
        now = datetime.now(timezone.utc)
        token_expiry = datetime.fromtimestamp(
            time.time() + new_token["expires_in"], tz=timezone.utc
        )
        update: dict = {
            "access_token": _encrypt(new_token["access_token"]),
            "token_expiry": token_expiry,
            "updated_at": now,
        }
        if "refresh_token" in new_token:
            update["refresh_token"] = _encrypt(new_token["refresh_token"])

        await db.oauth_tokens.update_one(
            {"user_email": user_email},
            {"$set": update},
        )

        return new_token["access_token"]
    finally:
        client.close()


async def get_google_credentials(user_email: str) -> Credentials:
    """Get google.oauth2.credentials.Credentials for a user.

    Suitable for use with googleapiclient.discovery.build().
    """
    access_token = await get_google_access_token(user_email)
    return Credentials(token=access_token)
