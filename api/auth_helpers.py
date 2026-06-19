"""Authorization helpers for role-based access control.

System roles (highest to lowest):
  admin      — sees all chats, manages users/roles, full access
  maintainer — same as admin except: cannot manage users, sees only own conversations
  operator   — own chats + create/edit flows & tasks + view analytics
  analyst    — own chats + view flows & flow-spawned conversations + view analytics
  chatter    — chat + view own chats only
"""

from aiohttp import web

ROLE_HIERARCHY = {"admin": 5, "maintainer": 4, "operator": 3, "analyst": 2, "chatter": 1}

# Tool-level role priorities (for getEffectiveRole resolution)
TOOL_ROLE_PRIORITY = {"Admin": 3, "Analyst": 2, "Read-only": 1, "Support": 1}


def get_system_role(request) -> str:
    """Get the system role from the request, defaulting to chatter."""
    return request.get("system_role", "chatter")


def get_user_email(request) -> str:
    """Get the authenticated user email from the request."""
    return request.get("user_email", "")


def _role_level(role: str) -> int:
    return ROLE_HIERARCHY.get(role, 0)


def require_admin(request):
    """Raise 403 if user is not admin. Use for user management only."""
    if get_system_role(request) != "admin":
        raise web.HTTPForbidden(
            text='{"error": "Admin access required"}',
            content_type="application/json",
        )


def require_maintainer_or_above(request):
    """Raise 403 if user is below maintainer level. Use for admin-like actions that maintainers can also perform."""
    if _role_level(get_system_role(request)) < _role_level("maintainer"):
        raise web.HTTPForbidden(
            text='{"error": "Maintainer access required"}',
            content_type="application/json",
        )


def require_operator_or_above(request):
    """Raise 403 if user is analyst or chatter."""
    if _role_level(get_system_role(request)) < _role_level("operator"):
        raise web.HTTPForbidden(
            text='{"error": "Operator access required"}',
            content_type="application/json",
        )


def require_analyst_or_above(request):
    """Raise 403 if user is chatter."""
    if _role_level(get_system_role(request)) < _role_level("analyst"):
        raise web.HTTPForbidden(
            text='{"error": "Analyst access required"}',
            content_type="application/json",
        )


def get_effective_role(user: dict, teams: list[dict], tool_key: str) -> dict:
    """Resolve effective tool-level role for a user.

    Resolution order: direct user assignment > highest-privilege team default > None.
    Python port of getEffectiveRole() from mock-governance.ts.

    Returns: {"role": str | None, "source": str}
    """
    # 1. Direct user-level assignment takes priority
    direct = (user.get("tool_assignments") or {}).get(tool_key, {})
    if direct.get("role"):
        return {"role": direct["role"], "source": "direct"}

    # 2. Check team defaults — highest-privilege team wins
    best = None
    for team in teams:
        if user.get("email") not in (team.get("members") or []):
            continue
        td = (team.get("tool_defaults") or {}).get(tool_key, {})
        if not td.get("role"):
            continue
        priority = TOOL_ROLE_PRIORITY.get(td["role"], 0)
        if best is None or priority > TOOL_ROLE_PRIORITY.get(best["role"], 0):
            best = {"role": td["role"], "source": team.get("name", team.get("team_id", ""))}

    if best:
        return best

    # 3. No access
    return {"role": None, "source": "none"}
