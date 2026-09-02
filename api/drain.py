"""Drain mode — stop accepting new agent runs so a deploy can wait for the
in-flight ones to finish instead of killing them.

`scripts/deploy.sh` drives this: it builds the new images first, then flips
drain on, polls the running count until it reaches zero (bounded), and only
then recreates the containers. Without drain, `docker compose up` SIGTERMs
the backend mid-run and every active task/chat dies silently.

State is in-process (a restart clears it, which is exactly what we want).

Routes live under the public `/health` prefix so the deploy script can call
them without a dashboard session. The mutating verbs are loopback-only: the
script reaches them via `docker compose exec loma-backend curl ...`, while
traffic through nginx arrives from the proxy's container IP and is refused.
"""

import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web

from observability.db import get_db
from observability.observer import HEARTBEAT_INTERVAL_SECONDS

logger = logging.getLogger(__name__)

# A conversation only counts as "running" if its heartbeat is fresh. Docs
# stuck at status=running with a dead heartbeat (crashed worker, legacy rows)
# must never block a deploy forever.
RUNNING_HEARTBEAT_WINDOW_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 2

# What callers show the user when a new run is refused during drain.
DRAIN_MESSAGE = "Loma is restarting for a deploy. Please try again in a minute."

_LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")

_state: dict = {"draining": False, "since": None, "reason": ""}


def is_draining() -> bool:
    return bool(_state["draining"])


def drain_reason() -> str:
    """Free-text reason set by the drainer (e.g. "deploy abc1234"), or ""."""
    return _state["reason"] if _state["draining"] else ""


def set_draining(on: bool, reason: str = "") -> None:
    _state["draining"] = bool(on)
    _state["since"] = datetime.now(timezone.utc) if on else None
    _state["reason"] = (reason or "").strip()[:200] if on else ""
    logger.info("[DRAIN] %s%s", "enabled" if on else "disabled",
                f" ({_state['reason']})" if _state["reason"] else "")


def running_query(now: datetime | None = None) -> dict:
    """Mongo filter for conversations that are genuinely running right now."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=RUNNING_HEARTBEAT_WINDOW_SECONDS)
    return {"status": "running", "last_heartbeat": {"$gte": cutoff}}


async def running_summary(db) -> dict:
    """Count live runs and report the oldest one (for deploy logs)."""
    if db is None:
        return {"running": 0, "oldest_started_at": None}
    query = running_query()
    running = await db.conversations.count_documents(query)
    oldest_started_at = None
    if running:
        oldest = await db.conversations.find(
            query, {"started_at": 1},
        ).sort("started_at", 1).limit(1).to_list(1)
        if oldest and oldest[0].get("started_at"):
            oldest_started_at = oldest[0]["started_at"].isoformat()
    return {"running": running, "oldest_started_at": oldest_started_at}


def _is_loopback(request: web.Request) -> bool:
    return (request.remote or "") in _LOOPBACK


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — liveness probe (public)."""
    return web.json_response({"status": "ok", "draining": is_draining()})


async def handle_get_drain(request: web.Request) -> web.Response:
    """GET /health/drain — drain flag plus the live running count."""
    summary = await running_summary(get_db())
    since = _state["since"]
    return web.json_response({
        "draining": is_draining(),
        "reason": drain_reason(),
        "since": since.isoformat() if since else None,
        **summary,
    })


async def handle_set_drain(request: web.Request) -> web.Response:
    """POST /health/drain — start refusing new agent runs (loopback only)."""
    if not _is_loopback(request):
        return web.json_response({"error": "Forbidden"}, status=403)
    reason = ""
    if request.can_read_body:
        try:
            body = await request.json()
            reason = str((body or {}).get("reason") or "")
        except Exception:
            pass
    set_draining(True, reason)
    return await handle_get_drain(request)


async def handle_clear_drain(request: web.Request) -> web.Response:
    """DELETE /health/drain — resume accepting runs (aborted deploy)."""
    if not _is_loopback(request):
        return web.json_response({"error": "Forbidden"}, status=403)
    set_draining(False)
    return await handle_get_drain(request)


def setup_drain_routes(app: web.Application) -> None:
    app.router.add_get("/health", handle_health)
    app.router.add_get("/health/drain", handle_get_drain)
    app.router.add_post("/health/drain", handle_set_drain)
    app.router.add_delete("/health/drain", handle_clear_drain)
