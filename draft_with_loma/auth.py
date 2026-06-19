"""Slack user token resolution using the shared DB connection."""

import os
import logging

from cryptography.fernet import Fernet, InvalidToken
from observability.db import get_db

logger = logging.getLogger(__name__)


def _decrypt(encrypted: str) -> str:
    key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip().strip("'\"")
    if not key:
        raise ValueError("OAUTH_ENCRYPTION_KEY environment variable is not set")
    try:
        return Fernet(key.encode()).decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt token — encryption key may have changed")


async def get_user_slack_token(user_email: str) -> str:
    """Get a decrypted Slack user token using the shared DB connection."""
    db = get_db()
    if db is None:
        raise RuntimeError("Database not initialized")

    doc = await db.oauth_tokens.find_one({
        "user_email": user_email,
        "provider": "slack",
    })
    if doc is None:
        raise ValueError(
            f"No Slack OAuth connection found for {user_email}. "
            "Please connect your Slack account in the Loma dashboard."
        )
    return _decrypt(doc["access_token"])
