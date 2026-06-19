import asyncio
import logging
from datetime import datetime, timezone

from aiohttp import web

from observability.db import get_db
from scheduler.models import (
    create_flow,
    update_flow,
    get_flow,
    list_flows,
    delete_flow,
    list_all_labels,
)
from scheduler.engine import add_flow_to_scheduler, remove_flow_from_scheduler, get_next_run_time
from api.auth_helpers import require_analyst_or_above, require_operator_or_above, get_system_role, get_user_email

logger = logging.getLogger(__name__)


def _serialize(doc):
    """Make a MongoDB document JSON-serializable."""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [_serialize(d) for d in doc]
    if isinstance(doc, dict):
        result = {}
        for k, v in doc.items():
            if k == "_id":
                result[k] = str(v)
            elif isinstance(v, datetime):
                # Motor returns naive UTC datetimes; tag them so JS
                # doesn't interpret them as local time.
                if v.tzinfo is None:
                    result[k] = v.isoformat() + "Z"
                else:
                    result[k] = v.isoformat()
            elif isinstance(v, dict):
                result[k] = _serialize(v)
            elif isinstance(v, list):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        if doc.tzinfo is None:
            return doc.isoformat() + "Z"
        return doc.isoformat()
    return doc


def _parse_datetime(value) -> datetime | None:
    """Parse an ISO datetime string or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_flow_creator(flow: dict, user_email: str) -> bool:
    """Check if user_email matches the flow's creator."""
    if not user_email:
        return False
    email_lower = user_email.lower()
    created_by = flow.get("created_by", {})
    return (
        (created_by.get("source") or "").lower() == email_lower
        or (created_by.get("user_name") or "").lower() == email_lower
    )


def _check_flow_access(flow: dict, request) -> bool:
    """Return True if the requesting user can access this flow."""
    if flow.get("visibility") != "private":
        return True
    if get_system_role(request) == "admin":
        return True
    return _is_flow_creator(flow, get_user_email(request))


def _normalize_flow_model(value) -> str | None:
    """Validate and normalize a flow model id.

    Runtime is inferred from provider/model. Empty values mean "use the
    backend's current Claude default" for backward compatibility.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("model must be a string in provider/model format")
    model = value.strip()
    if not model:
        return None
    if "/" not in model or model.startswith("/") or model.endswith("/"):
        raise ValueError("model must be in provider/model format")
    return model


def _normalize_model_field(body: dict) -> web.Response | None:
    if "model" not in body:
        return None
    try:
        body["model"] = _normalize_flow_model(body.get("model"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return None


async def handle_list_flows(request: web.Request) -> web.Response:
    """GET /api/flows — list all flows, optionally filtered by status and/or trigger_type."""
    require_analyst_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    status = request.query.get("status")
    trigger_type = request.query.get("trigger_type")
    user_email = get_user_email(request)
    system_role = get_system_role(request)
    flows = await list_flows(
        db, status=status, trigger_type=trigger_type,
        user_email=user_email, system_role=system_role,
    )
    return web.json_response({"flows": _serialize(flows)})


async def handle_get_flow(request: web.Request) -> web.Response:
    """GET /api/flows/{flow_id} — get a single flow."""
    require_analyst_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    flow = await get_flow(db, flow_id)
    if flow is None or not _check_flow_access(flow, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    return web.json_response({"flow": _serialize(flow)})


async def handle_create_flow(request: web.Request) -> web.Response:
    """POST /api/flows — create a new flow (scheduled or webhook-triggered)."""
    require_operator_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    trigger_type = body.get("trigger_type", "scheduled")

    # Validate required fields based on trigger type
    if trigger_type == "webhook":
        for field in ("name", "prompt_template", "webhook_config"):
            if field not in body:
                return web.json_response(
                    {"error": f"Missing required field: {field}"}, status=400,
                )
    else:
        for field in ("name", "prompt", "schedule_type", "channel_id"):
            if field not in body:
                return web.json_response(
                    {"error": f"Missing required field: {field}"}, status=400,
                )
        if body["schedule_type"] == "recurring" and not body.get("cron"):
            return web.json_response(
                {"error": "Recurring flows require a cron expression"}, status=400,
            )

    # Ensure created_by.source has an email.
    # Only override if the agent didn't already pass one (contains @).
    # The agent's curl bypasses Next.js middleware, so the auth email
    # may be a dev fallback — prefer the agent-provided value when it's an email.
    created_by = body.get("created_by", {})
    if "@" not in (created_by.get("source") or ""):
        user_email = get_user_email(request)
        if user_email:
            created_by["source"] = user_email
            body["created_by"] = created_by

    # Parse datetime fields
    body["start_time"] = _parse_datetime(body.get("start_time"))
    body["end_time"] = _parse_datetime(body.get("end_time"))
    model_error = _normalize_model_field(body)
    if model_error is not None:
        return model_error

    flow = await create_flow(db, body)

    # Add to live scheduler if active (scheduled flows only)
    if flow["status"] == "active" and trigger_type != "webhook":
        await add_flow_to_scheduler(flow)
        next_run = get_next_run_time(flow["flow_id"])
        if next_run:
            await db.flows.update_one(
                {"flow_id": flow["flow_id"]},
                {"$set": {"next_run_at": next_run}},
            )
            flow["next_run_at"] = next_run

    return web.json_response({"flow": _serialize(flow)}, status=201)


async def handle_update_flow(request: web.Request) -> web.Response:
    """PATCH /api/flows/{flow_id} — update flow fields."""
    require_operator_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    # Check access on existing flow
    existing = await get_flow(db, flow_id)
    if existing is None or not _check_flow_access(existing, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    # Don't allow updating internal fields
    for field in ("flow_id", "created_at", "run_count", "last_run_at",
                  "last_run_conversation_id", "last_error"):
        body.pop(field, None)

    # Validate visibility value if present
    if "visibility" in body and body["visibility"] not in ("private", "shared"):
        return web.json_response(
            {"error": "visibility must be 'private' or 'shared'"}, status=400,
        )
    model_error = _normalize_model_field(body)
    if model_error is not None:
        return model_error

    # Parse datetime fields if present
    if "start_time" in body:
        body["start_time"] = _parse_datetime(body["start_time"])
    if "end_time" in body:
        body["end_time"] = _parse_datetime(body["end_time"])

    flow = await update_flow(db, flow_id, body)
    if flow is None:
        return web.json_response({"error": "Flow not found"}, status=404)

    # Re-schedule if schedule-related fields changed (scheduled flows only)
    if flow.get("trigger_type", "scheduled") != "webhook":
        schedule_fields = {"schedule_type", "cron", "timezone", "start_time", "end_time"}
        if schedule_fields & body.keys():
            await remove_flow_from_scheduler(flow_id)
            if flow["status"] == "active":
                await add_flow_to_scheduler(flow)
                next_run = get_next_run_time(flow_id)
                if next_run:
                    await db.flows.update_one(
                        {"flow_id": flow_id},
                        {"$set": {"next_run_at": next_run}},
                    )
                    flow["next_run_at"] = next_run

    return web.json_response({"flow": _serialize(flow)})


async def handle_delete_flow(request: web.Request) -> web.Response:
    """DELETE /api/flows/{flow_id} — delete a flow."""
    require_operator_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    flow = await get_flow(db, flow_id)
    if flow is None or not _check_flow_access(flow, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    await remove_flow_from_scheduler(flow_id)
    await delete_flow(db, flow_id)
    return web.json_response({"deleted": True})


async def handle_pause_flow(request: web.Request) -> web.Response:
    """POST /api/flows/{flow_id}/pause — pause a flow."""
    require_operator_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    existing = await get_flow(db, flow_id)
    if existing is None or not _check_flow_access(existing, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    flow = await update_flow(db, flow_id, {"status": "paused"})
    await remove_flow_from_scheduler(flow_id)
    return web.json_response({"flow": _serialize(flow)})


async def handle_resume_flow(request: web.Request) -> web.Response:
    """POST /api/flows/{flow_id}/resume — resume a paused flow."""
    require_operator_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    existing = await get_flow(db, flow_id)
    if existing is None or not _check_flow_access(existing, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    flow = await update_flow(db, flow_id, {"status": "active"})

    # Only register with APScheduler for scheduled flows
    if flow.get("trigger_type", "scheduled") != "webhook":
        await add_flow_to_scheduler(flow)
        next_run = get_next_run_time(flow_id)
        if next_run:
            await db.flows.update_one(
                {"flow_id": flow_id},
                {"$set": {"next_run_at": next_run}},
            )
            flow["next_run_at"] = next_run

    return web.json_response({"flow": _serialize(flow)})


async def handle_run_now(request: web.Request) -> web.Response:
    """POST /api/flows/{flow_id}/run-now — trigger immediate execution."""
    require_operator_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    flow = await get_flow(db, flow_id)
    if flow is None or not _check_flow_access(flow, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    if flow.get("trigger_type") == "webhook":
        return web.json_response(
            {"error": "Webhook flows cannot be triggered manually — they require event data"},
            status=400,
        )

    # Fire and forget
    from scheduler.executor import execute_flow
    asyncio.create_task(execute_flow(flow_id))

    return web.json_response({"triggered": True, "flow_id": flow_id})


async def handle_flow_runs(request: web.Request) -> web.Response:
    """GET /api/flows/{flow_id}/runs — execution history for a flow."""
    require_analyst_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    flow = await get_flow(db, flow_id)
    if flow is None or not _check_flow_access(flow, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    limit = int(request.query.get("limit", 20))

    runs = await db.conversations.find(
        {"metadata.flow_id": flow_id},
    ).sort("started_at", -1).limit(limit).to_list(limit)

    return web.json_response({"runs": _serialize(runs)})


async def handle_update_flow_labels(request: web.Request) -> web.Response:
    """PATCH /api/flows/{flow_id}/labels — replace labels on a flow."""
    require_operator_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    flow_id = request.match_info["flow_id"]
    existing = await get_flow(db, flow_id)
    if existing is None or not _check_flow_access(existing, request):
        return web.json_response({"error": "Flow not found"}, status=404)

    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    labels = body.get("labels")
    if labels is None or not isinstance(labels, list):
        return web.json_response(
            {"error": "Request body must contain a 'labels' array"},
            status=400,
        )

    # Validate all labels are non-empty strings
    cleaned = [str(l).strip() for l in labels if str(l).strip()]

    flow = await update_flow(db, flow_id, {"labels": cleaned})
    if flow is None:
        return web.json_response({"error": "Flow not found"}, status=404)

    return web.json_response({"flow": _serialize(flow)})


async def handle_list_labels(request: web.Request) -> web.Response:
    """GET /api/labels — return all distinct labels used across flows."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    labels = await list_all_labels(db)
    return web.json_response({"labels": sorted(labels)})


def setup_flow_routes(app: web.Application):
    """Register flow API routes."""
    app.router.add_get("/api/flows", handle_list_flows)
    app.router.add_get("/api/flows/{flow_id}", handle_get_flow)
    app.router.add_post("/api/flows", handle_create_flow)
    app.router.add_patch("/api/flows/{flow_id}", handle_update_flow)
    app.router.add_delete("/api/flows/{flow_id}", handle_delete_flow)
    app.router.add_post("/api/flows/{flow_id}/pause", handle_pause_flow)
    app.router.add_post("/api/flows/{flow_id}/resume", handle_resume_flow)
    app.router.add_post("/api/flows/{flow_id}/run-now", handle_run_now)
    app.router.add_get("/api/flows/{flow_id}/runs", handle_flow_runs)
    app.router.add_patch("/api/flows/{flow_id}/labels", handle_update_flow_labels)
    app.router.add_get("/api/labels", handle_list_labels)
