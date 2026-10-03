"""Agent tool/skill scope is enforced on OpenCode and Codex, not just Claude.

OpenCode: the session's permission rules hide out-of-scope MCP tools and turn
Bash/Skill calls into permission requests that the runtime checks.
Codex: a per-run worker loads only in-scope MCP servers and asks before each
command; the command is checked before it runs.
"""
import json
import uuid

import pytest

from agent.agent_scope import (
    build_agent_scope, check_codex_command, check_opencode_permission, opencode_permission_rules,
)
from agent.codex_runtime import CodexWorker

HARRY = {
    "agent_id": "harry", "name": "Harry", "skills": ["pdf"],
    "tools": ["google-drive", "HubSpot"], "config_version": 2,
}
MCP_NAMES = {"hubspot", "github", "linear", "loma-recall", "google_drive_mcp"}


async def _scope(**overrides):
    from mongomock_motor import AsyncMongoMockClient
    db = AsyncMongoMockClient()[f"rt_{uuid.uuid4().hex}"]
    return await build_agent_scope(db, {**HARRY, **overrides})


# ── OpenCode ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opencode_rules_hide_other_mcp_servers_and_ask_for_bash_and_skills():
    rules = opencode_permission_rules(await _scope(), MCP_NAMES)
    assert rules[0] == {"permission": "*", "pattern": "*", "action": "allow"}
    denied = {r["permission"] for r in rules if r["action"] == "deny"}
    assert {"github_*", "linear_*", "google_drive_mcp_*", "task"} <= denied
    assert "hubspot_*" not in denied and "loma-recall_*" not in denied
    asks = {r["permission"] for r in rules if r["action"] == "ask"}
    assert asks == {"bash", "skill"}
    # Last match wins in OpenCode, so the restrictions must come after "allow *".
    assert rules.index({"permission": "bash", "pattern": "*", "action": "ask"}) > 0


@pytest.mark.asyncio
async def test_opencode_rules_are_plain_allow_without_an_enforced_scope():
    rules = opencode_permission_rules(await _scope(enforce_scope=False), MCP_NAMES)
    assert all(r["action"] == "allow" for r in rules)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_, target", [
    ({"permission": "bash", "patterns": ["python3 tools/cdn_upload.py --file x.png"]}, "cdn_upload"),
    ({"permission": "bash", "patterns": ["cd tools"], "metadata": {"command": "cd tools && python3 gmail.py search"}}, "gmail"),
    ({"permission": "bash", "patterns": ["python3 tools/loma_skills.py get --slug zoho-books"]}, "zoho-books"),
    ({"permission": "skill", "patterns": ["zoho-books"]}, "zoho-books"),
    ({"permission": "github_create_pull_request", "patterns": ["*"]}, "github"),
    ({"permission": "task", "patterns": ["general"]}, "subagents"),
])
async def test_opencode_out_of_scope_requests_are_rejected(request_, target):
    block = check_opencode_permission(await _scope(), request_, MCP_NAMES)
    assert block and block["target"] == target
    assert "Harry" in block["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_", [
    {"permission": "bash", "patterns": ["python3 tools/google_drive.py list"]},
    {"permission": "bash", "patterns": ["ls -la", "python3 tools/notify.py send"]},
    {"permission": "bash", "patterns": ["python3 tools/loma_skills.py get --slug pdf"]},
    {"permission": "skill", "patterns": ["pdf"]},
    {"permission": "hubspot_search_objects", "patterns": ["*"]},
    {"permission": "read", "patterns": ["/tmp/x"]},
])
async def test_opencode_in_scope_requests_are_allowed(request_):
    assert check_opencode_permission(await _scope(), request_, MCP_NAMES) is None


# ── Codex ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_codex_command_check():
    scope = await _scope()
    assert check_codex_command(scope, "python3 tools/cdn_upload.py --file a.png")["target"] == "cdn_upload"
    assert check_codex_command(scope, ["bash", "-lc", "python3 tools/gmail.py search"])["target"] == "gmail"
    assert check_codex_command(scope, "python3 tools/google_drive.py list") is None
    assert check_codex_command(await _scope(enforce_scope=False), "python3 tools/gmail.py") is None


class _FakeWorker(CodexWorker):
    def __init__(self, checker):
        super().__init__({"email": "a@x", "config_dir": "/tmp"}, model="gpt-5", command_checker=checker)
        self.sent = []

    async def _send(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("command, decision", [
    ("python3 tools/cdn_upload.py --file a.png", "decline"),
    ("python3 tools/google_drive.py list", "accept"),
])
async def test_codex_worker_answers_command_approvals_with_the_scope_check(command, decision):
    scope = await _scope()

    async def checker(params):
        return check_codex_command(scope, params.get("command")) is None

    worker = _FakeWorker(checker)
    request = {"jsonrpc": "2.0", "id": 7, "method": "item/commandExecution/requestApproval",
               "params": {"itemId": "i1", "command": command}}
    await worker._handle_line(json.dumps(request).encode())
    assert worker.sent == [{"jsonrpc": "2.0", "id": 7, "result": {"decision": decision}}]


@pytest.mark.asyncio
async def test_codex_worker_without_scope_still_auto_approves():
    worker = _FakeWorker(None)
    worker.command_checker = None
    request = {"jsonrpc": "2.0", "id": 1, "method": "item/commandExecution/requestApproval",
               "params": {"command": "python3 tools/gmail.py"}}
    await worker._handle_line(json.dumps(request).encode())
    assert worker.sent[0]["result"] == {"decision": "accept"}
