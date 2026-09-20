"""The dashboard Stop button must reach the runtime doing the work.

Regression coverage for: Stop only aborting the browser's SSE fetch while the
run kept going server-side and was then persisted as ``completed``.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent import active_streams
from agent.active_streams import ActiveStream, RunHandle, STOPPED_BY_USER_REASON


def _observer(conversation_id="convo-1"):
    observer = SimpleNamespace(conversation_id=conversation_id, turn_count=0)
    for name in ("record_account", "record_text", "record_tool_call", "record_tool_result",
                 "record_usage", "record_error", "finish", "mark_interrupted"):
        setattr(observer, name, AsyncMock())
    return observer


@pytest.mark.asyncio
async def test_interrupt_flags_stop_and_aborts_runtime():
    client = SimpleNamespace(interrupt=AsyncMock())
    stream = await active_streams.register("convo-1", client, "owner@example.test")
    assert isinstance(stream, ActiveStream) and not stream.stopped
    try:
        found = await active_streams.get_for_user("convo-1", "owner@example.test")
        assert found is stream
        await found.interrupt()
        client.interrupt.assert_awaited_once()
        assert stream.stopped
    finally:
        await active_streams.unregister("convo-1")
    assert await active_streams.get_for_user("convo-1", "owner@example.test") is None


@pytest.mark.asyncio
async def test_run_handle_rejects_injection_but_forwards_interrupt():
    aborted = AsyncMock()
    handle = RunHandle(aborted, "OpenCode")
    await handle.interrupt()
    aborted.assert_awaited_once()
    with pytest.raises(RuntimeError, match="OpenCode"):
        await handle.query("more context")


@pytest.mark.asyncio
async def test_codex_worker_interrupt_targets_the_in_flight_turn():
    from agent.codex_runtime import CodexError, CodexWorker

    worker = CodexWorker({"email": "dev@example.test", "config_dir": "/nonexistent"}, model="gpt-test")
    worker.thread_id = "thread-1"
    calls = []

    async def fake_request(method, params=None, timeout=60):
        calls.append((method, params))
        if method == "turn/start":
            return {"turn": {"id": "turn-9"}}
        return {}

    worker._request = fake_request  # type: ignore[method-assign]
    with pytest.raises(CodexError):
        await worker.interrupt()  # nothing in flight yet

    await worker._events.put({"method": "item/agentMessage/delta",
                              "params": {"threadId": "thread-1", "delta": "hi"}})
    await worker._events.put({"method": "turn/completed",
                              "params": {"threadId": "thread-1", "turn": {"status": "interrupted"}}})
    events = []
    async for event in worker.run_turn("go"):
        events.append(event["type"])
        if event["type"] == "agent_message_delta":
            await worker.interrupt()
    assert events == ["agent_message_delta", "turn_aborted"]
    assert ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-9"}) in calls
    assert worker.turn_id is None  # cleared once the turn is over


@pytest.mark.asyncio
async def test_run_codex_agent_persists_user_stop_as_interrupted(monkeypatch):
    from agent import codex_pool, codex_runtime

    stop = asyncio.Event()

    class Worker:
        account = {"email": "dev@example.test", "config_dir": "/nonexistent"}
        last_rate_limits = None

        async def run_turn(self, prompt):
            yield {"type": "agent_message_delta", "delta": "partial"}
            await stop.wait()
            yield {"type": "turn_aborted"}

        async def interrupt(self):
            stop.set()

    worker = Worker()
    pool = SimpleNamespace(acquire=AsyncMock(return_value=worker), release=AsyncMock(),
                           safe_disconnect=AsyncMock(), mark_account_exhausted=lambda *a, **k: None,
                           status=lambda: {"available": 0, "pool_size": 1})
    monkeypatch.setattr(codex_pool, "get_codex_pool", lambda: pool)
    observer = _observer()

    stream = codex_runtime.run_codex_agent(full_prompt="hi", selected_model="codex/gpt-test",
                                           observer=observer, include_steps=True,
                                           user_email="owner@example.test")
    events = []
    while True:
        event = await anext(stream)
        events.append(event)
        if isinstance(event, dict) and event.get("type") == "text":
            break
    active = await active_streams.get_for_user("convo-1", "owner@example.test")
    assert active is not None, "Codex runs must register a stop handle"
    await active.interrupt()
    events.extend([e async for e in stream])

    observer.mark_interrupted.assert_awaited_once_with(STOPPED_BY_USER_REASON)
    observer.finish.assert_not_awaited()
    observer.record_error.assert_not_awaited()
    assert not any(isinstance(e, str) for e in events), "no 'didn't generate a response' fallback"
    assert await active_streams.get_for_user("convo-1", "owner@example.test") is None
    pool.release.assert_awaited_once_with(worker)


def test_opencode_abort_error_is_recognised():
    from agent.opencode_runtime import _is_abort_error

    assert _is_abort_error({"name": "MessageAbortedError", "data": {"message": "aborted"}})
    assert not _is_abort_error({"name": "ProviderError", "data": {"message": "boom"}})
    assert not _is_abort_error("MessageAbortedError")
