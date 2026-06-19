"""Tests for scheduler/webhook_executor.py — thread-aware conversation continuity.

Covers:
- _resolve_jsonpath: dot-path resolution against nested dicts
- extract_thread_id: flow-config and auto-detect paths
- _build_conversation_context: message list → context string
- execute_webhook_flow: resume vs create logic with mocked DB and agent
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheduler.webhook_executor import (
    _resolve_jsonpath,
    extract_thread_id,
    _build_conversation_context,
    _MAX_CONTEXT_MESSAGES,
)


# ---------------------------------------------------------------------------
# _resolve_jsonpath
# ---------------------------------------------------------------------------


class TestResolveJsonpath:
    def test_simple_key(self):
        assert _resolve_jsonpath({"a": 1}, "a") == 1

    def test_nested_key(self):
        assert _resolve_jsonpath({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_missing_key_returns_none(self):
        assert _resolve_jsonpath({"a": 1}, "b") is None

    def test_missing_nested_key_returns_none(self):
        assert _resolve_jsonpath({"a": {"b": 1}}, "a.c") is None

    def test_non_dict_intermediate_returns_none(self):
        assert _resolve_jsonpath({"a": "string"}, "a.b") is None

    def test_none_value_returns_none(self):
        assert _resolve_jsonpath({"a": None}, "a") is None

    def test_empty_dict(self):
        assert _resolve_jsonpath({}, "a") is None

    def test_integer_value(self):
        assert _resolve_jsonpath({"x": {"y": 42}}, "x.y") == 42

    def test_list_value_returned(self):
        """Lists are valid values — they should be returned as-is."""
        assert _resolve_jsonpath({"a": [1, 2, 3]}, "a") == [1, 2, 3]

    def test_deeply_nested(self):
        obj = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        assert _resolve_jsonpath(obj, "a.b.c.d.e") == "deep"


# ---------------------------------------------------------------------------
# extract_thread_id
# ---------------------------------------------------------------------------


class TestExtractThreadId:
    def test_explicit_config_path(self):
        """Flow-level thread_id_path takes priority over auto-detect."""
        flow = {"webhook_config": {"thread_id_path": "custom.thread"}}
        payload = {"custom": {"thread": "abc-123"}, "data": {"issue_id": "xyz"}}
        assert extract_thread_id(flow, payload) == "abc-123"

    def test_explicit_config_path_missing_returns_autodetect(self):
        """If explicit path doesn't resolve, fall through to auto-detect."""
        flow = {"webhook_config": {"thread_id_path": "custom.nonexistent"}}
        payload = {"data": {"issue_id": "pylon-456"}}
        assert extract_thread_id(flow, payload) == "pylon-456"

    def test_autodetect_data_issue_id(self):
        """Pylon payloads typically have data.issue_id."""
        flow = {}
        payload = {"data": {"issue_id": "pylon-789"}}
        assert extract_thread_id(flow, payload) == "pylon-789"

    def test_autodetect_issue_id_top_level(self):
        flow = {}
        payload = {"issue_id": "top-level-123"}
        assert extract_thread_id(flow, payload) == "top-level-123"

    def test_autodetect_issue_dot_id(self):
        flow = {}
        payload = {"issue": {"id": "nested-issue-456"}}
        assert extract_thread_id(flow, payload) == "nested-issue-456"

    def test_autodetect_data_id(self):
        flow = {}
        payload = {"data": {"id": "data-id-789"}}
        assert extract_thread_id(flow, payload) == "data-id-789"

    def test_no_thread_id_returns_none(self):
        """Payloads without any recognized ID path return None."""
        flow = {}
        payload = {"event": "something", "value": 42}
        assert extract_thread_id(flow, payload) is None

    def test_integer_id_converted_to_string(self):
        flow = {}
        payload = {"data": {"issue_id": 12345}}
        assert extract_thread_id(flow, payload) == "12345"

    def test_empty_payload(self):
        flow = {}
        assert extract_thread_id(flow, {}) is None

    def test_no_webhook_config_key(self):
        """Flow without webhook_config should still auto-detect."""
        flow = {"name": "test-flow"}
        payload = {"issue_id": "abc"}
        assert extract_thread_id(flow, payload) == "abc"

    def test_priority_data_issue_id_over_data_id(self):
        """data.issue_id should be found before data.id due to path order."""
        flow = {}
        payload = {"data": {"issue_id": "specific", "id": "generic"}}
        assert extract_thread_id(flow, payload) == "specific"


# ---------------------------------------------------------------------------
# _build_conversation_context
# ---------------------------------------------------------------------------


class TestBuildConversationContext:
    def test_empty_messages(self):
        assert _build_conversation_context([]) == ""

    def test_single_user_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        assert _build_conversation_context(messages) == "**User**: Hello"

    def test_single_assistant_message(self):
        messages = [{"role": "assistant", "content": "Hi there"}]
        assert _build_conversation_context(messages) == "**Assistant**: Hi there"

    def test_mixed_conversation(self):
        messages = [
            {"role": "user", "content": "What is X?"},
            {"role": "assistant", "content": "X is Y."},
            {"role": "user", "content": "Thanks!"},
        ]
        result = _build_conversation_context(messages)
        assert "**User**: What is X?" in result
        assert "**Assistant**: X is Y." in result
        assert "**User**: Thanks!" in result

    def test_respects_max_messages(self):
        """Only the last N messages should be included."""
        messages = [
            {"role": "user", "content": f"msg-{i}"}
            for i in range(30)
        ]
        result = _build_conversation_context(messages, max_messages=5)
        # Should only contain the last 5
        assert "msg-25" in result
        assert "msg-29" in result
        assert "msg-0" not in result
        assert "msg-24" not in result

    def test_default_max_messages(self):
        """Default cap should be _MAX_CONTEXT_MESSAGES."""
        messages = [
            {"role": "user", "content": f"msg-{i}"}
            for i in range(50)
        ]
        result = _build_conversation_context(messages)
        # First message should NOT be in context (50 > 20)
        assert "msg-0" not in result
        # Last message should be in context
        assert "msg-49" in result

    def test_unknown_role_skipped(self):
        """Messages with unknown roles should be excluded."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "System msg"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = _build_conversation_context(messages)
        assert "**User**: Hello" in result
        assert "**Assistant**: Hi" in result
        assert "System msg" not in result

    def test_empty_content(self):
        messages = [{"role": "user", "content": ""}]
        result = _build_conversation_context(messages)
        assert result == "**User**: "


# ---------------------------------------------------------------------------
# execute_webhook_flow — integration-style tests with mocked dependencies
# ---------------------------------------------------------------------------


def _make_flow(flow_id="flow-1", name="Test Flow", status="active",
               prompt_template="Handle: {{payload}}", webhook_config=None):
    """Create a minimal flow document for testing."""
    flow = {
        "flow_id": flow_id,
        "name": name,
        "status": status,
        "prompt_template": prompt_template,
        "visibility": "shared",
        "created_by": {},
    }
    if webhook_config:
        flow["webhook_config"] = webhook_config
    return flow


def _make_payload(issue_id=None, extra=None):
    """Create a JSON-serializable payload dict."""
    payload = extra or {}
    if issue_id:
        payload.setdefault("data", {})["issue_id"] = issue_id
    return payload


def _make_db_mock(existing_convo=None):
    """Create a mock DB with configurable conversation lookup."""
    db = MagicMock()
    # conversations.find_one
    db.conversations.find_one = AsyncMock(return_value=existing_convo)
    # conversations.insert_one
    db.conversations.insert_one = AsyncMock()
    # conversations.update_one
    db.conversations.update_one = AsyncMock()
    # webhook_logs
    db.webhook_logs.update_one = AsyncMock()
    # flows
    db.flows.update_one = AsyncMock()
    return db


class TestExecuteWebhookFlowThreadContinuity:
    """Test that execute_webhook_flow correctly resumes or creates conversations."""

    @pytest.mark.asyncio
    async def test_no_thread_id_creates_new_conversation(self):
        """Payloads without a thread ID should always create a new conversation."""
        db = _make_db_mock()
        flow = _make_flow()
        payload = {"event": "test"}  # No issue_id

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "new-convo-id"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            # Agent returns no chunks
            mock_agent.return_value = _empty_async_gen()

            result = await _import_and_run(flow, payload, db, "log-1")

            # Should have called start(), NOT resume()
            mock_observer_instance.start.assert_called_once()
            mock_observer_instance.resume.assert_not_called()
            # Should NOT have queried for existing conversation (no thread_id)
            db.conversations.find_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_id_no_existing_creates_new(self):
        """First webhook for a thread ID should create a new conversation."""
        db = _make_db_mock(existing_convo=None)  # No existing
        flow = _make_flow()
        payload = _make_payload(issue_id="pylon-123")

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "new-convo-id"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            mock_agent.return_value = _empty_async_gen()

            result = await _import_and_run(flow, payload, db, "log-2")

            # Should have queried for existing conversation
            db.conversations.find_one.assert_called_once()
            call_args = db.conversations.find_one.call_args
            query = call_args[0][0]
            assert query["metadata.thread_id"] == "pylon-123"
            assert query["metadata.flow_id"] == "flow-1"

            # Should have created new
            mock_observer_instance.start.assert_called_once()
            mock_observer_instance.resume.assert_not_called()

            # thread_id should be in metadata
            constructor_kwargs = MockObserver.call_args
            metadata = constructor_kwargs[1]["metadata"] if "metadata" in constructor_kwargs[1] else constructor_kwargs[0][1]
            assert metadata["thread_id"] == "pylon-123"

    @pytest.mark.asyncio
    async def test_thread_id_with_existing_resumes(self):
        """Subsequent webhook for same thread ID should resume existing conversation."""
        existing = {
            "conversation_id": "existing-convo-123",
            "messages": [
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": "First response"},
            ],
            "status": "completed",
        }
        db = _make_db_mock(existing_convo=existing)
        flow = _make_flow()
        payload = _make_payload(issue_id="pylon-123")

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "existing-convo-123"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            mock_agent.return_value = _empty_async_gen()

            result = await _import_and_run(flow, payload, db, "log-3")

            # Should have called resume(), NOT start()
            mock_observer_instance.resume.assert_called_once()
            mock_observer_instance.start.assert_not_called()

            # Observer should be created with existing conversation_id
            constructor_kwargs = MockObserver.call_args
            assert constructor_kwargs[1].get("conversation_id") == "existing-convo-123"

    @pytest.mark.asyncio
    async def test_resumed_conversation_gets_context(self):
        """Resumed conversations should pass prior messages as conversation_context."""
        existing = {
            "conversation_id": "existing-convo-456",
            "messages": [
                {"role": "user", "content": "Investigate issue X"},
                {"role": "assistant", "content": "I found the root cause is Y."},
            ],
            "status": "completed",
        }
        db = _make_db_mock(existing_convo=existing)
        flow = _make_flow()
        payload = _make_payload(issue_id="pylon-456")

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "existing-convo-456"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            mock_agent.return_value = _empty_async_gen()

            result = await _import_and_run(flow, payload, db, "log-4")

            # stream_agent should be called with conversation_context
            agent_call = mock_agent.call_args
            context = agent_call[1].get("conversation_context", "") or agent_call[0][1] if len(agent_call[0]) > 1 else ""
            # Check the keyword arg
            if "conversation_context" in agent_call[1]:
                context = agent_call[1]["conversation_context"]
            assert "Investigate issue X" in context
            assert "I found the root cause is Y." in context

    @pytest.mark.asyncio
    async def test_new_conversation_no_context(self):
        """New conversations should have empty conversation_context."""
        db = _make_db_mock(existing_convo=None)
        flow = _make_flow()
        payload = _make_payload(issue_id="pylon-new")

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "new-convo"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            mock_agent.return_value = _empty_async_gen()

            result = await _import_and_run(flow, payload, db, "log-5")

            agent_call = mock_agent.call_args
            context = agent_call[1].get("conversation_context", "")
            assert context == ""

    @pytest.mark.asyncio
    async def test_flow_model_passed_to_agent_runtime(self):
        """Webhook flows should run with their configured provider/model id."""
        db = _make_db_mock(existing_convo=None)
        flow = _make_flow()
        flow["model"] = "openai/gpt-4.1"
        payload = _make_payload(issue_id="pylon-model")

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "new-convo"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            mock_agent.return_value = _empty_async_gen()

            await _import_and_run(flow, payload, db, "log-model")

            agent_call = mock_agent.call_args
            assert agent_call[1]["selected_model"] == "openai/gpt-4.1"
            metadata = MockObserver.call_args[1]["metadata"]
            assert metadata["model"] == "openai/gpt-4.1"

    @pytest.mark.asyncio
    async def test_errored_conversation_not_resumed(self):
        """Conversations with error status should NOT be resumed — create new instead."""
        # The query only matches completed|running, so errored convos won't be found
        db = _make_db_mock(existing_convo=None)  # find_one returns None for error status
        flow = _make_flow()
        payload = _make_payload(issue_id="pylon-err")

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "new-convo"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            mock_agent.return_value = _empty_async_gen()

            result = await _import_and_run(flow, payload, db, "log-6")

            # Should create new, not resume
            mock_observer_instance.start.assert_called_once()
            mock_observer_instance.resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_thread_id_path_from_flow_config(self):
        """Flow with custom thread_id_path should use that path."""
        db = _make_db_mock(existing_convo=None)
        flow = _make_flow(
            webhook_config={"thread_id_path": "custom.ticket.ref"}
        )
        payload = {"custom": {"ticket": {"ref": "TICKET-999"}}}

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent") as mock_agent, \
             patch("scheduler.webhook_executor.ConversationObserver") as MockObserver:

            mock_observer_instance = MagicMock()
            mock_observer_instance.conversation_id = "new-convo"
            mock_observer_instance.start = AsyncMock()
            mock_observer_instance.resume = AsyncMock()
            MockObserver.return_value = mock_observer_instance

            mock_agent.return_value = _empty_async_gen()

            result = await _import_and_run(flow, payload, db, "log-7")

            # Should query with custom thread_id
            call_args = db.conversations.find_one.call_args
            query = call_args[0][0]
            assert query["metadata.thread_id"] == "TICKET-999"

    @pytest.mark.asyncio
    async def test_inactive_flow_skips_execution(self):
        """Inactive flows should return None without any DB operations."""
        db = _make_db_mock()
        flow = _make_flow(status="paused")
        payload = _make_payload(issue_id="pylon-skip")

        with patch("scheduler.webhook_executor.get_db", return_value=db):
            from scheduler.webhook_executor import execute_webhook_flow
            result = await execute_webhook_flow(
                flow, json.dumps(payload).encode(), {}, "log-skip"
            )
            assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _empty_async_gen():
    """An async generator that yields nothing."""
    return
    yield  # noqa: unreachable — makes this a generator


async def _import_and_run(flow, payload, db, log_id):
    """Import and run execute_webhook_flow with the given params."""
    from scheduler.webhook_executor import execute_webhook_flow
    return await execute_webhook_flow(
        flow, json.dumps(payload).encode(), {}, log_id
    )
