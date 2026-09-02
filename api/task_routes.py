"""Tasks board routes — a kanban layer over conversations.

A task IS a conversation (1:1) plus board state stored on the conversation doc:
  - task_status: "todo" (staged: an unstarted draft OR a started chat parked
    to recontinue later) | "active" (started) | "done" (user-closed)
  - task_lane: staging lane id (meaningful only while task_status == "todo")
  - task_rank: float sort key within staged lanes

Board columns are derived — "working" vs "needs input" comes from the live
conversation status, never stored:
  todo               -> the task's staging lane
  active + running   -> working
  active + not-running -> needs input
  done               -> done

Per-user board config (staging lanes + personal prompt) lives on the users doc
under `task_board`; tasks reference lanes by id so renames are config-only.
"""

import asyncio
import copy
import logging
import os
import re
import uuid
from datetime import date, datetime, timezone

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import get_system_role, get_user_email

logger = logging.getLogger(__name__)


async def _run_task_headless(db, conversation_id: str, prompt: str,
                             model: str, files: list, owner: str):
    """Run a task's first agent turn in the background — no client stream.

    Powers quick-add: the task fires immediately and keeps running even if
    the user navigates away or locks their phone. Mirrors handle_chat's
    setup (observer resume, board-prompt injection, files, model); the
    observer records everything, so opening the chat later shows the run.
    """
    try:
        from agent.client import stream_agent
        from observability.observer import ConversationObserver

        # Same per-task context block handle_chat injects for board tasks.
        owner_doc = await db.users.find_one({"email": owner}, {"task_board": 1})
        board_prompt = ((owner_doc or {}).get("task_board") or {}).get("prompt", "").strip()
        conversation_context = (
            f"## User's role & working context (apply to this task)\n{board_prompt}"
            if board_prompt else ""
        )

        observer = ConversationObserver(
            db,
            metadata={
                "source": "dashboard",
                "prompt": prompt,
                "model": model or os.environ.get("AGENT_DEFAULT_MODEL", ""),
                "user_name": owner,
            },
            conversation_id=conversation_id,
        )
        await observer.resume()

        async for _ in stream_agent(
            prompt=prompt,
            conversation_context=conversation_context,
            files=files or None,
            observer=observer,
            include_steps=True,
            source="dashboard",
            user_email=owner,
            selected_model=model or None,
        ):
            pass  # observer records; nobody is watching the stream
    except Exception as e:
        logger.warning("Headless task run failed for %s: %s", conversation_id, e)


async def _auto_title_task(db, conversation_id: str, prompt: str):
    """Generate a short LLM title for a quick-added draft (fire-and-forget).

    Leaves title_edited unset so finish-time enrichment can still improve the
    title once the agent has actually run. Skips the write if the user has
    titled the task in the meantime.
    """
    try:
        from api.routes import _generate_title_llm
        title = await _generate_title_llm(prompt)
        if title and title != "Untitled conversation":
            await db.conversations.update_one(
                {"conversation_id": conversation_id, "title": None},
                {"$set": {"title": title}},
            )
    except Exception as e:
        logger.warning("Task auto-title failed for %s: %s", conversation_id, e)

# Statuses where the agent is no longer running — the user's turn.
NEEDS_INPUT_STATUSES = ("completed", "error", "interrupted")

DEFAULT_BOARD = {
    "prompt": "",
    "lanes": [{"id": "todo", "name": "Todo", "order": 0}],
    "tags": [],
}

MAX_LANE_NAME_LEN = 40
MAX_LANES = 10
MAX_BOARD_PROMPT_LEN = 10000
MAX_TAGS = 50
MAX_TAGS_PER_TASK = 10
TASK_PRIORITIES = ("low", "medium", "high", "urgent")
# Deadlines are date-only, stored as "YYYY-MM-DD" strings (timezone-agnostic).
DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TAG_NAME_LEN = 30
TAG_COLORS = ("slate", "red", "orange", "amber", "green", "teal", "blue", "violet", "pink")
# Attachments staged with a draft (base64 in the doc until the task starts).
# Mongo caps documents at 16MB — keep well under it.
MAX_DRAFT_FILES = 8
MAX_DRAFT_FILES_BYTES = 8 * 1024 * 1024


def _serialize(doc):
    """Make a MongoDB document JSON-serializable."""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [_serialize(d) for d in doc]
    if isinstance(doc, dict):
        result = {}
        for k, v in doc.items():
            if k == "_id":
                result[k] = str(v)
            elif isinstance(v, datetime):
                result[k] = v.isoformat()
            elif isinstance(v, (dict, list)):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


def get_board_config(user_doc: dict | None) -> dict:
    """Return the user's board config, materializing the default when absent."""
    board = (user_doc or {}).get("task_board") or {}
    lanes = board.get("lanes") or []
    if not lanes:
        lanes = [dict(lane) for lane in DEFAULT_BOARD["lanes"]]
    lanes = sorted(lanes, key=lambda lane: lane.get("order", 0))
    return {"prompt": board.get("prompt", ""), "lanes": lanes, "tags": board.get("tags") or []}


async def _get_board_config_for(db, user_email: str) -> dict:
    user_doc = await db.users.find_one({"email": user_email}, {"task_board": 1})
    return get_board_config(user_doc)


def derive_column(task: dict, lane_ids: list[str]) -> str:
    """Derive the board column for a task. Unknown lanes fold into the first."""
    task_status = task.get("task_status")
    if task_status == "todo":
        lane = task.get("task_lane") or (lane_ids[0] if lane_ids else "todo")
        return lane if lane in lane_ids else (lane_ids[0] if lane_ids else "todo")
    if task_status == "done":
        return "done"
    # active — status None means a quick-added task whose headless run is
    # spinning up (observer.resume() sets "running" moments later).
    if task.get("status") in (None, "running"):
        return "working"
    return "needs_input"


def _effective_rank(task: dict, column: str, lane_ids: list[str]) -> float:
    """Manual sort key for any column: the stored rank, or a recency fallback
    (negated epoch → newest first when ascending). Emitting this for every
    card means a drag-reorder can always slot an explicit rank between two
    neighbors, in derived columns as well as staging lanes."""
    rank = task.get("task_rank")
    if rank is not None:
        return rank
    if column in lane_ids:
        ts = task.get("task_staged_at") or task.get("task_created_at")
    elif column == "working":
        ts = task.get("started_at")
    elif column == "done":
        ts = task.get("task_done_at")
    else:  # needs_input
        ts = task.get("finished_at")
    ts = ts or task.get("task_created_at") or task.get("started_at")
    return -ts.timestamp() if isinstance(ts, datetime) else 0.0


def _task_view(task: dict, lane_ids: list[str]) -> dict:
    """Shape a conversation doc into the board card payload."""
    prompt = task.get("prompt") or ""
    column = derive_column(task, lane_ids)
    return {
        "conversation_id": task.get("conversation_id"),
        "title": task.get("title") or None,
        "prompt": prompt[:200],
        "model": task.get("model") or None,
        "status": task.get("status"),
        "task_status": task.get("task_status"),
        "task_lane": task.get("task_lane"),
        "task_rank": _effective_rank(task, column, lane_ids),
        "column": column,
        "total_turns": task.get("total_turns", 0),
        "started_at": _serialize(task.get("started_at")),
        "finished_at": _serialize(task.get("finished_at")),
        "task_created_at": _serialize(task.get("task_created_at")),
        "task_staged_at": _serialize(task.get("task_staged_at")),
        "task_started_at": _serialize(task.get("task_started_at")),
        "task_done_at": _serialize(task.get("task_done_at")),
        "task_tag_ids": task.get("task_tag_ids") or [],
        "task_priority": task.get("task_priority") or None,
        "task_deadline": task.get("task_deadline") or None,
        "forked_from_conversation_id": task.get("forked_from_conversation_id") or None,
    }


_TASK_PROJECTION = {
    "conversation_id": 1, "title": 1, "prompt": 1, "model": 1, "status": 1,
    "task_status": 1, "task_lane": 1, "task_rank": 1,
    "total_turns": 1, "started_at": 1, "finished_at": 1,
    "task_created_at": 1, "task_staged_at": 1, "task_started_at": 1,
    "task_done_at": 1,
    "task_tag_ids": 1,
    "task_priority": 1,
    "task_deadline": 1,
    "forked_from_conversation_id": 1,
}


async def handle_create_task(request: web.Request) -> web.Response:
    """POST /api/tasks — create a staged (draft) task."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    # Empty staged drafts are fine (the side drawer creates one on click and
    # the user fills it in) — title stays None so finish-time enrichment can
    # name the task from the first message. start=true still requires details.
    title = (body.get("title") or "").strip() or None

    model = (body.get("model") or "").strip()

    files = body.get("files") or []
    if files:
        if not isinstance(files, list) or len(files) > MAX_DRAFT_FILES:
            return web.json_response(
                {"error": f"At most {MAX_DRAFT_FILES} attachments"}, status=400)
        total = 0
        for f in files:
            if not isinstance(f, dict) or not f.get("name") or "data" not in f:
                return web.json_response({"error": "Invalid attachment"}, status=400)
            total += len(f.get("data") or "")
        if total > MAX_DRAFT_FILES_BYTES:
            return web.json_response(
                {"error": "Attachments too large (max 8MB total)"}, status=400)

    board = await _get_board_config_for(db, user_email)
    lane_ids = [lane["id"] for lane in board["lanes"]]
    lane = body.get("lane") or lane_ids[0]
    if lane not in lane_ids:
        return web.json_response({"error": "Unknown lane"}, status=400)

    # start=true (quick-add): the task fires immediately in the background
    # instead of waiting as a staged draft. Starting needs actual details.
    start = bool(body.get("start"))
    if start and not prompt:
        return web.json_response({"error": "Details are required to start"}, status=400)

    now = datetime.now(timezone.utc)
    # Draft doc mirrors observer.start()'s shape, but the run hasn't begun:
    # status/started_at stay None and messages stays [] — observer.resume()
    # $pushes the prompt as the first user message when the task starts, so
    # pre-filling messages here would duplicate it.
    doc = {
        "conversation_id": str(uuid.uuid4()),
        "source": "dashboard",
        "started_at": now if start else None,
        "finished_at": None,
        "duration_ms": None,
        "status": None,
        "metadata": {"user_name": user_email},
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
        # Guard user-provided titles against finish-time LLM enrichment.
        "title_edited": bool(title),
        "task_status": "active" if start else "todo",
        "task_lane": lane,
        # Attachments staged with the draft — sent with the first message on
        # start (immediately for quick-add; handle_chat clears them when a
        # staged draft flips to active).
        **({"draft_files": files} if files and not start else {}),
        # Newest first when sorted ascending; reorder uses neighbor midpoints.
        "task_rank": -now.timestamp(),
        "task_created_at": now,
        "task_staged_at": now,
        "task_started_at": now if start else None,
        "task_done_at": None,
        "task_tag_ids": [],
        "task_priority": None,
        "task_deadline": None,
    }
    await db.conversations.insert_one(doc)

    # Quick-added tasks (no explicit title) get an LLM title from the prompt.
    # Empty drafts skip this — enrichment titles them after the first run.
    if not title and prompt:
        asyncio.create_task(_auto_title_task(db, doc["conversation_id"], prompt))

    if start:
        asyncio.create_task(_run_task_headless(
            db, doc["conversation_id"], prompt, model, files, user_email,
        ))

    return web.json_response({"task": _task_view(doc, lane_ids)}, status=201)


async def handle_fork_task(request: web.Request) -> web.Response:
    """POST /api/tasks/{conversation_id}/fork — copy a task as an independent draft."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    cid = request.match_info["conversation_id"]
    source = await db.conversations.find_one({
        "conversation_id": cid,
        "deleted": {"$ne": True},
    })
    if not source:
        return web.json_response({"error": "Not found"}, status=404)

    from api.routes import _check_conversation_access
    if not _check_conversation_access(source, user_email, get_system_role(request)):
        return web.json_response({"error": "Not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    board = await _get_board_config_for(db, user_email)
    lane_ids = [lane["id"] for lane in board["lanes"]]
    source_owner = (source.get("metadata") or {}).get("user_name")
    default_lane = source.get("task_lane") if source_owner == user_email else None
    lane = body.get("lane") or (default_lane if default_lane in lane_ids else lane_ids[0])
    if lane not in lane_ids:
        return web.json_response({"error": "Unknown lane"}, status=400)

    source_title = source.get("title") or None
    title = (body.get("title") or "").strip() if "title" in body else (
        f"{source_title} (fork)" if source_title else None
    )
    if "title" in body and not title:
        return web.json_response({"error": "title must not be empty"}, status=400)

    now = datetime.now(timezone.utc)
    doc = {
        "conversation_id": str(uuid.uuid4()),
        "source": source.get("source") or "dashboard",
        "started_at": source.get("started_at"),
        "finished_at": source.get("finished_at"),
        "duration_ms": None,
        "status": "interrupted" if source.get("status") == "running" else source.get("status"),
        "metadata": {**copy.deepcopy(source.get("metadata") or {}), "user_name": user_email},
        "prompt": source.get("prompt") or "",
        "model": source.get("model") or "",
        "total_turns": source.get("total_turns", 0),
        "final_response": source.get("final_response") or "",
        "messages": copy.deepcopy(source.get("messages") or []),
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
        "task_tag_ids": copy.deepcopy(source.get("task_tag_ids") or [])
        if source_owner == user_email else [],
        "task_priority": source.get("task_priority"),
        "task_deadline": source.get("task_deadline"),
        "forked_from_conversation_id": cid,
        "forked_at": now,
        **({"draft_files": copy.deepcopy(source["draft_files"])}
           if source.get("draft_files") else {}),
    }
    await db.conversations.insert_one(doc)
    return web.json_response({"task": _task_view(doc, lane_ids)}, status=201)


async def handle_list_tasks(request: web.Request) -> web.Response:
    """GET /api/tasks — the caller's board: lanes, tasks with derived columns, counts."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    board = await _get_board_config_for(db, user_email)
    lane_ids = [lane["id"] for lane in board["lanes"]]

    query = {
        "metadata.user_name": user_email,
        "task_status": {"$in": ["todo", "active", "done"]},
        "deleted": {"$ne": True},
    }
    search = request.query.get("q", "").strip()
    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"title": pattern},
            {"prompt": pattern},
            {"messages.content": pattern},
            {"final_response": pattern},
        ]

    tasks = await db.conversations.find(
        query,
        _TASK_PROJECTION,
    ).to_list(500)

    # Every column orders by effective rank (manual rank, or recency fallback
    # baked in by _task_view) — so all columns are manually reorderable.
    ordered = sorted(
        (_task_view(t, lane_ids) for t in tasks),
        key=lambda view: view["task_rank"],
    )

    counts: dict[str, int] = {lane_id: 0 for lane_id in lane_ids}
    counts.update({"working": 0, "needs_input": 0, "done": 0})
    for view in ordered:
        counts[view["column"]] = counts.get(view["column"], 0) + 1

    return web.json_response({
        "lanes": board["lanes"],
        "tags": board["tags"],
        "tasks": ordered,
        "counts": counts,
    })


async def handle_needs_input_count(request: web.Request) -> web.Response:
    """GET /api/tasks/needs-input-count — cheap poll target for the attention system."""
    db = get_db()
    if db is None:
        return web.json_response({"count": 0})

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    count = await db.conversations.count_documents({
        "metadata.user_name": user_email,
        "task_status": "active",
        "status": {"$in": list(NEEDS_INPUT_STATUSES)},
        "deleted": {"$ne": True},
    })
    return web.json_response({"count": count})


async def handle_update_task(request: web.Request) -> web.Response:
    """PATCH /api/tasks/{conversation_id} — board moves, edits, add/remove.

    Accepts any of: task_status, task_lane, task_rank, prompt, title, model,
    task_tag_ids, task_priority, task_deadline.
    task_status: null removes the conversation from the board.
    """
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    cid = request.match_info["conversation_id"]
    system_role = get_system_role(request)

    conversation = await db.conversations.find_one({
        "conversation_id": cid,
        "deleted": {"$ne": True},
    })
    if not conversation:
        return web.json_response({"error": "Not found"}, status=404)

    from api.routes import _check_conversation_access
    if not _check_conversation_access(conversation, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    now = datetime.now(timezone.utc)
    current = conversation.get("task_status")
    has_run = conversation.get("status") is not None

    # Removing from the board clears all task fields.
    if "task_status" in body and body["task_status"] is None:
        await db.conversations.update_one(
            {"conversation_id": cid},
            {"$unset": {
                "task_status": "", "task_lane": "", "task_rank": "",
                "task_created_at": "", "task_staged_at": "",
                "task_started_at": "", "task_done_at": "",
            }},
        )
        return web.json_response({"task": None})

    updates: dict = {}

    if "task_status" in body:
        target = body["task_status"]
        if target not in ("todo", "active", "done"):
            return web.json_response({"error": "Invalid task_status"}, status=400)
        # Transition matrix (server side of components/tasks/transitions.ts):
        # - "todo" holds both unstarted drafts and *parked* started tasks
        #   (active -> todo shelves a chat in a lane to recontinue later;
        #   sending a message flips it back to active in handle_chat).
        #   done -> todo un-completes a task back into a staging lane.
        # - "active" from todo happens in handle_chat on first send, but is
        #   also allowed here for done->active (reopen) and for converting an
        #   existing chat into a task (current is None).
        if target == "todo" and current not in ("todo", "active", "done"):
            return web.json_response(
                {"error": "Only active or done tasks can move to a staging lane"}, status=400)
        if target == "active" and not has_run and current == "todo":
            return web.json_response(
                {"error": "Start the task by sending its prompt, not by PATCH"}, status=400)
        started = current in ("active", "done") or (current == "todo" and has_run)
        if target == "done" and not started:
            return web.json_response(
                {"error": "Only started tasks can be marked done"}, status=400)
        updates["task_status"] = target
        if target == "done" and current != "done":
            updates["task_done_at"] = now
        if target in ("active", "todo") and current == "done":
            updates["task_done_at"] = None
        if target == "active" and current is None:
            # Converting an existing chat into a board task.
            updates["task_created_at"] = conversation.get("task_created_at") or now
        if target == "todo" and current in ("active", "done"):
            # Parking (or un-completing): land in the requested lane (validated
            # below) or the task's previous lane, defaulting to the first lane.
            updates["task_staged_at"] = now
            if "task_lane" not in body and not conversation.get("task_lane"):
                owner = (conversation.get("metadata") or {}).get("user_name") or user_email
                board = await _get_board_config_for(db, owner)
                updates["task_lane"] = board["lanes"][0]["id"]
            if conversation.get("task_rank") is None and "task_rank" not in body:
                updates["task_rank"] = -now.timestamp()

    if "task_lane" in body:
        if (updates.get("task_status") or current) != "todo":
            return web.json_response({"error": "Only staged tasks have lanes"}, status=400)
        # Lanes belong to the task owner's board, not the caller's.
        owner = (conversation.get("metadata") or {}).get("user_name") or user_email
        board = await _get_board_config_for(db, owner)
        lane_ids = [lane["id"] for lane in board["lanes"]]
        if body["task_lane"] not in lane_ids:
            return web.json_response({"error": "Unknown lane"}, status=400)
        updates["task_lane"] = body["task_lane"]
        updates["task_staged_at"] = now

    if "task_rank" in body:
        try:
            updates["task_rank"] = float(body["task_rank"])
        except (TypeError, ValueError):
            return web.json_response({"error": "task_rank must be a number"}, status=400)

    if "prompt" in body:
        if current != "todo" or has_run:
            return web.json_response({"error": "Only drafts can be edited"}, status=400)
        prompt = (body["prompt"] or "").strip()
        # Details are optional as long as the task keeps a title.
        effective_title = (
            (body.get("title") or "").strip()
            if "title" in body else (conversation.get("title") or "")
        )
        if not prompt and not effective_title:
            return web.json_response(
                {"error": "A title or details are required"}, status=400)
        updates["prompt"] = prompt

    if "model" in body:
        if conversation.get("status") == "running":
            return web.json_response({"error": "The model cannot be changed while a task is running"}, status=400)
        updates["model"] = (body["model"] or "").strip()

    if "task_tag_ids" in body:
        tag_ids = body["task_tag_ids"]
        if not isinstance(tag_ids, list) or len(tag_ids) > MAX_TAGS_PER_TASK or len(tag_ids) != len(set(tag_ids)):
            return web.json_response({"error": f"Use at most {MAX_TAGS_PER_TASK} unique tags"}, status=400)
        owner = (conversation.get("metadata") or {}).get("user_name") or user_email
        board = await _get_board_config_for(db, owner)
        allowed = {tag["id"] for tag in board["tags"]}
        if any(not isinstance(tag_id, str) or tag_id not in allowed for tag_id in tag_ids):
            return web.json_response({"error": "Unknown tag"}, status=400)
        updates["task_tag_ids"] = tag_ids

    if "task_priority" in body:
        priority = body["task_priority"]
        if priority is not None and priority not in TASK_PRIORITIES:
            return web.json_response(
                {"error": "task_priority must be low, medium, high, urgent or null"},
                status=400)
        updates["task_priority"] = priority

    if "task_deadline" in body:
        deadline = body["task_deadline"]
        if deadline is not None:
            if not isinstance(deadline, str) or not DEADLINE_RE.match(deadline):
                return web.json_response(
                    {"error": "task_deadline must be a YYYY-MM-DD date or null"},
                    status=400)
            try:
                date.fromisoformat(deadline)
            except ValueError:
                return web.json_response(
                    {"error": "task_deadline must be a valid calendar date"},
                    status=400)
        updates["task_deadline"] = deadline

    if "title" in body:
        title = (body["title"] or "").strip() or None
        updates["title"] = title
        updates["title_edited"] = bool(title)

    if not updates:
        return web.json_response({"error": "Nothing to update"}, status=400)

    await db.conversations.update_one({"conversation_id": cid}, {"$set": updates})

    updated = await db.conversations.find_one(
        {"conversation_id": cid}, _TASK_PROJECTION)
    owner = (conversation.get("metadata") or {}).get("user_name") or user_email
    board = await _get_board_config_for(db, owner)
    lane_ids = [lane["id"] for lane in board["lanes"]]
    return web.json_response({"task": _task_view(updated, lane_ids)})


async def handle_get_board_settings(request: web.Request) -> web.Response:
    """GET /api/tasks/board-settings — the caller's lanes + personal prompt."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    board = await _get_board_config_for(db, user_email)
    return web.json_response(board)


async def handle_put_board_settings(request: web.Request) -> web.Response:
    """PUT /api/tasks/board-settings — save lanes + personal prompt.

    Deleting a lane migrates its staged tasks to the first remaining lane.
    """
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    prompt = body.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > MAX_BOARD_PROMPT_LEN:
        return web.json_response(
            {"error": f"prompt must be a string of at most {MAX_BOARD_PROMPT_LEN} characters"},
            status=400)

    lanes_in = body.get("lanes")
    if not isinstance(lanes_in, list) or not lanes_in:
        return web.json_response({"error": "At least one lane is required"}, status=400)
    if len(lanes_in) > MAX_LANES:
        return web.json_response({"error": f"At most {MAX_LANES} lanes allowed"}, status=400)

    lanes = []
    seen_ids: set[str] = set()
    for order, lane in enumerate(lanes_in):
        if not isinstance(lane, dict):
            return web.json_response({"error": "Invalid lane"}, status=400)
        name = (lane.get("name") or "").strip()
        if not name or len(name) > MAX_LANE_NAME_LEN:
            return web.json_response(
                {"error": f"Lane names must be 1-{MAX_LANE_NAME_LEN} characters"}, status=400)
        # Preserve existing ids (tasks reference lanes by id); mint for new lanes.
        lane_id = lane.get("id") or str(uuid.uuid4())[:8]
        if lane_id in seen_ids:
            return web.json_response({"error": "Duplicate lane id"}, status=400)
        seen_ids.add(lane_id)
        lanes.append({"id": lane_id, "name": name, "order": order})

    previous = await _get_board_config_for(db, user_email)
    removed_ids = [lane["id"] for lane in previous["lanes"] if lane["id"] not in seen_ids]
    first_lane_id = lanes[0]["id"]

    await db.users.update_one(
        {"email": user_email},
        {"$set": {"task_board.prompt": prompt, "task_board.lanes": lanes}},
    )

    migrated = 0
    if removed_ids:
        result = await db.conversations.update_many(
            {
                "metadata.user_name": user_email,
                "task_status": "todo",
                "task_lane": {"$in": removed_ids},
            },
            {"$set": {"task_lane": first_lane_id}},
        )
        migrated = result.modified_count

    return web.json_response({"prompt": prompt, "lanes": lanes, "migrated": migrated})


async def handle_create_tag(request: web.Request) -> web.Response:
    db = get_db()
    user_email = get_user_email(request)
    if db is None or not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name or len(name) > MAX_TAG_NAME_LEN:
        return web.json_response({"error": f"Tag names must be 1-{MAX_TAG_NAME_LEN} characters"}, status=400)
    board = await _get_board_config_for(db, user_email)
    if len(board["tags"]) >= MAX_TAGS:
        return web.json_response({"error": f"At most {MAX_TAGS} tags allowed"}, status=400)
    if any(tag["name"].casefold() == name.casefold() for tag in board["tags"]):
        return web.json_response({"error": "A tag with that name already exists"}, status=409)
    tag = {"id": str(uuid.uuid4())[:8], "name": name,
           "color": TAG_COLORS[len(board["tags"]) % len(TAG_COLORS)],
           "created_at": datetime.now(timezone.utc).isoformat()}
    await db.users.update_one({"email": user_email}, {"$push": {"task_board.tags": tag}})
    return web.json_response({"tag": tag}, status=201)


async def handle_delete_tag(request: web.Request) -> web.Response:
    db = get_db()
    user_email = get_user_email(request)
    if db is None or not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    tag_id = request.match_info["tag_id"]
    board = await _get_board_config_for(db, user_email)
    if tag_id not in {tag["id"] for tag in board["tags"]}:
        return web.json_response({"error": "Not found"}, status=404)
    await db.users.update_one({"email": user_email}, {"$pull": {"task_board.tags": {"id": tag_id}}})
    await db.conversations.update_many({"metadata.user_name": user_email}, {"$pull": {"task_tag_ids": tag_id}})
    return web.json_response({"deleted": True})


def setup_task_routes(app: web.Application):
    """Register tasks-board routes on the aiohttp app."""
    # Static paths must be registered before the {conversation_id} route.
    app.router.add_get("/api/tasks/board-settings", handle_get_board_settings)
    app.router.add_put("/api/tasks/board-settings", handle_put_board_settings)
    app.router.add_get("/api/tasks/needs-input-count", handle_needs_input_count)
    app.router.add_post("/api/tasks/tags", handle_create_tag)
    app.router.add_delete("/api/tasks/tags/{tag_id}", handle_delete_tag)
    app.router.add_post("/api/tasks", handle_create_task)
    app.router.add_get("/api/tasks", handle_list_tasks)
    app.router.add_post("/api/tasks/{conversation_id}/fork", handle_fork_task)
    app.router.add_patch("/api/tasks/{conversation_id}", handle_update_task)
