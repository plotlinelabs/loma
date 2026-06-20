"""Tests for Slack-triggered flows.

Covers the new logic introduced for ``trigger_type: "slack"`` flows:
- slack_app.channels.get_channel_config: DB-driven channel → flow resolution
- slack_app.channels._config_from_slack_flow: flow doc → channel config mapping
- api.flow_routes._slack_channel_taken: one-active-flow-per-channel guard

The message-handler top-level/thread/@mention/bot filtering is pre-existing Loma
code reused unchanged, so it is not re-tested here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_app.channels import (
    get_channel_config,
    _config_from_slack_flow,
    CHANNEL_CONFIGS,
)
from api.flow_routes import _slack_channel_taken


def _slack_flow(**overrides):
    flow = {
        "flow_id": "flow-123",
        "name": "Bug triage",
        "trigger_type": "slack",
        "status": "active",
        "channel_id": "C0001",
        "channel_name": "#bugs",
        "prompt": "Triage the bug.",
        "slack_config": {},
    }
    flow.update(overrides)
    return flow


# ---------------------------------------------------------------------------
# _config_from_slack_flow
# ---------------------------------------------------------------------------


class TestConfigFromSlackFlow:
    def test_basic_mapping(self):
        cfg = _config_from_slack_flow(_slack_flow())
        assert cfg["source"] == "slack_flow"
        assert cfg["flow_id"] == "flow-123"
        assert cfg["name"] == "bugs"  # leading '#' stripped
        assert cfg["allow_bot_messages"] is False
        # The flow prompt is embedded in the prefix, and the user message is appended after.
        assert "Triage the bug." in cfg["prompt_prefix"]
        assert cfg["prompt_prefix"].endswith("Message:\n")

    def test_allow_bot_messages_toggle(self):
        cfg = _config_from_slack_flow(_slack_flow(slack_config={"allow_bot_messages": True}))
        assert cfg["allow_bot_messages"] is True

    def test_missing_channel_name_falls_back_to_id(self):
        cfg = _config_from_slack_flow(_slack_flow(channel_name="", channel_id="C0009"))
        assert cfg["name"] == "C0009"


# ---------------------------------------------------------------------------
# get_channel_config
# ---------------------------------------------------------------------------


class TestGetChannelConfig:
    @pytest.mark.asyncio
    async def test_empty_channel_id_returns_none(self):
        assert await get_channel_config("") is None

    @pytest.mark.asyncio
    async def test_no_db_returns_none(self):
        with patch("slack_app.channels.get_db", return_value=None):
            assert await get_channel_config("C0001") is None

    @pytest.mark.asyncio
    async def test_resolves_active_slack_flow(self):
        db = MagicMock()
        db.flows.find_one = AsyncMock(return_value=_slack_flow())
        with patch("slack_app.channels.get_db", return_value=db):
            cfg = await get_channel_config("C0001")
        assert cfg is not None
        assert cfg["flow_id"] == "flow-123"
        # Query is scoped to active slack flows on this channel — paused/other-type are ignored.
        db.flows.find_one.assert_awaited_once_with({
            "trigger_type": "slack",
            "status": "active",
            "channel_id": "C0001",
        })

    @pytest.mark.asyncio
    async def test_no_matching_flow_returns_none(self):
        db = MagicMock()
        db.flows.find_one = AsyncMock(return_value=None)
        with patch("slack_app.channels.get_db", return_value=db):
            assert await get_channel_config("C0404") is None

    @pytest.mark.asyncio
    async def test_static_config_takes_precedence(self):
        CHANNEL_CONFIGS["C0001"] = {"name": "static", "source": "env", "prompt_prefix": "x"}
        try:
            db = MagicMock()
            db.flows.find_one = AsyncMock(return_value=_slack_flow())
            with patch("slack_app.channels.get_db", return_value=db):
                cfg = await get_channel_config("C0001")
            assert cfg["source"] == "env"
            db.flows.find_one.assert_not_awaited()
        finally:
            CHANNEL_CONFIGS.pop("C0001", None)


# ---------------------------------------------------------------------------
# _slack_channel_taken (one active flow per channel)
# ---------------------------------------------------------------------------


class TestSlackChannelTaken:
    @pytest.mark.asyncio
    async def test_taken_when_active_flow_exists(self):
        db = MagicMock()
        db.flows.find_one = AsyncMock(return_value=_slack_flow())
        assert await _slack_channel_taken(db, "C0001") is True

    @pytest.mark.asyncio
    async def test_free_when_no_flow(self):
        db = MagicMock()
        db.flows.find_one = AsyncMock(return_value=None)
        assert await _slack_channel_taken(db, "C0001") is False

    @pytest.mark.asyncio
    async def test_excludes_self(self):
        db = MagicMock()
        db.flows.find_one = AsyncMock(return_value=None)
        await _slack_channel_taken(db, "C0001", exclude_flow_id="flow-123")
        args, _ = db.flows.find_one.call_args
        assert args[0]["flow_id"] == {"$ne": "flow-123"}
        assert args[0]["trigger_type"] == "slack"
        assert args[0]["status"] == "active"
