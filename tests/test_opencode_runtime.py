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
