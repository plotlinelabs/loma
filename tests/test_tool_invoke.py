"""Broker tool.invoke / model.request operation tests. Fully synthetic."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from broker.operations import (
    ALL_TOOLS,
    AUTH_TOOLS,
    INTEGRATION_TOOLS,
    PERSONAL_TOOLS,
    ModelRequest,
    ToolInvoke,
    _strip_identity_flags,
)
from broker.service import Broker, Denied

EMAIL = "owner@example.test"


class FakeProcess:
    def __init__(self, argv):
        self.argv = argv
        self.returncode = 0

    async def communicate(self):
        return json.dumps({"argv": self.argv}).encode(), b""

    def kill(self):
        pass

    async def wait(self):
        return 0


@pytest.fixture
def captured_exec(monkeypatch):
    calls = []

    async def fake_exec(*argv, **kwargs):
        calls.append({"argv": list(argv), "kwargs": kwargs})
        return FakeProcess(list(argv))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def test_tool_registry_shape():
    # Every registered tool script must exist so the broker can execute it.
    from broker.operations import PROJECT_ROOT
    for tool in ALL_TOOLS:
        assert (PROJECT_ROOT / "tools" / f"{tool}.py").is_file(), tool
    # The registry never includes private helpers or admin scripts.
    assert "_auth_token" not in ALL_TOOLS
    assert "migrate_skills_to_workspace" not in ALL_TOOLS
    assert PERSONAL_TOOLS <= ALL_TOOLS
    assert set(INTEGRATION_TOOLS) <= ALL_TOOLS


def test_strip_identity_flags():
    argv = ["send", "--user-email", "victim@example.test", "--title", "x",
            "--auth-token", "stolen", "--body", "b"]
    assert _strip_identity_flags(argv) == ["send", "--title", "x", "--body", "b"]


@pytest.mark.asyncio
async def test_tool_invoke_injects_server_minted_identity(captured_exec, monkeypatch):
    monkeypatch.setattr(
        "tools._auth_token.create_user_auth_token", lambda email: f"minted-for-{email}",
    )
    op = ToolInvoke()
    result = await op.execute(None, EMAIL, "notify", {
        "argv": ["--user-email", "victim@example.test", "--auth-token", "forged",
                 "send", "--title", "hi"],
        "files": {},
    })
    assert result["exit_code"] == 0
    argv = captured_exec[0]["argv"]
    # Worker-supplied identity was stripped; the broker's identity appended.
    assert "victim@example.test" not in argv
    assert "forged" not in argv
    assert argv[-4:] == ["--user-email", EMAIL, "--auth-token", f"minted-for-{EMAIL}"]
    assert argv[1].endswith("tools/notify.py")


@pytest.mark.asyncio
async def test_tool_invoke_no_identity_flags_for_integration_tools(captured_exec, monkeypatch):
    monkeypatch.setattr(
        "tools._auth_token.create_user_auth_token", lambda email: "minted",
    )
    op = ToolInvoke()
    await op.execute(None, EMAIL, "posthog", {"argv": ["query", "--sql", "select 1"], "files": {}})
    argv = captured_exec[0]["argv"]
    assert "--auth-token" not in argv and "--user-email" not in argv
    assert "posthog" not in AUTH_TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    None,
    "argv=x",
    {"argv": "not-a-list"},
    {"argv": ["ok"], "extra_key": 1},
    {"argv": [1, 2]},
    {"argv": ["x"] * 65},
    {"argv": ["y" * 20_001]},
    {"argv": [], "files": "nope"},
    {"argv": [], "files": {i: "x" for i in range(9)}},
    {"argv": [], "files": {"a": "z" * 1_600_000}},
])
async def test_tool_invoke_rejects_malformed_params(params):
    with pytest.raises(Denied):
        await ToolInvoke().execute(None, EMAIL, "notify", params)


@pytest.mark.asyncio
async def test_tool_invoke_rejects_unknown_tools():
    for bad in ("example_tool", "_auth_token", "../../app", "bash", ""):
        with pytest.raises(Denied):
            await ToolInvoke().execute(None, EMAIL, bad, {"argv": []})


@pytest.mark.asyncio
async def test_tool_invoke_materializes_worker_files(captured_exec, monkeypatch):
    monkeypatch.setattr(
        "tools._auth_token.create_user_auth_token", lambda email: "minted",
    )
    op = ToolInvoke()
    await op.execute(None, EMAIL, "loma_skills", {
        "argv": ["update-file", "--slug", "s", "--path", "SKILL.md",
                 "--content-file", "/workspace/skill.md"],
        "files": {"/workspace/skill.md": "# updated"},
    })
    argv = captured_exec[0]["argv"]
    rewritten = argv[argv.index("--content-file") + 1]
    # The worker path was rewritten to a server-side private temp copy
    # (removed after execution; only the rewrite is asserted here).
    assert rewritten != "/workspace/skill.md"
    assert "loma-broker-files-" in rewritten


# ── Broker integration: params gating and grants ─────────────────────────


class Capabilities:
    def __init__(self):
        self.docs = {}
        self.create_index = AsyncMock()

    async def insert_one(self, doc):
        import copy
        self.docs[doc["_id"]] = copy.deepcopy(doc)

    async def find_one_and_update(self, query, update):
        import copy
        doc = self.docs.get(query["_id"])
        if not doc or doc["revoked"] or doc["remaining_calls"] <= 0:
            return None
        if doc["expires_at"] <= query["expires_at"]["$gt"]:
            return None
        if query["scopes"]["$elemMatch"] not in doc["scopes"]:
            return None
        old = copy.deepcopy(doc)
        doc["remaining_calls"] -= 1
        return old

    async def update_many(self, query, update):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(update["$set"])


def make_broker(operations):
    db = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={"status": "active"})),
        execution_capabilities=Capabilities(),
        execution_audit=SimpleNamespace(insert_one=AsyncMock()),
    )
    return db, Broker(db, "dep-test", operations)


@pytest.mark.asyncio
async def test_params_denied_for_paramless_operations():
    op = SimpleNamespace(valid_resource=lambda r: r == "res", execute=AsyncMock())
    db, broker = make_broker({"legacy.op": op})
    _, token = await broker.issue(user_email=EMAIL, grants={"legacy.op": ["res"]})
    with pytest.raises(Denied):
        await broker.execute(token, "legacy.op", "res", {"argv": []})
    op.execute.assert_not_awaited()
    # Without params the same capability works (budget was spent above).
    assert await broker.execute(token, "legacy.op", "res") is op.execute.return_value


@pytest.mark.asyncio
async def test_params_forwarded_to_supporting_operations():
    op = ToolInvoke()
    db, broker = make_broker({"tool.invoke": op})
    _, token = await broker.issue(user_email=EMAIL, grants={"tool.invoke": ["notify"]})
    with patch.object(ToolInvoke, "execute", new=AsyncMock(return_value={"exit_code": 0})) as mock_exec:
        result = await broker.execute(token, "tool.invoke", "notify", {"argv": ["list"], "files": {}})
    assert result == {"exit_code": 0}
    mock_exec.assert_awaited_once_with(db, EMAIL, "notify", {"argv": ["list"], "files": {}})


@pytest.mark.asyncio
async def test_ungranted_tool_is_denied_even_if_registered():
    op = ToolInvoke()
    db, broker = make_broker({"tool.invoke": op})
    _, token = await broker.issue(user_email=EMAIL, grants={"tool.invoke": ["notify"]})
    with pytest.raises(Denied):
        await broker.execute(token, "tool.invoke", "gmail", {"argv": []})


def test_model_request_only_configured_providers():
    op = ModelRequest({"anthropic"})
    assert op.valid_resource("anthropic")
    assert not op.valid_resource("openai")
    assert not op.valid_resource("")
    assert not op.valid_resource(None)


@pytest.mark.asyncio
async def test_model_request_execute_denies_unconfigured():
    op = ModelRequest(set())
    with pytest.raises(Denied):
        await op.execute(None, EMAIL, "anthropic")
    granted = ModelRequest({"openai"})
    assert (await granted.execute(None, EMAIL, "openai"))["ok"] is True
