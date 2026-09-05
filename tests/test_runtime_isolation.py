"""Wiring tests: every execution path goes through the worker boundary and
the broker; the legacy in-process execution path no longer exists."""

import copy
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import broker.controller as controller_mod
from broker.controller import (
    RunContext,
    end_run,
    proxy_mcp_servers_for_worker,
    start_run,
)
from broker.gateway import McpProxyRegistry
from broker.operations import ALL_TOOLS, INTEGRATION_TOOLS, PERSONAL_TOOLS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EMAIL = "owner@example.test"


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class Capabilities:
    def __init__(self):
        self.docs = {}
        self.create_index = AsyncMock()

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = copy.deepcopy(doc)

    async def find_one_and_update(self, query, update):
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


def make_db(integrations=(), user_status="active"):
    return SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={"status": user_status})),
        execution_capabilities=Capabilities(),
        execution_audit=SimpleNamespace(insert_one=AsyncMock()),
        integrations=SimpleNamespace(find=lambda query: FakeCursor(integrations)),
        teams=SimpleNamespace(count_documents=AsyncMock(return_value=0)),
    )


# ── Controller: run lifecycle & grants ───────────────────────────────────


@pytest.mark.asyncio
async def test_start_run_issues_capability_and_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    db = make_db()
    await controller_mod.init_execution_controller(db)
    try:
        ctx = await start_run(EMAIL, source="chat")
        assert ctx.run_id and ctx.capability.startswith("loma_run_v1_")
        assert ctx.workspace.is_dir()
        # Broker-backed shims for every registered tool.
        for tool in ALL_TOOLS:
            assert (ctx.workspace / "tools" / f"{tool}.py").exists()
        stored = list(db.execution_capabilities.docs.values())[0]
        granted = {s["resource"] for s in stored["scopes"] if s["operation"] == "tool.invoke"}
        assert PERSONAL_TOOLS <= granted
        await end_run(ctx)
        assert not ctx.workspace.exists()
        assert stored["revoked"] is True
    finally:
        controller_mod._broker = None
        controller_mod._registry = None


@pytest.mark.asyncio
async def test_start_run_excludes_unshared_integrations(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    db = make_db(integrations=[{
        "provider": "posthog", "status": "active",
        "shared_with": {"mode": "specific", "users": ["someone-else@example.test"]},
    }])
    await controller_mod.init_execution_controller(db)
    try:
        ctx = await start_run(EMAIL)
        stored = list(db.execution_capabilities.docs.values())[0]
        granted = {s["resource"] for s in stored["scopes"]}
        assert "posthog" not in granted
        assert "linear" in granted  # unrestricted integrations stay granted
        await end_run(ctx)
    finally:
        controller_mod._broker = None
        controller_mod._registry = None


@pytest.mark.asyncio
async def test_start_run_fails_closed_for_inactive_users(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    db = make_db(user_status="rejected")
    await controller_mod.init_execution_controller(db)
    try:
        ctx = await start_run(EMAIL)
        # No capability is issued; the run cannot reach any credential.
        assert ctx.capability is None and ctx.run_id is None
        await end_run(ctx)
    finally:
        controller_mod._broker = None
        controller_mod._registry = None


@pytest.mark.asyncio
async def test_start_run_without_controller_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    monkeypatch.setattr(controller_mod, "_broker", None)
    monkeypatch.setattr(controller_mod, "_registry", None)
    ctx = await start_run(EMAIL)
    assert ctx.capability is None
    await end_run(ctx)


@pytest.mark.asyncio
async def test_revoked_capability_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    db = make_db()
    broker = await controller_mod.init_execution_controller(db)
    try:
        ctx = await start_run(EMAIL)
        capability = ctx.capability
        await end_run(ctx)
        from broker.service import Denied
        with pytest.raises(Denied):
            await broker.execute(capability, "tool.invoke", "notify", {"argv": [], "files": {}})
    finally:
        controller_mod._broker = None
        controller_mod._registry = None


# ── MCP config rewriting ─────────────────────────────────────────────────


def test_proxy_mcp_servers_fail_closed(monkeypatch):
    monkeypatch.setattr(controller_mod, "_registry", McpProxyRegistry())
    proxied, tokens, disabled = proxy_mcp_servers_for_worker({
        "linear": {"type": "http", "url": "https://mcp.linear.test/sse",
                   "headers": {"Authorization": "Bearer org-secret"}},
        "sentry": {"type": "stdio", "command": "npx",
                   "env": {"SENTRY_ACCESS_TOKEN": "org-secret-2"}},
    })
    # HTTP server: proxied, no credentials in the worker-visible config.
    assert set(proxied) == {"linear"}
    assert "org-secret" not in str(proxied)
    assert proxied["linear"]["url"].startswith("http://127.0.0.1")
    assert "headers" not in proxied["linear"]
    assert len(tokens) == 1
    # Stdio server (env credentials): DISABLED, never silently passed through.
    assert disabled == ["sentry"]


# ── Claude pool wiring ───────────────────────────────────────────────────


def test_pool_build_options_isolates_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    monkeypatch.setattr(controller_mod, "_registry", McpProxyRegistry())
    from agent.pool import ClientPool

    pool = ClientPool(pool_size=1)
    pool.set_config({"mcp_servers": {
        "linear": {"type": "http", "url": "https://mcp.linear.test/sse",
                   "headers": {"Authorization": "Bearer org-secret"}},
        "sentry": {"type": "stdio", "command": "npx", "args": ["-y", "@sentry/mcp-server"],
                   "env": {"SENTRY_ACCESS_TOKEN": "org-secret-2"}},
    }})
    options = pool._build_options()

    workspace = Path(options.cwd)
    assert workspace != PROJECT_ROOT
    assert (workspace / "tools").is_dir()

    # Launcher scrubs the env before exec'ing the CLI.
    launcher = Path(options.cli_path)
    assert launcher.exists()
    body = launcher.read_text()
    assert "env -i" in body
    assert "org-secret" not in body
    assert "OAUTH_ENCRYPTION_KEY" not in body

    # MCP: http proxied without credentials, stdio disabled.
    assert set(options.mcp_servers) == {"linear"}
    assert "org-secret" not in str(options.mcp_servers)
    assert options._disabled_mcp_servers == ["sentry"]
    assert "mcp__linear" in options.allowed_tools
    assert "mcp__sentry" not in options.allowed_tools


def test_background_cli_env_is_scrubbed(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", "synthetic-key")
    monkeypatch.setenv("OBSERVABILITY_MONGODB_URI", "mongodb://synthetic")
    import agent.pool as pool_mod

    monkeypatch.setattr(pool_mod, "_background_cli_workspace", None)
    env = pool_mod.background_cli_env()
    assert "OAUTH_ENCRYPTION_KEY" not in env
    assert "OBSERVABILITY_MONGODB_URI" not in env
    assert env["HOME"] == pool_mod.background_cli_cwd()


# ── stream_agent lifecycle ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_agent_delivers_capability_and_revokes(monkeypatch, tmp_path):
    import agent.opencode_runtime as ocr
    from agent.client import stream_agent

    capability = "loma_run_v1_" + "c" * 43
    workspace = tmp_path / "ws"
    workspace.mkdir()
    lifecycle = {"started": 0, "ended": 0}

    async def fake_start_run(user_email, *, source="run"):
        lifecycle["started"] += 1
        return RunContext(run_id="run-1", capability=capability,
                          workspace=workspace, user_email=user_email)

    async def fake_end_run(ctx):
        lifecycle["ended"] += 1

    monkeypatch.setattr(controller_mod, "start_run", fake_start_run)
    monkeypatch.setattr(controller_mod, "end_run", fake_end_run)

    seen = {}

    async def fake_run_opencode_agent(**kwargs):
        seen.update(kwargs)
        yield "ok"

    monkeypatch.setattr(ocr, "run_opencode_agent", fake_run_opencode_agent)

    events = []
    async for event in stream_agent(
        "hello", user_email=EMAIL, selected_model="opencode-go/deepseek-v4-flash",
    ):
        events.append(event)

    assert "ok" in events
    assert lifecycle == {"started": 1, "ended": 1}
    # The prompt carries the run capability (the worker's only credential)…
    assert capability in seen["full_prompt"]
    # …and the run context reaches the runtime for workspace/sandbox use.
    assert seen["run_ctx"].workspace == workspace


@pytest.mark.asyncio
async def test_stream_agent_without_capability_disables_personal_tools(monkeypatch, tmp_path):
    import agent.opencode_runtime as ocr
    from agent.client import stream_agent

    workspace = tmp_path / "ws2"
    workspace.mkdir()

    async def fake_start_run(user_email, *, source="run"):
        return RunContext(run_id=None, capability=None,
                          workspace=workspace, user_email=user_email)

    async def fake_end_run(ctx):
        pass

    monkeypatch.setattr(controller_mod, "start_run", fake_start_run)
    monkeypatch.setattr(controller_mod, "end_run", fake_end_run)

    seen = {}

    async def fake_run_opencode_agent(**kwargs):
        seen.update(kwargs)
        yield "ok"

    monkeypatch.setattr(ocr, "run_opencode_agent", fake_run_opencode_agent)

    async for _ in stream_agent(
        "hello", user_email=EMAIL, selected_model="opencode-go/deepseek-v4-flash",
    ):
        pass

    prompt = seen["full_prompt"]
    assert "Personal tools are DISABLED" in prompt
    assert "Personal Tools Auth Token" not in prompt


# ── OpenCode / Codex config wiring ───────────────────────────────────────


def test_opencode_provider_overrides_use_gateway(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-synthetic")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from agent.opencode_runtime import _provider_gateway_overrides

    capability = "loma_run_v1_" + "d" * 43
    overrides = _provider_gateway_overrides(capability)
    assert overrides["anthropic"]["options"]["apiKey"] == capability
    assert "/model/anthropic" in overrides["anthropic"]["options"]["baseURL"]
    # The real key never appears in worker-visible config.
    assert "sk-ant-synthetic" not in str(overrides)
    # No capability -> no provider access through the gateway.
    assert _provider_gateway_overrides(None) == {}


def test_codex_config_from_proxied_servers_has_no_secrets(monkeypatch):
    monkeypatch.setattr(controller_mod, "_registry", McpProxyRegistry())
    from agent.codex_runtime import claude_mcp_to_codex_toml

    proxied, _, disabled = proxy_mcp_servers_for_worker({
        "linear": {"type": "http", "url": "https://mcp.linear.test/sse",
                   "headers": {"Authorization": "Bearer org-secret"}},
        "sentry": {"type": "stdio", "command": "npx",
                   "env": {"SENTRY_ACCESS_TOKEN": "org-secret-2"}},
    })
    toml = claude_mcp_to_codex_toml(proxied)
    assert "org-secret" not in toml
    assert "sentry" not in toml
    assert "linear" in toml


# ── The legacy in-process execution path is gone ─────────────────────────


EXECUTION_MODULES = [
    "agent/pool.py",
    "agent/client.py",
    "agent/opencode_runtime.py",
    "agent/codex_runtime.py",
    "agent/codex_pool.py",
    "api/terminal_routes.py",
    "scheduler/executor.py",
    "scheduler/webhook_executor.py",
]


@pytest.mark.parametrize("module", EXECUTION_MODULES)
def test_no_backend_env_inheritance_in_execution_paths(module):
    source = (PROJECT_ROOT / module).read_text()
    assert "**os.environ" not in source, module
    assert "dict(os.environ)" not in source, module


def test_scheduler_paths_have_no_direct_spawns():
    # Scheduled flows and webhook flows must execute only via stream_agent,
    # which owns the isolated-run lifecycle.
    for module in ("scheduler/executor.py", "scheduler/webhook_executor.py"):
        source = (PROJECT_ROOT / module).read_text()
        assert "create_subprocess" not in source, module
        assert "stream_agent" in source, module


def test_terminal_uses_worker_boundary():
    source = (PROJECT_ROOT / "api/terminal_routes.py").read_text()
    assert "build_worker_env" in source
    assert "create_workspace" in source
    assert re.search(r"\*\*os\.environ", source) is None


def test_personal_tools_no_longer_reachable_without_broker():
    # The prompt instructs `python3 tools/<name>.py` relative to the worker
    # cwd; workers only ever contain broker shims, never the real tools.
    from broker.worker import _SHIM_TEMPLATE
    assert "tool.invoke" in _SHIM_TEMPLATE
    assert "motor" not in _SHIM_TEMPLATE
    assert "Fernet" not in _SHIM_TEMPLATE
