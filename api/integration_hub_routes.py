"""Contract-compliant REST routes for Integration Hub Phase 1."""
import os
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from aiohttp import web

from api.auth_helpers import get_system_role, get_user_email
from integration_hub.models import HEALTH_STATES, PLAYBOOKS, STAGES, ValidationError
from integration_hub.repository import AccountRepository
from integration_hub.service import AccountService
from observability.db import get_db

_RATE_BUCKETS = defaultdict(deque)
_RATE_LIMIT = int(os.environ.get("INTEGRATION_HUB_RATE_LIMIT", "120"))


def _enabled():
    value = os.environ.get("INTEGRATION_HUB_ENABLED")
    if value is not None:
        return value.lower() in ("1", "true", "yes")
    return os.environ.get("PREVIEW_MODE", "").lower() in ("1", "true", "yes") or os.environ.get("ENV", "").upper() == "DEV"


def _serialize(value):
    if isinstance(value, list): return [_serialize(item) for item in value]
    if isinstance(value, dict): return {key: str(item) if key == "_id" else _serialize(item) for key, item in value.items()}
    if isinstance(value, datetime): return value.isoformat()
    return value


def _error(request, status, code, message, details=None):
    return web.json_response({"error": {"code": code, "message": message, "details": details or {},
                                        "request_id": request["request_id"]}}, status=status,
                             headers={"X-Request-ID": request["request_id"]})


def _ok(request, payload, status=200, version=None):
    headers = {"X-Request-ID": request["request_id"]}
    if version is not None: headers["ETag"] = f'"{version}"'
    return web.json_response(_serialize(payload), status=status, headers=headers)


def _context(request):
    request["request_id"] = request.headers.get("X-Request-ID", "").strip() or str(uuid.uuid4())
    if not _enabled(): raise web.HTTPNotFound()
    db = get_db()
    if db is None: raise web.HTTPServiceUnavailable(text="Integration Hub storage unavailable")
    actor = get_user_email(request)
    if not actor: raise web.HTTPUnauthorized(text="Authentication required")
    now = time.monotonic(); bucket = _RATE_BUCKETS[actor]
    while bucket and bucket[0] < now - 60: bucket.popleft()
    if len(bucket) >= _RATE_LIMIT: raise web.HTTPTooManyRequests(headers={"Retry-After": "60"})
    bucket.append(now)
    return AccountService(AccountRepository(db)), actor, get_system_role(request)


def _module_role(request):
    if get_system_role(request) == "admin": return "Admin"
    return (((request.get("user") or {}).get("tool_assignments") or {}).get("integration_hub") or {}).get("role")


def _require_module(request, write=False):
    role = _module_role(request)
    allowed = {"Admin", "Analyst"} if write else {"Admin", "Analyst", "Read-only", "Support"}
    if role not in allowed: raise web.HTTPForbidden(text="Integration Hub permission required")


async def _json(request):
    try: return await request.json()
    except Exception as exc: raise ValidationError("Invalid JSON") from exc


def _if_match(request, required=True):
    value = request.headers.get("If-Match")
    if not value:
        if required: raise ValidationError("If-Match header is required")
        return None
    try: return int(value.strip('W/"'))
    except ValueError as exc: raise ValidationError("If-Match must contain a numeric ETag") from exc


async def _account_context(request, permission="read", include_archived=False):
    service, actor, system_role = _context(request); _require_module(request, permission == "edit")
    account = await service.repository.get(request.match_info["account_id"], include_archived)
    if not account: return service, actor, system_role, None
    return service, actor, system_role, account


async def _run(request, handler):
    try: return await handler()
    except (ValidationError, ValueError) as exc: return _error(request, 400, "validation_error", str(exc))
    except RuntimeError as exc:
        if str(exc) == "version_conflict": return _error(request, 412, "precondition_failed", "Resource changed; reload and retry")
        raise
    except web.HTTPException as exc:
        request.setdefault("request_id", str(uuid.uuid4()))
        return _error(request, exc.status, "http_error", exc.text or exc.reason)


async def handle_create_account(request):
    async def action():
        service, actor, _ = _context(request); _require_module(request, True)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key: raise ValidationError("Idempotency-Key header is required")
        cached = await service.repository.find_idempotent(actor, key)
        if cached: return _ok(request, cached["response"], 200, cached["response"]["account"]["version"])
        account = await service.create(await _json(request), actor, request["request_id"])
        payload = {"account": _serialize(account)}
        await service.repository.save_idempotent(actor, key, payload)
        return _ok(request, payload, 201, account["version"])
    return await _run(request, action)


async def handle_list_accounts(request):
    async def action():
        service, actor, role = _context(request); _require_module(request)
        stage, health = request.query.get("stage"), request.query.get("health")
        if stage and stage not in STAGES: raise ValidationError("stage is invalid")
        if health and health not in HEALTH_STATES: raise ValidationError("health is invalid")
        limit = min(100, max(1, int(request.query.get("limit", request.query.get("page_size", "50")))))
        accounts, cursor = await service.list(actor, role, stage=stage, health=health,
            search=request.query.get("search"), owner=request.query.get("owner"),
            status=request.query.get("status", "active"), limit=limit, cursor=request.query.get("cursor"))
        return _ok(request, {"accounts": accounts, "pagination": {"next_cursor": cursor, "limit": limit}})
    return await _run(request, action)


async def handle_list_actions(request):
    async def action():
        service, actor, _ = _context(request); _require_module(request)
        actions, attention = await service.list_actions(actor)
        return _ok(request, {"actions": actions, "attention_accounts": attention})
    return await _run(request, action)


async def handle_get_account(request):
    async def action():
        service, _, _, account = await _account_context(request, include_archived=True)
        if not account: return _error(request, 404, "not_found", "Account not found")
        hydrated = await service.get(account["account_id"], True)
        return _ok(request, {"account": hydrated}, version=account["version"])
    return await _run(request, action)


async def handle_update_account(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account: return _error(request, 404, "not_found", "Account not found")
        updated = await service.update(account, await _json(request), actor, _if_match(request), request["request_id"])
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def _lifecycle_account(request, restore=False):
    async def action():
        service, actor, _, account = await _account_context(request, "edit", True)
        if not account: return _error(request, 404, "not_found", "Account not found")
        body = await _json(request) if request.can_read_body else {}
        updated = await (service.restore(account, actor, _if_match(request), request["request_id"])
                         if restore else service.archive(account, actor, _if_match(request), request["request_id"], body.get("reason")))
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_archive_account(request): return await _lifecycle_account(request)
async def handle_restore_account(request): return await _lifecycle_account(request, True)


async def handle_list_playbooks(request):
    async def action():
        _context(request); _require_module(request)
        return _ok(request, {"playbooks": [{"id": key, "name": value["name"], "item_count": len(value["items"])} for key, value in PLAYBOOKS.items()]})
    return await _run(request, action)


async def handle_create_project(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account: return _error(request, 404, "not_found", "Account not found")
        if _if_match(request) != account["version"]: raise RuntimeError("version_conflict")
        updated = await service.create_project(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


async def _archive_child_resource(request, kind, id_key):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account: return _error(request, 404, "not_found", "Account not found")
        resource = await service.repository.get_resource(
            kind, request.match_info[id_key], account["account_id"],
        )
        if not resource: return _error(request, 404, "not_found", f"{kind.title()} not found")
        body = await _json(request)
        updated = await service.archive_resource(
            account, kind, resource, actor, _if_match(request),
            request["request_id"], body.get("reason"),
        )
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_archive_project(request):
    return await _archive_child_resource(request, "project", "project_id")


async def handle_archive_source(request):
    return await _archive_child_resource(request, "source", "source_id")


async def handle_get_audit_log(request):
    async def action():
        service, _, _, account = await _account_context(request)
        if not account: return _error(request, 404, "not_found", "Account not found")
        rows, cursor = await service.repository.list_audit(account["account_id"], min(100, int(request.query.get("limit", "50"))), request.query.get("cursor"))
        return _ok(request, {"audit_entries": rows, "pagination": {"next_cursor": cursor}})
    return await _run(request, action)


def _find_item(account, item_id):
    return next((item for item in account.get("work_items", []) if item["item_id"] == item_id), None)


async def handle_create_work_item(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account: return _error(request, 404, "not_found", "Account not found")
        if _if_match(request) != account["version"]: raise RuntimeError("version_conflict")
        updated = await service.create_work_item(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


async def handle_update_work_item(request):
    async def action():
        service, actor, _, raw = await _account_context(request, "edit")
        if not raw: return _error(request, 404, "not_found", "Account not found")
        account = await service.get(raw["account_id"]); item = _find_item(account, request.match_info["item_id"])
        if not item: return _error(request, 404, "not_found", "Work item not found")
        updated = await service.update_work_item(raw, item["type"], item, await _json(request), actor, _if_match(request), request["request_id"])
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_archive_work_item(request):
    async def action():
        service, actor, _, raw = await _account_context(request, "edit")
        if not raw: return _error(request, 404, "not_found", "Account not found")
        account = await service.get(raw["account_id"]); item = _find_item(account, request.match_info["item_id"])
        if not item: return _error(request, 404, "not_found", "Work item not found")
        updated = await service.archive_work_item(raw, item["type"], item, actor, _if_match(request), request["request_id"], (await _json(request)).get("reason"))
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_create_activity(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account: return _error(request, 404, "not_found", "Account not found")
        if _if_match(request) != account["version"]: raise RuntimeError("version_conflict")
        updated = await service.create_activity(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


async def handle_create_source_link(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account: return _error(request, 404, "not_found", "Account not found")
        if _if_match(request) != account["version"]: raise RuntimeError("version_conflict")
        updated = await service.create_source_link(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


def setup_integration_hub_routes(app):
    p = "/api/integration-hub"
    app.router.add_post(f"{p}/accounts", handle_create_account)
    app.router.add_get(f"{p}/accounts", handle_list_accounts)
    app.router.add_get(f"{p}/actions", handle_list_actions)
    app.router.add_get(f"{p}/playbooks", handle_list_playbooks)
    app.router.add_get(f"{p}/accounts/{{account_id}}", handle_get_account)
    app.router.add_patch(f"{p}/accounts/{{account_id}}", handle_update_account)
    app.router.add_post(f"{p}/accounts/{{account_id}}/archive", handle_archive_account)
    app.router.add_post(f"{p}/accounts/{{account_id}}/restore", handle_restore_account)
    app.router.add_post(f"{p}/accounts/{{account_id}}/projects", handle_create_project)
    app.router.add_post(f"{p}/accounts/{{account_id}}/projects/{{project_id}}/archive", handle_archive_project)
    app.router.add_get(f"{p}/accounts/{{account_id}}/audit-log", handle_get_audit_log)
    app.router.add_post(f"{p}/accounts/{{account_id}}/work-items", handle_create_work_item)
    app.router.add_patch(f"{p}/accounts/{{account_id}}/work-items/{{item_id}}", handle_update_work_item)
    app.router.add_post(f"{p}/accounts/{{account_id}}/work-items/{{item_id}}/archive", handle_archive_work_item)
    app.router.add_post(f"{p}/accounts/{{account_id}}/activities", handle_create_activity)
    app.router.add_post(f"{p}/accounts/{{account_id}}/source-links", handle_create_source_link)
    app.router.add_post(f"{p}/accounts/{{account_id}}/source-links/{{source_id}}/archive", handle_archive_source)
