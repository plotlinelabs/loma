"""Session-only decision API. Agent CLI deliberately has no decision command."""
import json
from aiohttp import web
from api import human_tasks as service
from api.task_routes import _serialize
from api.session_gateway import verify_dashboard_signature
from observability.db import get_db


async def handle(request):
    try:
        actor, raw = await verify_dashboard_signature(request)
        db = get_db()
        if db is None:
            raise web.HTTPServiceUnavailable(text="Database unavailable")
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        cid = request.match_info["conversation_id"]
        if request.method == "POST":
            doc = await service.respond(db, actor, cid, data)
        else:
            doc = await service.get(db, actor, cid)
        return web.json_response({"task": _serialize(service.view(doc)),
                                  "can_respond": actor == doc["metadata"]["user_name"]})
    except (ValueError, web.HTTPException) as exc:
        return web.json_response({"error": str(exc)}, status=getattr(exc, "status", 400))


def setup_human_task_routes(app):
    from api.human_task_worker import lifecycle
    app.router.add_get("/api/human-tasks/{conversation_id}", handle)
    app.router.add_post("/api/human-tasks/{conversation_id}", handle)
    app.cleanup_ctx.append(lifecycle)
