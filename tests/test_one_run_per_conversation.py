"""One active run per conversation, handle ownership, and run-end cleanup.

Regression coverage for a conversation where a resubmitted "yes do it" started
a second agent run next to the first: both drove the same emulator, proxy and
branches, Stop only reached one of them, and their background processes
outlived the runs.
"""
import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import active_streams, run_processes
from agent.active_streams import RunHandle


def _hold_claim(conversation_id):
    """Claim ``conversation_id`` from another task (a run in progress)."""
    claimed, done = asyncio.Event(), asyncio.Event()

    async def run():
        assert await active_streams.try_claim(conversation_id, "first-run")
        claimed.set()
        await done.wait()
        await active_streams.release_claim(conversation_id, "first-run")

    task = asyncio.create_task(run())
    return task, claimed, done


# ── claims ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_claim_is_refused_until_the_first_run_releases():
    task, claimed, done = _hold_claim("c-claim")
    await claimed.wait()
    assert not await active_streams.try_claim("c-claim", "second-run")
    done.set()
    await task
    assert await active_streams.try_claim("c-claim", "second-run")
    await active_streams.release_claim("c-claim", "second-run")


@pytest.mark.asyncio
async def test_claim_is_reentrant_for_the_owning_task_and_released_only_by_owner():
    assert await active_streams.try_claim("c-reentrant", "outer")
    # stream_agent re-checks inside the route's task: allowed, outer keeps it.
    assert await active_streams.try_claim("c-reentrant", "inner")
    await active_streams.release_claim("c-reentrant", "inner")  # not the owner: no-op
    assert active_streams._claims["c-reentrant"].run_id == "outer"
    await active_streams.release_claim("c-reentrant", "outer")
    assert "c-reentrant" not in active_streams._claims


@pytest.mark.asyncio
async def test_claim_of_a_finished_task_is_stale():
    async def claim_and_vanish():
        assert await active_streams.try_claim("c-stale", "crashed-run")

    await asyncio.create_task(claim_and_vanish())  # never released
    assert await active_streams.try_claim("c-stale", "next-run")
    await active_streams.release_claim("c-stale", "next-run")


# ── handle ownership ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unregistering_an_old_handle_keeps_the_new_one():
    old = await active_streams.register("c-own", SimpleNamespace(interrupt=AsyncMock()), "o@x")
    await active_streams.unregister("c-own", old)
    new = await active_streams.register("c-own", SimpleNamespace(interrupt=AsyncMock()), "o@x")
    try:
        await active_streams.unregister("c-own", old)  # late cleanup of the old run
        assert await active_streams.get_for_user("c-own", "o@x") is new
    finally:
        await active_streams.unregister("c-own", new)
    assert await active_streams.get_for_user("c-own", "o@x") is None


@pytest.mark.asyncio
async def test_stop_reaches_the_live_run_even_if_a_handle_was_displaced():
    first_client = SimpleNamespace(interrupt=AsyncMock())
    second_client = SimpleNamespace(interrupt=AsyncMock())
    first = await active_streams.register("c-stop", first_client, "o@x")
    second = await active_streams.register("c-stop", second_client, "o@x")
    try:
        await (await active_streams.get_for_user("c-stop", "o@x")).interrupt()
        first_client.interrupt.assert_awaited_once()
        second_client.interrupt.assert_awaited_once()
        assert first.stopped and second.stopped
        # The newer run finishing hands the registry back to the older one.
        await active_streams.unregister("c-stop", second)
        assert await active_streams.get_for_user("c-stop", "o@x") is first
    finally:
        await active_streams.unregister("c-stop", first)
    assert await active_streams.get_for_user("c-stop", "o@x") is None


# ── dashboard chat entry point ────────────────────────────────────────────


class FakeRequest(dict):
    def __init__(self, body, user_email="owner@example.test"):
        super().__init__(user_email=user_email, system_role="chatter")
        self._body = body
        self.app = {}

    async def json(self):
        return self._body


def _chat_db(last_message=None):
    db = MagicMock()
    doc = {"conversation_id": "c-chat", "metadata": {"user_name": "owner@example.test"},
           "messages": [last_message] if last_message else []}
    db.conversations.find_one = AsyncMock(return_value=doc)
    db.conversations.update_one = AsyncMock()
    return db


async def _post_while_busy(db, message="yes do it"):
    from api import routes

    task, claimed, done = _hold_claim("c-chat")
    await claimed.wait()
    stream_agent = MagicMock(side_effect=AssertionError("must not start a second run"))
    try:
        with patch.object(routes, "get_db", return_value=db), \
                patch.object(routes, "stream_agent", stream_agent):
            response = await routes.handle_chat(
                FakeRequest({"message": message, "conversation_id": "c-chat"}))
    finally:
        done.set()
        await task
    stream_agent.assert_not_called()
    return response.status, json.loads(response.body)


@pytest.mark.asyncio
async def test_second_message_is_injected_into_the_running_claude_run():
    client = SimpleNamespace(interrupt=AsyncMock(), query=AsyncMock())
    stream = await active_streams.register("c-chat", client, "owner@example.test")
    db = _chat_db()
    try:
        status, body = await _post_while_busy(db)
    finally:
        await active_streams.unregister("c-chat", stream)
    assert status == 409 and body["busy"] and body["injected"]
    client.query.assert_awaited_once_with("yes do it")
    pushed = db.conversations.update_one.await_args.args[1]["$push"]["messages"]
    assert pushed["injected"] is True  # recorded once, as an injection


@pytest.mark.asyncio
async def test_second_message_is_queued_when_the_runtime_cannot_inject():
    handle = RunHandle(AsyncMock(), "OpenCode")
    stream = await active_streams.register("c-chat", handle, "owner@example.test")
    db = _chat_db()
    try:
        status, body = await _post_while_busy(db)
    finally:
        await active_streams.unregister("c-chat", stream)
    assert status == 409 and body["busy"] and not body["injected"] and not body["duplicate"]
    db.conversations.update_one.assert_not_awaited()  # nothing recorded until it is sent


@pytest.mark.asyncio
async def test_identical_resubmit_is_dropped_as_duplicate():
    client = SimpleNamespace(interrupt=AsyncMock(), query=AsyncMock())
    stream = await active_streams.register("c-chat", client, "owner@example.test")
    db = _chat_db({"role": "user", "content": "yes do it",
                   "timestamp": datetime.now(timezone.utc).replace(tzinfo=None)})
    try:
        status, body = await _post_while_busy(db)
    finally:
        await active_streams.unregister("c-chat", stream)
    assert status == 409 and body["duplicate"]
    client.query.assert_not_awaited()
    db.conversations.update_one.assert_not_awaited()


# ── stream_agent (Slack / webhooks / flows share it) ──────────────────────


@pytest.mark.asyncio
async def test_stream_agent_queues_a_second_run_of_the_same_conversation():
    from agent import client as agent_client

    running = 0
    peak = 0
    order = []
    release_first = asyncio.Event()

    async def fake_stream_agent(prompt, *args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        order.append(f"start:{prompt}")
        if prompt == "first":
            await release_first.wait()
        yield f"done:{prompt}"
        order.append(f"end:{prompt}")
        running -= 1

    def observer():
        return SimpleNamespace(conversation_id="c-queue", resume=AsyncMock())

    async def consume(prompt, obs):
        return [e async for e in agent_client.stream_agent(prompt, observer=obs)]

    first_obs, second_obs = observer(), observer()
    with patch.object(agent_client, "_stream_agent", fake_stream_agent), \
            patch("isolation.deployment.remote_workers_enabled", return_value=False):
        first = asyncio.create_task(consume("first", first_obs))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(consume("second", second_obs))
        await asyncio.sleep(0.05)
        assert order == ["start:first"], "second run must wait for the first"
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=10)

    assert peak == 1
    assert order == ["start:first", "end:first", "start:second", "end:second"]
    second_obs.resume.assert_awaited_once_with(record_prompt=False)
    first_obs.resume.assert_not_awaited()
    assert "c-queue" not in active_streams._claims


# ── run-end process cleanup ───────────────────────────────────────────────


def _alive(pid, settle=1.0):
    """True if ``pid`` is still running after giving a just-signalled process time to die."""
    deadline = time.monotonic() + settle
    while run_processes._alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return run_processes._alive(pid)


def _spawn_orphan(tag, script="sleep 300"):
    """Background a process from a shell that exits (reparented like an agent's `cmd &`)."""
    out = subprocess.run(
        ["sh", "-c", f"{script} >/dev/null 2>&1 & echo $!"],
        env={**os.environ, run_processes.PROC_TAG_ENV: tag},
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
@pytest.mark.asyncio
async def test_run_end_kills_backgrounded_processes_including_term_ignoring_ones():
    tag = run_processes.new_proc_tag()
    polite = _spawn_orphan(tag)
    stubborn = _spawn_orphan(tag, "trap '' TERM; exec sleep 300")
    untagged = _spawn_orphan(run_processes.new_proc_tag())
    try:
        assert run_processes._alive(polite) and run_processes._alive(stubborn)
        killed = await run_processes.kill_tagged(tag, grace=0.5)
        assert killed == 2
        assert not _alive(polite) and not _alive(stubborn)
        assert _alive(untagged), "other runs' processes are untouched"
    finally:
        for pid in (polite, stubborn, untagged):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
@pytest.mark.asyncio
async def test_shared_server_sweep_spares_attached_children_and_older_processes():
    tag = run_processes.new_proc_tag()
    env = {**os.environ, run_processes.PROC_TAG_ENV: tag}
    older = _spawn_orphan(tag)  # e.g. left from before the last sweep
    time.sleep(0.05)
    since = run_processes.clock_ticks_now()
    time.sleep(0.05)
    # A long-lived runtime server with an attached child (its MCP server).
    server = subprocess.Popen(["sh", "-c", "sleep 300 & wait"], env=env)
    orphan = _spawn_orphan(tag)
    try:
        await asyncio.sleep(0.1)
        killed = await run_processes.kill_tagged(
            tag, started_after=since, detached_from=server.pid, grace=0.5)
        assert killed == 1
        assert not _alive(orphan)
        assert server.poll() is None, "the shared server itself survives"
        assert _alive(older)
    finally:
        for pid in (older, orphan):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        await run_processes.kill_tagged(tag, grace=0.1)
        server.wait(timeout=5)
