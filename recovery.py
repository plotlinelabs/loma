"""Recovery module for interrupted conversations.

On startup, detects conversations that were interrupted by a server restart
using a heartbeat-based approach: any conversation with status='running' and
a stale last_heartbeat is considered interrupted.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from agent.client import stream_agent
from observability.db import get_db
from observability.observer import ConversationObserver, HEARTBEAT_INTERVAL_SECONDS
from recovery_handlers import post_recovery_response

logger = logging.getLogger(__name__)

# How far back to look for interrupted conversations (in minutes).
# Conversations older than this are marked as 'interrupted' and not resumed.
RECOVERY_WINDOW_MINUTES = int(os.environ.get("RECOVERY_WINDOW_MINUTES", "10"))

# A heartbeat is considered stale after 2x the interval (missed at least one beat).
HEARTBEAT_STALE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 2


def start_recovery_loop():
    """Start a background task that periodically checks for interrupted conversations.

    Runs every HEARTBEAT_STALE_SECONDS so that even if the server restarts
    immediately (before heartbeats go stale), the next sweep will catch them.
    """
    asyncio.create_task(_recovery_loop())


async def _recovery_loop():
    """Periodically run recovery checks."""
    while True:
        try:
            await _run_recovery_check()
        except Exception:
            logger.exception("[RECOVERY] Unexpected error in recovery loop")
        await asyncio.sleep(HEARTBEAT_STALE_SECONDS)


async def _run_recovery_check():
    """Single recovery sweep: find stale conversations, resume or mark interrupted."""
    db = get_db()
    if db is None:
        return

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
    recovery_cutoff = now - timedelta(minutes=RECOVERY_WINDOW_MINUTES)

    # Query: status=running, heartbeat is stale (not actively running)
    stale_query = {
        "status": "running",
        "$or": [
            {"last_heartbeat": {"$lt": stale_cutoff}},
            {"last_heartbeat": None},  # legacy docs without heartbeat
        ],
    }

    # Find recent ones (within recovery window) to resume
    try:
        resumable_cursor = db.conversations.find({
            **stale_query,
            "started_at": {"$gte": recovery_cutoff},
        })
        resumable = await resumable_cursor.to_list(length=100)
    except Exception as e:
        logger.exception("[RECOVERY] Failed to query interrupted conversations: %s", e)
        return

    if resumable:
        logger.info(
            "[RECOVERY] Found %d interrupted conversation(s) to resume (window=%dm)",
            len(resumable), RECOVERY_WINDOW_MINUTES,
        )
        for convo in resumable:
            asyncio.create_task(_resume_conversation(convo))

    # Mark stale conversations older than recovery window as 'interrupted'
    try:
        result = await db.conversations.update_many(
            {**stale_query, "started_at": {"$lt": recovery_cutoff}},
            {"$set": {
                "status": "interrupted",
                "finished_at": now,
                "error": "Server restarted — conversation was not within recovery window",
            }},
        )
        if result.modified_count > 0:
            logger.info(
                "[RECOVERY] Marked %d stale conversation(s) as 'interrupted'",
                result.modified_count,
            )
    except Exception as e:
        logger.exception("[RECOVERY] Failed to mark stale conversations: %s", e)


async def _resume_conversation(convo: dict):
    """Resume a single interrupted conversation.

    Reconstructs the conversation context from stored messages, re-runs the
    agent, and delivers the response to the original channel/issue.
    """
    conversation_id = convo.get("conversation_id", "")
    source = convo.get("source", "unknown")
    prompt = convo.get("prompt", "")
    messages = convo.get("messages", [])
    metadata = convo.get("metadata", {})

    logger.info(
        "[RECOVERY] Resuming conversation %s (source=%s, prompt=%.100s)",
        conversation_id, source, prompt,
    )

    if not prompt:
        logger.warning(
            "[RECOVERY] Conversation %s has no prompt — marking as interrupted",
            conversation_id,
        )
        db = get_db()
        if db is not None:
            await db.conversations.update_one(
                {"conversation_id": conversation_id},
                {"$set": {
                    "status": "interrupted",
                    "finished_at": datetime.now(timezone.utc),
                    "error": "No prompt available for recovery",
                }},
            )
        return

    # Reconstruct conversation context from stored messages.
    # All messages except the last user prompt form the context.
    conversation_context = ""
    if len(messages) > 1:
        context_parts = []
        for msg in messages[:-1]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "user":
                context_parts.append(f"**User**: {content}")
            elif role == "assistant":
                context_parts.append(f"**Assistant**: {content}")
        conversation_context = "\n".join(context_parts)

    # Create observer for the resumed conversation
    db = get_db()
    observer = None
    if db is not None:
        observer = ConversationObserver(
            db,
            metadata={
                "source": source,
                "prompt": prompt,
                "model": os.environ.get("CLAUDE_MODEL", ""),
                **metadata,
            },
            conversation_id=conversation_id,
        )
        await observer.resume()

    # Map stored source to the agent's expected source parameter
    agent_source = "dashboard" if source == "dashboard" else "slack"

    try:
        # Run the agent
        last_text = ""
        async for text in stream_agent(
            prompt=prompt,
            conversation_context=conversation_context,
            observer=observer,
            source=agent_source,
        ):
            last_text = text
            logger.info(
                "[RECOVERY] Agent output for %s: %.200s",
                conversation_id, text,
            )

        if last_text:
            # Deliver the response to the original channel
            await post_recovery_response(source, metadata, last_text)
            logger.info(
                "[RECOVERY] Successfully resumed and delivered response for %s",
                conversation_id,
            )
        else:
            logger.warning(
                "[RECOVERY] No response generated for conversation %s",
                conversation_id,
            )

    except Exception:
        logger.exception(
            "[RECOVERY] Failed to resume conversation %s", conversation_id
        )
