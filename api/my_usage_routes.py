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


# ?sort= values for the chat list -> (field, direction). _id breaks ties so
# pagination is stable.
CHAT_SORTS = {
    "cost_desc": ("total_cost_usd", -1),
    "cost_asc": ("total_cost_usd", 1),
    "recent": ("last_used_at", -1),
}
MAX_CHAT_PAGE = 100
MAX_RUNS = 500
UNKNOWN_MODEL = "unknown"
# Excluded from personal usage: nobody's own spend.
EXCLUDED_SOURCES = ["flow", "webhook"]


def _usage_sums() -> dict:
    return {
        "total_cost_usd": {"$sum": {"$ifNull": ["$cost_usd", 0]}},
        "input_tokens": {"$sum": {"$ifNull": ["$input_tokens", 0]}},
        "output_tokens": {"$sum": {"$ifNull": ["$output_tokens", 0]}},
        "cache_read_tokens": {"$sum": {"$ifNull": ["$cache_read_tokens", 0]}},
        "cache_creation_tokens": {"$sum": {"$ifNull": ["$cache_creation_tokens", 0]}},
        "runs": {"$sum": 1},
        # Rows written before cost_known existed are priced, so only an
        # explicit False counts.
        "unpriced_runs": {"$sum": {"$cond": [{"$eq": ["$cost_known", False]}, 1, 0]}},
        "unpriced_tokens": {"$sum": {"$cond": [
            {"$eq": ["$cost_known", False]},
            {"$add": [{"$ifNull": ["$input_tokens", 0]}, {"$ifNull": ["$output_tokens", 0]}]},
            0,
        ]}},
    }


def _sum_fields(row: dict) -> dict:
    return {
        k: row.get(k, 0) or 0
        for k in ("total_cost_usd", "input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_creation_tokens", "runs", "unpriced_runs", "unpriced_tokens")
    }


def _model_name(model) -> str:
    return model or UNKNOWN_MODEL


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _parse_window(request: web.Request) -> tuple[datetime, int | None] | web.Response:
    """?since=<ISO> (exact start, e.g. local midnight) beats ?days=N."""
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
            # Cap like ?days= so a stray value can't scan everything.
            floor = datetime.now(timezone.utc) - timedelta(days=365)
            since = max(parsed, floor)
            days = None
        except ValueError:
            return web.json_response({"error": "Invalid since timestamp"}, status=400)
    return since, days


def _parse_int(request: web.Request, name: str, default: int, lo: int, hi: int) -> int | None:
    try:
        return min(max(int(request.query.get(name, default)), lo), hi)
    except ValueError:
        return None


async def handle_my_usage(request: web.Request) -> web.Response:
    """GET /api/usage/me — the caller's spend, tokens, models and chats.

    ?since=<ISO> or ?days=N set the window; ?tz=<IANA> aligns daily buckets.
    The chat list is every chat with spend in the window, paginated:
    ?sort=cost_desc|cost_asc|recent (default cost_desc), ?limit=, ?offset=.
    """
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    window = _parse_window(request)
    if isinstance(window, web.Response):
        return window
    since, days = window
    sort_key = request.query.get("sort", "cost_desc")
    if sort_key not in CHAT_SORTS:
        return web.json_response({"error": "Invalid sort"}, status=400)
    limit = _parse_int(request, "limit", 25, 1, MAX_CHAT_PAGE)
    offset = _parse_int(request, "offset", 0, 0, 100_000)
    if limit is None or offset is None:
        return web.json_response({"error": "Invalid limit/offset"}, status=400)
    sort_field, sort_dir = CHAT_SORTS[sort_key]
    tz_name = _parse_timezone(request)

    # Deleted chats still cost money — events outlive the chat, the number is honest.
    match = {
        "user_email": user_email,
        "at": {"$gte": since},
        "source": {"$nin": EXCLUDED_SOURCES},
    }
    per_chat = {"$group": {
        "_id": "$conversation_id",
        **_usage_sums(),
        "last_used_at": {"$max": "$at"},
        "models": {"$addToSet": "$model"},
        "approx": {"$max": {"$cond": [{"$eq": ["$approx", True]}, 1, 0]}},
    }}

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
            "by_model": [
                {"$group": {
                    "_id": "$model",
                    **_usage_sums(),
                    "conversation_ids": {"$addToSet": "$conversation_id"},
                }},
                {"$sort": {"total_cost_usd": -1}},
            ],
            "chats": [
                per_chat,
                {"$sort": {sort_field: sort_dir, "_id": 1}},
                {"$skip": offset},
                {"$limit": limit},
            ],
            "chats_total": [
                {"$group": {"_id": "$conversation_id"}},
                {"$count": "n"},
            ],
        }},
    ]

    result = await db[USAGE_EVENTS].aggregate(pipeline).to_list(1)
    facets = result[0] if result else {}
    totals = (facets.get("totals") or [{}])[0]
    daily = [
        {
            "date": d["_id"],
            **_sum_fields(d),
            "conversations": len(d.get("conversation_ids") or []),
        }
        for d in facets.get("daily") or []
    ]

    # "" and missing model both mean unknown — merge them.
    by_model: dict[str, dict] = {}
    for row in facets.get("by_model") or []:
        name = _model_name(row.get("_id"))
        entry = by_model.setdefault(name, {"model": name, "conversation_ids": set(),
                                           **{k: 0 for k in _sum_fields({})}})
        for k, v in _sum_fields(row).items():
            entry[k] += v
        entry["conversation_ids"].update(row.get("conversation_ids") or [])
    models = sorted(
        ({**{k: v for k, v in m.items() if k != "conversation_ids"},
          "conversations": len(m["conversation_ids"])} for m in by_model.values()),
        key=lambda m: (-m["total_cost_usd"], -m["runs"]),
    )

    chat_rows = facets.get("chats") or []
    details: dict[str, dict] = {}
    if chat_rows:
        cursor = db.conversations.find(
            {"conversation_id": {"$in": [r["_id"] for r in chat_rows]}},
            {"_id": 0, "conversation_id": 1, "title": 1, "prompt": 1,
             "started_at": 1, "status": 1, "deleted": 1},
        )
        async for doc in cursor:
            details[doc["conversation_id"]] = doc
    chats = []
    for row in chat_rows:
        doc = details.get(row["_id"], {})
        chats.append({
            "conversation_id": row["_id"],
            "title": doc.get("title"),
            "prompt": (doc.get("prompt") or "")[:100],
            "started_at": _iso(doc.get("started_at")),
            "last_used_at": _iso(row.get("last_used_at")),
            "status": doc.get("status"),
            "deleted": bool(doc.get("deleted")) or not doc,
            **_sum_fields(row),
            "models": sorted({_model_name(m) for m in row.get("models") or []}),
            "approx": bool(row.get("approx")),
        })
    chats_total = ((facets.get("chats_total") or [{}])[0]).get("n", 0)

    return web.json_response({
        "days": days,
        "since": since.isoformat(),
        # Spend is attributed to when each run's usage was recorded.
        "basis": "run_recorded_at",
        # True when the window holds pre-ledger history, which is attributed
        # to the chat's start day (the old behaviour) rather than per run.
        "includes_approximate": bool(totals.get("approx")),
        "totals": {
            **_sum_fields(totals),
            "conversations": len(totals.get("conversation_ids") or []),
        },
        "daily": daily,
        "by_model": models,
        "chats": chats,
        "chats_total": chats_total,
        "chats_sort": sort_key,
        "chats_limit": limit,
        "chats_offset": offset,
        # Back-compat for older dashboards.
        "top_chats": chats[:5] if sort_key == "cost_desc" and offset == 0 else [],
    })


async def handle_my_chat_runs(request: web.Request) -> web.Response:
    """GET /api/usage/me/chats/{conversation_id}/runs — one row per run.

    Scoped by the ledger's user_email, so callers only ever see their own
    runs; same ?since=/?days= window as /api/usage/me.
    """
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Not authenticated"}, status=401)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    window = _parse_window(request)
    if isinstance(window, web.Response):
        return window
    since, _ = window

    cursor = db[USAGE_EVENTS].find(
        {
            "conversation_id": request.match_info["conversation_id"],
            "user_email": user_email,
            "at": {"$gte": since},
            "source": {"$nin": EXCLUDED_SOURCES},
        },
        {"_id": 0, "at": 1, "model": 1, "runtime": 1, "source": 1, "input_tokens": 1,
         "output_tokens": 1, "cache_read_tokens": 1, "cache_creation_tokens": 1,
         "cost_usd": 1, "cost_known": 1, "approx": 1},
    ).sort("at", -1).limit(MAX_RUNS)
    runs = []
    async for ev in cursor:
        runs.append({
            "at": _iso(ev.get("at")),
            "model": _model_name(ev.get("model")),
            "runtime": ev.get("runtime") or None,
            "source": ev.get("source"),
            "input_tokens": ev.get("input_tokens", 0),
            "output_tokens": ev.get("output_tokens", 0),
            "cache_read_tokens": ev.get("cache_read_tokens", 0),
            "cache_creation_tokens": ev.get("cache_creation_tokens", 0),
            "cost_usd": ev.get("cost_usd", 0),
            "cost_known": ev.get("cost_known", True) is not False,
            "approx": bool(ev.get("approx")),
        })
    return web.json_response({"runs": runs, "truncated": len(runs) == MAX_RUNS})


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
    app.router.add_get("/api/usage/me/chats/{conversation_id}/runs", handle_my_chat_runs)
    app.router.add_get("/api/conversations/{conversation_id}/cost", handle_conversation_cost)
    app.on_startup.append(_init_usage_ledger)
