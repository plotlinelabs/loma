"""Governance API — users, teams, tool configs, role resolution."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import (
    ROLE_HIERARCHY,
    get_user_email,
    get_system_role,
    require_admin,
    require_maintainer_or_above,
    get_effective_role,
)

logger = logging.getLogger(__name__)

VALID_SYSTEM_ROLES = ("admin", "maintainer", "operator", "analyst", "chatter")

_IS_PREVIEW = os.environ.get("PREVIEW_MODE", "").lower() in ("1", "true", "yes")
_IS_DEV = os.environ.get("ENV", "").upper() == "DEV"


def _configured_default_role() -> str:
    role = os.environ.get("PREVIEW_SYSTEM_ROLE", "").strip()
    if (_IS_PREVIEW or _IS_DEV) and role in ROLE_HIERARCHY:
        return role
    if _IS_PREVIEW:
        return "maintainer"
    if _IS_DEV:
        return "operator"
    return "chatter"


_DEFAULT_ROLE = _configured_default_role()


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
                result[k] = v.isoformat() + ("Z" if v.tzinfo is None else "")
            elif isinstance(v, dict):
                result[k] = _serialize(v)
            elif isinstance(v, list):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        return doc.isoformat() + ("Z" if doc.tzinfo is None else "")
    return doc


# ── Current user ───────────────────────────────────────────────────────────


async def handle_get_me(request: web.Request) -> web.Response:
    """GET /api/governance/me — current user profile, auto-provisions on first login."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        # No identity on the request (e.g. proxy did not forward X-User-Email).
        # Don't auto-provision a blank user or crash on email[0]; report unauthenticated.
        return web.json_response({"error": "Authentication required"}, status=401)
    user = await db.users.find_one({"email": email})

    if user is None:
        # Auto-provision: first user ever gets admin, rest get env-appropriate default
        count = await db.users.count_documents({})
        user = {
            "email": email,
            "name": email.split("@")[0].replace(".", " ").title(),
            "avatar": email[0].upper(),
            "system_role": "admin" if count == 0 else _DEFAULT_ROLE,
            # First user ever is auto-approved; everyone else awaits admin approval.
            "status": "active" if count == 0 else "pending",
            "tool_assignments": {},
            "theme_preference": "light",
            "claude_pool_enabled": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        await db.users.insert_one(user)
        logger.info(
            "Auto-provisioned user %s with role %s (status %s)",
            email, user["system_role"], user["status"],
        )

    serialized_user = _serialize(user)
    # Legacy users created before the approval flow are treated as active.
    serialized_user.setdefault("status", "active")
    if request.get("preview_fallback_user"):
        serialized_user["system_role"] = _DEFAULT_ROLE

    return web.json_response(serialized_user)


async def handle_update_my_theme(request: web.Request) -> web.Response:
    """PATCH /api/governance/me/theme — update own theme preference."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = get_user_email(request)
    if not email:
        return web.json_response({"error": "Authentication required"}, status=401)

    body = await request.json()
    theme = body.get("theme_preference")
    if theme not in ("light", "dark", "system"):
        return web.json_response(
            {"error": "Invalid theme. Must be one of: light, dark, system"},
            status=400,
        )

    await db.users.update_one(
        {"email": email},
        {"$set": {
            "theme_preference": theme,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return web.json_response({"theme_preference": theme})


# ── Users CRUD (admin only) ───────────────────────────────────────────────


async def handle_list_users(request: web.Request) -> web.Response:
    """GET /api/governance/users — list all users."""
    require_admin(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    users = await db.users.find().sort("email", 1).to_list(200)
    serialized = _serialize(users)

    # Enrich with Claude auth status per user
    claude_users_dir = Path(os.environ.get("CLAUDE_USERS_DIR", "/opt/claude-users"))
    for user in serialized:
        # Legacy users created before the approval flow are treated as active.
        user.setdefault("status", "active")
        email = user.get("email", "")
        config_file = claude_users_dir / email / ".claude.json"
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text())
                oauth = data.get("oauthAccount", {})
                if oauth.get("emailAddress"):
                    user["claude_connected"] = True
                    user["claude_email"] = oauth["emailAddress"]
                    # Default to True if field not yet set
                    if "claude_pool_enabled" not in user:
                        user["claude_pool_enabled"] = True
                    continue
            except Exception:
                pass
        user["claude_connected"] = False

    return web.json_response({"users": serialized})


async def handle_get_user(request: web.Request) -> web.Response:
    """GET /api/governance/users/{email} — single user detail."""
    require_admin(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = request.match_info["email"]
    user = await db.users.find_one({"email": email})
    if user is None:
        return web.json_response({"error": "User not found"}, status=404)

    # Also fetch teams this user belongs to
    teams = await db.teams.find({"members": email}).to_list(50)

    return web.json_response({
        "user": _serialize(user),
        "teams": _serialize(teams),
    })


async def handle_update_user(request: web.Request) -> web.Response:
    """PATCH /api/governance/users/{email} — update system_role and/or tool_assignments."""
    require_admin(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = request.match_info["email"]
    body = await request.json()

    updates = {"updated_at": datetime.now(timezone.utc)}

    if "system_role" in body:
        role = body["system_role"]
        if role not in VALID_SYSTEM_ROLES:
            return web.json_response(
                {"error": f"Invalid role. Must be one of: {', '.join(VALID_SYSTEM_ROLES)}"},
                status=400,
            )
        updates["system_role"] = role

    if "status" in body:
        status = body["status"]
        if status not in ("active", "pending", "rejected"):
            return web.json_response(
                {"error": "Invalid status. Must be one of: active, pending, rejected"},
                status=400,
            )
        updates["status"] = status

    if "tool_assignments" in body:
        updates["tool_assignments"] = body["tool_assignments"]

    if "name" in body:
        updates["name"] = body["name"]

    if "claude_pool_enabled" in body:
        if not isinstance(body["claude_pool_enabled"], bool):
            return web.json_response({"error": "claude_pool_enabled must be a boolean"}, status=400)
        updates["claude_pool_enabled"] = body["claude_pool_enabled"]

    result = await db.users.update_one({"email": email}, {"$set": updates})
    if result.matched_count == 0:
        return web.json_response({"error": "User not found"}, status=404)

    # If pool toggle changed, refresh the pool account list
    if "claude_pool_enabled" in updates:
        try:
            from agent.pool import get_pool
            pool = get_pool()
            pool.refresh_accounts()
        except RuntimeError:
            pass

    user = await db.users.find_one({"email": email})
    return web.json_response({"user": _serialize(user)})


async def handle_delete_user(request: web.Request) -> web.Response:
    """DELETE /api/governance/users/{email} — remove a user from the workspace."""
    require_admin(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = request.match_info["email"]

    if email == get_user_email(request):
        return web.json_response({"error": "You cannot remove yourself."}, status=400)

    target = await db.users.find_one({"email": email})
    if target is None:
        return web.json_response({"error": "User not found"}, status=404)

    # Never leave the workspace without an admin.
    if target.get("system_role") == "admin":
        admin_count = await db.users.count_documents({"system_role": "admin"})
        if admin_count <= 1:
            return web.json_response({"error": "Cannot remove the last admin."}, status=400)

    await db.users.delete_one({"email": email})

    # The removed user may have been a Claude pool account — refresh the list.
    try:
        from agent.pool import get_pool
        get_pool().refresh_accounts()
    except RuntimeError:
        pass

    return web.json_response({"deleted": True})


# ── Teams CRUD (admin only) ───────────────────────────────────────────────


async def handle_list_teams(request: web.Request) -> web.Response:
    """GET /api/governance/teams — list all teams."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    teams = await db.teams.find().sort("name", 1).to_list(50)
    return web.json_response({"teams": _serialize(teams)})


async def handle_get_team(request: web.Request) -> web.Response:
    """GET /api/governance/teams/{team_id} — single team detail."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    team_id = request.match_info["team_id"]
    team = await db.teams.find_one({"team_id": team_id})
    if team is None:
        return web.json_response({"error": "Team not found"}, status=404)

    # Fetch member details
    members = await db.users.find(
        {"email": {"$in": team.get("members", [])}},
    ).to_list(100)

    return web.json_response({
        "team": _serialize(team),
        "members": _serialize(members),
    })


async def handle_create_team(request: web.Request) -> web.Response:
    """POST /api/governance/teams — create a new team."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    body = await request.json()
    required = ["team_id", "name"]
    for field in required:
        if field not in body:
            return web.json_response({"error": f"Missing required field: {field}"}, status=400)

    existing = await db.teams.find_one({"team_id": body["team_id"]})
    if existing:
        return web.json_response({"error": "Team ID already exists"}, status=409)

    now = datetime.now(timezone.utc)
    team = {
        "team_id": body["team_id"],
        "name": body["name"],
        "color": body.get("color", "#6B7280"),
        "bg_color": body.get("bg_color", "#F3F4F6"),
        "members": body.get("members", []),
        "tool_defaults": body.get("tool_defaults", {}),
        "created_at": now,
        "updated_at": now,
    }
    await db.teams.insert_one(team)
    return web.json_response({"team": _serialize(team)}, status=201)


async def handle_update_team(request: web.Request) -> web.Response:
    """PATCH /api/governance/teams/{team_id} — update team fields."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    team_id = request.match_info["team_id"]
    body = await request.json()

    allowed = {"name", "color", "bg_color", "members", "tool_defaults"}
    updates = {k: v for k, v in body.items() if k in allowed}
    updates["updated_at"] = datetime.now(timezone.utc)

    result = await db.teams.update_one({"team_id": team_id}, {"$set": updates})
    if result.matched_count == 0:
        return web.json_response({"error": "Team not found"}, status=404)

    team = await db.teams.find_one({"team_id": team_id})
    return web.json_response({"team": _serialize(team)})


async def handle_delete_team(request: web.Request) -> web.Response:
    """DELETE /api/governance/teams/{team_id} — delete a team."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    team_id = request.match_info["team_id"]
    result = await db.teams.delete_one({"team_id": team_id})
    if result.deleted_count == 0:
        return web.json_response({"error": "Team not found"}, status=404)

    return web.json_response({"deleted": True})


# ── Tool Configs ───────────────────────────────────────────────────────────


async def handle_list_tool_configs(request: web.Request) -> web.Response:
    """GET /api/governance/tools — list all tool configs."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    configs = await db.tool_configs.find().sort("tool_key", 1).to_list(50)
    return web.json_response({"tools": _serialize(configs)})


async def handle_update_tool_config(request: web.Request) -> web.Response:
    """PATCH /api/governance/tools/{tool_key} — update a tool config."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    tool_key = request.match_info["tool_key"]
    body = await request.json()

    allowed = {"auth_mode", "roles", "oauth"}
    updates = {k: v for k, v in body.items() if k in allowed}
    updates["updated_at"] = datetime.now(timezone.utc)

    result = await db.tool_configs.update_one({"tool_key": tool_key}, {"$set": updates})
    if result.matched_count == 0:
        return web.json_response({"error": "Tool config not found"}, status=404)

    config = await db.tool_configs.find_one({"tool_key": tool_key})
    return web.json_response({"tool": _serialize(config)})


# ── Role resolution ────────────────────────────────────────────────────────


async def handle_resolve_role(request: web.Request) -> web.Response:
    """GET /api/governance/resolve/{email}/{tool} — compute effective tool-level role."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    email = request.match_info["email"]
    tool_key = request.match_info["tool"]

    user = await db.users.find_one({"email": email})
    if user is None:
        return web.json_response({"error": "User not found"}, status=404)

    teams = await db.teams.find({"members": email}).to_list(50)
    result = get_effective_role(user, teams, tool_key)

    return web.json_response(result)


# ── Route registration ─────────────────────────────────────────────────────


def setup_governance_routes(app: web.Application):
    """Register all governance routes."""
    # Current user (auto-provision)
    app.router.add_get("/api/governance/me", handle_get_me)

    app.router.add_patch("/api/governance/me/theme", handle_update_my_theme)

    # Users (admin only)
    app.router.add_get("/api/governance/users", handle_list_users)
    app.router.add_get("/api/governance/users/{email}", handle_get_user)
    app.router.add_patch("/api/governance/users/{email}", handle_update_user)
    app.router.add_delete("/api/governance/users/{email}", handle_delete_user)

    # Teams (admin only)
    app.router.add_get("/api/governance/teams", handle_list_teams)
    app.router.add_get("/api/governance/teams/{team_id}", handle_get_team)
    app.router.add_post("/api/governance/teams", handle_create_team)
    app.router.add_patch("/api/governance/teams/{team_id}", handle_update_team)
    app.router.add_delete("/api/governance/teams/{team_id}", handle_delete_team)

    # Tool configs
    app.router.add_get("/api/governance/tools", handle_list_tool_configs)
    app.router.add_patch("/api/governance/tools/{tool_key}", handle_update_tool_config)

    # Role resolution
    app.router.add_get("/api/governance/resolve/{email}/{tool}", handle_resolve_role)
