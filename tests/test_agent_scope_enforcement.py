"""An agent's tool and skill scope is enforced at runtime, and every reply says
which agent wrote it.

Covers agent/agent_scope.py (scope resolution, per-call blocking, the SDK hook),
per-message attribution and blocked-call logging in the observer, agent switch
events, agent config versioning, the run-list filters, and the backfill.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

import api.drain as drain
from agent.agent_scope import (
    build_agent_scope, check_tool_call, config_hash, filter_allowed_tools,
    filter_mcp_servers, make_pre_tool_use_hook,
)
from observability.agent_events import record_agent_switch
from observability.observer import ConversationObserver

USER = "user@example.com"
HARRY = {
    "agent_id": "harry", "name": "Harry", "description": "Account receivables",
    "skills": ["zoho-books", "account-receivables"],
    "tools": ["gmail", "google-sheets", "HubSpot", "MongoDB", "App Ninja", "Zoho Books"],
    "status": "active", "visibility": "workspace", "created_by": USER, "config_version": 3,
}


def _mongo():
    from mongomock_motor import AsyncMongoMockClient
    return AsyncMongoMockClient()[f"scope_{uuid.uuid4().hex}"]


async def _harry_scope(db=None, **overrides):
    db = db if db is not None else _mongo()
    await db.integrations.insert_one({"provider": "app_ninja", "display_name": "App Ninja", "is_custom": True})
    return await build_agent_scope(db, {**HARRY, **overrides})


# ── Scope resolution ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scope_maps_agent_tools_to_servers_and_cli_scripts():
    scope = await _harry_scope()
    assert scope["agent_name"] == "Harry" and scope["agent_config_version"] == 3
    assert set(scope["mcp_servers"]) == {"hubspot", "mongodb", "app_ninja"}
    assert {"gmail", "google_sheets", "zoho_books"} <= set(scope["scripts"])
    assert scope["skills"] == ["account-receivables", "zoho-books"]
    assert scope["enforce"] and scope["tools_restricted"] and scope["skills_restricted"]


@pytest.mark.asyncio
async def test_mcp_servers_outside_scope_are_not_loaded():
    scope = await _harry_scope()
    servers = {"hubspot": {}, "github": {}, "linear": {}, "loma-recall": {}, "app_ninja": {}}
    assert set(filter_mcp_servers(scope, servers)) == {"hubspot", "loma-recall", "app_ninja"}
    tools = ["Bash", "Read", "mcp__hubspot", "mcp__github", "mcp__loma-recall__*"]
    assert filter_allowed_tools(scope, tools) == ["Bash", "Read", "mcp__hubspot", "mcp__loma-recall__*"]


# ── Per-call checks ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("tool,tool_input,target", [
    ("mcp__github__create_pull_request", {}, "github"),
    ("mcp__claude_ai_Gmail__send", {}, "claude_ai_Gmail"),
    ("Bash", {"command": "python3 tools/monetize_now.py contracts --customer x"}, "monetize_now"),
    ("Bash", {"command": "cd tools && python3 linear.py list"}, "linear"),
    ("Bash", {"command": "python3 tools/loma_skills.py get --slug implement-ticket"}, "implement-ticket"),
    ("Skill", {"skill": "pptx"}, "pptx"),
])
async def test_out_of_scope_calls_are_blocked_with_a_redirect(tool, tool_input, target):
    block = check_tool_call(await _harry_scope(), tool, tool_input)
    assert block is not None and block["target"] == target
    assert "isn't available to Harry" in block["reason"]
    assert "switch to Loma" in block["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,tool_input", [
    ("mcp__hubspot__search", {}),
    ("mcp__app_ninja__run_script", {}),
    ("mcp__loma-recall__search_history", {}),
    ("Bash", {"command": "python3 tools/zoho_books.py invoices --status overdue"}),
    ("Bash", {"command": "python3 tools/gmail.py search --q invoice"}),
    ("Bash", {"command": "python3 tools/notify.py send --title done"}),
    ("Bash", {"command": "python3 tools/loma_skills.py get --slug zoho-books"}),
    ("Bash", {"command": "ls -la /tmp && cat state.json"}),
    ("Read", {"file_path": "/tmp/x"}),
])
async def test_in_scope_and_generic_calls_are_allowed(tool, tool_input):
    assert check_tool_call(await _harry_scope(), tool, tool_input) is None


@pytest.mark.asyncio
async def test_empty_lists_and_admin_override_mean_no_blocking():
    open_scope = await _harry_scope(tools=[], skills=[])
    assert check_tool_call(open_scope, "mcp__github__x", {}) is None
    off = await _harry_scope(enforce_scope=False)
    assert check_tool_call(off, "mcp__github__x", {}) is None
    assert filter_mcp_servers(off, {"github": {}}) == {"github": {}}
    assert check_tool_call(None, "mcp__github__x", {}) is None


@pytest.mark.asyncio
async def test_hook_denies_and_records_the_blocked_call():
    db = _mongo()
    await db.conversations.insert_one({"conversation_id": "c1", "messages": []})
    observer = ConversationObserver(db, metadata={"agent_id": "harry", "agent_name": "Harry"},
                                    conversation_id="c1")
    observer.turn_count = 4
    hook = make_pre_tool_use_hook(await _harry_scope(), observer)

    denied = await hook({"tool_name": "mcp__github__create_pull_request", "tool_input": {}}, "t1", None)
    out = denied["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"].startswith("github isn't available to Harry")
    assert await hook({"tool_name": "mcp__hubspot__search", "tool_input": {}}, "t2", None) == {}

    convo = await db.conversations.find_one({"conversation_id": "c1"})
    assert convo["blocked_call_count"] == 1
    [entry] = convo["blocked_calls"]
    assert entry["target"] == "github" and entry["turn_number"] == 4 and entry["agent_name"] == "Harry"
    assert entry["tool_use_id"] == "t1"


# ── Attribution and switch events ───────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("metadata,expected", [
    ({"agent_id": "harry", "agent_name": "Harry", "agent_config_version": 3},
     {"agent_id": "harry", "agent_name": "Harry", "agent_config_version": 3}),
    ({"agent_id": "harry", "agent_snapshot": {"name": "Harry"}},
     {"agent_id": "harry", "agent_name": "Harry"}),
    ({}, {"agent_id": None, "agent_name": "Loma"}),
])
async def test_each_assistant_message_records_its_agent(metadata, expected):
    db = _mongo()
    observer = ConversationObserver(db, metadata={"prompt": "hi", **metadata})
    await observer.start()
    with patch.object(ConversationObserver, "_run_title_topic_enrichment", new=AsyncMock()), \
         patch.object(ConversationObserver, "_run_savings_estimation", new=AsyncMock()), \
         patch("observability.push.fire_task_push"):
        await observer.finish("Done.")
    convo = await db.conversations.find_one({"conversation_id": observer.conversation_id})
    reply = convo["messages"][-1]
    assert reply["role"] == "assistant"
    assert {k: reply.get(k) for k in expected} == expected


@pytest.mark.asyncio
async def test_agent_switch_events_are_recorded_and_noops_skipped():
    db = _mongo()
    await db.conversations.insert_one({"conversation_id": "c1"})
    assert await record_agent_switch(db, "c1", None, {"agent_id": "harry", "name": "Harry"}, USER)
    assert await record_agent_switch(db, "c1", {"agent_id": "harry", "name": "Harry"}, None, USER)
    assert await record_agent_switch(db, "c1", None, None, USER) is None
    events = (await db.conversations.find_one({"conversation_id": "c1"}))["agent_events"]
    assert [(e["from_agent_name"], e["to_agent_name"]) for e in events] == [("Loma", "Harry"), ("Harry", "Loma")]
    assert all(e["switched_by"] == USER for e in events)


# ── Chat handler ────────────────────────────────────────────────────────────

class FakeRequest(dict):
    def __init__(self, body=None, query=None, role="chatter"):
        super().__init__(user_email=USER, system_role=role)
        self._body = body or {}
        self.query = query or {}
        self.match_info = {}
        self.remote = "127.0.0.1"
        self.can_read_body = True

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_switching_agents_in_a_thread_records_who_switched_and_scopes_the_run():
    from api.routes import handle_chat

    db = _mongo()
    await db.agent_identities.insert_one(dict(HARRY))
    await db.conversations.insert_one({
        "conversation_id": "c1", "status": "completed", "source": "dashboard",
        "metadata": {"user_name": USER}, "messages": [{"role": "user", "content": "first"}],
    })
    drain.set_draining(True, "test")
    try:
        with patch("api.routes.get_db", return_value=db):
            body = {"message": "next", "conversation_id": "c1", "agent_id": "harry",
                    "tool_config": {"agent_scope": {"enforce": False}}}
            response = await handle_chat(FakeRequest(body))
    finally:
        drain.set_draining(False)
    assert response.status == 202
    run = await db.pending_runs.find_one({})
    convo = await db.conversations.find_one({"conversation_id": "c1"})
    # The server builds the scope; a client-sent one is ignored.
    assert run["tool_config"]["agent_scope"]["enforce"] is True
    assert "hubspot" in run["tool_config"]["agent_scope"]["mcp_servers"]
    assert run["metadata"]["agent_config_version"] == 3
    assert "agent_scope" not in (convo.get("tool_config") or {})
    [event] = convo["agent_events"]
    assert (event["from_agent_name"], event["to_agent_name"], event["switched_by"]) == ("Loma", "Harry", USER)


@pytest.mark.asyncio
async def test_run_list_filters_by_agent_and_blocked_calls():
    import json
    from api.routes import handle_list_conversations

    db = _mongo()
    await db.conversations.insert_many([
        {"conversation_id": "a", "started_at": 3, "metadata": {"user_name": USER, "agent_id": "harry"},
         "blocked_call_count": 2},
        {"conversation_id": "b", "started_at": 2, "metadata": {"user_name": USER},
         "messages": [{"role": "assistant", "agent_id": "harry"}]},
        {"conversation_id": "c", "started_at": 1, "metadata": {"user_name": USER}},
    ])

    async def ids(query):
        with patch("api.routes.get_db", return_value=db), \
             patch("api.routes._ensure_search_index", new=AsyncMock(return_value=False)):
            response = await handle_list_conversations(FakeRequest(query=query, role="admin"))
        return [c["conversation_id"] for c in json.loads(response.text)["conversations"]]

    assert await ids({"agent": "harry"}) == ["a", "b"]
    assert await ids({"agent": "loma"}) == ["b", "c"]
    assert await ids({"blocked": "1"}) == ["a"]


# ── Versioning ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_editing_scope_bumps_the_version_and_keeps_a_snapshot():
    from api.agent_identity_routes import handle_update_agent

    db = _mongo()
    legacy = {k: v for k, v in HARRY.items() if k != "config_version"}
    await db.agent_identities.insert_one(legacy)

    async def patch_agent(body, role="admin"):
        req = FakeRequest(body, role=role)
        req.match_info = {"agent_id": "harry"}
        with patch("api.agent_identity_routes.get_db", return_value=db):
            return await handle_update_agent(req)

    await patch_agent({"tools": ["gmail"]})
    agent = await db.agent_identities.find_one({"agent_id": "harry"})
    assert agent["config_version"] == 2 and agent["config_hash"] == config_hash(agent)
    versions = await db.agent_identity_versions.find({"agent_id": "harry"}).sort("version", 1).to_list(5)
    assert [(v["version"], v["tools"]) for v in versions] == [(1, HARRY["tools"]), (2, ["gmail"])]

    await patch_agent({"tools": ["gmail"]})  # no behaviour change, no new version
    assert (await db.agent_identities.find_one({"agent_id": "harry"}))["config_version"] == 2

    denied = await patch_agent({"enforce_scope": False}, role="operator")
    assert denied.status in (403, 404)
    ok = await patch_agent({"enforce_scope": False}, role="admin")
    assert ok.status == 200
    assert (await db.agent_identities.find_one({"agent_id": "harry"}))["config_version"] == 3


# ── Backfill ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_attributes_old_replies_to_loma_only_once():
    from scripts.backfill_agent_attribution import backfill

    db = _mongo()
    batches = []

    async def bulk_write(_self, ops, ordered=True):  # mongomock's bulk_write breaks on this pymongo
        batches.append(len(ops))
        modified = 0
        for op in ops:
            modified += (await db.conversations.update_one(op._filter, op._doc)).modified_count
        return type("R", (), {"modified_count": modified})()

    patcher = patch.object(type(db.conversations), "bulk_write", new=bulk_write)
    patcher.start()
    await db.conversations.insert_one({"conversation_id": "c1", "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "old"},
        {"role": "assistant", "content": "new", "agent_id": "harry", "agent_name": "Harry"},
    ]})
    assert (await backfill(db, apply=False))["conversations"] == 1
    assert (await backfill(db, apply=True))["modified"] == 1
    assert batches == [1]
    messages = (await db.conversations.find_one({"conversation_id": "c1"}))["messages"]
    assert "agent_name" not in messages[0]
    assert messages[1]["agent_name"] == "Loma" and messages[1]["attribution"] == "inferred"
    assert messages[2]["agent_name"] == "Harry" and "attribution" not in messages[2]
    assert (await backfill(db, apply=False))["conversations"] == 0
    patcher.stop()


# ── Slack ───────────────────────────────────────────────────────────────────

def test_slack_replies_name_the_agent(monkeypatch):
    from slack_app.handlers import agent_post_identity

    class Obs:
        metadata = {"agent_id": "harry", "agent_name": "Harry"}

    monkeypatch.delenv("SLACK_POST_AS_AGENT", raising=False)
    assert agent_post_identity(Obs()) == {"footer": True, "agent_name": "Harry"}
    monkeypatch.setenv("SLACK_POST_AS_AGENT", "1")
    monkeypatch.setenv("SLACK_AGENT_ICON_URL", "https://cdn/x.png")
    assert agent_post_identity(Obs()) == {"username": "Harry (Loma)", "icon_url": "https://cdn/x.png"}
    Obs.metadata = {}
    assert agent_post_identity(Obs()) == {}  # plain Loma replies are unchanged
