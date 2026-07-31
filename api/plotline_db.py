"""Separate MongoDB connection for Plotline dashboard configuration."""

import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)
_client: AsyncIOMotorClient | None = None
_db = None


async def init_plotline_db():
    """Initialize the optional Plotline dashboard database connection."""
    global _client, _db
    uri = os.environ.get("PLOTLINE_MONGODB_URI", "").strip()
    if not uri or not uri.startswith("mongodb"):
        logger.warning("PLOTLINE_MONGODB_URI not set or invalid; billing mapping disabled")
        return
    _client = AsyncIOMotorClient(uri)
    database_name = os.environ.get("PLOTLINE_MONGODB_DB_NAME", "plotline").strip() or "plotline"
    _db = _client[database_name]
    await _db.command("ping")
    logger.info("Plotline dashboard MongoDB connected")


def get_plotline_db():
    return _db
