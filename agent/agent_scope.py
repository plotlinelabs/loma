"""Runtime enforcement of an agent's tool and skill scope.

An agent's `tools[]` and `skills[]` used to be prompt text only: the model was
told to stay in scope, but every MCP server and CLI tool stayed loaded and
nothing stopped or recorded a call outside the list.

This module turns an agent document into a JSON-safe *scope* (built once per
run in the request handler, so it rides `tool_config` through the deploy queue
and remote workers), and checks every tool call against it:

- MCP servers outside the scope are not loaded into the session at all.
- Calls that cannot be filtered that way (Bash running `tools/<x>.py`, the
  `loma_skills.py` CLI, the Skill tool, claude.ai connectors) are denied by a
  PreToolUse hook whose message tells the model which agent to send the user to.

The same check runs on every runtime:

- Claude Agent SDK: PreToolUse hook (`make_pre_tool_use_hook`).
- OpenCode: a dedicated session whose permission rules remove out-of-scope MCP
  tools and turn Bash/Skill calls into permission requests, which the runtime
  answers with `check_opencode_permission` (`opencode_permission_rules`).
- Codex: a per-run worker with only in-scope MCP servers loaded and command
  approval switched on; each command is checked with `check_codex_command`.

Empty `tools[]` / `skills[]` keep their existing meaning: everything allowed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_AGENT_NAME = "Loma"

# Loma's own plumbing servers stay available to every agent.
INTERNAL_MCP_SERVERS = frozenset({"loma-recall", "loma-tasks"})

# Agent `tools[]` values for personal tools are saved as these CLI-ish keys
# (dashboard/src/app/agents/selection-options.ts PERSONAL_TOOLS).
PERSONAL_TOOL_SCRIPTS: dict[str, tuple[str, ...]] = {
    "gmail": ("gmail",),
    "google-drive": ("google_drive",),
    "google-calendar": ("google_calendar",),
    "google-docs": ("google_docs_personal", "google_apps_script", "google_slides"),
    "google-sheets": ("google_sheets",),
    "slack": ("slack_user", "slack_reader"),
    "telegram": ("telegram",),
}

# Integration provider key -> CLI scripts under tools/ that reach the same
# system through Bash. MCP server names come from integrations.registry.
PROVIDER_SCRIPTS: dict[str, tuple[str, ...]] = {
    "apollo": ("apollo",),
    "pylon": ("pylon",),
    "posthog": ("posthog",),
    "grafana": ("grafana",),
    "grain": ("grain",),
    "phantombuster": ("phantombuster",),
    "monetize_now": ("monetize_now",),
    "stitch": ("stitch",),
    "customer_admin": ("customer_admin",),
    "dataroom": ("dataroom",),
    "cdn_r2": ("cdn_upload",),
    "zoho_books": ("zoho_books",),
    "slack_bot": ("slack_reader",),
    "linear": ("linear",),
    "sentry": ("sentry",),
    "github": ("github_pr_resolve", "sonarqube"),
    "ashby": ("ashby",),
}

# Every script tied to an integration or a personal account. Scripts not listed
# (notify, task_card, diagrams, pptx_creator, loma_skills, ...) are generic
# helpers and stay available to every agent.
SCOPED_SCRIPTS: frozenset[str] = frozenset(
    s for group in (*PERSONAL_TOOL_SCRIPTS.values(), *PROVIDER_SCRIPTS.values()) for s in group
)

_SCRIPT_RE = re.compile(r"(?<![\w.-])(?:[\w./-]*/)?([a-z_][a-z0-9_]*)\.py\b")
_SKILL_CLI_RE = re.compile(r"loma_skills\.py\b")
_SLUG_RE = re.compile(r"--slug(?:\s+|=)['\"]?([\w.-]+)")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


# ── Versioning ──────────────────────────────────────────────────────────────

def config_hash(agent: dict) -> str:
    """Stable hash of the parts of an agent that change how it behaves."""
    payload = {
        "identity_prompt": (agent.get("identity_prompt") or "").strip(),
        "description": (agent.get("description") or "").strip(),
        "skills": sorted(agent.get("skills") or []),
        "tools": sorted(agent.get("tools") or []),
        "enforce_scope": agent.get("enforce_scope", True) is not False,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def agent_attribution(agent: dict | None) -> dict:
    """Fields stamped on runs and assistant messages for the active agent."""
    if not agent:
        return {"agent_id": None, "agent_name": DEFAULT_AGENT_NAME}
    return {
        "agent_id": agent.get("agent_id"),
        "agent_name": agent.get("name") or DEFAULT_AGENT_NAME,
        "agent_config_version": int(agent.get("config_version") or 1),
    }


# ── Scope resolution ────────────────────────────────────────────────────────

async def build_agent_scope(db, agent: dict) -> dict:
    """Resolve an agent's `tools[]` into MCP server names and CLI scripts."""
    from integrations.registry import PROVIDER_CATALOG

    tools = [t for t in (agent.get("tools") or []) if isinstance(t, str) and t.strip()]
    skills = [s for s in (agent.get("skills") or []) if isinstance(s, str) and s.strip()]

    mcp_servers: set[str] = set()
    scripts: set[str] = set()

    custom: dict[str, str] = {}
    if db is not None and tools:
        try:
            async for doc in db.integrations.find(
                {"is_custom": True}, {"provider": 1, "display_name": 1},
            ):
                custom[_norm(doc.get("display_name") or doc["provider"])] = doc["provider"]
                custom[_norm(doc["provider"])] = doc["provider"]
        except Exception:
            logger.warning("agent scope: could not load custom connectors", exc_info=True)

    catalog: dict[str, str] = {}
    for key, entry in PROVIDER_CATALOG.items():
        catalog[_norm(key)] = key
        catalog[_norm(entry.get("display_name") or key)] = key
        if entry.get("mcp_server_name"):
            catalog[_norm(entry["mcp_server_name"])] = key

    for value in tools:
        if value in PERSONAL_TOOL_SCRIPTS:
            scripts.update(PERSONAL_TOOL_SCRIPTS[value])
            continue
        n = _norm(value)
        provider = catalog.get(n)
        if provider:
            server = PROVIDER_CATALOG[provider].get("mcp_server_name")
            if server:
                mcp_servers.add(server)
            scripts.update(PROVIDER_SCRIPTS.get(provider, ()))
            continue
        if n in custom:
            mcp_servers.add(custom[n])
            continue
        # Unknown value: treat it as a raw MCP server key.
        mcp_servers.add(value.strip())

    return {
        "agent_id": agent.get("agent_id"),
        "agent_name": agent.get("name") or DEFAULT_AGENT_NAME,
        "agent_config_version": int(agent.get("config_version") or 1),
        "enforce": agent.get("enforce_scope", True) is not False,
        "tools_restricted": bool(tools),
        "skills_restricted": bool(skills),
        "mcp_servers": sorted(mcp_servers),
        "scripts": sorted(scripts),
        "skills": sorted(skills),
        "tool_labels": tools,
    }


def scope_is_enforced(scope: dict | None) -> bool:
    return bool(
        isinstance(scope, dict)
        and scope.get("enforce")
        and (scope.get("tools_restricted") or scope.get("skills_restricted"))
    )


def _tools_enforced(scope: dict | None) -> bool:
    return scope_is_enforced(scope) and bool(scope.get("tools_restricted"))


def _allowed_servers(scope: dict) -> set[str]:
    return set(scope.get("mcp_servers") or ()) | INTERNAL_MCP_SERVERS


def filter_mcp_servers(scope: dict | None, servers: dict) -> dict:
    """Drop MCP servers the agent may not use, so their tools never load."""
    if not _tools_enforced(scope):
        return servers
    allowed = _allowed_servers(scope)
    return {name: cfg for name, cfg in servers.items() if name in allowed}


def filter_allowed_tools(scope: dict | None, allowed_tools: list[str]) -> list[str]:
    if not _tools_enforced(scope):
        return allowed_tools
    allowed = _allowed_servers(scope)
    return [
        tool for tool in allowed_tools
        if not tool.startswith("mcp__") or tool[5:].split("__", 1)[0] in allowed
    ]


# ── Per-call check ──────────────────────────────────────────────────────────

def _blocked(scope: dict, kind: str, target: str) -> dict:
    name = scope.get("agent_name") or "This agent"
    if kind == "skill":
        allowed = ", ".join(scope.get("skills") or []) or "none"
        reason = f"The skill '{target}' isn't available to {name}. {name} can use these skills: {allowed}."
    else:
        allowed = ", ".join(scope.get("tool_labels") or []) or "none"
        reason = (
            f"{target} isn't available to {name}. {name} can use: {allowed} "
            f"(plus basic file and shell operations)."
        )
    reason += (
        " Do not retry or work around this. Tell the user this needs a different agent "
        f"and that they can switch to Loma (or an agent that has {target}) from the agent picker."
    )
    return {"kind": kind, "target": target, "reason": reason}


def check_tool_call(scope: dict | None, tool_name: str, tool_input: Any) -> dict | None:
    """Return a block record if this call is outside the agent's scope, else None."""
    if not scope_is_enforced(scope):
        return None
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    if tool_name.startswith("mcp__"):
        if not scope.get("tools_restricted"):
            return None
        server = tool_name[5:].split("__", 1)[0]
        return None if server in _allowed_servers(scope) else _blocked(scope, "tool", server)

    if tool_name == "Skill":
        if not scope.get("skills_restricted"):
            return None
        skill = str(tool_input.get("skill") or tool_input.get("name") or "").split(":")[-1]
        if skill and skill not in set(scope.get("skills") or ()):
            return _blocked(scope, "skill", skill)
        return None

    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        if scope.get("tools_restricted"):
            allowed_scripts = set(scope.get("scripts") or ())
            for script in _SCRIPT_RE.findall(command):
                if script in SCOPED_SCRIPTS and script not in allowed_scripts:
                    return _blocked(scope, "tool", script)
        if scope.get("skills_restricted") and _SKILL_CLI_RE.search(command):
            allowed_skills = set(scope.get("skills") or ())
            for slug in _SLUG_RE.findall(command):
                if slug not in allowed_skills:
                    return _blocked(scope, "skill", slug)
    return None


def make_pre_tool_use_hook(scope: dict, observer=None):
    """Build a claude-agent-sdk PreToolUse hook that denies out-of-scope calls."""

    async def _hook(input_data, tool_use_id, context):  # noqa: ARG001 (SDK signature)
        try:
            tool_name = input_data.get("tool_name", "")
            block = check_tool_call(scope, tool_name, input_data.get("tool_input"))
        except Exception:
            logger.exception("agent scope hook failed; allowing call")
            return {}
        if block is None:
            return {}
        logger.info("Blocked %s for agent %s: %s", tool_name, scope.get("agent_name"), block["target"])
        if observer is not None:
            try:
                await observer.record_blocked_call(
                    tool_name=tool_name, kind=block["kind"], target=block["target"],
                    reason=block["reason"], tool_use_id=tool_use_id or input_data.get("tool_use_id"),
                )
            except Exception:
                logger.warning("Could not record blocked call", exc_info=True)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": block["reason"],
            }
        }

    return _hook


# ── OpenCode ────────────────────────────────────────────────────────────────

def _opencode_name(value: str) -> str:
    # OpenCode registers MCP tools as `<server>_<tool>` with both parts sanitized.
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value or "")


def opencode_permission_rules(scope: dict, mcp_server_names) -> list[dict]:
    """Session permission rules for an agent run on OpenCode.

    OpenCode evaluates rules last-match-wins. Start from "allow everything"
    (the normal dashboard rules), then:
    - deny every tool of an out-of-scope MCP server, which removes those tools
      from the session entirely (OpenCode drops tools denied for pattern "*");
    - ask before every Bash and Skill call so the runtime can run
      `check_opencode_permission` and reply once/reject;
    - deny `task`, because subagent sessions do not inherit these rules.
    """
    rules = [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "external_directory", "pattern": "*", "action": "allow"},
    ]
    if not scope_is_enforced(scope):
        return rules
    if scope.get("tools_restricted"):
        allowed = _allowed_servers(scope)
        for server in sorted(set(mcp_server_names or ())):
            if server not in allowed:
                rules.append({"permission": f"{_opencode_name(server)}_*", "pattern": "*", "action": "deny"})
    rules.append({"permission": "bash", "pattern": "*", "action": "ask"})
    if scope.get("skills_restricted"):
        rules.append({"permission": "skill", "pattern": "*", "action": "ask"})
    rules.append({"permission": "task", "pattern": "*", "action": "deny"})
    return rules


def _opencode_mcp_tool(permission: str, mcp_server_names) -> str | None:
    """Map an OpenCode tool permission like `linear_list_issues` to `mcp__linear__list_issues`."""
    # Longest server name first so `google_drive` wins over `google`.
    for server in sorted(set(mcp_server_names or ()), key=len, reverse=True):
        prefix = f"{_opencode_name(server)}_"
        if permission.startswith(prefix):
            return f"mcp__{server}__{permission[len(prefix):]}"
    return None


def check_opencode_permission(scope: dict | None, request: dict, mcp_server_names=()) -> dict | None:
    """Check an OpenCode `permission.asked` request against the agent's scope."""
    if not scope_is_enforced(scope):
        return None
    permission = str(request.get("permission") or "")
    patterns = [str(p) for p in (request.get("patterns") or []) if p]
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}

    if permission == "bash":
        commands = list(patterns)
        if metadata.get("command"):
            commands.append(str(metadata["command"]))
        return check_tool_call(scope, "Bash", {"command": "\n".join(commands)})
    if permission == "skill":
        for skill in patterns or [str(metadata.get("name") or "")]:
            block = check_tool_call(scope, "Skill", {"skill": skill})
            if block:
                return block
        return None
    if permission == "task":
        return _blocked(scope, "tool", "subagents")
    mcp_tool = _opencode_mcp_tool(permission, mcp_server_names)
    if mcp_tool:
        return check_tool_call(scope, mcp_tool, {})
    return None


# ── Codex ───────────────────────────────────────────────────────────────────

def check_codex_command(scope: dict | None, command: Any) -> dict | None:
    """Check a Codex command-execution approval request against the agent's scope."""
    if isinstance(command, (list, tuple)):
        command = " ".join(str(part) for part in command)
    return check_tool_call(scope, "Bash", {"command": str(command or "")})
