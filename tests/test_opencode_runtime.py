import pytest


def test_parse_configured_models_filters_to_connected_tool_models():
    from agent.opencode_runtime import parse_configured_models

    payload = {
        "default": {"openai": "gpt-5.5", "other": "no-tools"},
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "models": {
                    "gpt-5.5": {
                        "name": "GPT-5.5",
                        "capabilities": {
                            "toolcall": True,
                            "reasoning": True,
                            "input": {"text": True, "image": True},
                            "output": {"text": True},
                        },
                        "limit": {"context": 400000, "output": 32000},
                        "cost": {"input": 1.0, "output": 5.0, "cache": {"read": 0.1, "write": 0.2}},
                        "status": "active",
                    },
                    "audio-only": {
                        "name": "Audio Only",
                        "capabilities": {
                            "toolcall": True,
                            "input": {"text": False},
                            "output": {"text": True},
                        },
                    },
                },
            },
            {
                "id": "other",
                "name": "Other",
                "models": {
                    "no-tools": {
                        "name": "No Tools",
                        "capabilities": {
                            "toolcall": False,
                            "input": {"text": True},
                            "output": {"text": True},
                        },
                    },
                },
            },
        ],
    }

    result = parse_configured_models(payload)

    assert result["default_model"] == "openai/gpt-5.5"
    assert [model["id"] for model in result["models"]] == ["openai/gpt-5.5"]
    model = result["models"][0]
    assert model["provider_id"] == "openai"
    assert model["model_id"] == "gpt-5.5"
    assert model["supports_attachments"] is True
    assert model["supports_reasoning"] is True
    assert model["context_limit"] == 400000
    assert model["cost"]["input"] == 1.0


def test_parse_configured_models_falls_back_to_first_supported_model():
    from agent.opencode_runtime import parse_configured_models

    payload = {
        "default": {"missing": "model"},
        "providers": [
            {
                "id": "opencode",
                "name": "OpenCode",
                "models": {
                    "model-a": {
                        "name": "Model A",
                        "capabilities": {
                            "toolcall": True,
                            "input": {"text": True},
                            "output": {"text": True},
                        },
                    },
                },
            },
        ],
    }

    result = parse_configured_models(payload)

    assert result["default_model"] == "opencode/model-a"


def test_claude_mcp_to_opencode_converts_local_and_remote_servers():
    from agent.opencode_runtime import claude_mcp_to_opencode

    result = claude_mcp_to_opencode({
        "clickhouse": {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "mcp-clickhouse"],
            "env": {"CLICKHOUSE_HOST": "example"},
        },
        "github": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp",
            "headers": {"Authorization": "Bearer token"},
        },
        "broken": {
            "type": "stdio",
        },
    })

    assert result == {
        "clickhouse": {
            "type": "local",
            "command": ["uv", "run", "mcp-clickhouse"],
            "enabled": True,
            "environment": {"CLICKHOUSE_HOST": "example"},
        },
        "github": {
            "type": "remote",
            "url": "https://api.githubcopilot.com/mcp",
            "enabled": True,
            "headers": {"Authorization": "Bearer token"},
            "oauth": False,
        },
    }


def test_opencode_config_does_not_use_native_skill_paths(monkeypatch, tmp_path):
    import asyncio
    import agent.opencode_runtime as opencode_runtime

    monkeypatch.setattr(opencode_runtime, "_opencode_config_cache", {})
    monkeypatch.setattr(opencode_runtime.tempfile, "gettempdir", lambda: str(tmp_path))

    async def fake_load_config():
        return {"mcp_servers": {}}

    monkeypatch.setattr(opencode_runtime, "_load_current_agent_config", fake_load_config)

    config_home, config_hash = asyncio.run(opencode_runtime._write_managed_opencode_config())
    config_text = (config_home / "opencode" / "opencode.json").read_text()

    assert config_home == tmp_path / f"loma-opencode-config-{config_hash[:12]}"
    assert "skills" not in config_text
    assert ".claude/skills" not in config_text


def test_opencode_runtime_uses_shared_pooled_prompt():
    from agent.opencode_runtime import _opencode_system_prompt
    from agent.prompt import build_pooled_system_prompt

    pooled_prompt = build_pooled_system_prompt()

    assert _opencode_system_prompt() == pooled_prompt


def test_claude_model_selection_detection():
    from agent.client import _normalize_claude_model, _selected_model_is_claude

    assert _selected_model_is_claude("opencode/claude-opus-4-7") is True
    assert _normalize_claude_model("opencode/claude-opus-4-7") == "claude-opus-4-7"
    assert _selected_model_is_claude("anthropic/claude-sonnet-4-5") is True
    assert _normalize_claude_model("anthropic/claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert _selected_model_is_claude("opencode-go/deepseek-v4-flash") is False
    assert _normalize_claude_model("opencode-go/deepseek-v4-flash") is None


@pytest.mark.asyncio
async def test_stream_agent_uses_opencode_runtime_by_default(monkeypatch):
    import agent.opencode_runtime as opencode_runtime
    from agent.client import stream_agent

    seen = {}

    async def fake_run_opencode_agent(**kwargs):
        seen.update(kwargs)
        yield {"type": "account_info", "runtime": "opencode", "provider": "opencode-go", "model": "deepseek-v4-flash"}
        yield "ok"

    monkeypatch.setattr(opencode_runtime, "run_opencode_agent", fake_run_opencode_agent)

    events = []
    async for event in stream_agent("hello", include_steps=True, source="dashboard"):
        events.append(event)

    assert seen["selected_model"] == "opencode-go/deepseek-v4-flash"
    assert events[-1] == "ok"


@pytest.mark.asyncio
async def test_stream_agent_uses_opencode_runtime_when_model_selected(monkeypatch):
    import agent.opencode_runtime as opencode_runtime
    from agent.client import stream_agent

    captured = {}

    async def fake_run_opencode_agent(**kwargs):
        captured.update(kwargs)
        yield {"type": "account_info", "runtime": "opencode", "provider": "openai", "model": "gpt-5.5"}
        yield "done"

    monkeypatch.setattr(opencode_runtime, "run_opencode_agent", fake_run_opencode_agent)

    events = [
        event
        async for event in stream_agent(
            prompt="hello",
            include_steps=True,
            source="dashboard",
            selected_model="openai/gpt-5.5",
        )
    ]

    assert captured["selected_model"] == "openai/gpt-5.5"
    assert captured["include_steps"] is True
    assert captured["source"] == "dashboard"
    assert "## Current Message\nhello" in captured["full_prompt"]
    assert events[-1] == "done"


def test_chat_idle_timeout_default_is_raised():
    from agent.opencode_runtime import OPENCODE_EVENT_IDLE_TIMEOUT_SECONDS

    assert OPENCODE_EVENT_IDLE_TIMEOUT_SECONDS >= 480


def test_retryable_turn_error_classification():
    import asyncio

    import aiohttp

    from agent.opencode_runtime import (
        OpenCodeError,
        OpenCodeModelError,
        _is_retryable_turn_error,
    )

    assert _is_retryable_turn_error(
        OpenCodeError("Timed out waiting for OpenCode response events after 480s")
    )
    assert _is_retryable_turn_error(aiohttp.ClientConnectionError())
    assert _is_retryable_turn_error(asyncio.TimeoutError())
    assert not _is_retryable_turn_error(OpenCodeModelError("provider rejected the request"))
    assert not _is_retryable_turn_error(ValueError("boom"))


class _FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_retire_stale_servers_never_kills_busy_server(monkeypatch):
    from pathlib import Path

    import agent.opencode_runtime as ocr

    monkeypatch.setattr(ocr, "_opencode_servers", {})
    monkeypatch.setattr(ocr, "_opencode_session_cache", {})
    monkeypatch.setattr(ocr, "_opencode_warm_sessions", {})
    monkeypatch.setattr(ocr, "_opencode_prewarm_tasks", {})
    monkeypatch.setattr(ocr, "OPENCODE_MAX_SERVERS", 1)

    def make_server(config_hash, port):
        return ocr._OpenCodeServer(
            config_hash=config_hash,
            config_home=Path("/tmp"),
            host="127.0.0.1",
            port=port,
            process=_FakeProcess(),
        )

    busy = make_server("busy", 1)
    busy.active_turns = 1
    idle = make_server("idle", 2)
    new = make_server("new", 3)
    ocr._opencode_servers.update({"busy": busy, "idle": idle, "new": new})

    await ocr._retire_stale_servers(keep_hash="new")

    # A server with in-flight turns must never be terminated by config rotation.
    assert busy.process.terminated is False
    assert "busy" in ocr._opencode_servers
    # Idle servers beyond the cap are reaped.
    assert idle.process.terminated is True
    assert "idle" not in ocr._opencode_servers
    assert "new" in ocr._opencode_servers


@pytest.mark.asyncio
async def test_run_opencode_agent_retries_idle_timeout_on_fresh_session(monkeypatch):
    from pathlib import Path

    import agent.opencode_runtime as ocr

    monkeypatch.setattr(ocr, "_opencode_session_cache", {})

    server = ocr._OpenCodeServer(
        config_hash="hash",
        config_home=Path("/tmp"),
        host="127.0.0.1",
        port=1,
        process=None,
    )

    async def fake_ensure(user_mcp_overrides=None):
        return server

    monkeypatch.setattr(ocr, "_ensure_server_instance", fake_ensure)

    async def fake_is_known_model(model_id):
        return True

    monkeypatch.setattr(ocr, "is_known_model", fake_is_known_model)

    created_sessions = []

    async def fake_create_session(title, *, base_url=None):
        created_sessions.append(title)
        return f"ses_{len(created_sessions)}"

    monkeypatch.setattr(ocr, "_create_session", fake_create_session)

    async def fake_checkout(server, model_id):
        return None

    monkeypatch.setattr(ocr, "_checkout_warm_session", fake_checkout)
    monkeypatch.setattr(ocr, "_schedule_prewarm", lambda server, model_id: None)

    attempts = {"count": 0}

    async def fake_iter(base_url, *, session_id, body, idle_timeout_seconds, request_timeout_seconds):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ocr.OpenCodeError(
                f"Timed out waiting for OpenCode response events after {idle_timeout_seconds}s"
            )
        yield {
            "type": "message.updated",
            "properties": {"info": {"id": "m1", "role": "assistant", "sessionID": session_id}},
        }
        yield {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "id": "p1",
                    "type": "text",
                    "text": "hello there",
                    "messageID": "m1",
                    "sessionID": session_id,
                    "time": {"end": 1},
                }
            },
        }
        yield {
            "type": "message.updated",
            "properties": {
                "info": {
                    "id": "m1",
                    "role": "assistant",
                    "sessionID": session_id,
                    "time": {"completed": 1},
                    "finish": "stop",
                    "tokens": {"input": 1, "output": 2},
                }
            },
        }

    monkeypatch.setattr(ocr, "_iter_opencode_turn_events", fake_iter)

    events = [
        event
        async for event in ocr.run_opencode_agent(
            full_prompt="hi",
            selected_model="opencode-go/glm-5.3-flash",
        )
    ]

    assert attempts["count"] == 2
    # The retry must run on a brand-new session, not the timed-out one.
    assert len(created_sessions) == 2
    text = "".join(
        event.get("text", "")
        for event in events
        if isinstance(event, dict) and event.get("type") == "text"
    )
    assert "hello there" in text
    assert server.active_turns == 0


@pytest.mark.asyncio
async def test_run_opencode_agent_does_not_retry_model_errors(monkeypatch):
    from pathlib import Path

    import agent.opencode_runtime as ocr

    monkeypatch.setattr(ocr, "_opencode_session_cache", {})

    server = ocr._OpenCodeServer(
        config_hash="hash",
        config_home=Path("/tmp"),
        host="127.0.0.1",
        port=1,
        process=None,
    )

    async def fake_ensure(user_mcp_overrides=None):
        return server

    monkeypatch.setattr(ocr, "_ensure_server_instance", fake_ensure)

    async def fake_is_known_model(model_id):
        return True

    monkeypatch.setattr(ocr, "is_known_model", fake_is_known_model)

    async def fake_create_session(title, *, base_url=None):
        return "ses_1"

    monkeypatch.setattr(ocr, "_create_session", fake_create_session)

    async def fake_checkout(server, model_id):
        return None

    monkeypatch.setattr(ocr, "_checkout_warm_session", fake_checkout)
    monkeypatch.setattr(ocr, "_schedule_prewarm", lambda server, model_id: None)

    attempts = {"count": 0}

    async def fake_iter(base_url, *, session_id, body, idle_timeout_seconds, request_timeout_seconds):
        attempts["count"] += 1
        raise ocr.OpenCodeModelError("provider rejected the request")
        yield  # pragma: no cover

    monkeypatch.setattr(ocr, "_iter_opencode_turn_events", fake_iter)

    with pytest.raises(ocr.OpenCodeModelError):
        async for _ in ocr.run_opencode_agent(
            full_prompt="hi",
            selected_model="opencode-go/glm-5.3-flash",
        ):
            pass

    assert attempts["count"] == 1
    assert server.active_turns == 0


def test_chat_turn_has_no_wall_clock_cap_by_default():
    import agent.opencode_runtime as ocr

    assert ocr.OPENCODE_REQUEST_TIMEOUT_SECONDS == 0
    timeout = ocr._turn_client_timeout(ocr.OPENCODE_REQUEST_TIMEOUT_SECONDS)
    assert timeout.total is None
    assert timeout.sock_connect == 30


def test_flow_turn_keeps_wall_clock_cap():
    import agent.opencode_runtime as ocr

    timeout = ocr._turn_client_timeout(ocr.OPENCODE_FLOW_REQUEST_TIMEOUT_SECONDS)
    assert timeout.total == ocr.OPENCODE_FLOW_REQUEST_TIMEOUT_SECONDS


def test_turn_timeout_message_is_explicit():
    import agent.opencode_runtime as ocr

    assert "1800s wall-clock limit" in ocr._describe_turn_timeout(1800)
    assert "connection timed out" in ocr._describe_turn_timeout(0)
