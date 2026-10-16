"""Edit a sent message, retry a reply, and branch a chat from a reply.

Every run starts a fresh agent session with the thread as context, so rerunning
from an earlier message only needs the stored thread cut back to it: the
messages from that point, and the turns and artifacts recorded since, move out
of the chat (into `rewinds`, `rewound_turns`, `rewound_artifacts`) and the
dashboard then sends the edited message as a normal follow-up. Nothing is
deleted, and nothing the agent already did outside the chat is undone.
"""

import copy
import logging
import uuid
from datetime import datetime, timezone

from aiohttp import web

from agent.active_streams import release_claim, try_claim
from api.auth_helpers import get_system_role, get_user_email
from observability.db import get_db

logger = logging.getLogger(__name__)

BUSY_STATUSES = ("running", "queued")


def find_user_message(messages: list, user_index: int, expected: str) -> int | None:
    """Position in `messages` of the user message the dashboard showed as its
    user_index-th, checked against its text. The dashboard hides repeated
    identical messages, so on a mismatch the nearest message with that text wins."""
    users = [i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"]
    expected = (expected or "").strip()

    def matches(position: int) -> bool:
        content = (messages[position].get("content") or "").strip()
        # Board context is sent as "[Context: ...]\n\n<message>" but shown without it.
        return content == expected or content.endswith("\n\n" + expected)

    if 0 <= user_index < len(users) and matches(users[user_index]):
        return users[user_index]
    candidates = [k for k, position in enumerate(users) if matches(position)]
    if not candidates:
        return None
    return users[min(candidates, key=lambda k: abs(k - user_index))]


def _last_reply(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return message["content"]
    return ""


async def _load(request: web.Request):
    db = get_db()
    if db is None:
        return None, None, web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return None, None, web.json_response({"error": "Authentication required"}, status=401)
    cid = request.match_info["conversation_id"]
    conversation = await db.conversations.find_one({"conversation_id": cid, "deleted": {"$ne": True}})
    if not conversation or conversation.get("human_task"):
        return None, None, web.json_response({"error": "Not found"}, status=404)
    return db, conversation, None


async def _body(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


async def handle_rewind(request: web.Request) -> web.Response:
    """POST /api/conversations/{id}/rewind {user_index, content}

    Cuts the chat back to just before that user message so the dashboard can
    send it again (edited, or as is to retry the reply)."""
    from api.routes import _check_conversation_manage_access
    from api.task_routes import can_run_task

    db, conversation, error = await _load(request)
    if error:
        return error
    user_email = get_user_email(request)
    role = get_system_role(request)
    if not (_check_conversation_manage_access(conversation, user_email, role)
            or (conversation.get("task_status") and await can_run_task(db, conversation, user_email, role))):
        return web.json_response({"error": "Only the chat's owner can edit it"}, status=403)

    body = await _body(request)
    if body is None or not isinstance(body.get("user_index"), int) or not isinstance(body.get("content"), str):
        return web.json_response({"error": "user_index and content are required"}, status=400)

    cid = conversation["conversation_id"]
    run_id = uuid.uuid4().hex
    if conversation.get("status") in BUSY_STATUSES or not await try_claim(cid, run_id):
        return web.json_response({"error": "Wait for Loma to finish, or stop it, before editing"}, status=409)
    try:
        # Re-read under the claim: a run may have finished and added to the thread.
        conversation = await db.conversations.find_one({"conversation_id": cid})
        if conversation.get("status") in BUSY_STATUSES:
            return web.json_response({"error": "Wait for Loma to finish, or stop it, before editing"}, status=409)
        messages = conversation.get("messages") or []
        position = find_user_message(messages, body["user_index"], body["content"])
        cut_at = messages[position].get("timestamp") if position is not None else None
        if not isinstance(cut_at, datetime):
            return web.json_response({"error": "This chat changed. Reload it and try again."}, status=409)

        rewind_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        moved = {}
        for name in ("turns", "artifacts"):
            source, archive = db[name], db[f"rewound_{name}"]
            query = {"conversation_id": cid, "timestamp": {"$gte": cut_at}}
            docs = await source.find(query).to_list(None)
            if docs:
                await archive.insert_many([
                    {**{k: v for k, v in doc.items() if k != "_id"}, "rewind_id": rewind_id, "original_id": doc["_id"]}
                    for doc in docs])
                await source.delete_many({"_id": {"$in": [doc["_id"] for doc in docs]}})
            moved[name] = len(docs)

        kept, removed = messages[:position], messages[position:]
        await db.conversations.update_one(
            {"conversation_id": cid},
            {
                "$set": {"messages": kept, "final_response": _last_reply(kept), "error": None},
                "$unset": {"task_summary": "", "needs_you_reason": ""},
                "$push": {"rewinds": {"rewind_id": rewind_id, "at": now, "by": user_email,
                                      "from_message": position, "messages": removed}},
            },
        )
    finally:
        await release_claim(cid, run_id)

    logger.info("Rewound %s to message %d (%d turns, %d artifacts set aside)",
                cid, position, moved["turns"], moved["artifacts"])
    return web.json_response({"rewound": True, "kept_messages": len(kept), "rewind_id": rewind_id})


async def handle_branch(request: web.Request) -> web.Response:
    """POST /api/conversations/{id}/branch {before_user_index?, content?}

    A new chat holding this one up to just before that user message (the whole
    chat when it's omitted), with its steps and artifacts. The caller owns it."""
    from api.routes import _check_conversation_access
    from api.task_routes import task_access

    db, source, error = await _load(request)
    if error:
        return error
    user_email = get_user_email(request)
    role = get_system_role(request)
    if not _check_conversation_access(source, user_email, role):
        can_view, _, _ = await task_access(db, source, user_email, role)
        if not can_view:
            return web.json_response({"error": "Not found"}, status=404)

    body = await _body(request)
    if body is None:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    messages = source.get("messages") or []
    before = body.get("before_user_index")
    cut_at = None
    if before is not None:
        if not isinstance(before, int) or not isinstance(body.get("content"), str):
            return web.json_response({"error": "before_user_index needs the message's content"}, status=400)
        position = find_user_message(messages, before, body["content"])
        cut_at = messages[position].get("timestamp") if position is not None else None
        if not isinstance(cut_at, datetime):
            return web.json_response({"error": "This chat changed. Reload it and try again."}, status=409)
        messages = messages[:position]
    if not any(m.get("role") == "assistant" for m in messages):
        return web.json_response({"error": "Nothing to branch yet"}, status=400)

    cid = source["conversation_id"]
    new_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    window = {"conversation_id": cid, **({"timestamp": {"$lt": cut_at}} if cut_at else {})}
    turns = await db.turns.find({**window, "deleted": {"$ne": True}}).sort("turn_number", 1).to_list(None)
    artifacts = await db.artifacts.find(window).to_list(None)
    title = source.get("title")
    metadata = copy.deepcopy(source.get("metadata") or {})
    metadata.update({"user_name": user_email, "visibility": "private"})
    doc = {
        "conversation_id": new_id,
        "source": "dashboard",
        "started_at": now,
        "finished_at": now,
        "duration_ms": None,
        "status": "completed",
        "metadata": metadata,
        "prompt": next((m.get("content", "") for m in messages if m.get("role") == "user"), ""),
        "model": source.get("model") or "",
        "total_turns": max((t.get("turn_number", 0) for t in turns), default=0),
        "final_response": _last_reply(messages),
        "messages": copy.deepcopy(messages),
        "error": None,
        "deleted": False,
        "title": f"{title} (branch)" if title else None,
        "title_edited": bool(title),
        "tool_config": source.get("tool_config"),
        "branched_from_conversation_id": cid,
        "branched_at": now,
    }
    await db.conversations.insert_one(doc)
    for collection, docs in (("turns", turns), ("artifacts", artifacts)):
        if docs:
            await db[collection].insert_many(
                [{k: v for k, v in {**d, "conversation_id": new_id}.items() if k != "_id"} for d in docs])
    return web.json_response({"conversation_id": new_id, "title": doc["title"]}, status=201)


def setup_chat_rewind_routes(app: web.Application) -> None:
    app.router.add_post("/api/conversations/{conversation_id}/rewind", handle_rewind)
    app.router.add_post("/api/conversations/{conversation_id}/branch", handle_branch)
