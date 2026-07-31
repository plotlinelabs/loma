"""Separate MongoDB connection for the product dashboard configuration database."""

import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)
_client: AsyncIOMotorClient | None = None
_db = None
# Records *why* the optional connection is unavailable so the billing-mapping API can
# return an actionable 503 (env var absent vs. cluster unreachable) without ever
# exposing a secret value. Not "connected" => get_dashboard_db() is None.
_status = "uninitialized"


def _dashboard_mongodb_uri() -> str:
    """Resolve the dashboard DB URI across new and existing deployments."""
    return (
        os.environ.get("DASHBOARD_MONGODB_URI", "").strip()
        or os.environ.get("MONGODB_DASHBOARD_URI", "").strip()
    )


async def init_dashboard_db():
    """Initialize or reconnect the optional dashboard configuration database."""
    global _client, _db, _status
    old_client = _client
    _client, _db = None, None
    if old_client is not None:
        old_client.close()
    # Production and preview hosts historically expose this connection as
    # MONGODB_DASHBOARD_URI. Keep the feature-specific name as an override,
    # but do not require a brand-new secret name just for this page.
    uri = _dashboard_mongodb_uri()
    if not uri or not uri.startswith("mongodb"):
        _status = "env-missing"
        logger.warning(
            "DASHBOARD_MONGODB_URI/MONGODB_DASHBOARD_URI not set or invalid; "
            "billing mapping disabled"
        )
        return
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        database_name = (
            os.environ.get("DASHBOARD_MONGODB_DB_NAME", "").strip() or "dashboard"
        )
        db = client[database_name]
        await db.command("ping")
    except Exception:
        _status = "connect-failed"
        logger.exception("Dashboard MongoDB unavailable; billing mapping disabled")
        if "client" in locals():
            client.close()
        _client, _db = None, None
        return
    _client, _db, _status = client, db, "connected"
    logger.info("Dashboard MongoDB connected")


async def close_dashboard_db():
    """Close the optional dashboard connection during application shutdown."""
    global _client, _db
    if _client is not None:
        _client.close()
    _client, _db = None, None


def get_dashboard_db():
    return _db


def get_dashboard_db_status() -> str:
    """Return why the optional dashboard connection is (un)available: "connected",
    "env-missing", "connect-failed", or "uninitialized". Never includes secrets."""
    return _status
