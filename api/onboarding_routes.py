"""Onboarding tracker routes.

GET    /api/onboarding/config                  stages, field definitions, can_edit
GET    /api/onboarding/records                 all records with computed columns
POST   /api/onboarding/records                 create {name, account?, stage?, fields?}
GET    /api/onboarding/records/{record_id}     record + activity + sibling apps
PATCH  /api/onboarding/records/{record_id}     {changes: {...}, note?}

Every signed-in user can view. Editing needs `edit_min_role` from the
config (default: every signed-in user). Dashboard edits are logged with
source `human`, so agent syncs never overwrite them.
"""

import logging

from aiohttp import web

from api.auth_helpers import ROLE_HIERARCHY, get_system_role, get_user_email
from observability import onboarding as svc
from observability.db import get_db

logger = logging.getLogger(__name__)


def _ctx(request):
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(
            text='{"error": "Observability not configured"}', content_type="application/json")
    email = get_user_email(request)
    if not email:
        raise web.HTTPUnauthorized(
            text='{"error": "Authentication required"}', content_type="application/json")
    return db, email


def _can_edit(request, cfg: dict) -> bool:
    need = ROLE_HIERARCHY.get(cfg.get("edit_min_role") or "chatter", 1)
    return ROLE_HIERARCHY.get(get_system_role(request), 0) >= need


def _require_edit(request, cfg: dict):
    if not _can_edit(request, cfg):
        raise web.HTTPForbidden(
            text='{"error": "You do not have edit access to Onboarding"}',
            content_type="application/json")


async def _json_body(request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text='{"error": "Invalid JSON"}', content_type="application/json")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text='{"error": "Expected an object"}', content_type="application/json")
    return body


async def handle_config(request: web.Request) -> web.Response:
    db, _ = _ctx(request)
    cfg = await svc.get_config(db)
    return web.json_response({**cfg, "can_edit": _can_edit(request, cfg)})


async def handle_list(request: web.Request) -> web.Response:
    db, _ = _ctx(request)
    cfg = await svc.get_config(db)
    records = await svc.list_records(db)
    return web.json_response({"records": [svc.serialize(r, cfg) for r in records]})


async def handle_get(request: web.Request) -> web.Response:
    db, _ = _ctx(request)
    cfg = await svc.get_config(db)
    record = await svc.get_record(db, request.match_info["record_id"])
    if not record:
        return web.json_response({"error": "Not found"}, status=404)
    events = await svc.list_events(db, record["record_id"])
    siblings = []
    if record.get("account_lower"):
        siblings = await db.onboarding_records.find(
            {"account_lower": record["account_lower"], "record_id": {"$ne": record["record_id"]},
             "archived": {"$ne": True}},
            {"_id": 0, "record_id": 1, "name": 1, "stage": 1},
        ).to_list(50)
    return web.json_response({
        "record": svc.serialize(record, cfg),
        "events": [svc.serialize_event(e) for e in events],
        "siblings": siblings,
    })


async def handle_create(request: web.Request) -> web.Response:
    db, email = _ctx(request)
    cfg = await svc.get_config(db)
    _require_edit(request, cfg)
    body = await _json_body(request)
    try:
        record = await svc.create_record(db, body, actor=email)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"record": svc.serialize(record, cfg)}, status=201)


async def handle_update(request: web.Request) -> web.Response:
    db, email = _ctx(request)
    cfg = await svc.get_config(db)
    _require_edit(request, cfg)
    record = await svc.get_record(db, request.match_info["record_id"])
    if not record:
        return web.json_response({"error": "Not found"}, status=404)
    body = await _json_body(request)
    changes = body.get("changes") or {}
    if not isinstance(changes, dict):
        return web.json_response({"error": "changes must be an object"}, status=400)
    try:
        updated, applied, _ = await svc.apply_changes(
            db, record, changes, actor=email, note=(body.get("note") or "").strip() or None)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"record": svc.serialize(updated, cfg), "applied": applied})


def setup_onboarding_routes(app: web.Application):
    app.router.add_get("/api/onboarding/config", handle_config)
    app.router.add_get("/api/onboarding/records", handle_list)
    app.router.add_post("/api/onboarding/records", handle_create)
    app.router.add_get("/api/onboarding/records/{record_id}", handle_get)
    app.router.add_patch("/api/onboarding/records/{record_id}", handle_update)
