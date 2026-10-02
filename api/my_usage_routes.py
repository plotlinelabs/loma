"""Personal AI-usage routes — the current user's own spend and tokens.

Unlike /api/cost-stats and /api/token-usage (org-wide, analytics), these
answer "what am *I* spending?" and need no special role. Ownership is
matched on the conversation's metadata.user_name (copied onto each usage
event), which holds the user's email for dashboard chats (board-task runs
fired headlessly included); flow/webhook runs are nobody's personal usage
and are excluded, mirroring token-usage.

Windows and daily buckets are computed on `usage_events.at` — when each
run's usage was recorded — not on the conversation's `started_at`, so a chat
started yesterday and used today counts toward today.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web

from api.auth_helpers import ROLE_HIERARCHY, get_system_role, get_user_email
from observability.db import get_db
from observability.usage_ledger import (
    COLLECTION as USAGE_EVENTS,
    backfill_usage_events,
    ensure_usage_indexes,
)

logger = logging.getLogger(__name__)


def _parse_timezone(request: web.Request) -> str | None:
    """Validate an IANA timezone from ?tz= so daily buckets match the
    user's local days (Mongo groups in UTC otherwise)."""
    tz_param = request.query.get("tz", "").strip()
    if not tz_param:
        return None
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz_param)
        return tz_param
    except Exception:
        return None


def _usage_sums() -> dict:
    return {
        "total_cost_usd": {"$sum": {"$ifNull": ["$cost_usd", 0]}},
        "input_tokens": {"$sum": {"$ifNull": ["$input_tokens", 0]}},
        "output_tokens": {"$sum": {"$ifNull": ["$output_tokens", 0]}},
        "cache_read_tokens": {"$sum": {"$ifNull": ["$cache_read_tokens", 0]}},
        "cache_creation_tokens": {"$sum": {"$ifNull": ["$cache_creation_tokens", 0]}},
    }


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


async def handle_my_usage(request: web.Request) -> web.Response:
    """GET /api/usage/me?days=30 — the caller's spend, tokens, and top chats.

    ?since=<ISO> overrides days for exact windows (the dashboard sends the
    user's local midnight); ?tz=<IANA> aligns the daily buckets.
    """
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    try:
        days: int | None = min(max(int(request.query.get("days", 30)), 1), 365)
    except ValueError:
        return web.json_response({"error": "Invalid days"}, status=400)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_param = request.query.get("since", "").strip()
    if since_param:
        try:
            parsed = datetime.fromisoformat(since_param.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            # Sanity-bound custom windows the same way as ?days.
            floor = datetime.now(timezone.utc) - timedelta(days=365)
            since = max(parsed, floor)
            days = None
        except ValueError:
            return web.json_response({"error": "Invalid since timestamp"}, status=400)
    tz_name = _parse_timezone(request)
    # Deleted chats still cost money — events outlive the chat, the number is honest.
    match = {
        "user_email": user_email,
        "at": {"$gte": since},
        "source": {"$nin": ["flow", "webhook"]},
    }

    pipeline = [
        {"$match": match},
        {"$facet": {
            "totals": [
                {"$group": {
                    "_id": None,
                    **_usage_sums(),
                    "conversation_ids": {"$addToSet": "$conversation_id"},
                    "approx": {"$max": {"$cond": [{"$eq": ["$approx", True]}, 1, 0]}},
                }},
            ],
            "daily": [
                {"$group": {
                    "_id": {"$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$at",
                        **({"timezone": tz_name} if tz_name else {}),
                    }},
                    **_usage_sums(),
                    "conversation_ids": {"$addToSet": "$conversation_id"},
                }},
                {"$sort": {"_id": 1}},
            ],
            "top_chats": [
                {"$group": {
                    "_id": "$conversation_id",
                    **_usage_sums(),
                    "last_used_at": {"$max": "$at"},
                }},
                {"$sort": {"total_cost_usd": -1}},
                {"$limit": 5},
            ],
        }},
    ]

    result = await db[USAGE_EVENTS].aggregate(pipeline).to_list(1)
    facets = result[0] if result else {}
    totals = (facets.get("totals") or [{}])[0]
    daily = [
        {
            "date": d["_id"],
            "total_cost_usd": d.get("total_cost_usd", 0),
            "input_tokens": d.get("input_tokens", 0),
            "output_tokens": d.get("output_tokens", 0),
            "conversations": len(d.get("conversation_ids") or []),
        }
        for d in facets.get("daily") or []
    ]

    top_rows = facets.get("top_chats") or []
    details: dict[str, dict] = {}
    if top_rows:
        cursor = db.conversations.find(
            {"conversation_id": {"$in": [r["_id"] for r in top_rows]}},
            {"_id": 0, "conversation_id": 1, "title": 1, "prompt": 1,
             "started_at": 1, "status": 1},
        )
        async for doc in cursor:
            details[doc["conversation_id"]] = doc
    top_chats = []
    for row in top_rows:
        doc = details.get(row["_id"], {})
        top_chats.append({
            "conversation_id": row["_id"],
            "title": doc.get("title"),
            "prompt": (doc.get("prompt") or "")[:100],
            "started_at": _iso(doc.get("started_at")),
            "last_used_at": _iso(row.get("last_used_at")),
            "status": doc.get("status"),
            "total_cost_usd": row.get("total_cost_usd", 0),
            "input_tokens": row.get("input_tokens", 0),
            "output_tokens": row.get("output_tokens", 0),
        })

    return web.json_response({
        "days": days,
        "since": since.isoformat(),
        # Spend is attributed to when each run's usage was recorded.
        "basis": "run_recorded_at",
        # True when the window holds pre-ledger history, which is attributed
        # to the chat's start day (the old behaviour) rather than per run.
        "includes_approximate": bool(totals.get("approx")),
        "totals": {
            "total_cost_usd": totals.get("total_cost_usd", 0),
            "input_tokens": totals.get("input_tokens", 0),
            "output_tokens": totals.get("output_tokens", 0),
            "cache_read_tokens": totals.get("cache_read_tokens", 0),
            "cache_creation_tokens": totals.get("cache_creation_tokens", 0),
            "conversations": len(totals.get("conversation_ids") or []),
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
        "cache_read_tokens": cost.get("cache_read_tokens", 0),
        "cache_creation_tokens": cost.get("cache_creation_tokens", 0),
        "total_turns": doc.get("total_turns", 0),
        "status": doc.get("status"),
    })


async def _init_usage_ledger(app: web.Application):
    """Create ledger indexes and backfill pre-ledger spend in the background."""
    db = get_db()
    if db is None:
        return
    try:
        await ensure_usage_indexes(db)
    except Exception as e:
        logger.warning("Usage ledger: index creation failed: %s", e)

    async def _backfill():
        try:
            await backfill_usage_events(db)
        except Exception as e:
            logger.warning("Usage ledger: backfill failed: %s", e)

    # Keep a reference so the task isn't garbage-collected mid-run.
    app["usage_ledger_backfill_task"] = asyncio.create_task(_backfill())


def setup_my_usage_routes(app: web.Application):
    """Register personal usage routes."""
    app.router.add_get("/api/usage/me", handle_my_usage)
    app.router.add_get("/api/conversations/{conversation_id}/cost", handle_conversation_cost)
    app.on_startup.append(_init_usage_ledger)
