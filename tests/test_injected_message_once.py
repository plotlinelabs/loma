"""A message sent while a run is going must be handled exactly once.

The bug: a message injected near the end of a Claude run was accepted, but the
run loop stopped at the current turn's result and never read the follow-up
turn that answers it. The message got no reply, and the user/dashboard sent it
again as a brand-new run, so it showed up (and ran) twice.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from agent import active_streams


def _result(n):
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=n, session_id="s",
    )


def _text(t):
    return AssistantMessage(content=[TextBlock(text=t)], model="claude-test")


class ScriptedClaude:
    """Fake ClaudeSDKClient: one CLI session, one turn per user message."""

    def __init__(self):
        self.queries = []
        self._inbox = asyncio.Queue()
        self.first_turn_started = asyncio.Event()
        self.release_first_result = asyncio.Event()
        self.reads_after_last_result = 0

    async def query(self, message):
        self.queries.append(message)
        await self._inbox.put(message)

    async def set_permission_mode(self, mode):
        pass

    async def interrupt(self):
        pass

    async def receive_messages(self):
        turn = 0
        while True:
            msg = await self._inbox.get()
            turn += 1
            if turn == 1:
                yield _text("working on it")
                self.first_turn_started.set()
                await self.release_first_result.wait()
                yield _result(1)
            else:
                yield _text(f"answer to: {msg}")
                yield _result(turn)


class FakePool:
    def __init__(self, client):
        self.client = client
        self.released = False

    async def acquire(self, **kwargs):
        self.client._pool_account = {"email": "acct@x"}
        return self.client

    async def release(self, client):
        self.released = True

    def status(self):
        return {}

    def mark_account_exhausted(self, *a, **k):
        pass


def _observer(cid):
    obs = AsyncMock()
    obs.conversation_id = cid
    return obs


async def _run(monkeypatch, client, cid):
    import agent.client as agent_client

    monkeypatch.setattr(agent_client, "get_pool", lambda: FakePool(client))
    out = []
    async for ev in agent_client._stream_agent(
        "start", observer=_observer(cid), source="dashboard",
        selected_model="claude-sonnet-4-5",
    ):
        out.append(ev)
    return out


def _texts(events):
    return "".join(e if isinstance(e, str) else e.get("text", "") for e in events
                   if isinstance(e, str) or e.get("type") == "text")


@pytest.mark.asyncio
async def test_message_injected_before_turn_ends_is_answered_by_the_same_run(monkeypatch):
    client = ScriptedClaude()
    cid = "c-inject-once"
    run = asyncio.create_task(_run(monkeypatch, client, cid))

    await asyncio.wait_for(client.first_turn_started.wait(), 5)
    stream = await active_streams.get_stream(cid)
    assert await active_streams.inject(stream, "follow up")
    client.release_first_result.set()

    events = await asyncio.wait_for(run, 5)
    # The same run answered it, so nothing needs to send it again.
    assert "answer to: follow up" in _texts(events)
    assert client.queries == [client.queries[0], "follow up"]
    # The run has finished: later messages are refused (and so get queued for
    # a new run) instead of being swallowed by a run that no longer reads.
    assert await active_streams.get_stream(cid) is None
    assert not await active_streams.inject(stream, "late")


@pytest.mark.asyncio
async def test_run_without_injection_still_finishes_after_first_result(monkeypatch):
    client = ScriptedClaude()
    client.release_first_result.set()
    events = await asyncio.wait_for(_run(monkeypatch, client, "c-plain"), 5)
    assert "working on it" in _texts(events)
    assert len(client.queries) == 1


# --- registry-level guarantees ------------------------------------------------

async def _registered(cid):
    client = MagicMock()
    client.query = AsyncMock()
    client.interrupt = AsyncMock()
    return client, await active_streams.register(cid, client, "a@x")


@pytest.mark.asyncio
async def test_injection_is_refused_once_the_run_decided_to_finish():
    client, stream = await _registered("c-closing")
    try:
        assert await active_streams.end_turn(stream, may_finish=True)
        assert not await active_streams.inject(stream, "too late")
        client.query.assert_not_called()  # never sent into a dead run
    finally:
        await active_streams.unregister("c-closing", stream)


@pytest.mark.asyncio
async def test_pending_injection_keeps_the_run_going_for_one_more_turn():
    client, stream = await _registered("c-pending")
    try:
        assert await active_streams.inject(stream, "hi")
        assert not await active_streams.end_turn(stream, may_finish=True)  # its turn is next
        assert await active_streams.end_turn(stream, may_finish=True)      # answered; finish
    finally:
        await active_streams.unregister("c-pending", stream)


@pytest.mark.asyncio
async def test_failed_send_does_not_leave_a_pending_injection():
    client, stream = await _registered("c-fail")
    client.query.side_effect = RuntimeError("boom")
    try:
        with pytest.raises(RuntimeError):
            await active_streams.inject(stream, "hi")
        assert stream.pending_injections == 0
    finally:
        await active_streams.unregister("c-fail", stream)


@pytest.mark.asyncio
async def test_inject_endpoint_reports_success_even_if_recording_fails(monkeypatch):
    """A 500 after the agent already has the message made the dashboard queue
    it and send it a second time."""
    from api import routes
    import observability.observer as obs_mod

    client, stream = await _registered("c-record")
    monkeypatch.setattr(routes, "get_user_email", lambda req: "a@x")

    class BrokenObserver:
        def __init__(self, *a, **k):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(obs_mod, "ConversationObserver", BrokenObserver)
    request = MagicMock()
    request.match_info = {"conversation_id": "c-record"}
    request.json = AsyncMock(return_value={"message": "hi"})
    request.app = {"db": MagicMock()}
    try:
        resp = await routes.handle_inject_message(request)
        assert resp.status == 200
        client.query.assert_awaited_once_with("hi")
    finally:
        await active_streams.unregister("c-record", stream)


@pytest.mark.asyncio
async def test_inject_endpoint_refuses_a_finishing_run_so_the_dashboard_queues(monkeypatch):
    from api import routes

    client, stream = await _registered("c-finishing")
    await active_streams.end_turn(stream, may_finish=True)
    monkeypatch.setattr(routes, "get_user_email", lambda req: "a@x")
    request = MagicMock()
    request.match_info = {"conversation_id": "c-finishing"}
    request.json = AsyncMock(return_value={"message": "hi"})
    try:
        resp = await routes.handle_inject_message(request)
        assert resp.status == 409
        client.query.assert_not_called()
    finally:
        await active_streams.unregister("c-finishing", stream)
