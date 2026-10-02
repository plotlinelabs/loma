"""Project CRUD routes for chat organization.

Projects are folders: users organize conversations into named groups that can
be nested to any depth via ``parent_id``. A folder is private to its creator
unless the creator turns on link sharing, which gives every signed-in user
read-only access to that folder, its sub-folders and their conversations.
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


def _is_shared(project: dict) -> bool:
    return project.get("visibility") == "shared"


async def _ancestors(db, project: dict) -> list:
    """Return the folder's ancestors, nearest parent first.

    One lookup per level; the ``seen`` set stops a corrupt parent loop from
    spinning forever.
    """
    chain = []
    seen = {project["project_id"]}
    parent_id = project.get("parent_id")
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = await db.projects.find_one({"project_id": parent_id, "deleted": {"$ne": True}})
        if not parent:
            break
        chain.append(parent)
        parent_id = parent.get("parent_id")
    return chain


async def _descendant_ids(db, project_id: str) -> list:
    """Return the ids of every folder nested under ``project_id`` (any depth)."""
    found = []
    seen = {project_id}
    frontier = [project_id]
    while frontier:
        children = await db.projects.find(
            {"parent_id": {"$in": frontier}, "deleted": {"$ne": True}},
            {"project_id": 1},
        ).to_list(None)
        frontier = [c["project_id"] for c in children if c["project_id"] not in seen]
        seen.update(frontier)
        found.extend(frontier)
    return found


async def conversation_in_shared_project(db, conversation: dict) -> bool:
    """True when the conversation sits in a folder shared directly or via an ancestor."""
    project_id = conversation.get("project_id")
    if not project_id:
        return False
    project = await db.projects.find_one({"project_id": project_id, "deleted": {"$ne": True}})
    if not project:
        return False
    return _is_shared(project) or any(_is_shared(a) for a in await _ancestors(db, project))


def _validate_name(name) -> tuple:
    """Return (clean_name, error)."""
    name = (name or "").strip()
    if not name:
        return None, "Project name is required"
    if len(name) > 100:
        return None, "Project name must be 100 characters or less"
    return name, None


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

    name, error = _validate_name(body.get("name"))
    if error:
        return web.json_response({"error": error}, status=400)

    # Sub-folders live under a folder the caller owns.
    parent_id = body.get("parent_id") or None
    if parent_id:
        parent = await db.projects.find_one({"project_id": parent_id, "deleted": {"$ne": True}})
        if not parent or parent.get("created_by") != user_email:
            return web.json_response({"error": "Parent folder not found"}, status=404)

    now = datetime.now(timezone.utc)
    project = {
        "project_id": str(uuid.uuid4()),
        "name": name,
        "parent_id": parent_id,
        "visibility": "private",
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

    # Folders are personal: the sidebar tree only ever holds the caller's own.
    query = {"deleted": {"$ne": True}, "created_by": user_email}
    projects = await db.projects.find(query).sort("name", 1).to_list(None)

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
        counts_result = await db.conversations.aggregate(counts_pipeline).to_list(None)
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
    if not project:
        return web.json_response({"error": "Not found"}, status=404)

    ancestors = await _ancestors(db, project)
    can_manage = _check_project_access(project, user_email, system_role)
    if not can_manage:
        # Link sharing: the folder itself or any ancestor is shared. A viewer's
        # breadcrumbs stop at the topmost shared folder so private parents
        # above it are never disclosed.
        shared_at = [i for i, a in enumerate(ancestors) if _is_shared(a)]
        if not _is_shared(project) and not shared_at:
            return web.json_response({"error": "Not found"}, status=404)
        ancestors = ancestors[:shared_at[-1] + 1] if shared_at else []

    subfolders = await db.projects.find({
        "parent_id": project_id,
        "deleted": {"$ne": True},
    }).sort("name", 1).to_list(None)

    # Fetch conversations in this project (transcripts are loaded on open)
    conversations = await db.conversations.find(
        {"project_id": project_id, "deleted": {"$ne": True}},
        {"messages": 0, "final_response": 0},
    ).sort("started_at", -1).to_list(200)

    return web.json_response({
        "project": _serialize(project),
        "breadcrumbs": _serialize(list(reversed(ancestors))),
        "subfolders": _serialize(subfolders),
        "conversations": _serialize(conversations),
        "can_manage": can_manage,
        "shared": _is_shared(project) or any(_is_shared(a) for a in ancestors),
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
        name, error = _validate_name(body["name"])
        if error:
            return web.json_response({"error": error}, status=400)
        updates["name"] = name
    if "parent_id" in body:
        # Move the folder (null = top level). The target must belong to the same
        # owner and must not be the folder itself or anything nested inside it.
        parent_id = body["parent_id"] or None
        if parent_id:
            parent = await db.projects.find_one({"project_id": parent_id, "deleted": {"$ne": True}})
            if not parent or parent.get("created_by") != project.get("created_by"):
                return web.json_response({"error": "Parent folder not found"}, status=404)
            if parent_id == project_id or parent_id in await _descendant_ids(db, project_id):
                return web.json_response(
                    {"error": "A folder cannot be moved into itself or one of its sub-folders"}, status=400)
        updates["parent_id"] = parent_id
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
    """DELETE /api/projects/{project_id} -- soft delete the folder and its sub-folders.

    Conversations are never deleted; they are unlinked and return to the main list.
    """
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

    doomed = [project_id, *await _descendant_ids(db, project_id)]

    # Soft-delete the folder and everything nested under it
    await db.projects.update_many(
        {"project_id": {"$in": doomed}},
        {"$set": {"deleted": True, "deleted_at": now, "deleted_by": user_email}},
    )

    # Unlink all conversations from the deleted folders
    await db.conversations.update_many(
        {"project_id": {"$in": doomed}},
        {"$set": {"project_id": None}},
    )

    return web.json_response({"deleted": True})


async def handle_share_project(request: web.Request) -> web.Response:
    """PUT /api/projects/{project_id}/share -- owner toggles link sharing."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    project_id = request.match_info["project_id"]
    project = await db.projects.find_one({"project_id": project_id, "deleted": {"$ne": True}})
    if not project:
        return web.json_response({"error": "Not found"}, status=404)
    # Same rule as conversation sharing: only the owner, not even an admin.
    if project.get("created_by") != user_email:
        return web.json_response({"error": "Only the folder owner can change sharing"}, status=403)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body.get("shared"), bool):
        return web.json_response({"error": "shared must be a boolean"}, status=400)

    await db.projects.update_one(
        {"project_id": project_id},
        {"$set": {
            "visibility": "shared" if body["shared"] else "private",
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return web.json_response({"shared": body["shared"], "project_id": project_id})


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
    from api.routes import _check_conversation_manage_access
    if not _check_conversation_manage_access(conversation, user_email, system_role):
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

    from api.routes import _check_conversation_manage_access
    if not _check_conversation_manage_access(conversation, user_email, system_role):
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
    app.router.add_put("/api/projects/{project_id}/share", handle_share_project)
    app.router.add_post("/api/conversations/{conversation_id}/project", handle_assign_conversation_to_project)
    app.router.add_delete("/api/conversations/{conversation_id}/project", handle_remove_conversation_from_project)
