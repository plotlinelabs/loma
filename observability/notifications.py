"""Notification inbox — persistent per-user notifications.

One doc per notification in the `notifications` collection. Created by
agents/flows via tools/notify.py (or backend code), listed and managed by
the dashboard through api/notification_routes.py. A notification persists
until the user explicitly dismisses it; `read` only clears the unread badge.

Doc shape:
    notification_id: str (uuid4)
    user_email: str            — owner (recipient)
    title: str
    body: str
    conversation_id: str|None  — deep-link target (/chat?continue=<id>)
    flow_id: str|None
    link: str|None             — fallback URL when there is no conversation
    source: str                — "flow" | "agent" | "system"
    read: bool
    dismissed: bool
    created_at: datetime (utc)
"""

import os
import uuid
from datetime import datetime, timezone

from observability.push import fire_user_push

MAX_TITLE_LEN = 200
MAX_BODY_LEN = 2000


async def create_notification(
    db,
    *,
    user_email: str,
    title: str,
    body: str = "",
    conversation_id: str | None = None,
    flow_id: str | None = None,
    link: str | None = None,
    source: str = "agent",
    fire_push: bool = True,
) -> dict:
    """Insert a notification and (best-effort) fire a web push to its owner.

    Returns the inserted doc (without Mongo's _id). Raises ValueError on
    missing required fields.
    """
    user_email = (user_email or "").strip().lower()
    title = (title or "").strip()
    if not user_email or "@" not in user_email:
        raise ValueError("user_email is required")
    if not title:
        raise ValueError("title is required")

    doc = {
        "notification_id": str(uuid.uuid4()),
        "user_email": user_email,
        "title": title[:MAX_TITLE_LEN],
        "body": (body or "").strip()[:MAX_BODY_LEN],
        "conversation_id": conversation_id or None,
        "flow_id": flow_id or None,
        "link": (link or "").strip() or None,
        "source": source or "agent",
        "read": False,
        "dismissed": False,
        "created_at": datetime.now(timezone.utc),
    }
    await db.notifications.insert_one({**doc})

    if fire_push:
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        if doc["conversation_id"]:
            url = f"{base_url}/chat?continue={doc['conversation_id']}"
        else:
            url = doc["link"] or f"{base_url}/notifications"
        fire_user_push(
            db, user_email,
            title=doc["title"], body=doc["body"],
            url=url, tag=doc["notification_id"],
        )

    return doc
