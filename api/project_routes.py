"""Project CRUD routes for chat organization.

Projects allow users to organize conversations into named groups.
Each project is user-scoped (admins can see all).
"""

import logging
import uuid
from datetime import datetime, timezone

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import get_system_role, get_user_email

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
                result[k] = v.isoformat()
            elif isinstance(v, dict):
                result[k] = _serialize(v)
            elif isinstance(v, list):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


def _check_project_access(project: dict, user_email: str, system_role: str) -> bool:
    """Return True if the user has access to this project."""
    if system_role == "admin":
        return True
    return project.get("created_by") == user_email


async def handle_create_project(request: web.Request) -> web.Response:
    """POST /api/projects -- create a new project."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "Project name is required"}, status=400)
    if len(name) > 100:
        return web.json_response({"error": "Project name must be 100 characters or less"}, status=400)

    now = datetime.now(timezone.utc)
    project = {
        "project_id": str(uuid.uuid4()),
        "name": name,
        "description": (body.get("description") or "").strip() or None,
        "color": body.get("color") or None,
        "icon": body.get("icon") or None,
        "created_by": user_email,
        "created_at": now,
        "updated_at": now,
        "deleted": False,
    }

    await db.projects.insert_one(project)
    return web.json_response({"project": _serialize(project)}, status=201)


async def handle_list_projects(request: web.Request) -> web.Response:
    """GET /api/projects -- list user's projects with conversation counts."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    system_role = get_system_role(request)
    query = {"deleted": {"$ne": True}}
    if system_role != "admin":
        query["created_by"] = user_email

    projects = await db.projects.find(query).sort("created_at", -1).to_list(100)

    # Get conversation counts per project
    project_ids = [p["project_id"] for p in projects]
    if project_ids:
        counts_pipeline = [
            {"$match": {
                "project_id": {"$in": project_ids},
                "deleted": {"$ne": True},
            }},
            {"$group": {"_id": "$project_id", "count": {"$sum": 1}}},
        ]
        counts_result = await db.conversations.aggregate(counts_pipeline).to_list(100)
        counts_map = {c["_id"]: c["count"] for c in counts_result}
    else:
        counts_map = {}

    serialized = _serialize(projects)
    for p in serialized:
        p["conversation_count"] = counts_map.get(p["project_id"], 0)

    return web.json_response({"projects": serialized})


async def handle_get_project(request: web.Request) -> web.Response:
    """GET /api/projects/{project_id} -- get a single project with its conversations."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    project_id = request.match_info["project_id"]
    system_role = get_system_role(request)

    project = await db.projects.find_one({
        "project_id": project_id,
        "deleted": {"$ne": True},
    })
    if not project or not _check_project_access(project, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    # Fetch conversations in this project
    conversations = await db.conversations.find({
        "project_id": project_id,
        "deleted": {"$ne": True},
    }).sort("started_at", -1).to_list(200)

    return web.json_response({
        "project": _serialize(project),
        "conversations": _serialize(conversations),
    })


async def handle_update_project(request: web.Request) -> web.Response:
    """PATCH /api/projects/{project_id} -- update project (rename, change color/icon)."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    project_id = request.match_info["project_id"]
    system_role = get_system_role(request)

    project = await db.projects.find_one({
        "project_id": project_id,
        "deleted": {"$ne": True},
    })
    if not project or not _check_project_access(project, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    updates = {}
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            return web.json_response({"error": "Project name cannot be empty"}, status=400)
        if len(name) > 100:
            return web.json_response({"error": "Project name must be 100 characters or less"}, status=400)
        updates["name"] = name
    if "description" in body:
        updates["description"] = (body["description"] or "").strip() or None
    if "color" in body:
        updates["color"] = body["color"] or None
    if "icon" in body:
        updates["icon"] = body["icon"] or None

    if not updates:
        return web.json_response({"project": _serialize(project)})

    updates["updated_at"] = datetime.now(timezone.utc)
    await db.projects.update_one(
        {"project_id": project_id},
        {"$set": updates},
    )

    updated = await db.projects.find_one({"project_id": project_id})
    return web.json_response({"project": _serialize(updated)})


async def handle_delete_project(request: web.Request) -> web.Response:
    """DELETE /api/projects/{project_id} -- soft delete project (unlinks conversations)."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    project_id = request.match_info["project_id"]
    system_role = get_system_role(request)

    project = await db.projects.find_one({
        "project_id": project_id,
        "deleted": {"$ne": True},
    })
    if not project or not _check_project_access(project, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    now = datetime.now(timezone.utc)

    # Soft-delete the project
    await db.projects.update_one(
        {"project_id": project_id},
        {"$set": {"deleted": True, "deleted_at": now, "deleted_by": user_email}},
    )

    # Unlink all conversations from this project
    await db.conversations.update_many(
        {"project_id": project_id},
        {"$set": {"project_id": None}},
    )

    return web.json_response({"deleted": True})


async def handle_assign_conversation_to_project(request: web.Request) -> web.Response:
    """POST /api/conversations/{conversation_id}/project -- assign to a project."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    cid = request.match_info["conversation_id"]
    system_role = get_system_role(request)

    # Verify conversation exists and user has access
    conversation = await db.conversations.find_one({
        "conversation_id": cid,
        "deleted": {"$ne": True},
    })
    if not conversation:
        return web.json_response({"error": "Conversation not found"}, status=404)

    # Access check: reuse same logic as pin
    from api.routes import _check_conversation_access
    if not _check_conversation_access(conversation, user_email, system_role):
        return web.json_response({"error": "Conversation not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    project_id = body.get("project_id")
    if not project_id:
        return web.json_response({"error": "project_id is required"}, status=400)

    # Verify project exists and user has access
    project = await db.projects.find_one({
        "project_id": project_id,
        "deleted": {"$ne": True},
    })
    if not project or not _check_project_access(project, user_email, system_role):
        return web.json_response({"error": "Project not found"}, status=404)

    await db.conversations.update_one(
        {"conversation_id": cid},
        {"$set": {"project_id": project_id}},
    )

    return web.json_response({"project_id": project_id})


async def handle_remove_conversation_from_project(request: web.Request) -> web.Response:
    """DELETE /api/conversations/{conversation_id}/project -- remove from project."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    cid = request.match_info["conversation_id"]
    system_role = get_system_role(request)

    conversation = await db.conversations.find_one({
        "conversation_id": cid,
        "deleted": {"$ne": True},
    })
    if not conversation:
        return web.json_response({"error": "Conversation not found"}, status=404)

    from api.routes import _check_conversation_access
    if not _check_conversation_access(conversation, user_email, system_role):
        return web.json_response({"error": "Conversation not found"}, status=404)

    await db.conversations.update_one(
        {"conversation_id": cid},
        {"$set": {"project_id": None}},
    )

    return web.json_response({"project_id": None})


def setup_project_routes(app: web.Application):
    """Register project routes on the aiohttp app."""
    app.router.add_post("/api/projects", handle_create_project)
    app.router.add_get("/api/projects", handle_list_projects)
    app.router.add_get("/api/projects/{project_id}", handle_get_project)
    app.router.add_patch("/api/projects/{project_id}", handle_update_project)
    app.router.add_delete("/api/projects/{project_id}", handle_delete_project)
    app.router.add_post("/api/conversations/{conversation_id}/project", handle_assign_conversation_to_project)
    app.router.add_delete("/api/conversations/{conversation_id}/project", handle_remove_conversation_from_project)
