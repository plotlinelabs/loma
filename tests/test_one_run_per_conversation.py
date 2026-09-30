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


async def _post_while_busy(db, message="yes do it", files=None):
    from api import routes

    task, claimed, done = _hold_claim("c-chat")
    await claimed.wait()
    stream_agent = MagicMock(side_effect=AssertionError("must not start a second run"))
    body = {"message": message, "conversation_id": "c-chat"}
    if files:
        body["files"] = files
    try:
        with patch.object(routes, "get_db", return_value=db), \
                patch.object(routes, "stream_agent", stream_agent):
            response = await routes.handle_chat(FakeRequest(body))
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


# ── review follow-ups ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unregister_does_not_restore_a_dead_superseded_handle():
    async def register_and_vanish():
        # A run whose task ended without unregistering (crash, abandoned generator).
        return await active_streams.register("c-dead", SimpleNamespace(interrupt=AsyncMock()), "o@x")

    await asyncio.create_task(register_and_vanish())
    live = await active_streams.register("c-dead", SimpleNamespace(interrupt=AsyncMock()), "o@x")
    await active_streams.unregister("c-dead", live)
    assert await active_streams.get_for_user("c-dead", "o@x") is None

    # Same for a superseded handle that already ended.
    first = await active_streams.register("c-dead", SimpleNamespace(interrupt=AsyncMock()), "o@x")
    second = await active_streams.register("c-dead", SimpleNamespace(interrupt=AsyncMock()), "o@x")
    assert second.superseded is first
    first.ended = True
    await active_streams.unregister("c-dead", second)
    assert await active_streams.get_for_user("c-dead", "o@x") is None


@pytest.mark.asyncio
async def test_message_with_files_is_queued_not_injected():
    client = SimpleNamespace(interrupt=AsyncMock(), query=AsyncMock())
    stream = await active_streams.register("c-chat", client, "owner@example.test")
    db = _chat_db()
    files = [{"name": "a.png", "mimetype": "image/png", "type": "image", "data": "aGk="}]
    try:
        status, body = await _post_while_busy(db, files=files)
    finally:
        await active_streams.unregister("c-chat", stream)
    assert status == 409 and body["busy"] and not body["injected"]
    client.query.assert_not_awaited()  # its files would have been dropped
    db.conversations.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_chat_releases_its_claim_on_early_error_return():
    from api import routes

    db = _chat_db()
    with patch.object(routes, "get_db", return_value=db):
        response = await routes.handle_chat(FakeRequest(
            {"message": "hi", "conversation_id": "c-chat", "agent_id": 5}))
    assert response.status == 400
    assert "c-chat" not in active_streams._claims


# Slack: only the run's owner may inject; files and drain wait/bail.


def _injectable_run(conversation_id, owner):
    client = SimpleNamespace(interrupt=AsyncMock(), query=AsyncMock())
    return client, active_streams.register(conversation_id, client, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", ["b@x", "", None])
async def test_slack_reply_is_not_injected_into_another_users_run(sender):
    from slack_app.handlers import _inject_into_active_run

    client, registering = _injectable_run("c-slack-own", "a@x")
    stream = await registering
    try:
        assert not await _inject_into_active_run("c-slack-own", "do it", MagicMock(), sender)
    finally:
        await active_streams.unregister("c-slack-own", stream)
    client.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_reply_from_the_owner_is_injected():
    from slack_app.handlers import _inject_into_active_run

    client, registering = _injectable_run("c-slack-own", "a@x")
    stream = await registering
    db = MagicMock()
    db.conversations.update_one = AsyncMock()
    try:
        assert await _inject_into_active_run("c-slack-own", "do it", db, "a@x")
    finally:
        await active_streams.unregister("c-slack-own", stream)
    client.query.assert_awaited_once_with("do it")


def _slack_client(email):
    client = MagicMock()
    client.reactions_add = AsyncMock()
    client.reactions_remove = AsyncMock()
    client.chat_postMessage = AsyncMock()
    client.users_info = AsyncMock(return_value={"user": {"profile": {"email": email}}})
    return client


async def _slack_followup_while_busy(files, draining_after_wait=False):
    """Post a Slack follow-up while the owner's injectable run holds the thread."""
    from slack_app import handlers

    run_client, registering = _injectable_run("c-slack", "a@x")
    stream = await registering
    task, claimed, done = _hold_claim("c-slack")
    await claimed.wait()
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value={"conversation_id": "c-slack"})
    observer = MagicMock(conversation_id="c-slack", resume=AsyncMock())
    slack = _slack_client("a@x")
    drain_checks = iter([False, draining_after_wait])
    agent = MagicMock(return_value=_events_of("ok"))
    try:
        with patch.object(handlers, "is_draining", side_effect=lambda: next(drain_checks)), \
                patch.object(handlers, "get_db", return_value=db), \
                patch.object(handlers, "ConversationObserver", return_value=observer), \
                patch.object(handlers, "stream_agent", agent), \
                patch.object(handlers, "_stream_response", AsyncMock()), \
                patch.object(handlers, "ingest_dashboard_chat", AsyncMock()):
            request = asyncio.create_task(handlers._handle_agent_request(
                slack, "C1", "1.0", "2.0", "look at this", "", files, "slack_dm", "U1",
                message_has_files=bool(files),
            ))
            await asyncio.sleep(0.05)
            assert not request.done(), "the follow-up must wait for the running run"
            await active_streams.unregister("c-slack", stream)
            done.set()
            await task
            await asyncio.wait_for(request, timeout=10)
    finally:
        done.set()
        await active_streams.unregister("c-slack", stream)
    assert "c-slack" not in active_streams._claims
    return run_client, slack, agent


async def _events_of(*chunks):
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_slack_followup_with_files_waits_for_its_own_run():
    files = [{"name": "a.png", "mimetype": "image/png", "type": "image", "data": "aGk="}]
    run_client, _, agent = await _slack_followup_while_busy(files)
    run_client.query.assert_not_awaited()
    agent.assert_called_once()
    assert agent.call_args.kwargs["files"] == files


@pytest.mark.asyncio
async def test_slack_queued_followup_bails_if_drain_started_while_waiting():
    from api.drain import DRAIN_MESSAGE

    files = [{"name": "a.txt", "mimetype": "text/plain", "type": "text", "data": "x"}]
    _, slack, agent = await _slack_followup_while_busy(files, draining_after_wait=True)
    agent.assert_not_called()
    assert slack.chat_postMessage.await_args.kwargs["text"] == DRAIN_MESSAGE


@pytest.mark.asyncio
async def test_stream_agent_waiter_bails_if_drain_started_while_waiting():
    from agent import client as agent_client
    from api.drain import DRAIN_MESSAGE

    release_first = asyncio.Event()
    started = []

    async def fake_stream_agent(prompt, *args, **kwargs):
        started.append(prompt)
        if prompt == "first":
            await release_first.wait()
        yield f"done:{prompt}"

    def observer():
        return SimpleNamespace(conversation_id="c-drain", resume=AsyncMock())

    async def consume(prompt, obs):
        return [e async for e in agent_client.stream_agent(prompt, observer=obs)]

    second_obs = observer()
    with patch.object(agent_client, "_stream_agent", fake_stream_agent), \
            patch("isolation.deployment.remote_workers_enabled", return_value=False):
        first = asyncio.create_task(consume("first", observer()))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(consume("second", second_obs))
        await asyncio.sleep(0.05)
        with patch("api.drain.is_draining", return_value=True):
            release_first.set()
            _, second_events = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)

    assert started == ["first"]
    assert second_events == [DRAIN_MESSAGE]
    second_obs.resume.assert_not_awaited()
    assert "c-drain" not in active_streams._claims


# Run-end cleanup must finish before the claim is released.


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["claude", "codex"])
async def test_pool_release_kills_the_runs_processes_before_returning(runtime):
    from agent.codex_pool import CodexClientPool
    from agent.pool import ClientPool

    tag = run_processes.new_proc_tag()
    orphan = _spawn_orphan(tag)
    if runtime == "claude":
        pool = ClientPool()
        used = SimpleNamespace(_loma_proc_tag=tag, _pool_ephemeral=True)
    else:
        pool = CodexClientPool()
        used = SimpleNamespace(proc_tag=tag, _pool_ephemeral=True)
    pool._in_use = 1
    pool.safe_disconnect = AsyncMock()
    try:
        await pool.release(used)
        # No settle time: release() itself waited for the kill.
        assert not run_processes._alive(orphan)
    finally:
        try:
            os.kill(orphan, 9)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_kill_run_processes_is_bounded_and_ignores_missing_tags():
    assert await run_processes.kill_run_processes(None) == 0
    assert await run_processes.kill_run_processes(MagicMock()) == 0

    async def hang(tag):
        await asyncio.sleep(60)

    with patch.object(run_processes, "kill_tagged", hang):
        assert await run_processes.kill_run_processes("tag", timeout=0.05) == 0


@pytest.mark.asyncio
async def test_opencode_terminate_closes_the_log_file(tmp_path):
    from agent.opencode_runtime import _OpenCodeServer

    log_file = open(tmp_path / "server.log", "w")
    server = _OpenCodeServer(
        config_hash="h", config_home=tmp_path, host="127.0.0.1", port=1,
        process=None, log_path=tmp_path / "server.log", log_file=log_file,
    )
    await server.terminate()
    assert log_file.closed and server.log_file is None


@pytest.mark.asyncio
async def test_opencode_run_never_leaves_a_registered_handle_behind(monkeypatch):
    from agent import opencode_runtime

    server = SimpleNamespace(
        base_url="http://127.0.0.1:1", config_hash="h", active_turns=0,
        touch=lambda: None, sweep_detached=AsyncMock(), terminate=AsyncMock(),
    )
    monkeypatch.setattr(opencode_runtime, "is_known_model", AsyncMock(return_value=True))
    monkeypatch.setattr(opencode_runtime, "_ensure_server_instance", AsyncMock(return_value=server))
    monkeypatch.setattr(opencode_runtime, "_checkout_warm_session", AsyncMock(return_value="s1"))
    monkeypatch.setattr(opencode_runtime, "_schedule_prewarm", lambda *a, **k: None)
    observer = SimpleNamespace(conversation_id="c-oc", metadata={}, turn_count=0)

    # Closed after the first event: before this fix the handle was already
    # registered but the try/finally that unregisters it was not entered yet.
    gen = opencode_runtime._run_opencode_agent(
        full_prompt="hi", selected_model="opencode/x", observer=observer, include_steps=True,
    )
    await gen.__anext__()
    await gen.aclose()
    assert await active_streams.get_for_user("c-oc", "") is None


# Conversation work dir reaches per-run processes; device.py falls back safely.


@pytest.mark.asyncio
async def test_per_run_codex_worker_gets_the_conversation_dir_env(tmp_path, monkeypatch):
    from agent import codex_runtime

    captured = {}

    async def fake_exec(*args, env=None, **kwargs):
        captured.update(env)
        raise RuntimeError("stop here")

    monkeypatch.setattr(codex_runtime.asyncio, "create_subprocess_exec", fake_exec)
    worker = codex_runtime.CodexWorker(
        {"email": "a@x", "config_dir": str(tmp_path)},
        extra_env={"LOMA_CONVERSATION_DIR": "/work/c1"},
    )
    with pytest.raises(RuntimeError):
        await worker.connect()
    assert captured["LOMA_CONVERSATION_DIR"] == "/work/c1"
    assert captured["CODEX_HOME"] == str(tmp_path)


@pytest.mark.asyncio
async def test_one_off_claude_client_gets_the_conversation_dir_env(monkeypatch):
    from agent import pool as pool_module

    captured = {}

    class FakeClient:
        def __init__(self, options):
            captured.update(options.env)

        async def connect(self):
            pass

    monkeypatch.setattr(pool_module, "ClaudeSDKClient", FakeClient)
    pool = pool_module.ClientPool()
    monkeypatch.setattr(pool, "_build_options",
                        lambda model_override=None: SimpleNamespace(model=model_override, env=None))
    await pool._create_client({"email": "a@x", "config_dir": "/cfg"}, model_override="m",
                              extra_env={"LOMA_CONVERSATION_DIR": "/work/c1"})
    assert captured["LOMA_CONVERSATION_DIR"] == "/work/c1"
    assert captured["CLAUDE_CONFIG_DIR"] == "/cfg"
    assert captured[run_processes.PROC_TAG_ENV]


@pytest.mark.parametrize("env_value", [None, "", "/"])
def test_device_output_dir_never_falls_back_to_the_filesystem_root(env_value, monkeypatch):
    from tools import device

    if env_value is None:
        monkeypatch.delenv("LOMA_CONVERSATION_DIR", raising=False)
    else:
        monkeypatch.setenv("LOMA_CONVERSATION_DIR", env_value)
    folder = device.output_dir("conv-123/../x")
    assert folder.parent == device.Path("/tmp/loma-device")
    assert folder.name == "conv-123..x"


def test_device_output_dir_uses_the_conversation_dir_when_set(tmp_path, monkeypatch):
    from tools import device

    monkeypatch.setenv("LOMA_CONVERSATION_DIR", str(tmp_path))
    assert device.output_dir("ignored") == tmp_path / "device"
