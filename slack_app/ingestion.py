"""
Slack Event Ingestion (Pass 1)

Middleware that captures ALL Slack events, normalizes them into a unified
schema, and stores them in MongoDB. Runs as fire-and-forget alongside
existing agent handlers — never blocks or interferes with them.
"""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from observability.db import get_db

logger = logging.getLogger(__name__)

# Slack event types we know how to normalize
# Message subtypes → normalized event_type
_MESSAGE_SUBTYPE_MAP = {
    None: ("message", None),
    "file_share": ("file_share", None),
    "channel_topic": ("topic_change", None),
    "channel_purpose": ("purpose_change", None),
    "channel_join": ("member_join", None),
    "channel_leave": ("member_leave", None),
    "bot_message": ("message", "bot_message"),
    "message_changed": ("message_edit", None),
    "message_deleted": ("message_delete", None),
    "thread_broadcast": ("message", "thread_broadcast"),
}

# Top-level event types (non-message)
_EVENT_TYPE_MAP = {
    "app_mention": "mention",
    "reaction_added": "reaction",
    "reaction_removed": "reaction_removed",
    "member_joined_channel": "member_join",
    "member_left_channel": "member_leave",
    "pin_added": "pin",
    "pin_removed": "pin_removed",
    "file_shared": "file_share",
}


def _content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slack_ts_to_datetime(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def _build_source_event_id(event: dict, event_type_raw: str) -> str:
    """Build a composite key unique to this Slack event."""
    # For messages: channel + ts is unique
    # For reactions: channel + ts + user + reaction (item.ts is the target message)
    if event_type_raw in ("reaction_added", "reaction_removed"):
        item = event.get("item", {})
        return f"{item.get('channel', '')}:{item.get('ts', '')}:{event.get('user', '')}:{event.get('reaction', '')}:{event_type_raw}"
    if event_type_raw in ("member_joined_channel", "member_left_channel"):
        return f"{event.get('channel', '')}:{event.get('user', '')}:{event.get('event_ts', '')}:{event_type_raw}"
    if event_type_raw in ("pin_added", "pin_removed"):
        item = event.get("item", {})
        return f"{event.get('channel_id', item.get('channel', ''))}:{item.get('created', '')}:{event.get('user', '')}:{event_type_raw}"
    # Messages and mentions
    channel = event.get("channel", "")
    ts = event.get("ts", event.get("event_ts", ""))
    return f"{channel}:{ts}"


def normalize_slack_event(body: dict) -> dict | None:
    """
    Normalize a raw Slack event payload into the unified event schema.
    Returns None for events we can't meaningfully normalize.
    """
    event_type_raw = body.get("type", "")
    event = body.get("event", {})

    if not event:
        return None

    slack_event_type = event.get("type", "")

    # Skip internal Slack system events
    if slack_event_type in ("hello", "goodbye"):
        return None

    # Determine normalized event_type and subtype
    normalized_type = None
    normalized_subtype = None

    if slack_event_type == "message":
        subtype = event.get("subtype")
        if subtype in _MESSAGE_SUBTYPE_MAP:
            normalized_type, normalized_subtype = _MESSAGE_SUBTYPE_MAP[subtype]
        else:
            # Unknown subtype — still capture as message
            normalized_type = "message"
            normalized_subtype = subtype
        # Thread replies
        if event.get("thread_ts") and event.get("thread_ts") != event.get("ts"):
            normalized_subtype = normalized_subtype or "thread_reply"
    elif slack_event_type in _EVENT_TYPE_MAP:
        normalized_type = _EVENT_TYPE_MAP[slack_event_type]
    else:
        # Unknown event type — still capture it
        normalized_type = slack_event_type

    # Extract fields depending on event type
    channel_id = event.get("channel", "")
    user_id = event.get("user")
    text = event.get("text")
    ts = event.get("ts") or event.get("event_ts")
    thread_ts = event.get("thread_ts")
    is_bot = bool(event.get("bot_id")) or event.get("subtype") == "bot_message"

    # Reaction events have a different structure
    reaction = None
    reaction_target_ts = None
    if slack_event_type in ("reaction_added", "reaction_removed"):
        reaction = event.get("reaction")
        item = event.get("item", {})
        channel_id = item.get("channel", channel_id)
        reaction_target_ts = item.get("ts")
        ts = event.get("event_ts", ts)

    # Member join/leave events
    if slack_event_type in ("member_joined_channel", "member_left_channel"):
        ts = event.get("event_ts", ts)

    # Pin events
    if slack_event_type in ("pin_added", "pin_removed"):
        item = event.get("item", {})
        channel_id = event.get("channel_id", item.get("channel", channel_id))
        text = item.get("message", {}).get("text") if item.get("type") == "message" else None
        ts = event.get("event_ts", ts)

    # File metadata
    files = []
    for f in event.get("files", []):
        files.append({
            "file_id": f.get("id", ""),
            "name": f.get("name", ""),
            "mimetype": f.get("mimetype", ""),
            "size": f.get("size", 0),
        })

    # For message_changed, extract the actual message
    if event.get("subtype") == "message_changed":
        msg = event.get("message", {})
        text = msg.get("text", text)
        user_id = msg.get("user", user_id)
        is_bot = bool(msg.get("bot_id")) or is_bot

    # For message_deleted, capture the deleted text if available
    if event.get("subtype") == "message_deleted":
        prev = event.get("previous_message", {})
        text = prev.get("text")
        user_id = prev.get("user", user_id)

    source_event_id = _build_source_event_id(event, slack_event_type)
    timestamp = _slack_ts_to_datetime(ts)

    return {
        "event_id": str(uuid.uuid4()),
        "source": "slack",
        "source_event_id": source_event_id,
        "event_type": normalized_type,
        "event_subtype": normalized_subtype,
        "timestamp": timestamp or datetime.now(timezone.utc),
        "ingested_at": datetime.now(timezone.utc),
        "channel_id": channel_id,
        "channel_name": None,  # Resolved lazily later
        "thread_ts": thread_ts,
        "thread_refs": {
            k: [v] for k, v in {
                "slack_thread_ts": thread_ts,
                "slack_channel_id": channel_id,
            }.items() if v
        },
        "user_id": user_id,
        "user_name": None,  # Resolved lazily later
        "is_bot": is_bot,
        "text": text,
        "content_hash": _content_hash(text),
        "files": files,
        "reaction": reaction,
        "reaction_target_ts": reaction_target_ts,
        "entities": [],
        "embedding": None,
        "raw_event": event,
        "processed": False,
        "processing_version": 1,
    }


async def _store_event(normalized: dict) -> None:
    """Upsert a normalized event into the changestreams collection."""
    db = get_db()
    if db is None:
        return
    try:
        await db.changestreams.update_one(
            {"source_event_id": normalized["source_event_id"]},
            {"$setOnInsert": normalized},
            upsert=True,
        )
    except Exception:
        logger.exception("[INGESTION] Failed to store event: %s", normalized.get("source_event_id"))


async def _ingest(body: dict) -> None:
    """Normalize and store a single Slack event."""
    normalized = normalize_slack_event(body)
    if normalized is None:
        return
    await _store_event(normalized)
    logger.debug("[INGESTION] Stored %s event: %s", normalized["event_type"], normalized["source_event_id"])


def register_ingestion_middleware(app) -> None:
    """Register global middleware that ingests every Slack event."""

    async def ingestion_middleware(body, next):
        # Fire-and-forget: normalize + store in background
        event_type = body.get("type", "")
        if event_type == "event_callback" and body.get("event"):
            asyncio.create_task(_ingest(body))
        # Always continue to next handler
        await next()

    app.middleware(ingestion_middleware)
    logger.info("[INGESTION] Event ingestion middleware registered")
