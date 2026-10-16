"""Search your chats and tasks by title and message text, for the Cmd+K palette.

An empty query lists your most recent chats instead.
"""

import re
from datetime import datetime

from aiohttp import web

from api.auth_helpers import get_user_email
from observability.db import get_db

MAX_RESULTS = 20
SNIPPET_RADIUS = 70


def snippet(text: str, pattern: re.Pattern) -> str | None:
    """The match with some text either side, on one line."""
    match = pattern.search(text or "")
    if not match:
        return None
    start = max(0, match.start() - SNIPPET_RADIUS)
    end = min(len(text), match.end() + SNIPPET_RADIUS)
    body = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def _last_active(doc: dict) -> str | None:
    value = doc.get("last_heartbeat") or doc.get("finished_at") or doc.get("started_at")
    return value.isoformat() if isinstance(value, datetime) else None


def result_for(doc: dict, pattern: re.Pattern | None) -> dict:
    found, role = None, None
    if pattern is not None and not pattern.search(doc.get("title") or ""):
        # The newest message that mentions it, so a long chat shows its latest context.
        for message in reversed(doc.get("messages") or []):
            found = snippet(message.get("content") or "", pattern)
            if found:
                role = message.get("role")
                break
        if found is None:
            for field in ("final_response", "prompt"):
                found = snippet(doc.get(field) or "", pattern)
                if found:
                    role = "assistant" if field == "final_response" else "user"
                    break
    return {
        "conversation_id": doc["conversation_id"],
        "title": doc.get("title") or (doc.get("prompt") or "")[:80] or None,
        "snippet": found,
        "snippet_role": role,
        "task_status": doc.get("task_status"),
        "task_board_id": (doc.get("task_board_id") or "personal") if doc.get("task_status") else None,
        "last_active": _last_active(doc),
    }


async def handle_search_chats(request: web.Request) -> web.Response:
    """GET /api/conversations/search?q=&limit= — the caller's own chats and tasks."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    q = (request.query.get("q") or "").strip()[:200]
    try:
        limit = max(1, min(MAX_RESULTS, int(request.query.get("limit", MAX_RESULTS))))
    except ValueError:
        limit = MAX_RESULTS

    query: dict = {"metadata.user_name": user_email, "deleted": {"$ne": True}, "human_task": None}
    pattern = None
    if q:
        pattern = re.compile(re.escape(q), re.IGNORECASE)
        regex = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"title": regex}, {"prompt": regex}, {"final_response": regex},
                        {"messages.content": regex}]
    projection = {"conversation_id": 1, "title": 1, "prompt": 1, "task_status": 1, "task_board_id": 1,
                  "last_heartbeat": 1, "finished_at": 1, "started_at": 1}
    if q:
        projection.update({"final_response": 1, "messages.content": 1, "messages.role": 1})
    docs = await db.conversations.find(query, projection) \
        .sort([("last_heartbeat", -1), ("started_at", -1)]).limit(limit).to_list(limit)
    return web.json_response({"query": q, "results": [result_for(doc, pattern) for doc in docs]})
