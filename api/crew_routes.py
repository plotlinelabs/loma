"""Personal crew settings, controls and the fleet view (/api/crew*)."""

import json
import logging

from aiohttp import web

from api.auth_helpers import get_user_email
from fleet import service as svc
from observability.db import get_db

logger = logging.getLogger(__name__)


def _json(payload, status=200):
    return web.json_response(json.loads(json.dumps(payload, default=str)), status=status)


async def _ctx(request):
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(text="Database unavailable")
    email = get_user_email(request)
    if not email:
        raise web.HTTPUnauthorized(text="Authentication required")
    return db, email


async def _status(request, db, email):
    crew = await svc.get_crew(db, email)
    view = svc.crew_view(crew)
    payload = {"enabled": svc.crews_enabled(),
               "dispatcher_running": request.app.get("crew_dispatcher_running", False),
               "crew": view, "spend_today_usd": 0.0, "tasks_today": 0, "running": 0,
               "needs_you": 0}
    if crew:
        payload["spend_today_usd"] = await svc.spend_today(db, crew)
        payload["tasks_today"] = await svc.created_today(db, crew)
        payload["running"] = await db.conversations.count_documents(
            {"fleet.crew_id": crew["crew_id"], "fleet.state": "running"})
        payload["needs_you"] = await db.conversations.count_documents(
            {"fleet.crew_id": crew["crew_id"], "fleet.state": "blocked"})
    return payload


async def handle_get(request):
    db, email = await _ctx(request)
    return _json(await _status(request, db, email))


async def handle_put(request):
    db, email = await _ctx(request)
    try:
        data = await request.json()
        await svc.save_crew(db, email, data)
    except (ValueError, json.JSONDecodeError) as e:
        return _json({"error": str(e)}, 400)
    return _json(await _status(request, db, email))


async def handle_action(request):
    from fleet import dispatcher
    db, email = await _ctx(request)
    action = request.match_info["action"]
    try:
        if action in ("activate", "pause", "stop"):
            if action == "activate" and not svc.crews_enabled():
                raise svc.FleetError("Crews are turned off on this server (LOMA_CREWS_ENABLED)")
            if action == "activate" and not await svc.get_crew(db, email):
                await svc.save_crew(db, email, {})
            await svc.set_state(db, email, {"activate": "active", "pause": "paused",
                                            "stop": "stopped"}[action])
        elif action in ("run-plan", "run-report"):
            crew = await svc.get_crew(db, email)
            if not crew or crew.get("state") != "active":
                raise svc.FleetError("Turn the crew on first")
            fn = dispatcher.start_plan if action == "run-plan" else dispatcher.start_report
            doc, _ = await fn(db, crew)
            payload = await _status(request, db, email)
            payload["started"] = doc["conversation_id"]
            return _json(payload)
        else:
            return _json({"error": "Unknown action"}, 404)
    except svc.FleetError as e:
        return _json({"error": str(e)}, 400)
    return _json(await _status(request, db, email))


async def handle_tree(request):
    db, email = await _ctx(request)
    try:
        days = min(14, max(1, int(request.query.get("days", "3"))))
    except ValueError:
        days = 3
    return _json({"roots": await svc.tree(db, email, days)})


def setup_crew_routes(app: web.Application):
    from fleet.dispatcher import lifecycle
    app.router.add_get("/api/crew", handle_get)
    app.router.add_put("/api/crew", handle_put)
    app.router.add_get("/api/crew/tree", handle_tree)
    app.router.add_post("/api/crew/actions/{action}", handle_action)
    app.cleanup_ctx.append(lifecycle)
