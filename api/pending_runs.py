"""Pending runs: hold new work that arrives while a deploy drains.

During drain (api/drain.py) the old server must not start runs the restart
would kill. Before this module those requests were refused and the user's
message was lost. Now every entry point saves the run to the
``pending_runs`` collection instead, and the next server to boot (or this
one, if the drain is cancelled) starts it. Same idea as scheduled flows'
``deferred_run_at`` (see scheduler.engine._run_deferred_flows).

Entry kinds:
  - ``dashboard``: a chat message or quick-added task. The user message is
    already recorded on the conversation (status "queued"), so a reload shows
    it. Several entries for one conversation run as one merged turn, the same
    way the dashboard merges messages sent while the agent is busy.
  - ``slack``: a Slack message. Replayed through the normal Slack handler, one
    at a time per thread, so reactions and replies work as usual.
  - ``headless``: a run that waited behind a busy conversation (webhooks,
    Linear, Telegram, recovery). The result is recorded on the conversation.

Each entry is claimed with an atomic update, so it never runs twice.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from api.drain import is_draining
from observability.db import get_db

logger = logging.getLogger(__name__)

COLLECTION = "pending_runs"
STATUS_QUEUED = "queued"
STATUS_CLAIMED = "claimed"
STATUS_FAILED = "failed"

# Conversation status while its message waits for the new server.
CONVERSATION_QUEUED = "queued"

# What callers tell the user when their run is queued.
QUEUED_MESSAGE = "Queued. Loma is updating and will start this as soon as it's back."

# Entries older than this are given up on (a deploy never takes this long).
MAX_AGE = timedelta(hours=24)
# Mongo caps documents at 16MB; keep attachments well under it.
MAX_FILES_BYTES = 8 * 1024 * 1024
DISPATCH_INTERVAL_SECONDS = 30

_dispatch_lock = asyncio.Lock()
_slack_client = None
_loop_task: asyncio.Task | None = None


def files_fit(files) -> bool:
    """True when attachments are small enough to store on a pending entry."""
    if not files:
        return True
    try:
        return len(json.dumps(files, default=str)) <= MAX_FILES_BYTES
    except (TypeError, ValueError):
        return False


async def enqueue(db, kind: str, **fields) -> dict:
    """Save a run to start after the deploy. Returns the stored entry."""
    entry = {
        "run_id": uuid.uuid4().hex,
        "kind": kind,
        "status": STATUS_QUEUED,
        "created_at": datetime.now(timezone.utc),
        **fields,
    }
    await db[COLLECTION].insert_one(entry)
    logger.info("[PENDING] Queued %s run %s (conversation %s)",
                kind, entry["run_id"], fields.get("conversation_id"))
    return entry


async def record_queued_message(db, conversation_id: str, message: str, *,
                                sender: str | None, new_conversation: dict | None = None) -> None:
    """Show a queued dashboard message on its conversation right away.

    ``new_conversation`` is the metadata for a conversation that doesn't exist
    yet (first message of a new chat); otherwise the message is appended to
    the existing one. Status "queued" tells the dashboard to keep polling.
    """
    now = datetime.now(timezone.utc)
    user_message = {"role": "user", "content": message, "timestamp": now}
    if sender:
        user_message["sender"] = sender
    if new_conversation is not None:
        await db.conversations.insert_one({
            "conversation_id": conversation_id,
            "source": new_conversation.get("source", "dashboard"),
            "started_at": now,
            "finished_at": None,
            "duration_ms": None,
            "status": CONVERSATION_QUEUED,
            "metadata": {k: v for k, v in new_conversation.items()
                         if k not in ("source", "prompt", "model")},
            "prompt": message,
            "model": new_conversation.get("model", ""),
            "total_turns": 0,
            "final_response": "",
            "messages": [user_message],
            "confidence": None,
            "cost": None,
            "savings": None,
            "claude_account": None,
            "error": None,
        })
        return
    await db.conversations.update_one(
        {"conversation_id": conversation_id},
        {"$set": {"status": CONVERSATION_QUEUED}, "$push": {"messages": user_message}},
    )


async def cancel_conversation(db, conversation_id: str) -> int:
    """Drop a conversation's queued runs (user pressed Stop). Returns how many."""
    result = await db[COLLECTION].delete_many(
        {"conversation_id": conversation_id, "status": STATUS_QUEUED},
    )
    await db.conversations.update_one(
        {"conversation_id": conversation_id, "status": CONVERSATION_QUEUED},
        {"$set": {"status": "interrupted", "error": "Stopped by user",
                  "finished_at": datetime.now(timezone.utc)}},
    )
    return result.deleted_count


# ── dispatch ──────────────────────────────────────────────────────────────


def _group(entries: list[dict]) -> list[list[dict]]:
    """Group entries that must run in order, oldest group first."""
    groups: dict[tuple, list[dict]] = {}
    for entry in entries:
        if entry["kind"] == "slack":
            key = ("slack", entry.get("channel"), entry.get("thread_ts"))
        else:
            key = (entry["kind"], entry.get("conversation_id"), entry.get("user_email"))
        groups.setdefault(key, []).append(entry)
    return list(groups.values())


async def _claim(db, entry: dict) -> bool:
    claimed = await db[COLLECTION].find_one_and_update(
        {"run_id": entry["run_id"], "status": STATUS_QUEUED},
        {"$set": {"status": STATUS_CLAIMED, "claimed_at": datetime.now(timezone.utc)}},
    )
    return claimed is not None


async def _expire_old(db) -> None:
    cutoff = datetime.now(timezone.utc) - MAX_AGE
    result = await db[COLLECTION].update_many(
        {"status": STATUS_QUEUED, "created_at": {"$lt": cutoff}},
        {"$set": {"status": STATUS_FAILED, "error": "Expired before a server picked it up"}},
    )
    if getattr(result, "modified_count", 0):
        logger.warning("[PENDING] Expired %d queued run(s)", result.modified_count)


async def dispatch_pending(db=None, slack_client=None) -> int:
    """Start every queued run. Returns how many groups were started.

    No-op while draining: the server that is about to restart must not pick
    up work. Slack entries wait until a Slack client is available.
    """
    if is_draining():
        return 0
    db = db if db is not None else get_db()
    if db is None:
        return 0
    slack_client = slack_client or _slack_client
    async with _dispatch_lock:
        await _expire_old(db)
        entries = await db[COLLECTION].find(
            {"status": STATUS_QUEUED},
        ).sort("created_at", 1).to_list(None)
        started = 0
        for group in _group(entries):
            if group[0]["kind"] == "slack" and slack_client is None:
                continue
            claimed = [e for e in group if await _claim(db, e)]
            if not claimed:
                continue
            asyncio.create_task(_run_group(db, claimed, slack_client))
            started += 1
        if started:
            logger.info("[PENDING] Started %d queued run group(s)", started)
        return started


async def _run_group(db, entries: list[dict], slack_client) -> None:
    run_ids = [e["run_id"] for e in entries]
    try:
        if entries[0]["kind"] == "slack":
            # One at a time so replies land in order in the thread.
            for entry in entries:
                await _run_slack(entry, slack_client)
        else:
            await _run_agent(db, _merge(entries))
    except Exception as e:
        logger.exception("[PENDING] Queued run failed")
        await db[COLLECTION].update_many(
            {"run_id": {"$in": run_ids}},
            {"$set": {"status": STATUS_FAILED, "error": str(e)[:500]}},
        )
        return
    await db[COLLECTION].delete_many({"run_id": {"$in": run_ids}})


def _merge(entries: list[dict]) -> dict:
    """Several queued messages for one conversation become one turn."""
    if len(entries) == 1:
        return entries[0]
    first, last = entries[0], entries[-1]
    return {
        **last,
        "prompt": "\n\n".join(e["prompt"] for e in entries if e.get("prompt")),
        "files": [f for e in entries for f in (e.get("files") or [])],
        # History as it stood before the first queued message.
        "conversation_context": first.get("conversation_context", ""),
    }


async def _run_agent(db, entry: dict) -> None:
    """Run a dashboard or headless entry; the observer records the result."""
    from agent.client import stream_agent
    from observability.observer import ConversationObserver

    conversation_id = entry["conversation_id"]
    metadata = {
        "source": entry.get("source") or "dashboard",
        "prompt": entry.get("prompt", ""),
        "model": entry.get("model") or "",
        "user_name": entry.get("user_email"),
        **(entry.get("metadata") or {}),
    }
    observer = ConversationObserver(db, metadata=metadata, conversation_id=conversation_id)
    # The user message was recorded when it was queued.
    await observer.resume(record_prompt=False)
    logger.info("[PENDING] Running queued %s run for %s", entry["kind"], conversation_id)
    async for _ in stream_agent(
        prompt=entry.get("prompt", ""),
        conversation_context=entry.get("conversation_context", ""),
        files=entry.get("files") or None,
        observer=observer,
        include_steps=True,
        source=metadata["source"],
        user_email=entry.get("user_email"),
        selected_model=entry.get("model") or None,
        tool_config=entry.get("tool_config"),
    ):
        pass  # observer records; the dashboard polls the conversation
    if metadata["source"] == "dashboard":
        from api.dashboard_ingestion import ingest_dashboard_chat
        asyncio.create_task(ingest_dashboard_chat(
            conversation_id, entry.get("prompt", ""), entry.get("user_email") or "",
        ))


async def _run_slack(entry: dict, slack_client) -> None:
    from slack_app.handlers import _handle_agent_request

    logger.info("[PENDING] Running queued Slack run in %s/%s", entry.get("channel"), entry.get("thread_ts"))
    await _handle_agent_request(
        slack_client, entry["channel"], entry["thread_ts"], entry["event_ts"],
        entry.get("prompt", ""), entry.get("context", ""), entry.get("files") or [],
        entry.get("source", "slack_mention"), entry.get("user_id"),
        flow_id=entry.get("flow_id"), message_has_files=entry.get("message_has_files"),
    )


# ── background loop ───────────────────────────────────────────────────────


def schedule_dispatch() -> None:
    """Kick off a dispatch soon (e.g. right after a drain is cancelled)."""
    try:
        asyncio.get_running_loop().create_task(dispatch_pending())
    except RuntimeError:
        pass  # No running loop; the periodic loop picks it up.


async def _dispatch_loop() -> None:
    while True:
        try:
            await dispatch_pending()
        except Exception:
            logger.exception("[PENDING] Dispatch failed")
        await asyncio.sleep(DISPATCH_INTERVAL_SECONDS)


def start_dispatcher(slack_client=None) -> None:
    """Start queued runs now and keep checking every 30s.

    Called once the server is up (and Slack connected, when enabled). The
    periodic pass also covers a drain cleared without the DELETE route.
    """
    global _slack_client, _loop_task
    _slack_client = slack_client
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.get_running_loop().create_task(_dispatch_loop())
