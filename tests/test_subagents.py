"""device-tester sub-agent: model selection and registration on Claude runs."""

import pytest

from agent import subagents
from agent.subagents import DEVICE_TESTER, build_subagents, device_tester_model


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LOMA_DEVICE_TESTER_MODEL", raising=False)
    monkeypatch.delenv("LOMA_DEVICE_TESTER_MAX_TURNS", raising=False)


def test_default_model_is_cheap():
    assert device_tester_model() == "haiku"


@pytest.mark.parametrize("value, expected", [
    ("sonnet", "sonnet"),
    ("  claude-sonnet-4-5  ", "claude-sonnet-4-5"),
    ("inherit", None),
    ("INHERIT", None),
    ("", "haiku"),
])
def test_model_override(monkeypatch, value, expected):
    monkeypatch.setenv("LOMA_DEVICE_TESTER_MODEL", value)
    assert device_tester_model() == expected


def test_definition_is_scoped():
    agent = build_subagents()[DEVICE_TESTER]
    assert agent.model == "haiku"
    assert agent.tools == ["Bash", "Read"]
    assert agent.maxTurns == subagents.DEFAULT_DEVICE_TESTER_MAX_TURNS
    assert "tools/device.py" in agent.prompt
    assert "PASS or FAIL or BLOCKED" in agent.prompt


@pytest.mark.parametrize("value, expected", [("40", 40), ("0", 80), ("-3", 80), ("abc", 80)])
def test_max_turns(monkeypatch, value, expected):
    monkeypatch.setenv("LOMA_DEVICE_TESTER_MAX_TURNS", value)
    assert build_subagents()[DEVICE_TESTER].maxTurns == expected


def test_pool_options_register_device_tester(monkeypatch):
    from agent.pool import ClientPool

    monkeypatch.setenv("LOMA_DEVICE_TESTER_MODEL", "sonnet")
    pool = ClientPool.__new__(ClientPool)
    pool._config = {"mcp_servers": {}}
    options = pool._build_options(model_override="claude-opus-4-8")
    assert options.model == "claude-opus-4-8"
    assert options.agents[DEVICE_TESTER].model == "sonnet"
    assert "Agent" in options.allowed_tools
