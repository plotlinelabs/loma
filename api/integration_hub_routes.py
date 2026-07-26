"""REST routes for the manual Integration Hub onboarding foundation."""

from datetime import datetime

from aiohttp import web

from api.auth_helpers import get_user_email
from integration_hub.models import HEALTH_STATES, STAGES, ValidationError
from integration_hub.repository import AccountRepository
from integration_hub.service import AccountService
from observability.db import get_db


def _serialize(value):
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {
            key: str(item) if key == "_id" else _serialize(item)
            for key, item in value.items()
        }
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _context(request):
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(
            text='{"error":"Observability not configured"}',
            content_type="application/json",
        )
    actor = get_user_email(request)
    if not actor:
        raise web.HTTPUnauthorized(
            text='{"error":"Authentication required"}',
            content_type="application/json",
        )
    return AccountService(AccountRepository(db)), actor


async def _json(request):
    try:
        return await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(
            text='{"error":"Invalid JSON"}', content_type="application/json"
        ) from exc


async def handle_create_account(request):
    service, actor = _context(request)
    try:
        account = await service.create(await _json(request), actor)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"account": _serialize(account)}, status=201)


async def handle_list_accounts(request):
    service, _ = _context(request)
    stage = request.query.get("stage")
    health = request.query.get("health")
    if stage and stage not in STAGES:
        return web.json_response({"error": "stage is invalid"}, status=400)
    if health and health not in HEALTH_STATES:
        return web.json_response({"error": "health is invalid"}, status=400)
    accounts = await service.list(
        stage=stage, health=health, search=request.query.get("search")
    )
    return web.json_response({"accounts": _serialize(accounts)})


async def handle_get_account(request):
    service, _ = _context(request)
    account = await service.repository.get(request.match_info["account_id"])
    if not account:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({"account": _serialize(account)})


async def handle_update_account(request):
    service, actor = _context(request)
    account = await service.repository.get(request.match_info["account_id"])
    if not account:
        return web.json_response({"error": "Not found"}, status=404)
    try:
        updated = await service.update(account, await _json(request), actor)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"account": _serialize(updated)})


def setup_integration_hub_routes(app):
    app.router.add_post("/api/integration-hub/accounts", handle_create_account)
    app.router.add_get("/api/integration-hub/accounts", handle_list_accounts)
    app.router.add_get("/api/integration-hub/accounts/{account_id}", handle_get_account)
    app.router.add_patch("/api/integration-hub/accounts/{account_id}", handle_update_account)
