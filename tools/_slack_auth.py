"""Shared Slack OAuth token resolution for CLI tools.

CLI tools (slack_user.py) import this module to get a valid
Slack user token for a specific user. The module handles:
  1. Connecting to MongoDB (OBSERVABILITY_MONGODB_URI)
  2. Looking up the user's encrypted tokens
  3. Decrypting tokens using OAUTH_ENCRYPTION_KEY
  4. Returning the raw user token string

Slack user tokens are long-lived and don't expire, so no refresh logic is needed.

Usage:
    from _slack_auth import get_slack_user_token
    token = await get_slack_user_token(user_email)
"""

import os
import logging

from cryptography.fernet import Fernet, InvalidToken
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)


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


async def _get_db():
    """Connect to the observability MongoDB and return the database."""
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("OBSERVABILITY_MONGODB_URI environment variable is not set")
    client = AsyncIOMotorClient(uri)
    return client, client.loma_observability


async def get_slack_user_token(user_email: str) -> str:
    """Get a valid Slack user token for a user.

    Connects to MongoDB, looks up tokens, decrypts and returns.
    Raises ValueError if no connection exists.
    """
    client, db = await _get_db()
    try:
        doc = await db.oauth_tokens.find_one({
            "user_email": user_email,
            "provider": "slack",
        })
        if doc is None:
            raise ValueError(
                f"No Slack OAuth connection found for {user_email}. "
                "Please connect your Slack account at the Integrations page in the Loma dashboard."
            )

        return _decrypt(doc["access_token"])
    finally:
        client.close()
