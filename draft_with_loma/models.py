"""CRUD operations for the drafts MongoDB collection."""

import uuid
import logging
from datetime import datetime, timezone

from observability.db import get_db

logger = logging.getLogger(__name__)


async def create_draft(
    user_email: str,
    slack_user_id: str,
    channel_id: str,
    thread_ts: str,
    user_context: str = "",
) -> dict:
    """Create a new draft record. Returns the inserted document."""
    db = get_db()
    if db is None:
        raise RuntimeError("Database not initialized")

    doc = {
        "draft_id": str(uuid.uuid4()),
        "user_email": user_email,
        "slack_user_id": slack_user_id,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "user_context": user_context,
        "draft_text": "",
        "status": "drafting",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db.drafts.insert_one(doc)
    logger.info("[DRAFT] Created draft %s for %s", doc["draft_id"], user_email)
    return doc


async def get_draft(draft_id: str) -> dict | None:
    """Fetch a draft by its ID."""
    db = get_db()
    if db is None:
        return None
    return await db.drafts.find_one({"draft_id": draft_id})


async def update_draft(draft_id: str, **fields) -> None:
    """Update fields on a draft."""
    db = get_db()
    if db is None:
        return
    fields["updated_at"] = datetime.now(timezone.utc)
    await db.drafts.update_one(
        {"draft_id": draft_id},
        {"$set": fields},
    )


async def delete_draft(draft_id: str) -> None:
    """Delete a draft."""
    db = get_db()
    if db is None:
        return
    await db.drafts.delete_one({"draft_id": draft_id})
