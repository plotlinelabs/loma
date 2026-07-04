"""Tasks board routes — a kanban layer over conversations.

A task IS a conversation (1:1) plus board state stored on the conversation doc:
  - task_status: "todo" (staged draft) | "active" (started) | "done" (user-closed)
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

import logging
import uuid
from datetime import datetime, timezone

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import get_system_role, get_user_email

logger = logging.getLogger(__name__)

# Statuses where the agent is no longer running — the user's turn.
NEEDS_INPUT_STATUSES = ("completed", "error", "interrupted")

DEFAULT_BOARD = {
    "prompt": "",
    "lanes": [{"id": "todo", "name": "Todo", "order": 0}],
}

MAX_LANE_NAME_LEN = 40
MAX_LANES = 10
MAX_BOARD_PROMPT_LEN = 10000


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
    return {"prompt": board.get("prompt", ""), "lanes": lanes}


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
    # active
    if task.get("status") == "running":
        return "working"
    return "needs_input"


def _task_view(task: dict, lane_ids: list[str]) -> dict:
    """Shape a conversation doc into the board card payload."""
    prompt = task.get("prompt") or ""
    return {
        "conversation_id": task.get("conversation_id"),
        "title": task.get("title") or None,
        "prompt": prompt[:200],
        "status": task.get("status"),
        "task_status": task.get("task_status"),
        "task_lane": task.get("task_lane"),
        "task_rank": task.get("task_rank"),
        "column": derive_column(task, lane_ids),
        "total_turns": task.get("total_turns", 0),
        "started_at": _serialize(task.get("started_at")),
        "finished_at": _serialize(task.get("finished_at")),
        "task_created_at": _serialize(task.get("task_created_at")),
        "task_staged_at": _serialize(task.get("task_staged_at")),
        "task_started_at": _serialize(task.get("task_started_at")),
        "task_done_at": _serialize(task.get("task_done_at")),
    }


_TASK_PROJECTION = {
    "conversation_id": 1, "title": 1, "prompt": 1, "status": 1,
    "task_status": 1, "task_lane": 1, "task_rank": 1,
    "total_turns": 1, "started_at": 1, "finished_at": 1,
    "task_created_at": 1, "task_staged_at": 1, "task_started_at": 1,
    "task_done_at": 1,
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
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    title = (body.get("title") or "").strip() or None
    board = await _get_board_config_for(db, user_email)
    lane_ids = [lane["id"] for lane in board["lanes"]]
    lane = body.get("lane") or lane_ids[0]
    if lane not in lane_ids:
        return web.json_response({"error": "Unknown lane"}, status=400)

    now = datetime.now(timezone.utc)
    # Draft doc mirrors observer.start()'s shape, but the run hasn't begun:
    # status/started_at stay None and messages stays [] — observer.resume()
    # $pushes the prompt as the first user message when the task starts, so
    # pre-filling messages here would duplicate it.
    doc = {
        "conversation_id": str(uuid.uuid4()),
        "source": "dashboard",
        "started_at": None,
        "finished_at": None,
        "duration_ms": None,
        "status": None,
        "metadata": {"user_name": user_email},
        "prompt": prompt,
        "model": "",
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
        "task_status": "todo",
        "task_lane": lane,
        # Newest first when sorted ascending; reorder uses neighbor midpoints.
        "task_rank": -now.timestamp(),
        "task_created_at": now,
        "task_staged_at": now,
        "task_started_at": None,
        "task_done_at": None,
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

    tasks = await db.conversations.find(
        {
            "metadata.user_name": user_email,
            "task_status": {"$in": ["todo", "active", "done"]},
            "deleted": {"$ne": True},
        },
        _TASK_PROJECTION,
    ).to_list(500)

    views = [_task_view(t, lane_ids) for t in tasks]

    def sort_key(view):
        column = view["column"]
        if column in lane_ids:
            return view.get("task_rank") or 0
        # Newest first for the derived/done columns.
        stamp = {
            "working": view.get("started_at"),
            "needs_input": view.get("finished_at"),
            "done": view.get("task_done_at"),
        }.get(column) or ""
        return stamp

    staged = sorted([v for v in views if v["column"] in lane_ids], key=sort_key)
    others = sorted(
        [v for v in views if v["column"] not in lane_ids],
        key=sort_key, reverse=True,
    )
    ordered = staged + others

    counts: dict[str, int] = {lane_id: 0 for lane_id in lane_ids}
    counts.update({"working": 0, "needs_input": 0, "done": 0})
    for view in ordered:
        counts[view["column"]] = counts.get(view["column"], 0) + 1

    return web.json_response({
        "lanes": board["lanes"],
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

    Accepts any of: task_status, task_lane, task_rank, prompt, title.
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
        # - "todo" is only valid before the first run (drafts); once a
        #   conversation exists it can never go back to a staging lane.
        # - "active" from todo happens in handle_chat on first send, but is
        #   also allowed here for done->active (reopen) and for converting an
        #   existing chat into a task (current is None).
        if target == "todo" and has_run:
            return web.json_response(
                {"error": "A started task cannot return to a staging lane"}, status=400)
        if target == "active" and not has_run and current == "todo":
            return web.json_response(
                {"error": "Start the task by sending its prompt, not by PATCH"}, status=400)
        if target == "done" and current not in ("active", "done"):
            return web.json_response(
                {"error": "Only started tasks can be marked done"}, status=400)
        updates["task_status"] = target
        if target == "done" and current != "done":
            updates["task_done_at"] = now
        if target == "active" and current == "done":
            updates["task_done_at"] = None
        if target == "active" and current is None:
            # Converting an existing chat into a board task.
            updates["task_created_at"] = conversation.get("task_created_at") or now

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
        if current != "todo":
            return web.json_response({"error": "Only drafts can be edited"}, status=400)
        prompt = (body["prompt"] or "").strip()
        if not prompt:
            return web.json_response({"error": "prompt cannot be empty"}, status=400)
        updates["prompt"] = prompt

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
        {"$set": {"task_board": {"prompt": prompt, "lanes": lanes}}},
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


def setup_task_routes(app: web.Application):
    """Register tasks-board routes on the aiohttp app."""
    # Static paths must be registered before the {conversation_id} route.
    app.router.add_get("/api/tasks/board-settings", handle_get_board_settings)
    app.router.add_put("/api/tasks/board-settings", handle_put_board_settings)
    app.router.add_get("/api/tasks/needs-input-count", handle_needs_input_count)
    app.router.add_post("/api/tasks", handle_create_task)
    app.router.add_get("/api/tasks", handle_list_tasks)
    app.router.add_patch("/api/tasks/{conversation_id}", handle_update_task)
