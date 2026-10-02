"""Tests for api/pending_runs.py: work that arrives during a deploy drain is
queued in Mongo and started by the next server (or when the drain is
cancelled), exactly once."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient

import api.drain as drain
import api.pending_runs as pending_runs


@pytest.fixture(autouse=True)
def _reset_drain():
    drain.set_draining(False)
    yield
    drain.set_draining(False)


@pytest.fixture
def db():
    return AsyncMongoMockClient()[f"pending_{uuid.uuid4().hex}"]


async def _settle():
    """Let fire-and-forget dispatch tasks finish."""
    for _ in range(5):
        await asyncio.sleep(0)


async def _queue_chat(db, conversation_id, prompt, **extra):
    return await pending_runs.enqueue(
        db, "dashboard", conversation_id=conversation_id, user_email="a@x",
        source="dashboard", prompt=prompt, files=extra.pop("files", []),
        model=extra.pop("model", ""), tool_config=None,
        conversation_context=extra.pop("conversation_context", ""), **extra,
    )


@pytest.mark.asyncio
async def test_queued_chat_runs_on_startup_and_is_removed(db):
    await _queue_chat(db, "c1", "hello", conversation_context="ctx")
    run_agent = AsyncMock()
    with patch.object(pending_runs, "_run_agent", run_agent):
        started = await pending_runs.dispatch_pending(db)
        await _settle()
    assert started == 1
    entry = run_agent.await_args.args[1]
    assert entry["conversation_id"] == "c1"
    assert entry["prompt"] == "hello"
    assert entry["conversation_context"] == "ctx"
    assert await db.pending_runs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_nothing_starts_while_still_draining(db):
    await _queue_chat(db, "c1", "hello")
    drain.set_draining(True, "deploy abc")
    run_agent = AsyncMock()
    with patch.object(pending_runs, "_run_agent", run_agent):
        assert await pending_runs.dispatch_pending(db) == 0
    run_agent.assert_not_awaited()
    assert await db.pending_runs.count_documents({"status": "queued"}) == 1


@pytest.mark.asyncio
async def test_several_messages_for_one_chat_run_as_one_merged_turn(db):
    f1 = {"name": "a.txt", "data": "YQ=="}
    f2 = {"name": "b.txt", "data": "Yg=="}
    await _queue_chat(db, "c1", "first", files=[f1], conversation_context="before")
    await _queue_chat(db, "c1", "second", files=[f2], conversation_context="later", model="m2")
    await _queue_chat(db, "c2", "other chat")
    run_agent = AsyncMock()
    with patch.object(pending_runs, "_run_agent", run_agent):
        assert await pending_runs.dispatch_pending(db) == 2
        await _settle()
    by_cid = {c.args[1]["conversation_id"]: c.args[1] for c in run_agent.await_args_list}
    merged = by_cid["c1"]
    assert merged["prompt"] == "first\n\nsecond"
    assert merged["files"] == [f1, f2]
    assert merged["conversation_context"] == "before"
    assert merged["model"] == "m2"
    assert by_cid["c2"]["prompt"] == "other chat"


@pytest.mark.asyncio
async def test_concurrent_dispatchers_never_run_an_entry_twice(db):
    await _queue_chat(db, "c1", "hello")
    run_agent = AsyncMock()
    with patch.object(pending_runs, "_run_agent", run_agent):
        results = await asyncio.gather(
            pending_runs.dispatch_pending(db), pending_runs.dispatch_pending(db))
        await _settle()
    assert sorted(results) == [0, 1]
    assert run_agent.await_count == 1


@pytest.mark.asyncio
async def test_claim_is_atomic_per_entry(db):
    entry = await _queue_chat(db, "c1", "hello")
    assert await pending_runs._claim(db, entry) is True
    assert await pending_runs._claim(db, entry) is False


@pytest.mark.asyncio
async def test_slack_entries_wait_for_a_slack_client_then_run_in_order(db):
    for ts in ("2.0", "3.0"):
        await pending_runs.enqueue(
            db, "slack", channel="C1", thread_ts="1.0", event_ts=ts, prompt=f"msg {ts}",
            context="", files=[], source="slack_dm", user_id="U1", flow_id=None,
            message_has_files=False,
        )
    run_slack = AsyncMock()
    with patch.object(pending_runs, "_run_slack", run_slack), \
            patch.object(pending_runs, "_slack_client", None):
        assert await pending_runs.dispatch_pending(db) == 0
        run_slack.assert_not_awaited()

        client = object()
        assert await pending_runs.dispatch_pending(db, slack_client=client) == 1
        await _settle()
    assert [c.args[0]["event_ts"] for c in run_slack.await_args_list] == ["2.0", "3.0"]
    assert all(c.args[1] is client for c in run_slack.await_args_list)
    assert await db.pending_runs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_failed_run_is_kept_as_failed(db):
    await _queue_chat(db, "c1", "hello")
    with patch.object(pending_runs, "_run_agent", AsyncMock(side_effect=RuntimeError("boom"))):
        await pending_runs.dispatch_pending(db)
        await _settle()
    doc = await db.pending_runs.find_one({})
    assert doc["status"] == "failed"
    assert "boom" in doc["error"]


@pytest.mark.asyncio
async def test_entries_older_than_a_day_expire(db):
    entry = await _queue_chat(db, "c1", "hello")
    await db.pending_runs.update_one(
        {"run_id": entry["run_id"]},
        {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(hours=25)}})
    run_agent = AsyncMock()
    with patch.object(pending_runs, "_run_agent", run_agent):
        assert await pending_runs.dispatch_pending(db) == 0
    run_agent.assert_not_awaited()
    assert (await db.pending_runs.find_one({}))["status"] == "failed"


@pytest.mark.asyncio
async def test_record_queued_message_new_and_existing_conversation(db):
    await pending_runs.record_queued_message(
        db, "new", "hi", sender="a@x",
        new_conversation={"source": "dashboard", "prompt": "hi", "model": "m", "user_name": "a@x"})
    doc = await db.conversations.find_one({"conversation_id": "new"})
    assert doc["status"] == "queued"
    assert doc["prompt"] == "hi"
    assert doc["metadata"] == {"user_name": "a@x"}
    assert [m["content"] for m in doc["messages"]] == ["hi"]

    await db.conversations.update_one({"conversation_id": "new"}, {"$set": {"status": "completed"}})
    await pending_runs.record_queued_message(db, "new", "again", sender="a@x")
    doc = await db.conversations.find_one({"conversation_id": "new"})
    assert doc["status"] == "queued"
    assert [m["content"] for m in doc["messages"]] == ["hi", "again"]
    assert doc["messages"][-1]["sender"] == "a@x"


@pytest.mark.asyncio
async def test_stop_drops_queued_runs(db):
    await db.conversations.insert_one({"conversation_id": "c1", "status": "queued"})
    await _queue_chat(db, "c1", "hello")
    assert await pending_runs.cancel_conversation(db, "c1") == 1
    assert await db.pending_runs.count_documents({}) == 0
    doc = await db.conversations.find_one({"conversation_id": "c1"})
    assert doc["status"] == "interrupted"
    assert doc["error"] == "Stopped by user"


@pytest.mark.asyncio
async def test_run_agent_resumes_without_re_recording_the_message(db):
    observer = MagicMock(resume=AsyncMock())
    seen = {}

    async def fake_stream_agent(**kwargs):
        seen.update(kwargs)
        yield "done"

    entry = {
        "kind": "dashboard", "conversation_id": "c1", "user_email": "a@x",
        "source": "dashboard", "prompt": "hello", "files": [], "model": "m1",
        "tool_config": {"enabled_tools": ["x"]}, "conversation_context": "ctx",
        "metadata": {"agent_id": "ag1"},
    }
    with patch("agent.client.stream_agent", fake_stream_agent), \
            patch("observability.observer.ConversationObserver", return_value=observer) as obs_cls, \
            patch("api.dashboard_ingestion.ingest_dashboard_chat", AsyncMock()):
        await pending_runs._run_agent(db, entry)
    observer.resume.assert_awaited_once_with(record_prompt=False)
    metadata = obs_cls.call_args.kwargs["metadata"]
    assert metadata["agent_id"] == "ag1"
    assert metadata["user_name"] == "a@x"
    assert seen["prompt"] == "hello"
    assert seen["conversation_context"] == "ctx"
    assert seen["selected_model"] == "m1"
    assert seen["tool_config"] == {"enabled_tools": ["x"]}
    assert seen["files"] is None


@pytest.mark.asyncio
async def test_cancelling_the_drain_starts_the_queue():
    from tests.test_drain import FakeRequest

    drain.set_draining(True)
    dispatch = AsyncMock(return_value=0)
    with patch.object(pending_runs, "dispatch_pending", dispatch), \
            patch.object(drain, "get_db", return_value=None):
        response = await drain.handle_clear_drain(FakeRequest())
        await _settle()
    assert response.status == 200
    dispatch.assert_awaited_once()


def test_files_fit_limits_stored_attachments():
    assert pending_runs.files_fit(None)
    assert pending_runs.files_fit([{"name": "a", "data": "x" * 100}])
    assert not pending_runs.files_fit([{"name": "a", "data": "x" * (pending_runs.MAX_FILES_BYTES + 1)}])
