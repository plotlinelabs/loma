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

Shared boards: besides the personal board, a user can own extra boards in the
`task_boards` collection and share them with chosen people (viewer/editor).
A shared board doc keeps the same `task_board` config shape (prompt, lanes,
tags); its tasks carry `task_board_id`. Tasks without `task_board_id` stay on
their creator's personal board, so existing data needs no migration.
Agent runs always use the task creator's identity and connections: members
of a shared board can see a task's chat, but only its creator can message it.
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
from agent.prompt import get_prompt_setting
from api.auth_helpers import get_system_role, get_user_email
from api.drain import DRAIN_MESSAGE, is_draining

logger = logging.getLogger(__name__)

BOARD_CONTEXT_HEADING = "## User's role & working context (apply to this task)"
BOARD_PERSONAL_HEADING = "### Personal additions"
# Placeholders admins can use in the global default context (Admin > Settings).
_BOARD_PLACEHOLDER_RE = re.compile(r"\{\{\s*(user_name|user_email)\s*\}\}")


def render_board_default_context(template: str, owner_doc: dict | None, owner_email: str) -> str:
    """Fill {{user_name}} / {{user_email}} in the global default board context."""
    template = (template or "").strip()
    if not template:
        return ""
    email = ((owner_doc or {}).get("email") or owner_email or "").strip()
    name = ((owner_doc or {}).get("name") or "").strip() or email.split("@")[0] or "the user"
    values = {"user_name": name, "user_email": email}
    return _BOARD_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def merge_board_context(default_context: str, personal_prompt: str) -> str:
    """Compose the per-task context block: global default first, then the
    owner's personal board context. Empty when neither is configured."""
    default_context = (default_context or "").strip()
    personal_prompt = (personal_prompt or "").strip()
    if not default_context and not personal_prompt:
        return ""
    parts = [BOARD_CONTEXT_HEADING]
    if default_context:
        parts.append(default_context)
    if personal_prompt:
        if default_context:
            parts.append(f"\n{BOARD_PERSONAL_HEADING}")
        parts.append(personal_prompt)
    return "\n".join(parts)


async def build_board_context(db, owner: str, board_id: str | None = None) -> str:
    """The context block injected on every turn of a board task owned by `owner`.

    Tasks on a shared board use that board's context prompt instead of the
    owner's personal one.
    """
    owner_doc = await db.users.find_one(
        {"email": owner}, {"task_board": 1, "name": 1, "email": 1})
    personal = ((owner_doc or {}).get("task_board") or {}).get("prompt", "")
    if board_id and board_id != PERSONAL_BOARD_ID:
        shared = await db.task_boards.find_one({"board_id": board_id}, {"task_board": 1})
        personal = ((shared or {}).get("task_board") or {}).get("prompt", "")
    default_context = render_board_default_context(
        get_prompt_setting("task_board_default_context"), owner_doc, owner)
    return merge_board_context(default_context, personal)


async def _run_task_headless(db, conversation_id: str, prompt: str,
                             model: str, files: list, owner: str,
                             tool_config: dict | None = None, recall_session: dict | None = None,
                             board_id: str | None = None):
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
        conversation_context = await build_board_context(db, owner, board_id)

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
            tool_config=tool_config,
            recall_session=recall_session,
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
        title = await _generate_title_llm(prompt, db=db, conversation_id=conversation_id)
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

# Shared boards. "personal" addresses the caller's own board (users.task_board).
PERSONAL_BOARD_ID = "personal"
PERSONAL_BOARD_NAME = "My tasks"
MAX_BOARD_NAME_LEN = 60
MAX_OWNED_BOARDS = 20
MAX_BOARD_MEMBERS = 50
BOARD_MEMBER_ROLES = ("viewer", "editor")
# Roles that may add/move/edit tasks and change lanes, tags and context.
EDIT_ROLES = ("owner", "editor")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    return {"prompt": board.get("prompt", ""), "lanes": lanes, "tags": board.get("tags") or [],
            "show_agent_work": board.get("show_agent_work", True)}


async def _get_board_config_for(db, user_email: str) -> dict:
    user_doc = await db.users.find_one({"email": user_email}, {"task_board": 1})
    return get_board_config(user_doc)


def _board_role(board_doc: dict, user_email: str) -> str | None:
    """The caller's role on a shared board: owner, editor, viewer or None."""
    if board_doc.get("owner") == user_email:
        return "owner"
    for member in board_doc.get("members") or []:
        if member.get("email") == user_email:
            role = member.get("role")
            return role if role in BOARD_MEMBER_ROLES else "viewer"
    return None


def _board_summary(board_doc: dict | None, role: str | None, user_email: str) -> dict:
    """Public shape of a board for the switcher and the board header."""
    if board_doc is None:
        return {"id": PERSONAL_BOARD_ID, "name": PERSONAL_BOARD_NAME, "owner": user_email,
                "role": role, "shared": False, "members": []}
    return {
        "id": board_doc["board_id"],
        "name": board_doc.get("name") or "Untitled board",
        "owner": board_doc.get("owner"),
        "role": role,
        "shared": True,
        "members": [
            {"email": m.get("email"), "role": m.get("role") or "viewer"}
            for m in board_doc.get("members") or []
        ],
    }


async def _load_board(db, user_email: str, board_id: str | None,
                      personal_owner: str | None = None) -> dict | None:
    """Load a board with the caller's role on it (role may be None).

    Returns everything a route needs: `summary`, `config` (lanes, tags,
    prompt), `task_filter` (which conversations sit on the board) and `store`
    (collection + filter for config writes). None if the board is missing.
    """
    if not board_id or board_id == PERSONAL_BOARD_ID:
        owner = personal_owner or user_email
        role = "owner" if owner == user_email else None
        return {
            "id": PERSONAL_BOARD_ID, "shared": False, "role": role, "owner": owner,
            "summary": _board_summary(None, role, owner),
            "config": await _get_board_config_for(db, owner),
            "task_filter": {"metadata.user_name": owner, "task_board_id": None},
            "store": (db.users, {"email": owner}),
        }
    board_doc = await db.task_boards.find_one({"board_id": board_id})
    if not board_doc:
        return None
    role = _board_role(board_doc, user_email)
    return {
        "id": board_id, "shared": True, "role": role, "owner": board_doc.get("owner"),
        "summary": _board_summary(board_doc, role, user_email),
        "config": get_board_config(board_doc),
        "task_filter": {"task_board_id": board_id},
        "store": (db.task_boards, {"board_id": board_id}),
    }


async def resolve_board(db, user_email: str, board_id: str | None) -> dict | None:
    """A board the caller owns or is a member of; None otherwise."""
    board = await _load_board(db, user_email, board_id)
    return board if board and board["role"] else None


async def _task_board(db, conversation: dict, user_email: str) -> dict:
    """The board a task sits on, with the caller's role on it.

    A task whose shared board no longer exists falls back to its creator's
    personal board.
    """
    creator = (conversation.get("metadata") or {}).get("user_name") or user_email
    board_id = conversation.get("task_board_id")
    board = await _load_board(db, user_email, board_id, personal_owner=creator) if board_id else None
    return board or await _load_board(db, user_email, None, personal_owner=creator)


async def task_access(db, conversation: dict, user_email: str,
                      system_role: str) -> tuple[bool, bool, bool]:
    """(can_view, can_edit, full) for a task conversation.

    `full` is the existing per-conversation access (creator, admin, ...).
    Members of the task's shared board can view it; editors can also move
    and annotate it, but never change what the agent runs (prompt, model,
    tools) since runs use the creator's identity.
    """
    from api.routes import _check_conversation_access
    if _check_conversation_access(conversation, user_email, system_role):
        return True, True, True
    board_id = conversation.get("task_board_id")
    if not board_id or not conversation.get("task_status"):
        return False, False, False
    board = await resolve_board(db, user_email, board_id)
    if not board:
        return False, False, False
    return True, board["role"] in EDIT_ROLES, False


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
        "tool_config": task.get("tool_config"),
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
        "task_board_id": task.get("task_board_id") or None,
        # The creator: only they can message the task (runs use their identity).
        "owner": (task.get("metadata") or {}).get("user_name") or None,
    }


_TASK_PROJECTION = {
    "conversation_id": 1, "title": 1, "prompt": 1, "model": 1, "status": 1, "tool_config": 1,
    "task_status": 1, "task_lane": 1, "task_rank": 1,
    "total_turns": 1, "started_at": 1, "finished_at": 1,
    "task_created_at": 1, "task_staged_at": 1, "task_started_at": 1,
    "task_done_at": 1,
    "task_tag_ids": 1,
    "task_priority": 1,
    "task_deadline": 1,
    "forked_from_conversation_id": 1,
    "task_board_id": 1,
    "metadata.user_name": 1,
}

# Done is the only column that grows without bound, so it is the only one we
# cap. Todo/active cards must never be dropped from the board.
DONE_TASKS_LIMIT = 500


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

    tool_config = body.get("tool_config")
    if tool_config is not None:
        if not isinstance(tool_config, dict):
            return web.json_response({"error": "tool_config must be an object"}, status=400)
        for key in ("enabled_skills", "enabled_tools"):
            val = tool_config.get(key)
            if val is not None and not isinstance(val, list):
                return web.json_response(
                    {"error": f"tool_config.{key} must be an array or null"}, status=400,
                )

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

    # Quick-add fires an agent run immediately; refuse while a deploy drains.
    # Plain drafts are fine — nothing runs until the user sends a message.
    if body.get("start") and is_draining():
        return web.json_response({"error": DRAIN_MESSAGE, "draining": True}, status=503)

    board = await resolve_board(db, user_email, body.get("board"))
    if board is None:
        return web.json_response({"error": "Board not found"}, status=404)
    if board["role"] not in EDIT_ROLES:
        return web.json_response({"error": "You have view-only access to this board"}, status=403)
    lane_ids = [lane["id"] for lane in board["config"]["lanes"]]
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
        **({"tool_config": tool_config} if tool_config else {}),
        **({"task_board_id": board["id"]} if board["shared"] else {}),
    }
    await db.conversations.insert_one(doc)

    # Quick-added tasks (no explicit title) get an LLM title from the prompt.
    # Empty drafts skip this — enrichment titles them after the first run.
    if not title and prompt:
        asyncio.create_task(_auto_title_task(db, doc["conversation_id"], prompt))

    if start:
        from api.recall_session import launch_recall
        recall_session = await launch_recall(request, doc["conversation_id"], user_email)
        asyncio.create_task(_run_task_headless(
            db, doc["conversation_id"], prompt, model, files, user_email,
            tool_config=tool_config,
            recall_session=recall_session,
            board_id=board["id"] if board["shared"] else None,
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

    can_view, _, _ = await task_access(db, source, user_email, get_system_role(request))
    if not can_view:
        return web.json_response({"error": "Not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # The fork lands on the requested board (default: the caller's own board).
    board = await resolve_board(db, user_email, body.get("board"))
    if board is None:
        return web.json_response({"error": "Board not found"}, status=404)
    if board["role"] not in EDIT_ROLES:
        return web.json_response({"error": "You have view-only access to this board"}, status=403)
    lane_ids = [lane["id"] for lane in board["config"]["lanes"]]
    source_owner = (source.get("metadata") or {}).get("user_name")
    source_board = source.get("task_board_id") or PERSONAL_BOARD_ID
    # Lanes and tags only carry over when the fork stays on the same board.
    same_board = source_board == board["id"] and (board["shared"] or source_owner == user_email)
    default_lane = source.get("task_lane") if same_board else None
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
        if same_board else [],
        **({"task_board_id": board["id"]} if board["shared"] else {}),
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

    board = await resolve_board(db, user_email, request.query.get("board"))
    if board is None:
        return web.json_response({"error": "Board not found"}, status=404)
    lane_ids = [lane["id"] for lane in board["config"]["lanes"]]
    # "Show agent work" is a per-person view setting, even on shared boards.
    show_agent_work = (
        (await _get_board_config_for(db, user_email))["show_agent_work"]
        if board["shared"] else board["config"]["show_agent_work"]
    )

    query = {
        **board["task_filter"],
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

    # Load todo/active in full and cap only Done (newest first). A single
    # unsorted .to_list(500) over all statuses let old Done tasks crowd out
    # live cards, which then only reappeared when a search narrowed the match.
    active_query = {**query, "task_status": {"$in": ["todo", "active"]}}
    done_query = {**query, "task_status": "done"}
    active_cursor = db.conversations.find(active_query, _TASK_PROJECTION)
    done_cursor = (
        db.conversations.find(done_query, _TASK_PROJECTION)
        .sort([("task_done_at", -1), ("_id", -1)])
        .limit(DONE_TASKS_LIMIT)
    )
    active_tasks, done_tasks = await asyncio.gather(
        active_cursor.to_list(None),
        done_cursor.to_list(DONE_TASKS_LIMIT),
    )
    tasks = active_tasks + done_tasks

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
        "board": board["summary"],
        "lanes": board["config"]["lanes"],
        "show_agent_work": show_agent_work,
        "tags": board["config"]["tags"],
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
    task_tag_ids, task_priority, task_deadline, task_board_id.
    task_status: null removes the conversation from the board.
    task_board_id moves the task to another board ("personal" or null = the
    creator's own board); lane and tags reset to the target board's.
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

    can_view, can_edit, full = await task_access(db, conversation, user_email, system_role)
    if not can_view:
        return web.json_response({"error": "Not found"}, status=404)
    if not can_edit:
        return web.json_response({"error": "You have view-only access to this board"}, status=403)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    creator = (conversation.get("metadata") or {}).get("user_name") or user_email
    # Board editors can move and annotate a teammate's task, but what the
    # agent runs (and under whose account) stays with the creator.
    if not full and (
        {"prompt", "model", "tool_config", "task_board_id"} & set(body)
        or ("task_status" in body and body["task_status"] is None)
    ):
        return web.json_response(
            {"error": "Only the task's creator can change that"}, status=403)

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
                "task_board_id": "",
            }},
        )
        return web.json_response({"task": None})

    updates: dict = {}
    unsets: dict = {}
    board = await _task_board(db, conversation, user_email)

    if "task_board_id" in body:
        target_id = body["task_board_id"] or PERSONAL_BOARD_ID
        if not isinstance(target_id, str):
            return web.json_response({"error": "task_board_id must be a string or null"}, status=400)
        if target_id == PERSONAL_BOARD_ID:
            if creator != user_email:
                return web.json_response(
                    {"error": "Only the task's creator can move it to their board"}, status=403)
            target = await _load_board(db, user_email, None)
        else:
            target = await resolve_board(db, user_email, target_id)
            if target is None:
                return web.json_response({"error": "Board not found"}, status=404)
            if target["role"] not in EDIT_ROLES:
                return web.json_response(
                    {"error": "You have view-only access to that board"}, status=403)
        if target["id"] != board["id"]:
            if target["shared"]:
                updates["task_board_id"] = target["id"]
            else:
                unsets["task_board_id"] = ""
            # Lanes and tags are per board: land in the target's first lane.
            updates["task_lane"] = target["config"]["lanes"][0]["id"]
            updates["task_tag_ids"] = []
            board = target

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
            if "task_lane" not in body and not updates.get("task_lane") and not conversation.get("task_lane"):
                updates["task_lane"] = board["config"]["lanes"][0]["id"]
            if conversation.get("task_rank") is None and "task_rank" not in body:
                updates["task_rank"] = -now.timestamp()

    if "task_lane" in body:
        if (updates.get("task_status") or current) != "todo":
            return web.json_response({"error": "Only staged tasks have lanes"}, status=400)
        # Lanes belong to the board the task sits on, not the caller's.
        lane_ids = [lane["id"] for lane in board["config"]["lanes"]]
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
        allowed = {tag["id"] for tag in board["config"]["tags"]}
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

    if "tool_config" in body:
        if conversation.get("status") == "running":
            return web.json_response(
                {"error": "tool_config cannot be changed while a task is running"}, status=400)
        tc = body["tool_config"]
        if tc is not None:
            if not isinstance(tc, dict):
                return web.json_response({"error": "tool_config must be an object or null"}, status=400)
            for key in ("enabled_skills", "enabled_tools"):
                val = tc.get(key)
                if val is not None and not isinstance(val, list):
                    return web.json_response(
                        {"error": f"tool_config.{key} must be an array or null"}, status=400)
        updates["tool_config"] = tc

    if not updates and not unsets:
        return web.json_response({"error": "Nothing to update"}, status=400)

    operation: dict = {}
    if updates:
        operation["$set"] = updates
    if unsets:
        operation["$unset"] = unsets
    await db.conversations.update_one({"conversation_id": cid}, operation)

    updated = await db.conversations.find_one(
        {"conversation_id": cid}, _TASK_PROJECTION)
    lane_ids = [lane["id"] for lane in board["config"]["lanes"]]
    return web.json_response({"task": _task_view(updated, lane_ids)})


async def handle_get_board_settings(request: web.Request) -> web.Response:
    """GET /api/tasks/board-settings?board= — a board's lanes + context prompt."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    user_doc = await db.users.find_one(
        {"email": user_email}, {"task_board": 1, "name": 1, "email": 1})
    board = get_board_config(user_doc)
    board_id = request.query.get("board")
    if board_id and board_id != PERSONAL_BOARD_ID:
        shared = await resolve_board(db, user_email, board_id)
        if shared is None:
            return web.json_response({"error": "Board not found"}, status=404)
        # Lanes, tags and context come from the shared board; "show agent
        # work" stays the caller's own preference.
        board = {**shared["config"], "show_agent_work": board["show_agent_work"],
                 "board": shared["summary"]}
    # Resolved global default (Admin > Settings) so the drawer can show what
    # is already applied ahead of the personal context.
    board["default_context"] = render_board_default_context(
        get_prompt_setting("task_board_default_context"), user_doc, user_email)
    return web.json_response(board)


async def handle_put_board_settings(request: web.Request) -> web.Response:
    """PUT /api/tasks/board-settings?board= — save a board's lanes + context prompt.

    Deleting a lane migrates its staged tasks to the first remaining lane.
    Owners and editors can change a shared board; show_agent_work is always
    saved on the caller's own profile.
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

    if not isinstance(body, dict):
        return web.json_response({"error": "Expected a JSON object"}, status=400)
    if "show_agent_work" in body and not isinstance(body["show_agent_work"], bool):
        return web.json_response({"error": "show_agent_work must be a boolean"}, status=400)
    # A dismissal only changes this preference, never context, lanes or tasks.
    if set(body) == {"show_agent_work"}:
        await db.users.update_one(
            {"email": user_email},
            {"$set": {"task_board.show_agent_work": body["show_agent_work"]}},
        )
        board = await _get_board_config_for(db, user_email)
        return web.json_response({**board, "migrated": 0})

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

    target = await resolve_board(db, user_email, request.query.get("board"))
    if target is None:
        return web.json_response({"error": "Board not found"}, status=404)
    if target["role"] not in EDIT_ROLES:
        return web.json_response({"error": "You have view-only access to this board"}, status=403)

    previous = target["config"]
    removed_ids = [lane["id"] for lane in previous["lanes"] if lane["id"] not in seen_ids]
    first_lane_id = lanes[0]["id"]

    updates = {"task_board.prompt": prompt, "task_board.lanes": lanes}
    show_agent_work = previous["show_agent_work"]
    if target["shared"]:
        show_agent_work = (await _get_board_config_for(db, user_email))["show_agent_work"]
    if "show_agent_work" in body:
        show_agent_work = body["show_agent_work"]
        if target["shared"]:
            await db.users.update_one(
                {"email": user_email},
                {"$set": {"task_board.show_agent_work": show_agent_work}},
            )
        else:
            updates["task_board.show_agent_work"] = show_agent_work
    collection, store_filter = target["store"]
    await collection.update_one(store_filter, {"$set": updates})

    migrated = 0
    if removed_ids:
        result = await db.conversations.update_many(
            {
                **target["task_filter"],
                "task_status": "todo",
                "task_lane": {"$in": removed_ids},
            },
            {"$set": {"task_lane": first_lane_id}},
        )
        migrated = result.modified_count

    return web.json_response({"prompt": prompt, "lanes": lanes, "migrated": migrated,
                              "show_agent_work": show_agent_work})


async def handle_create_tag(request: web.Request) -> web.Response:
    db = get_db()
    user_email = get_user_email(request)
    if db is None or not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name or len(name) > MAX_TAG_NAME_LEN:
        return web.json_response({"error": f"Tag names must be 1-{MAX_TAG_NAME_LEN} characters"}, status=400)
    target = await resolve_board(db, user_email, request.query.get("board") or body.get("board"))
    if target is None:
        return web.json_response({"error": "Board not found"}, status=404)
    if target["role"] not in EDIT_ROLES:
        return web.json_response({"error": "You have view-only access to this board"}, status=403)
    board = target["config"]
    if len(board["tags"]) >= MAX_TAGS:
        return web.json_response({"error": f"At most {MAX_TAGS} tags allowed"}, status=400)
    if any(tag["name"].casefold() == name.casefold() for tag in board["tags"]):
        return web.json_response({"error": "A tag with that name already exists"}, status=409)
    tag = {"id": str(uuid.uuid4())[:8], "name": name,
           "color": TAG_COLORS[len(board["tags"]) % len(TAG_COLORS)],
           "created_at": datetime.now(timezone.utc).isoformat()}
    collection, store_filter = target["store"]
    await collection.update_one(store_filter, {"$push": {"task_board.tags": tag}})
    return web.json_response({"tag": tag}, status=201)


async def handle_delete_tag(request: web.Request) -> web.Response:
    db = get_db()
    user_email = get_user_email(request)
    if db is None or not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    tag_id = request.match_info["tag_id"]
    target = await resolve_board(db, user_email, request.query.get("board"))
    if target is None:
        return web.json_response({"error": "Board not found"}, status=404)
    if target["role"] not in EDIT_ROLES:
        return web.json_response({"error": "You have view-only access to this board"}, status=403)
    if tag_id not in {tag["id"] for tag in target["config"]["tags"]}:
        return web.json_response({"error": "Not found"}, status=404)
    collection, store_filter = target["store"]
    await collection.update_one(store_filter, {"$pull": {"task_board.tags": {"id": tag_id}}})
    await db.conversations.update_many(target["task_filter"], {"$pull": {"task_tag_ids": tag_id}})
    return web.json_response({"deleted": True})


# ── Boards (list / create / share / delete) ──────────────────────────────────

async def handle_list_boards(request: web.Request) -> web.Response:
    """GET /api/tasks/boards — the caller's personal board plus every shared
    board they own or are a member of."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    docs = await db.task_boards.find(
        {"$or": [{"owner": user_email}, {"members.email": user_email}]},
        {"board_id": 1, "name": 1, "owner": 1, "members": 1, "created_at": 1},
    ).sort("created_at", 1).to_list(200)
    boards = [_board_summary(None, "owner", user_email)]
    boards += [_board_summary(doc, _board_role(doc, user_email), user_email) for doc in docs]
    return web.json_response({"boards": boards})


def _validate_board_name(name) -> str | None:
    name = (name or "").strip() if isinstance(name, str) else ""
    return name if 0 < len(name) <= MAX_BOARD_NAME_LEN else None


async def handle_create_board(request: web.Request) -> web.Response:
    """POST /api/tasks/boards — create a new (unshared) board owned by the caller."""
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

    name = _validate_board_name((body or {}).get("name"))
    if not name:
        return web.json_response(
            {"error": f"Board names must be 1-{MAX_BOARD_NAME_LEN} characters"}, status=400)
    if await db.task_boards.count_documents({"owner": user_email}) >= MAX_OWNED_BOARDS:
        return web.json_response(
            {"error": f"You can own at most {MAX_OWNED_BOARDS} boards"}, status=400)

    now = datetime.now(timezone.utc)
    doc = {
        "board_id": uuid.uuid4().hex[:12],
        "name": name,
        "owner": user_email,
        "members": [],
        # Same config shape as users.task_board so get_board_config works on both.
        "task_board": {
            "prompt": "",
            "lanes": copy.deepcopy(DEFAULT_BOARD["lanes"]),
            "tags": [],
        },
        "created_at": now,
        "updated_at": now,
    }
    await db.task_boards.insert_one(doc)
    return web.json_response({"board": _board_summary(doc, "owner", user_email)}, status=201)


async def _owned_board_or_error(db, request, user_email):
    board_id = request.match_info["board_id"]
    doc = await db.task_boards.find_one({"board_id": board_id})
    role = _board_role(doc, user_email) if doc else None
    if not role:
        return None, web.json_response({"error": "Board not found"}, status=404)
    if role != "owner":
        return None, web.json_response(
            {"error": "Only the board owner can change sharing"}, status=403)
    return doc, None


async def handle_update_board(request: web.Request) -> web.Response:
    """PATCH /api/tasks/boards/{board_id} — owner renames the board or sets
    its members: [{"email": ..., "role": "viewer"|"editor"}] (full list)."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    doc, error = await _owned_board_or_error(db, request, user_email)
    if error:
        return error
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Expected a JSON object"}, status=400)

    updates: dict = {}
    if "name" in body:
        name = _validate_board_name(body["name"])
        if not name:
            return web.json_response(
                {"error": f"Board names must be 1-{MAX_BOARD_NAME_LEN} characters"}, status=400)
        updates["name"] = name

    if "members" in body:
        members_in = body["members"]
        if not isinstance(members_in, list) or len(members_in) > MAX_BOARD_MEMBERS:
            return web.json_response(
                {"error": f"members must be a list of at most {MAX_BOARD_MEMBERS} people"}, status=400)
        members: list[dict] = []
        seen: set[str] = set()
        for member in members_in:
            if not isinstance(member, dict):
                return web.json_response({"error": "Invalid member"}, status=400)
            email = (member.get("email") or "").strip().lower() if isinstance(member.get("email"), str) else ""
            role = member.get("role") or "viewer"
            if not _EMAIL_RE.match(email):
                return web.json_response({"error": f"Invalid email: {email or '(empty)'}"}, status=400)
            if role not in BOARD_MEMBER_ROLES:
                return web.json_response({"error": "role must be viewer or editor"}, status=400)
            if email == doc.get("owner") or email in seen:
                continue
            seen.add(email)
            members.append({"email": email, "role": role})
        # Only people who can sign in to Loma can be added.
        if members:
            known = await db.users.find(
                {"email": {"$in": [m["email"] for m in members]}}, {"email": 1},
            ).to_list(None)
            known_emails = {u.get("email") for u in known}
            unknown = [m["email"] for m in members if m["email"] not in known_emails]
            if unknown:
                return web.json_response(
                    {"error": f"No Loma user found for: {', '.join(unknown)}"}, status=400)
        updates["members"] = members

    if not updates:
        return web.json_response({"error": "Nothing to update"}, status=400)
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.task_boards.update_one({"board_id": doc["board_id"]}, {"$set": updates})
    return web.json_response({"board": _board_summary({**doc, **updates}, "owner", user_email)})


async def handle_delete_board(request: web.Request) -> web.Response:
    """DELETE /api/tasks/boards/{board_id} — owner deletes a shared board.

    Nothing is lost: its tasks go back to their creators' own boards (first
    lane, tags cleared, since lanes and tags belonged to the deleted board).
    """
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    doc, error = await _owned_board_or_error(db, request, user_email)
    if error:
        return error
    result = await db.conversations.update_many(
        {"task_board_id": doc["board_id"]},
        {"$unset": {"task_board_id": ""}, "$set": {"task_lane": None, "task_tag_ids": []}},
    )
    await db.task_boards.delete_one({"board_id": doc["board_id"]})
    return web.json_response({"deleted": True, "moved": result.modified_count})


def setup_task_routes(app: web.Application):
    """Register tasks-board routes on the aiohttp app."""
    # Static paths must be registered before the {conversation_id} route.
    app.router.add_get("/api/tasks/boards", handle_list_boards)
    app.router.add_post("/api/tasks/boards", handle_create_board)
    app.router.add_patch("/api/tasks/boards/{board_id}", handle_update_board)
    app.router.add_delete("/api/tasks/boards/{board_id}", handle_delete_board)
    app.router.add_get("/api/tasks/board-settings", handle_get_board_settings)
    app.router.add_put("/api/tasks/board-settings", handle_put_board_settings)
    app.router.add_get("/api/tasks/needs-input-count", handle_needs_input_count)
    app.router.add_post("/api/tasks/tags", handle_create_tag)
    app.router.add_delete("/api/tasks/tags/{tag_id}", handle_delete_tag)
    app.router.add_post("/api/tasks", handle_create_task)
    app.router.add_get("/api/tasks", handle_list_tasks)
    app.router.add_post("/api/tasks/{conversation_id}/fork", handle_fork_task)
    app.router.add_patch("/api/tasks/{conversation_id}", handle_update_task)
