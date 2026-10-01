"""Shared task creation helpers used by HTTP and external integrations."""

import uuid
from datetime import datetime, timezone

from api.task_routes import _get_board_config_for


async def create_staged_task(
    db,
    user_email: str,
    prompt: str,
    *,
    title: str | None = None,
    model: str = "",
    metadata: dict | None = None,
    dedupe_filter: dict | None = None,
) -> tuple[dict, bool]:
    """Create a Todo task, returning ``(document, created)``.

    ``dedupe_filter`` lets event-driven callers make retries idempotent.
    """
    if dedupe_filter:
        existing = await db.conversations.find_one(dedupe_filter)
        if existing:
            return existing, False

    board = await _get_board_config_for(db, user_email)
    lane = board["lanes"][0]["id"]
    now = datetime.now(timezone.utc)
    task_metadata = {"user_name": user_email}
    task_metadata.update(metadata or {})
    doc = {
        "conversation_id": str(uuid.uuid4()),
        "source": task_metadata.get("source", "dashboard"),
        "started_at": None,
        "finished_at": None,
        "duration_ms": None,
        "status": None,
        "metadata": task_metadata,
        "prompt": prompt,
        "model": model,
        "total_turns": 0,
        "final_response": "",
        "messages": [],
        "confidence": None,
        "cost": None,
        "savings": None,
        "claude_account": None,
        "error": None,
        "deleted": False,
        "title": title,
        "title_edited": bool(title),
        "task_status": "todo",
        "task_lane": lane,
        "task_rank": -now.timestamp(),
        "task_created_at": now,
        "task_staged_at": now,
        "task_started_at": None,
        "task_done_at": None,
        "task_tag_ids": [],
        "task_priority": None,
        "task_deadline": None,
    }
    await db.conversations.insert_one(doc)
    return doc, True
