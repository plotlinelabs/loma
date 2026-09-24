"""Google source endpoints and authenticated skill read context."""
from aiohttp import web
from api import skill_service, skill_sync_service as sync
from api.auth_helpers import get_user_email, require_analyst_or_above, require_maintainer_or_above
from observability.db import get_db


@web.middleware
async def skill_context(request, handler):
    actor = skill_service.skill_actor.set(get_user_email(request) or None)
    dashboard = skill_service.skill_dashboard.set(True)
    try:
        return await handler(request)
    except skill_service.SkillError as exc:
        return web.json_response({"error": str(exc), "code": getattr(exc, "code", "skill_error")}, status=exc.status)
    finally:
        skill_service.skill_actor.reset(actor)
        skill_service.skill_dashboard.reset(dashboard)


async def capabilities(request):
    require_analyst_or_above(request)
    return web.json_response({"enabled": sync.enabled("google_sheet" if "google-sheets" in request.path else "google_doc")})


async def source_action(request):
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable()
    actor = get_user_email(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise skill_service.SkillError("Expected a JSON object")
    source_type = "google_sheet" if "google-sheets" in request.path else "google_doc"
    action = request.match_info["action"]
    if action == "preview":
        if source_type == "google_sheet":
            result = await sync.preview_sheet(db, actor, body.get("url", ""), body.get("tab_id"), body.get("header_row", False))
        else:
            result = await sync.preview(db, actor, body.get("url", ""), body.get("tab_id"))
    elif action == "import":
        fields = {key: body.get(key) for key in ("url", "tab_id", "slug", "name", "description", "preview_hash")}
        if not all(isinstance(fields[k], str) for k in fields):
            raise skill_service.SkillError("Import fields must be strings")
        result = await sync.import_doc(db, actor, **fields, scope=body.get("scope", "personal"), confirm_workspace=body.get("confirm_workspace") is True, source_type=source_type, header_row=body.get("header_row", False))
    else:
        raise web.HTTPNotFound()
    return web.json_response(result)


async def manage_source(request):
    require_maintainer_or_above(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise skill_service.SkillError("Expected a JSON object")
    slug = skill_service.slugify(request.match_info["name"])
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable()
    if body.get("action") == "sync":
        result = await sync.sync(db, slug, get_user_email(request))
    else:
        result = await sync.configure(db, slug, get_user_email(request), body.get("action"))
    return web.json_response(result)


async def sync_history(request):
    require_analyst_or_above(request)
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable()
    return web.json_response({"history": await sync.history(db, request.match_info["name"], get_user_email(request))})


def setup_skill_source_routes(app):
    app.router.add_get("/api/skill-sources/google-docs", capabilities)
    app.router.add_post("/api/skill-sources/google-docs/{action}", source_action)
    app.router.add_get("/api/skill-sources/google-sheets", capabilities)
    app.router.add_post("/api/skill-sources/google-sheets/{action}", source_action)
    app.router.add_post("/api/skills/{name}/source", manage_source)
    app.router.add_get("/api/skills/{name}/source/history", sync_history)
