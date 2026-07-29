"""Contract-compliant REST routes for Integration Hub Phase 1."""
import os
import re
import uuid
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from aiohttp import web
from pymongo import ReturnDocument

from api.auth_helpers import get_system_role, get_user_email
from integration_hub.models import HEALTH_STATES, PLAYBOOKS, STAGES, ValidationError
from integration_hub.repository import AccountRepository
from integration_hub.read_only_sync import SyncError
from integration_hub.service import AccountService
from observability.db import get_db

_RATE_LIMIT = int(os.environ.get("INTEGRATION_HUB_RATE_LIMIT", "120"))
_PYLON_DETAIL_REQUESTS = {}
logger = logging.getLogger(__name__)


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


async def _context(request, cost=1):
    supplied_request_id = request.headers.get("X-Request-ID", "").strip()
    request["request_id"] = supplied_request_id[:128] if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied_request_id) else str(uuid.uuid4())
    if not _enabled(): raise web.HTTPNotFound()
    db = get_db()
    if db is None: raise web.HTTPServiceUnavailable(text="Integration Hub storage unavailable")
    actor = get_user_email(request)
    if not actor: raise web.HTTPUnauthorized(text="Authentication required")
    # Mongo-backed fixed windows apply consistently across all web workers.
    now = datetime.now(timezone.utc)
    window = now.replace(second=0, microsecond=0)
    bucket = await db.integration_rate_limits.find_one_and_update(
        {"actor": actor, "window": window},
        {"$inc": {"count": cost}, "$setOnInsert": {"expires_at": now}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    if bucket["count"] > _RATE_LIMIT:
        raise web.HTTPTooManyRequests(headers={"Retry-After": "60"})
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
    match = re.fullmatch(r'"([0-9]+)"', value.strip())
    try:
        if not match:
            raise ValueError
        return int(match.group(1))
    except ValueError as exc: raise ValidationError("If-Match must contain a numeric ETag") from exc


async def _account_context(request, permission="read", include_archived=False, cost=1):
    service, actor, system_role = await _context(request, cost)
    _require_module(request, permission == "edit")
    account = await service.repository.get(request.match_info["account_id"], include_archived)
    if not account: return service, actor, system_role, None
    return service, actor, system_role, account


async def _run(request, handler):
    try: return await handler()
    except (ValidationError, ValueError) as exc: return _error(request, 400, "validation_error", str(exc))
    except SyncError as exc:
        logger.warning("Integration Hub upstream sync failed", exc_info=exc)
        return _error(request, 502, "upstream_error", "Connected service request failed")
    except RuntimeError as exc:
        if str(exc) == "version_conflict": return _error(request, 412, "precondition_failed", "Resource changed; reload and retry")
        logger.exception("Integration Hub request failed")
        raise
    except web.HTTPException as exc:
        request.setdefault("request_id", str(uuid.uuid4()))
        response = _error(request, exc.status, "http_error", exc.text or exc.reason)
        if "Retry-After" in exc.headers:
            response.headers["Retry-After"] = exc.headers["Retry-After"]
        return response


async def handle_create_account(request):
    async def action():
        service, actor, _ = await _context(request); _require_module(request, True)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key: raise ValidationError("Idempotency-Key header is required")
        payload, created = await service.create_idempotent(
            await _json(request), actor, request["request_id"], key,
        )
        account = payload["account"]
        return _ok(request, payload, 201 if created else 200, account["version"])
    return await _run(request, action)


async def handle_list_accounts(request):
    async def action():
        service, actor, role = await _context(request); _require_module(request)
        stage, health = request.query.get("stage"), request.query.get("health")
        if stage and stage not in STAGES: raise ValidationError("stage is invalid")
        if health and health not in HEALTH_STATES: raise ValidationError("health is invalid")
        limit = min(100, max(1, int(request.query.get("limit", request.query.get("page_size", "50")))))
        accounts, cursor = await service.list(stage=stage, health=health,
            search=request.query.get("search"), owner=request.query.get("owner"),
            status=request.query.get("status", "active"), limit=limit, cursor=request.query.get("cursor"))
        return _ok(request, {"accounts": accounts, "pagination": {"next_cursor": cursor, "limit": limit}})
    return await _run(request, action)


async def handle_list_actions(request):
    async def action():
        service, actor, _ = await _context(request); _require_module(request)
        limit = min(100, max(1, int(request.query.get("limit", "100"))))
        actions, attention = await service.list_actions(actor, limit)
        return _ok(request, {"actions": actions, "attention_accounts": attention})
    return await _run(request, action)


async def handle_get_account(request):
    async def action():
        service, _, _, account = await _account_context(request, include_archived=True)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        hydrated = await service.get(account["account_id"], True)
        return _ok(request, {"account": hydrated}, version=hydrated["version"])
    return await _run(request, action)


async def handle_update_account(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        updated = await service.update(account, await _json(request), actor, _if_match(request), request["request_id"])
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def _lifecycle_account(request, restore=False):
    async def action():
        service, actor, _, account = await _account_context(request, "edit", True)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        body = await _json(request) if request.can_read_body else {}
        updated = await (service.restore(account, actor, _if_match(request), request["request_id"])
                         if restore else service.archive(account, actor, _if_match(request), request["request_id"], body.get("reason")))
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_archive_account(request): return await _lifecycle_account(request)
async def handle_restore_account(request): return await _lifecycle_account(request, True)


async def handle_list_playbooks(request):
    async def action():
        await _context(request); _require_module(request)
        return _ok(request, {"playbooks": [{"id": key, "name": value["name"], "item_count": len(value["items"])} for key, value in PLAYBOOKS.items()]})
    return await _run(request, action)


async def handle_create_project(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        if _if_match(request) != account["version"]: raise RuntimeError("version_conflict")
        updated = await service.create_project(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


async def _archive_child_resource(request, kind, id_key):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
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
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        limit = min(100, max(1, int(request.query.get("limit", "50"))))
        rows, cursor = await service.repository.list_audit(
            account["account_id"], limit, request.query.get("cursor")
        )
        return _ok(request, {"audit_entries": rows, "pagination": {"next_cursor": cursor}})
    return await _run(request, action)


def _find_item(account, item_id):
    return next((item for item in account.get("work_items", []) if item["item_id"] == item_id), None)


async def handle_create_work_item(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
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
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        if _if_match(request) != account["version"]: raise RuntimeError("version_conflict")
        updated = await service.create_activity(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


async def handle_create_source_link(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        if _if_match(request) != account["version"]: raise RuntimeError("version_conflict")
        updated = await service.create_source_link(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


async def handle_list_interactions(request):
    async def action():
        service, _, _, account = await _account_context(request)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        limit = min(100, max(1, int(request.query.get("limit", "50"))))
        rows, cursor = await service.repository.list_interactions(
            account["account_id"], limit, request.query.get("cursor"),
        )
        return _ok(request, {"interactions": rows, "pagination": {"next_cursor": cursor, "limit": limit}})
    return await _run(request, action)


async def handle_ingest_interaction(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        interaction, created = await service.ingest_interaction(
            account, await _json(request), actor, request["request_id"],
        )
        return _ok(request, {"interaction": interaction}, 201 if created else 200)
    return await _run(request, action)


async def handle_list_sync_sources(request):
    async def action():
        service, _, _, account = await _account_context(request)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        rows = await service.repository.list_sync_sources(account["account_id"])
        return _ok(request, {"sources": rows})
    return await _run(request, action)


async def handle_list_sync_jobs(request):
    async def action():
        service, _, _, account = await _account_context(request)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        limit = min(100, max(1, int(request.query.get("limit", "50"))))
        rows = await service.repository.list_sync_jobs(account["account_id"], limit)
        return _ok(request, {"jobs": rows, "limit": limit})
    return await _run(request, action)


async def handle_create_sync_source(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        mapping = await service.create_sync_source(account, await _json(request), actor, request["request_id"])
        return _ok(request, {"source": mapping}, 201)
    return await _run(request, action)


async def handle_search_pylon_customers(request):
    async def action():
        await _context(request, cost=10); _require_module(request)
        from integration_hub.read_only_sync import discover_pylon_customers
        query = request.query.get("query", "")
        if len(query) > 100: raise ValidationError("query must be 100 characters or less")
        return _ok(request, {"customers": await discover_pylon_customers(query)})
    return await _run(request, action)


async def _pylon_mapping(service, account_id):
    return await service.repository.sync_sources.find_one({
        "account_id": account_id, "source": "pylon", "archived_at": None, "status": "active",
    })


async def handle_list_pylon_issues(request):
    async def action():
        service, _, _, account = await _account_context(request, cost=2)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        mapping = await _pylon_mapping(service, account["account_id"])
        if not mapping:
            return _error(request, 409, "pylon_not_connected", "Connect a Pylon customer first")
        from tools.pylon import list_account_issues_page
        limit = min(50, max(1, int(request.query.get("limit", "25"))))
        query = request.query.get("query", "").strip()
        if len(query) > 100:
            raise ValidationError("query must be 100 characters or less")
        result = await list_account_issues_page(
            mapping["external_id"], limit=limit, cursor=request.query.get("cursor"),
            state=request.query.get("status"), assignee_id=request.query.get("assignee"),
            query=query or None,
        )
        if result.get("error"):
            raise SyncError(result["error"])
        return _ok(request, result)
    return await _run(request, action)


async def handle_get_pylon_issue(request):
    async def action():
        service, _, _, account = await _account_context(request, cost=4)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        mapping = await _pylon_mapping(service, account["account_id"])
        if not mapping:
            return _error(request, 409, "pylon_not_connected", "Connect a Pylon customer first")
        from tools.pylon import get_issue, get_messages, issue_web_url, normalize_message
        import asyncio
        issue_id = request.match_info["issue_id"]
        request_key = (account["account_id"], issue_id)
        task = _PYLON_DETAIL_REQUESTS.get(request_key)
        if task is None:
            task = asyncio.ensure_future(asyncio.gather(
                get_issue(issue_id), get_messages(issue_id),
            ))
            _PYLON_DETAIL_REQUESTS[request_key] = task
        try:
            issue_result, messages_result = await task
        finally:
            if task.done():
                _PYLON_DETAIL_REQUESTS.pop(request_key, None)
        if issue_result.get("error"):
            raise SyncError(issue_result["error"])
        if messages_result.get("error"):
            raise SyncError(messages_result["error"])
        issue = issue_result.get("data") or issue_result
        issue_account_id = (issue.get("account") or {}).get("id") or issue.get("account_id")
        if not issue_account_id or str(issue_account_id) != str(mapping["external_id"]):
            return _error(request, 404, "not_found", "Issue not found for this client")
        issue["url"] = issue_web_url(issue)
        messages = messages_result.get("data") or messages_result.get("messages") or []
        if isinstance(messages, dict):
            messages = messages.get("data", [])
        normalized_messages = [
            normalize_message(message) for message in messages[:200]
        ]
        return _ok(request, {
            "issue": issue,
            "messages": [
                message for message in normalized_messages
                if not message["is_private"]
            ],
        })
    return await _run(request, action)


async def handle_create_contact(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        updated = await service.create_contact(
            account, await _json(request), actor, request["request_id"], _if_match(request),
        )
        return _ok(request, {"account": updated}, 201, updated["version"])
    return await _run(request, action)


async def handle_archive_contact(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        updated = await service.archive_contact(
            account, request.match_info["contact_id"], actor, request["request_id"],
            _if_match(request),
        )
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_update_contact(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit")
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        updated = await service.update_contact(
            account, request.match_info["contact_id"], await _json(request),
            actor, request["request_id"], _if_match(request),
        )
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_invite_contact(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit", cost=5)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        hydrated = await service.get(account["account_id"])
        contact = next((
            item for item in hydrated.get("contacts", [])
            if item["contact_id"] == request.match_info["contact_id"]
        ), None)
        if not contact:
            return _error(request, 404, "not_found", "Contact not found")
        access_url = contact.get("access_url")
        if not access_url:
            return _error(request, 422, "access_url_required",
                          "Add a dashboard access URL before sending an invite")
        parsed_url = urlparse(access_url)
        allowed_hosts = {
            host.strip().lower() for host in
            os.environ.get("INTEGRATION_HUB_INVITE_HOSTS", "app.lomahq.com").split(",")
            if host.strip()
        }
        hostname = (parsed_url.hostname or "").lower()
        host_allowed = any(
            hostname == allowed or hostname.endswith("." + allowed)
            for allowed in allowed_hosts
        )
        if parsed_url.scheme != "https" or not parsed_url.hostname or (
            not host_allowed
        ):
            return _error(request, 422, "invalid_access_url",
                          "Dashboard access URL must use HTTPS and an approved host")
        expected_version = _if_match(request)
        invited_at = datetime.now(timezone.utc)
        duration_days = contact.get("access_duration_days")
        if not contact.get("dashboard_access") or not duration_days:
            return _error(
                request, 422, "dashboard_access_required",
                "Choose an access role and duration before sending an invite",
            )
        updated = await service.update_contact(
            account, contact["contact_id"],
            {**{key: contact.get(key) for key in (
                "name", "email", "role", "role_description", "phone",
                "dashboard_access", "access_duration_days", "organization_ids",
                "access_url",
            )}, "invite_sent_at": invited_at.isoformat(),
                "access_starts_at": invited_at.isoformat(),
                "access_expires_at": (
                    invited_at + timedelta(days=duration_days)
                ).isoformat(),
                "access_status": "invite_sent"},
            actor, request["request_id"], expected_version,
        )
        from tools.gmail import send_email
        try:
            await send_email(
                actor, contact["email"],
                f"{account['name']} dashboard access",
                f"Hi {contact['name']},\n\nYour dashboard access is ready: {access_url}\n\nRegards,\nLoma",
            )
        except Exception:
            logger.exception("Integration Hub dashboard invitation failed")
            raise
        return _ok(request, {"account": updated}, version=updated["version"])
    return await _run(request, action)


async def handle_discover_grain_meetings(request):
    async def action():
        service, _, _, account = await _account_context(request, cost=4)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        hydrated = await service.get(account["account_id"])
        from tools.grain import discover_client_recordings
        result = await discover_client_recordings(
            account["name"], [contact["email"] for contact in hydrated.get("contacts", [])],
        )
        return _ok(request, result)
    return await _run(request, action)


async def handle_get_grain_summary(request):
    async def action():
        service, _, _, account = await _account_context(request, cost=4)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        from tools.grain import discover_client_recordings, get_transcript
        recording_id = request.match_info["recording_id"]
        hydrated = await service.get(account["account_id"])
        discovered = await discover_client_recordings(
            account["name"], [contact["email"] for contact in hydrated.get("contacts", [])],
        )
        recording = next(
            (item for item in discovered.get("recordings", []) if item["id"] == recording_id),
            None,
        )
        if not recording:
            return _error(request, 404, "not_found", "Grain meeting not found for this client")
        transcript = await get_transcript(recording_id, "text")
        return _ok(request, {
            "recording": recording,
            "summary": recording.get("ai_summary") or "No Grain summary is available.",
            "action_items": recording.get("action_items", []),
            "transcript_excerpt": (
                "" if transcript.get("error") else transcript.get("transcript", "")[:5000]
            ),
        })
    return await _run(request, action)


async def handle_sync_source(request):
    async def action():
        service, actor, _, account = await _account_context(request, "edit", cost=10)
        if not account:
            return _error(request, 404, "not_found", "Account not found")
        mapping = await service.repository.sync_sources.find_one({
            "account_id": account["account_id"], "mapping_id": request.match_info["mapping_id"],
            "archived_at": None, "status": "active",
        })
        if not mapping:
            return _error(request, 404, "not_found", "Sync source not found")
        job, created = await service.queue_sync(account, mapping, actor, request["request_id"])
        return _ok(request, {"job": job}, 202 if created else 200)
    return await _run(request, action)

def setup_integration_hub_routes(app):
    from integration_hub.sync_worker import worker_context
    app.cleanup_ctx.append(worker_context)
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
    app.router.add_get(f"{p}/accounts/{{account_id}}/interactions", handle_list_interactions)
    app.router.add_get(f"{p}/accounts/{{account_id}}/sync-sources", handle_list_sync_sources)
    app.router.add_get(f"{p}/accounts/{{account_id}}/sync-jobs", handle_list_sync_jobs)
    app.router.add_post(f"{p}/accounts/{{account_id}}/sync-sources", handle_create_sync_source)
    app.router.add_get(f"{p}/pylon/customers", handle_search_pylon_customers)
    app.router.add_get(f"{p}/accounts/{{account_id}}/pylon/issues", handle_list_pylon_issues)
    app.router.add_get(f"{p}/accounts/{{account_id}}/pylon/issues/{{issue_id}}", handle_get_pylon_issue)
    app.router.add_post(f"{p}/accounts/{{account_id}}/contacts", handle_create_contact)
    app.router.add_patch(f"{p}/accounts/{{account_id}}/contacts/{{contact_id}}", handle_update_contact)
    app.router.add_post(f"{p}/accounts/{{account_id}}/contacts/{{contact_id}}/invite", handle_invite_contact)
    app.router.add_post(f"{p}/accounts/{{account_id}}/contacts/{{contact_id}}/archive", handle_archive_contact)
    app.router.add_get(f"{p}/accounts/{{account_id}}/grain/meetings", handle_discover_grain_meetings)
    app.router.add_get(f"{p}/accounts/{{account_id}}/grain/meetings/{{recording_id}}/summary", handle_get_grain_summary)
    app.router.add_post(f"{p}/accounts/{{account_id}}/sync-sources/{{mapping_id}}/sync", handle_sync_source)
    app.router.add_post(f"{p}/accounts/{{account_id}}/interactions", handle_ingest_interaction)
