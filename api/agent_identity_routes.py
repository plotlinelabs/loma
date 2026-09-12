"""Agent identity CRUD routes.

Agent identities are user-created, shareable agent personas: a name, a 1-2 line
description, an identity prompt, a scoped set of Loma skills and tools, and an
auth policy. In each chat the user picks an agent to talk to; the agent's scope
is layered into the conversation as a context block (same mechanism as the
task-board working-context prompt).

v1 supports auth_mode="requester" only: the agent always acts with the
credentials of the person chatting, so skill/tool scoping here is a focus and
governance concern, not a privilege boundary. Owner-auth (run-as-creator, per
the scheduler/executor.py precedent) is intentionally deferred — it turns the
tool allowlist into a security boundary and needs hard enforcement plus an
audit trail first.
"""

import logging
import uuid
from datetime import datetime, timezone

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import (
    ROLE_HIERARCHY,
    get_system_role,
    get_user_email,
)

logger = logging.getLogger(__name__)

VISIBILITIES = ("private", "workspace")
AUTH_MODES = ("requester",)  # "owner" and "shared" land with the audit trail
AVATAR_MOTIFS = ("round", "square", "halo", "antenna")

MAX_NAME_LEN = 60
MAX_DESCRIPTION_LEN = 200
MAX_PROMPT_LEN = 8000
MAX_SKILLS = 50
MAX_TOOLS = 50
MAX_SHARES = 100


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
            elif isinstance(v, (dict, list)):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


def _visible_query(user_email: str, team_ids: list[str]) -> dict:
    """Agents this user can see and chat with: their own, workspace-shared,
    shared with them directly, or shared with a team they belong to."""
    ors: list[dict] = [
        {"created_by": user_email},
        {"visibility": "workspace"},
        {"shared_with.users": user_email},
    ]
    if team_ids:
        ors.append({"shared_with.teams": {"$in": team_ids}})
    return {"deleted": {"$ne": True}, "$or": ors}


async def _user_team_ids(db, user_email: str) -> list[str]:
    teams = await db.teams.find({"members": user_email}, {"team_id": 1}).to_list(100)
    return [t["team_id"] for t in teams if t.get("team_id")]


def _can_manage(agent: dict, user_email: str, system_role: str) -> bool:
    """Edit/delete: the owner, or an admin (so shared agents stay governable)."""
    return system_role == "admin" or agent.get("created_by") == user_email


def _role_at_least(system_role: str, required: str) -> bool:
    return ROLE_HIERARCHY.get(system_role, 0) >= ROLE_HIERARCHY.get(required, 0)


def _validate_avatar(avatar) -> dict:
    """Avatar spec: a deterministic seed plus a motif; the SVG renders client-side."""
    if not isinstance(avatar, dict):
        return {"seed": 1, "motif": "round"}
    seed = avatar.get("seed")
    motif = avatar.get("motif")
    return {
        "seed": int(seed) if isinstance(seed, (int, float)) else 1,
        "motif": motif if motif in AVATAR_MOTIFS else "round",
    }


async def _validate_body(db, body: dict, *, partial: bool) -> tuple[dict, str | None]:
    """Validate a create/update payload. Returns (fields, error)."""
    fields: dict = {}

    if "name" in body or not partial:
        name = (body.get("name") or "").strip()
        if not name:
            return {}, "Agent name is required"
        if len(name) > MAX_NAME_LEN:
            return {}, f"Agent name must be {MAX_NAME_LEN} characters or less"
        fields["name"] = name

    if "description" in body or not partial:
        description = (body.get("description") or "").strip()
        if not description:
            return {}, "A short description is required so others can see what this agent does"
        if len(description) > MAX_DESCRIPTION_LEN:
            return {}, f"Description must be {MAX_DESCRIPTION_LEN} characters or less"
        fields["description"] = description

    if "identity_prompt" in body:
        prompt = (body.get("identity_prompt") or "").strip()
        if len(prompt) > MAX_PROMPT_LEN:
            return {}, f"Identity prompt must be {MAX_PROMPT_LEN} characters or less"
        fields["identity_prompt"] = prompt

    if "skills" in body:
        skills = body.get("skills") or []
        if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
            return {}, "skills must be a list of skill slugs"
        skills = [s.strip() for s in skills if s.strip()][:MAX_SKILLS]
        if skills:
            known = await db.skills.find(
                {"slug": {"$in": skills}}, {"slug": 1},
            ).to_list(MAX_SKILLS)
            known_slugs = {doc["slug"] for doc in known}
            unknown = [s for s in skills if s not in known_slugs]
            if unknown:
                return {}, f"Unknown skills: {', '.join(unknown[:5])}"
        fields["skills"] = skills

    if "tools" in body:
        tools = body.get("tools") or []
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            return {}, "tools must be a list of tool keys"
        fields["tools"] = [t.strip()[:100] for t in tools if t.strip()][:MAX_TOOLS]

    if "visibility" in body:
        visibility = body.get("visibility")
        if visibility not in VISIBILITIES:
            return {}, f"visibility must be one of: {', '.join(VISIBILITIES)}"
        fields["visibility"] = visibility

    if "shared_with" in body:
        shared = body.get("shared_with") or {}
        if not isinstance(shared, dict):
            return {}, "shared_with must be an object with users and teams lists"
        users = shared.get("users") or []
        teams = shared.get("teams") or []
        if not isinstance(users, list) or not all(isinstance(u, str) for u in users):
            return {}, "shared_with.users must be a list of emails"
        if not isinstance(teams, list) or not all(isinstance(t, str) for t in teams):
            return {}, "shared_with.teams must be a list of team ids"
        users = sorted({u.strip() for u in users if u.strip()})[:MAX_SHARES]
        teams = sorted({t.strip() for t in teams if t.strip()})[:MAX_SHARES]
        if users:
            known = await db.users.find(
                {"email": {"$in": users}}, {"email": 1},
            ).to_list(MAX_SHARES)
            unknown = set(users) - {doc["email"] for doc in known}
            if unknown:
                return {}, f"Unknown users: {', '.join(sorted(unknown)[:5])}"
        if teams:
            known = await db.teams.find(
                {"team_id": {"$in": teams}}, {"team_id": 1},
            ).to_list(MAX_SHARES)
            unknown = set(teams) - {doc["team_id"] for doc in known}
            if unknown:
                return {}, f"Unknown teams: {', '.join(sorted(unknown)[:5])}"
        fields["shared_with"] = {"users": users, "teams": teams}

    if "auth_mode" in body:
        auth_mode = body.get("auth_mode")
        if auth_mode not in AUTH_MODES:
            return {}, "Only requester auth is supported for now — the agent acts with the credentials of the person chatting"
        fields["auth_mode"] = auth_mode

    if "default_model" in body:
        model = body.get("default_model")
        if model is not None and not isinstance(model, str):
            return {}, "default_model must be a provider/model string"
        fields["default_model"] = (model or "").strip() or None

    if "avatar" in body:
        fields["avatar"] = _validate_avatar(body.get("avatar"))

    return fields, None


async def handle_create_agent(request: web.Request) -> web.Response:
    """POST /api/agent-identities — create an agent identity."""
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

    fields, error = await _validate_body(db, body, partial=False)
    if error:
        return web.json_response({"error": error}, status=400)

    visibility = fields.get("visibility", "private")
    if visibility == "workspace" and not _role_at_least(get_system_role(request), "operator"):
        return web.json_response(
            {"error": "Operator access required to share an agent with the whole workspace"},
            status=403,
        )

    now = datetime.now(timezone.utc)
    agent = {
        "agent_id": str(uuid.uuid4()),
        "name": fields["name"],
        "description": fields["description"],
        "identity_prompt": fields.get("identity_prompt", ""),
        "skills": fields.get("skills", []),
        "tools": fields.get("tools", []),
        "auth_mode": fields.get("auth_mode", "requester"),
        "visibility": visibility,
        "shared_with": fields.get("shared_with", {"users": [], "teams": []}),
        "default_model": fields.get("default_model"),
        "avatar": fields.get("avatar", _validate_avatar(None)),
        "status": "active",
        "created_by": user_email,
        "created_at": now,
        "updated_at": now,
        "deleted": False,
    }
    await db.agent_identities.insert_one(agent)
    return web.json_response({"agent": _serialize(agent)}, status=201)


async def handle_list_agents(request: web.Request) -> web.Response:
    """GET /api/agent-identities — agents visible to the requester, with usage counts."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    team_ids = await _user_team_ids(db, user_email)
    agents = await db.agent_identities.find(_visible_query(user_email, team_ids)).sort(
        [("visibility", -1), ("created_at", -1)],
    ).to_list(200)

    agent_ids = [a["agent_id"] for a in agents]
    counts_map: dict = {}
    if agent_ids:
        counts = await db.conversations.aggregate([
            {"$match": {"metadata.agent_id": {"$in": agent_ids}, "deleted": {"$ne": True}}},
            {"$group": {"_id": "$metadata.agent_id", "count": {"$sum": 1}}},
        ]).to_list(200)
        counts_map = {c["_id"]: c["count"] for c in counts}

    serialized = _serialize(agents)
    for agent in serialized:
        agent["conversation_count"] = counts_map.get(agent["agent_id"], 0)

    return web.json_response({"agents": serialized})


async def handle_share_directory(request: web.Request) -> web.Response:
    """GET /api/agent-identities/directory — people and teams available as share
    targets. Deliberately minimal fields (no roles, no tool assignments) so it's
    safe to expose to every authenticated user, unlike the governance lists.
    """
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    if not get_user_email(request):
        return web.json_response({"error": "Authentication required"}, status=401)

    users = await db.users.find(
        {"status": {"$nin": ["rejected", "pending"]}},
        {"email": 1, "name": 1, "avatar": 1},
    ).sort("email", 1).to_list(300)
    teams = await db.teams.find(
        {}, {"team_id": 1, "name": 1, "color": 1, "bg_color": 1},
    ).sort("name", 1).to_list(100)

    return web.json_response({
        "users": [
            {"email": u["email"], "name": u.get("name", ""), "avatar": u.get("avatar", "")}
            for u in users
        ],
        "teams": _serialize([{k: t.get(k) for k in ("team_id", "name", "color", "bg_color")} for t in teams]),
    })


async def handle_get_agent(request: web.Request) -> web.Response:
    """GET /api/agent-identities/{agent_id}."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    team_ids = await _user_team_ids(db, user_email)
    agent = await db.agent_identities.find_one({
        "agent_id": request.match_info["agent_id"],
        **_visible_query(user_email, team_ids),
    })
    if not agent:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({"agent": _serialize(agent)})


async def handle_update_agent(request: web.Request) -> web.Response:
    """PATCH /api/agent-identities/{agent_id} — owner or admin."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    agent = await db.agent_identities.find_one({
        "agent_id": request.match_info["agent_id"],
        "deleted": {"$ne": True},
    })
    system_role = get_system_role(request)
    if not agent or not _can_manage(agent, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    fields, error = await _validate_body(db, body, partial=True)
    if error:
        return web.json_response({"error": error}, status=400)

    if (
        fields.get("visibility") == "workspace"
        and agent.get("visibility") != "workspace"
        and not _role_at_least(system_role, "operator")
    ):
        return web.json_response(
            {"error": "Operator access required to share an agent with the whole workspace"},
            status=403,
        )

    # Owners and admins can disable a misbehaving agent without deleting it.
    if "status" in body:
        if body["status"] not in ("active", "disabled"):
            return web.json_response({"error": "status must be active or disabled"}, status=400)
        fields["status"] = body["status"]

    if not fields:
        return web.json_response({"agent": _serialize(agent)})

    fields["updated_at"] = datetime.now(timezone.utc)
    await db.agent_identities.update_one(
        {"agent_id": agent["agent_id"]}, {"$set": fields},
    )
    updated = await db.agent_identities.find_one({"agent_id": agent["agent_id"]})
    return web.json_response({"agent": _serialize(updated)})


async def handle_delete_agent(request: web.Request) -> web.Response:
    """DELETE /api/agent-identities/{agent_id} — soft delete, owner or admin."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    agent = await db.agent_identities.find_one({
        "agent_id": request.match_info["agent_id"],
        "deleted": {"$ne": True},
    })
    if not agent or not _can_manage(agent, user_email, get_system_role(request)):
        return web.json_response({"error": "Not found"}, status=404)

    await db.agent_identities.update_one(
        {"agent_id": agent["agent_id"]},
        {"$set": {
            "deleted": True,
            "deleted_at": datetime.now(timezone.utc),
            "deleted_by": user_email,
        }},
    )
    return web.json_response({"deleted": True})


# ── Chat integration ─────────────────────────────────────────────────────────

async def resolve_agent_for_chat(db, agent_id: str, user_email: str) -> dict | None:
    """Load an active agent the user is allowed to chat with, or None."""
    if not agent_id:
        return None
    team_ids = await _user_team_ids(db, user_email)
    return await db.agent_identities.find_one({
        "agent_id": agent_id,
        "status": {"$ne": "disabled"},
        **_visible_query(user_email, team_ids),
    })


async def build_agent_context_block(db, agent: dict) -> str:
    """Render an agent identity as a conversation context block.

    Layered into the message text the same way the task-board working-context
    prompt is — the pooled system prompt (and its cache) stays untouched.
    """
    lines = [
        f"## Active Agent: {agent['name']}",
        f"For this conversation you are \"{agent['name']}\" — {agent['description']}",
        "Stay within this agent's scope: politely decline requests that clearly "
        "belong to a different agent or need tools outside the scope below, and "
        "point the user to the Agents page to switch.",
    ]

    identity_prompt = (agent.get("identity_prompt") or "").strip()
    if identity_prompt:
        lines.append(f"\n{identity_prompt}")

    skills = agent.get("skills") or []
    if skills:
        docs = await db.skills.find(
            {"slug": {"$in": skills}, "enabled": {"$ne": False}},
            {"slug": 1, "name": 1, "description": 1},
        ).to_list(len(skills))
        if docs:
            skill_lines = "\n".join(
                f"- {d['slug']} — {d.get('description') or d.get('name', '')}".rstrip(" —")
                for d in docs
            )
            lines.append(
                "\nSkill scope: this agent works from ONLY these Loma skills — "
                "ignore other entries in the skill index:\n" + skill_lines
            )

    tools = agent.get("tools") or []
    if tools:
        lines.append(
            "\nTool scope: this agent may use ONLY these tools/integrations "
            "(plus basic file and shell operations): " + ", ".join(tools) + ". "
            "If a request needs a tool outside this list, say so instead of using it."
        )

    return "\n".join(lines)


def setup_agent_identity_routes(app: web.Application):
    """Register agent identity routes on the aiohttp app."""
    app.router.add_post("/api/agent-identities", handle_create_agent)
    app.router.add_get("/api/agent-identities", handle_list_agents)
    # Static route must precede the {agent_id} wildcard
    app.router.add_get("/api/agent-identities/directory", handle_share_directory)
    app.router.add_get("/api/agent-identities/{agent_id}", handle_get_agent)
    app.router.add_patch("/api/agent-identities/{agent_id}", handle_update_agent)
    app.router.add_delete("/api/agent-identities/{agent_id}", handle_delete_agent)
