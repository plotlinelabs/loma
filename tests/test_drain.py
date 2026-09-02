"""Tests for drain-before-deploy: api/drain.py, the run gates, scheduler
deferral, and the shutdown notification path in recovery.py."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.drain as drain
import recovery
import scheduler.engine as engine
import scheduler.executor as executor
from api.drain import (
    DRAIN_MESSAGE,
    RUNNING_HEARTBEAT_WINDOW_SECONDS,
    handle_clear_drain,
    handle_get_drain,
    handle_set_drain,
    running_query,
)
from api.routes import handle_chat
from api.task_routes import handle_create_task


class FakeRequest(dict):
    def __init__(self, *, remote="127.0.0.1", body=None, user_email="", role="chatter"):
        super().__init__(user_email=user_email, system_role=role)
        self.remote = remote
        self._body = body
        self.can_read_body = body is not None

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def response_json(response):
    return json.loads(response.body)


def _db_with_running(count, oldest=None):
    db = MagicMock()
    db.conversations.count_documents = AsyncMock(return_value=count)
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[{"started_at": oldest}] if oldest else [])
    db.conversations.find.return_value = cursor
    return db


@pytest.fixture(autouse=True)
def _reset_drain():
    drain.set_draining(False)
    yield
    drain.set_draining(False)


# ── api/drain.py ──────────────────────────────────────────────────────────


def test_running_query_only_counts_fresh_heartbeats():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    query = running_query(now)
    assert query["status"] == "running"
    assert query["last_heartbeat"] == {
        "$gte": now - timedelta(seconds=RUNNING_HEARTBEAT_WINDOW_SECONDS)}


@pytest.mark.asyncio
async def test_set_drain_rejects_non_loopback():
    response = await handle_set_drain(FakeRequest(remote="172.18.0.5", body={}))
    assert response.status == 403
    assert drain.is_draining() is False


@pytest.mark.asyncio
async def test_set_and_clear_drain_roundtrip():
    oldest = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    db = _db_with_running(2, oldest=oldest)
    with patch.object(drain, "get_db", return_value=db):
        response = await handle_set_drain(
            FakeRequest(body={"reason": "deploy abc1234"}))
        assert response.status == 200
        body = response_json(response)
        assert body["draining"] is True
        assert body["reason"] == "deploy abc1234"
        assert body["running"] == 2
        assert body["oldest_started_at"] == oldest.isoformat()
        assert drain.drain_reason() == "deploy abc1234"

        response = await handle_clear_drain(FakeRequest())
        body = response_json(response)
        assert body["draining"] is False
        assert body["reason"] == ""
        assert drain.is_draining() is False


@pytest.mark.asyncio
async def test_get_drain_is_readable_from_anywhere_and_tolerates_no_db():
    with patch.object(drain, "get_db", return_value=None):
        response = await handle_get_drain(FakeRequest(remote="172.18.0.5"))
    assert response.status == 200
    assert response_json(response) == {
        "draining": False, "reason": "", "since": None,
        "running": 0, "oldest_started_at": None,
    }


# ── run gates ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_refused_while_draining():
    drain.set_draining(True, "deploy abc1234")
    request = FakeRequest(body={"message": "hi"}, user_email="user@example.com")
    response = await handle_chat(request)
    assert response.status == 503
    body = response_json(response)
    assert body["error"] == DRAIN_MESSAGE
    assert body["draining"] is True


@pytest.mark.asyncio
async def test_quick_add_task_refused_while_draining_but_drafts_allowed():
    drain.set_draining(True)
    db = MagicMock()
    with patch("api.task_routes.get_db", return_value=db):
        response = await handle_create_task(
            FakeRequest(body={"prompt": "do it", "start": True}, user_email="user@example.com"))
        assert response.status == 503
        assert response_json(response)["error"] == DRAIN_MESSAGE

        # A staged draft doesn't run anything, so it must get past the gate
        # (it then fails on the mocked board lookup, which is fine here).
        board = {"lanes": [{"id": "todo", "name": "Todo", "order": 0}], "prompt": "", "tags": []}
        db.conversations.insert_one = AsyncMock()
        with patch("api.task_routes._get_board_config_for", new=AsyncMock(return_value=board)), \
             patch("api.task_routes._auto_title_task", new=AsyncMock()):
            response = await handle_create_task(
                FakeRequest(body={"prompt": "later", "title": "Draft"}, user_email="user@example.com"))
        assert response.status == 201


@pytest.mark.asyncio
async def test_scheduled_flow_deferred_while_draining():
    drain.set_draining(True)
    flow = {"flow_id": "flow-1", "name": "Nightly", "status": "active", "prompt": "go"}
    db = MagicMock()
    db.flows.find_one = AsyncMock(return_value=flow)
    db.flows.update_one = AsyncMock()
    with patch.object(executor, "get_db", return_value=db), \
         patch.object(executor, "stream_agent") as stream_agent:
        await executor.execute_flow("flow-1")
    stream_agent.assert_not_called()
    update = db.flows.update_one.call_args[0]
    assert update[0] == {"flow_id": "flow-1"}
    assert "deferred_run_at" in update[1]["$set"]


@pytest.mark.asyncio
async def test_deferred_flows_run_on_startup_and_are_cleared():
    flows = [
        {"flow_id": "flow-1", "deferred_run_at": datetime.now(timezone.utc)},
        {"flow_id": "flow-2"},
    ]
    db = MagicMock()
    db.flows.update_many = AsyncMock()
    executed = []

    async def fake_execute(flow_id):
        executed.append(flow_id)

    with patch("scheduler.executor.execute_flow", new=fake_execute):
        await engine._run_deferred_flows(db, flows)
        # Let the fire-and-forget task run.
        import asyncio
        await asyncio.sleep(0)
    assert executed == ["flow-1"]
    unset = db.flows.update_many.call_args[0]
    assert unset[0] == {"flow_id": {"$in": ["flow-1"]}}
    assert unset[1] == {"$unset": {"deferred_run_at": ""}}


# ── shutdown: mark interrupted + notify owners ────────────────────────────


def _shutdown_db(running_docs):
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=running_docs)
    db.conversations.find.return_value = cursor
    db.conversations.update_many = AsyncMock(return_value=MagicMock(modified_count=len(running_docs)))
    return db


@pytest.mark.asyncio
async def test_shutdown_marks_interrupted_with_deploy_reason_and_notifies():
    drain.set_draining(True, "deploy abc1234")
    docs = [
        {"conversation_id": "task-1", "source": "dashboard", "task_status": "active",
         "title": "Ship the thing", "metadata": {"user_name": "owner@example.com"}},
        {"conversation_id": "chat-1", "source": "dashboard", "task_status": None,
         "prompt": "quick question", "metadata": {"user_name": "owner@example.com"}},
        {"conversation_id": "slack-1", "source": "slack_mention",
         "metadata": {"slack_channel_id": "C1", "slack_thread_ts": "1.0"}},
    ]
    db = _shutdown_db(docs)
    with patch.object(recovery, "get_db", return_value=db), \
         patch.object(recovery, "create_notification", new=AsyncMock()) as notify, \
         patch.object(recovery, "send_task_needs_input_push", new=AsyncMock()) as push, \
         patch.object(recovery, "_post_to_slack", new=AsyncMock()) as slack:
        await recovery.mark_all_running_interrupted()

    update = db.conversations.update_many.call_args[0]
    assert update[0] == {"status": "running"}
    assert update[1]["$set"]["status"] == "interrupted"
    assert update[1]["$set"]["error"] == "Interrupted by deploy abc1234"

    # Both dashboard conversations get an inbox entry; only the board task
    # gets the deep-linked task push.
    assert notify.await_count == 2
    titles = {c.kwargs["title"] for c in notify.await_args_list}
    assert titles == {"Task interrupted by a restart", "Chat interrupted by a restart"}
    for call in notify.await_args_list:
        assert call.kwargs["user_email"] == "owner@example.com"
        assert "Interrupted by deploy abc1234" in call.kwargs["body"]
        assert call.kwargs["fire_push"] is False
    push.assert_awaited_once_with(db, "task-1")

    slack.assert_awaited_once()
    metadata, text = slack.await_args[0]
    assert metadata["slack_channel_id"] == "C1"
    assert "Interrupted by deploy abc1234" in text


@pytest.mark.asyncio
async def test_shutdown_without_drain_keeps_generic_error_and_skips_when_idle():
    db = _shutdown_db([])
    with patch.object(recovery, "get_db", return_value=db), \
         patch.object(recovery, "create_notification", new=AsyncMock()) as notify:
        await recovery.mark_all_running_interrupted()
    assert db.conversations.update_many.call_args[0][1]["$set"]["error"] == "Server shutting down"
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_notification_failure_does_not_raise():
    docs = [{"conversation_id": "task-1", "source": "dashboard", "task_status": "active",
             "metadata": {"user_name": "owner@example.com"}}]
    db = _shutdown_db(docs)
    with patch.object(recovery, "get_db", return_value=db), \
         patch.object(recovery, "create_notification", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(recovery, "send_task_needs_input_push", new=AsyncMock()):
        await recovery.mark_all_running_interrupted()  # must not raise
