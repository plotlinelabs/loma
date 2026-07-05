"""Personal AI-usage routes — the current user's own spend and tokens.

Unlike /api/cost-stats and /api/token-usage (org-wide, analytics), these
answer "what am *I* spending?" and need no special role. Ownership is
matched on metadata.user_name, which holds the user's email for dashboard
chats (board-task runs fired headlessly included); flow/webhook runs are
nobody's personal usage and are excluded, mirroring token-usage.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web

from api.auth_helpers import ROLE_HIERARCHY, get_system_role, get_user_email
from observability.db import get_db

logger = logging.getLogger(__name__)


async def handle_my_usage(request: web.Request) -> web.Response:
    """GET /api/usage/me?days=30 — the caller's spend, tokens, and top chats."""
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    days = min(max(int(request.query.get("days", 30)), 1), 365)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    # Deleted chats still cost money — no deleted filter, the number is honest.
    match = {
        "started_at": {"$gte": since},
        "cost": {"$ne": None},
        "metadata.user_name": user_email,
        "source": {"$nin": ["flow", "webhook"]},
    }

    pipeline = [
        {"$match": match},
        {"$facet": {
            "totals": [
                {"$group": {
                    "_id": None,
                    "total_cost_usd": {"$sum": {"$ifNull": ["$cost.total_cost_usd", 0]}},
                    "input_tokens": {"$sum": {"$ifNull": ["$cost.input_tokens", 0]}},
                    "output_tokens": {"$sum": {"$ifNull": ["$cost.output_tokens", 0]}},
                    "conversations": {"$sum": 1},
                }},
            ],
            "daily": [
                {"$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$started_at"}},
                    "total_cost_usd": {"$sum": {"$ifNull": ["$cost.total_cost_usd", 0]}},
                    "input_tokens": {"$sum": {"$ifNull": ["$cost.input_tokens", 0]}},
                    "output_tokens": {"$sum": {"$ifNull": ["$cost.output_tokens", 0]}},
                    "conversations": {"$sum": 1},
                }},
                {"$sort": {"_id": 1}},
            ],
            "top_chats": [
                {"$sort": {"cost.total_cost_usd": -1}},
                {"$limit": 5},
                {"$project": {
                    "_id": 0,
                    "conversation_id": 1,
                    "title": 1,
                    "prompt": {"$substrCP": [{"$ifNull": ["$prompt", ""]}, 0, 100]},
                    "started_at": 1,
                    "status": 1,
                    "total_cost_usd": {"$ifNull": ["$cost.total_cost_usd", 0]},
                    "input_tokens": {"$ifNull": ["$cost.input_tokens", 0]},
                    "output_tokens": {"$ifNull": ["$cost.output_tokens", 0]},
                }},
            ],
        }},
    ]

    result = await db.conversations.aggregate(pipeline).to_list(1)
    facets = result[0] if result else {}
    totals = (facets.get("totals") or [{}])[0] if facets.get("totals") else {}
    totals.pop("_id", None)
    daily = [
        {"date": d.pop("_id"), **d}
        for d in facets.get("daily") or []
    ]
    top_chats = []
    for chat in facets.get("top_chats") or []:
        started = chat.get("started_at")
        if isinstance(started, datetime):
            chat["started_at"] = started.isoformat()
        top_chats.append(chat)

    return web.json_response({
        "days": days,
        "totals": {
            "total_cost_usd": totals.get("total_cost_usd", 0),
            "input_tokens": totals.get("input_tokens", 0),
            "output_tokens": totals.get("output_tokens", 0),
            "conversations": totals.get("conversations", 0),
        },
        "daily": daily,
        "top_chats": top_chats,
    })


async def handle_conversation_cost(request: web.Request) -> web.Response:
    """GET /api/conversations/{conversation_id}/cost — lightweight cost poll.

    The chat page's cost chip refreshes on this; the full conversation
    payload (messages, turns) would be wasteful at poll frequency.
    """
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    conversation_id = request.match_info["conversation_id"]
    doc = await db.conversations.find_one(
        {"conversation_id": conversation_id},
        {"cost": 1, "total_turns": 1, "status": 1, "metadata.user_name": 1, "source": 1},
    )
    if doc is None:
        return web.json_response({"error": "Not found"}, status=404)

    # Owners see their own chats; analysts and above see everything (same
    # visibility they already have through the conversations list).
    owner = (doc.get("metadata") or {}).get("user_name", "")
    is_analyst_up = ROLE_HIERARCHY.get(get_system_role(request), 0) >= ROLE_HIERARCHY["analyst"]
    if owner != user_email and not is_analyst_up:
        return web.json_response({"error": "Forbidden"}, status=403)

    cost = doc.get("cost") or {}
    return web.json_response({
        "total_cost_usd": cost.get("total_cost_usd", 0),
        "input_tokens": cost.get("input_tokens", 0),
        "output_tokens": cost.get("output_tokens", 0),
        "total_turns": doc.get("total_turns", 0),
        "status": doc.get("status"),
    })


def setup_my_usage_routes(app: web.Application):
    """Register personal usage routes."""
    app.router.add_get("/api/usage/me", handle_my_usage)
    app.router.add_get("/api/conversations/{conversation_id}/cost", handle_conversation_cost)
