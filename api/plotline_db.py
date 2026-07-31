"""Separate MongoDB connection for Plotline dashboard configuration."""

import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)
_client: AsyncIOMotorClient | None = None
_db = None


def _dashboard_mongodb_uri() -> str:
    """Resolve the dashboard DB URI across new and existing deployments."""
    return (
        os.environ.get("PLOTLINE_MONGODB_URI", "").strip()
        or os.environ.get("MONGODB_DASHBOARD_URI", "").strip()
    )


async def init_plotline_db():
    """Initialize the optional Plotline dashboard database connection."""
    global _client, _db
    # Production and preview hosts historically expose this connection as
    # MONGODB_DASHBOARD_URI. Keep the feature-specific name as an override,
    # but do not require a brand-new secret name just for this page.
    uri = _dashboard_mongodb_uri()
    if not uri or not uri.startswith("mongodb"):
        logger.warning(
            "PLOTLINE_MONGODB_URI/MONGODB_DASHBOARD_URI not set or invalid; "
            "billing mapping disabled"
        )
        return
    _client = AsyncIOMotorClient(uri)
    database_name = os.environ.get("PLOTLINE_MONGODB_DB_NAME", "plotline").strip() or "plotline"
    _db = _client[database_name]
    await _db.command("ping")
    logger.info("Plotline dashboard MongoDB connected")


def get_plotline_db():
    return _db
