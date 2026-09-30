"""Bounded dispatcher; per-skill fencing also covers multiple scheduler processes."""
import asyncio
import logging
from api import skill_service, skill_sync_service
from observability.db import get_db

logger = logging.getLogger(__name__)


async def sync_due_skills():
    types = [t for t in ("google_doc", "google_sheet") if skill_sync_service.enabled(t)]
    if not types:
        return
    db = get_db()
    if db is None:
        return
    due = await db.skills.find({"enabled": True, "source.type": {"$in": types},
        "source.next_check": {"$lte": skill_service.now_utc()},
        "$or": [{"source.auto_sync_enabled": True}, {"source.pending": {"$exists": True}}]}, {"slug": 1}).sort("source.next_check", 1).limit(100).to_list(100)
    semaphore = asyncio.Semaphore(4)
    async def run(record):
        async with semaphore:
            try:
                await skill_sync_service.sync(db, record["slug"])
            except Exception:
                logger.warning("Linked skill sync failed for %s", record["slug"], exc_info=True)
    await asyncio.gather(*(run(record) for record in due))
