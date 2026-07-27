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


async def handle_list_actions(request):
    service, actor = _context(request)
    actions, attention_accounts = await service.list_actions(actor)
    return web.json_response({
        "actions": _serialize(actions),
        "attention_accounts": _serialize(attention_accounts),
    })


async def handle_get_account(request):
    service, _ = _context(request)
    account = await service.get(request.match_info["account_id"])
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


def _find_work_item(account, item_id):
    return next(
        (item for item in account.get("work_items", []) if item["item_id"] == item_id),
        None,
    )


async def handle_create_work_item(request):
    service, actor = _context(request)
    account_id = request.match_info["account_id"]
    if not await service.repository.get(account_id):
        return web.json_response({"error": "Not found"}, status=404)
    try:
        account = await service.create_work_item(account_id, await _json(request), actor)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"account": _serialize(account)}, status=201)


async def handle_update_work_item(request):
    service, actor = _context(request)
    account_id = request.match_info["account_id"]
    account = await service.repository.get(account_id)
    item = _find_work_item(account or {}, request.match_info["item_id"])
    if not item:
        return web.json_response({"error": "Not found"}, status=404)
    try:
        updated = await service.update_work_item(
            account_id, item, await _json(request), actor
        )
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"account": _serialize(updated)})


async def handle_delete_work_item(request):
    service, actor = _context(request)
    account_id = request.match_info["account_id"]
    current = await service.repository.get(account_id)
    item = _find_work_item(current or {}, request.match_info["item_id"])
    if not item:
        return web.json_response({"error": "Not found"}, status=404)
    account = await service.delete_work_item(account_id, item, actor)
    return web.json_response({"account": _serialize(account)})


async def handle_create_activity(request):
    service, actor = _context(request)
    account_id = request.match_info["account_id"]
    if not await service.repository.get(account_id):
        return web.json_response({"error": "Not found"}, status=404)
    try:
        account = await service.create_activity(account_id, await _json(request), actor)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"account": _serialize(account)}, status=201)


async def handle_create_source_link(request):
    service, actor = _context(request)
    account_id = request.match_info["account_id"]
    if not await service.repository.get(account_id):
        return web.json_response({"error": "Not found"}, status=404)
    try:
        account = await service.create_source_link(account_id, await _json(request), actor)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"account": _serialize(account)}, status=201)


async def handle_delete_source_link(request):
    service, actor = _context(request)
    account_id = request.match_info["account_id"]
    current = await service.repository.get(account_id)
    link = next((link for link in (current or {}).get("source_links", [])
                 if link["link_id"] == request.match_info["link_id"]), None)
    if not link:
        return web.json_response({"error": "Not found"}, status=404)
    account = await service.delete_source_link(account_id, link, actor)
    return web.json_response({"account": _serialize(account)})


def setup_integration_hub_routes(app):
    app.router.add_post("/api/integration-hub/accounts", handle_create_account)
    app.router.add_get("/api/integration-hub/accounts", handle_list_accounts)
    app.router.add_get("/api/integration-hub/actions", handle_list_actions)
    app.router.add_get("/api/integration-hub/accounts/{account_id}", handle_get_account)
    app.router.add_patch("/api/integration-hub/accounts/{account_id}", handle_update_account)
    app.router.add_post(
        "/api/integration-hub/accounts/{account_id}/work-items",
        handle_create_work_item,
    )
    app.router.add_patch(
        "/api/integration-hub/accounts/{account_id}/work-items/{item_id}",
        handle_update_work_item,
    )
    app.router.add_delete(
        "/api/integration-hub/accounts/{account_id}/work-items/{item_id}",
        handle_delete_work_item,
    )
    app.router.add_post(
        "/api/integration-hub/accounts/{account_id}/activities",
        handle_create_activity,
    )
    app.router.add_post(
        "/api/integration-hub/accounts/{account_id}/source-links",
        handle_create_source_link,
    )
    app.router.add_delete(
        "/api/integration-hub/accounts/{account_id}/source-links/{link_id}",
        handle_delete_source_link,
    )
