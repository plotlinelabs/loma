"""Tests for scheduler/executor.py — scheduled flows act via MCP tools.

Scheduled flows behave like webhook flows: time is just the trigger, the agent
acts via MCP tools, and nothing is auto-posted to Slack. These tests assert that
behavior with a mocked DB and agent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scheduler.executor as executor


def _make_flow(flow_id="flow-1", name="Test Flow", status="active",
               prompt="Do the thing.", schedule_type="recurring", labels=None):
    """Minimal scheduled-flow document — note: NO channel_id needed anymore."""
    return {
        "flow_id": flow_id,
        "name": name,
        "status": status,
        "prompt": prompt,
        "schedule_type": schedule_type,
        "visibility": "shared",
        "created_by": {},
        "labels": labels or [],
    }


def _make_db_mock(flow):
    db = MagicMock()
    db.flows.find_one = AsyncMock(return_value=flow)
    db.flows.update_one = AsyncMock()
    db.conversations.update_one = AsyncMock()
    return db


async def _text_async_gen():
    yield "I took the action via MCP tools."


def _patched(db):
    """Common patches: get_db, stream_agent, observer, scheduler helpers."""
    observer = MagicMock()
    observer.conversation_id = "convo-1"
    observer.start = AsyncMock()
    observer.finish = AsyncMock()
    observer.record_error = AsyncMock()

    p_db = patch.object(executor, "get_db", return_value=db)
    p_agent = patch.object(executor, "stream_agent", return_value=_text_async_gen())
    p_obs = patch.object(executor, "ConversationObserver", return_value=observer)
    p_next = patch.object(executor, "get_next_run_time", return_value=None)
    p_rm = patch.object(executor, "remove_flow_from_scheduler", new=AsyncMock())
    return observer, (p_db, p_agent, p_obs, p_next, p_rm)


class TestExecuteFlowActsViaTools:
    @pytest.mark.asyncio
    async def test_uses_action_preamble_not_slack_post(self):
        """The prompt tells the agent to act via tools, not to expect auto-posting."""
        flow = _make_flow()
        db = _make_db_mock(flow)
        observer, (p_db, p_agent, p_obs, p_next, p_rm) = _patched(db)

        with p_db, p_agent as mock_agent, p_obs, p_next, p_rm:
            await executor.execute_flow("flow-1")

            prompt = mock_agent.call_args[1]["prompt"]
            assert "NOT posted anywhere" in prompt
            assert "MCP tools" in prompt
            assert "AUTOMATICALLY posted" not in prompt
            observer.start.assert_awaited_once()
            observer.finish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_slack_posting_symbols_exist(self):
        """The Slack-posting machinery has been removed from the module."""
        assert not hasattr(executor, "_post_to_slack_channel")
        assert not hasattr(executor, "SlackPostError")
        assert not hasattr(executor, "EMPTY_OUTPUT_SENTINEL")

    @pytest.mark.asyncio
    async def test_metadata_has_no_slack_channel(self):
        """Conversation metadata no longer carries a Slack channel."""
        flow = _make_flow()
        db = _make_db_mock(flow)
        observer, (p_db, p_agent, p_obs, p_next, p_rm) = _patched(db)

        with p_db, p_agent, p_obs as MockObserver, p_next, p_rm:
            await executor.execute_flow("flow-1")
            metadata = MockObserver.call_args[1]["metadata"]
            assert "slack_channel_id" not in metadata
            assert metadata["flow_id"] == "flow-1"  # run history still works

    @pytest.mark.asyncio
    async def test_increments_run_count_and_clears_error(self):
        flow = _make_flow()
        db = _make_db_mock(flow)
        observer, (p_db, p_agent, p_obs, p_next, p_rm) = _patched(db)

        with p_db, p_agent, p_obs, p_next, p_rm:
            await executor.execute_flow("flow-1")

            update = db.flows.update_one.call_args
            assert update[0][1]["$set"]["last_error"] is None
            assert update[0][1]["$inc"]["run_count"] == 1

    @pytest.mark.asyncio
    async def test_inactive_flow_skips(self):
        flow = _make_flow(status="paused")
        db = _make_db_mock(flow)
        observer, (p_db, p_agent, p_obs, p_next, p_rm) = _patched(db)

        with p_db, p_agent as mock_agent, p_obs, p_next, p_rm:
            await executor.execute_flow("flow-1")
            mock_agent.assert_not_called()
            db.flows.update_one.assert_not_called()
