"""Plan mode is read-only on every runtime, and the plan reaches history."""
import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

from agent.plan_mode import PLAN_MODE_BLOCK, PLAN_MODE_MARKER
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

from tests.test_injected_message_once import FakePool as _BasePool


class FakePool(_BasePool):
    def status(self):
        return {"available": 1, "pool_size": 1, "warming": 0, "active_sessions": 1}


def _observer():
    obs = AsyncMock()
    obs.conversation_id = f"c-{uuid.uuid4().hex}"
    obs.metadata = {"source": "dashboard"}
    return obs


def _opencode(monkeypatch, events_for_session):
    import agent.opencode_runtime as ocr

    monkeypatch.setattr(ocr, "_opencode_session_cache", {})
    server = ocr._OpenCodeServer(config_hash="hash", config_home=Path("/tmp"), host="127.0.0.1", port=1, process=None)

    async def fake_ensure(user_mcp_overrides=None):
        return server

    async def fake_known(model_id):
        return True

    async def fake_iter(base_url, *, session_id, body, idle_timeout_seconds, request_timeout_seconds):
        for event in events_for_session(session_id):
            yield event

    monkeypatch.setattr(ocr, "_ensure_server_instance", fake_ensure)
    monkeypatch.setattr(ocr, "is_known_model", fake_known)
    monkeypatch.setattr(ocr, "_schedule_prewarm", lambda server, model_id: None)
    monkeypatch.setattr(ocr, "_iter_opencode_turn_events", fake_iter)
    return ocr


def _part(session_id, part_id, type_, text, done=False):
    part = {"id": part_id, "type": type_, "text": text, "messageID": "m1", "sessionID": session_id}
    if done:
        part["time"] = {"end": 1}
    return {"type": "message.part.updated", "properties": {"part": part}}


def _assistant(session_id, done=False):
    info = {"id": "m1", "role": "assistant", "sessionID": session_id}
    if done:
        info.update({"time": {"completed": 1}, "finish": "stop", "tokens": {"input": 1, "output": 2}})
    return {"type": "message.updated", "properties": {"info": info}}


# ── Codex: the turn's sandbox ──────────────────────────────────────────────


async def _codex_turn_params(read_only):
    from agent.codex_runtime import CodexWorker

    worker = CodexWorker.__new__(CodexWorker)
    worker.thread_id, worker.turn_id = "th", None
    worker._events = asyncio.Queue()
    sent = {}

    async def fake_request(method, params=None, timeout=60):
        sent[method] = params
        await worker._events.put({"method": "turn/completed", "params": {"threadId": "th", "turn": {"status": "completed"}}})
        return {"turn": {"id": "t1"}}

    worker._request = fake_request
    turn = worker.run_turn("go", read_only=True) if read_only else worker.run_turn("go")
    [_ async for _ in turn]
    return sent["turn/start"]


@pytest.mark.asyncio
async def test_codex_plan_turn_is_sandboxed_read_only():
    params = await _codex_turn_params(read_only=True)
    assert params["sandboxPolicy"] == {"type": "readOnly", "networkAccess": True}


@pytest.mark.asyncio
async def test_codex_normal_turn_resets_full_access_on_a_reused_thread():
    params = await _codex_turn_params(read_only=False)
    assert params["sandboxPolicy"] == {"type": "dangerFullAccess"}


# ── OpenCode: the session's permission rules ───────────────────────────────


async def _opencode_session(monkeypatch, prompt):
    sessions, warm = [], []
    ocr = _opencode(monkeypatch, lambda sid: [
        _assistant(sid), _part(sid, "t1", "text", "ok", done=True), _assistant(sid, done=True)])

    async def fake_create_session(title, *, base_url=None, permission=None):
        sessions.append(permission)
        return f"ses_{len(sessions)}"

    async def fake_checkout(server, model_id):
        warm.append(model_id)
        return None

    monkeypatch.setattr(ocr, "_create_session", fake_create_session)
    monkeypatch.setattr(ocr, "_checkout_warm_session", fake_checkout)
    [_ async for _ in ocr.run_opencode_agent(full_prompt=prompt, selected_model="opencode-go/glm-5.3-flash",
                                              observer=_observer(), include_steps=True)]
    return sessions, warm


@pytest.mark.asyncio
async def test_opencode_plan_session_denies_edits_and_skips_warm_sessions(monkeypatch):
    sessions, warm = await _opencode_session(monkeypatch, f"## Conversation Context\n{PLAN_MODE_BLOCK}\n\n## Current Message\nplan it")
    [rules] = sessions
    assert rules[-1] == {"permission": "edit", "pattern": "*", "action": "deny"}
    assert rules[0] == {"permission": "*", "pattern": "*", "action": "allow"}
    assert warm == []


@pytest.mark.asyncio
async def test_opencode_normal_session_is_unchanged(monkeypatch):
    sessions, warm = await _opencode_session(monkeypatch, "do it")
    assert sessions == [None] and warm == ["opencode-go/glm-5.3-flash"]


# ── Claude: ExitPlanMode reaches the chat history ──────────────────────────


class PlanningClaude:
    def __init__(self, plan):
        self.plan = plan
        self.modes = []

    async def query(self, message):
        pass

    async def set_permission_mode(self, mode):
        self.modes.append(mode)

    async def interrupt(self):
        pass

    async def receive_messages(self):
        yield AssistantMessage(content=[ToolUseBlock(id="tu1", name="ExitPlanMode", input={"plan": self.plan})],
                               model="claude-test")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id="s")


@pytest.mark.asyncio
async def test_exit_plan_mode_plan_is_sent_as_text_then_as_the_card(monkeypatch):
    import agent.client as agent_client

    plan = "# Alerts\n\n```mermaid\nflowchart LR\n  A --> B\n```"
    client = PlanningClaude(plan)
    monkeypatch.setattr(agent_client, "get_pool", lambda: FakePool(client))
    observer = _observer()
    events = [e async for e in agent_client._stream_agent(
        "plan it", observer=observer, source="dashboard", selected_model="claude-opus-5-5",
        include_steps=True, conversation_context=PLAN_MODE_BLOCK)]
    assert client.modes == ["plan"]
    kinds = [e["type"] for e in events if isinstance(e, dict) and e.get("type") in ("text", "artifact")]
    assert kinds == ["text", "artifact"]
    text = next(e for e in events if isinstance(e, dict) and e.get("type") == "text")["text"]
    assert text == f"````plan\n{plan}\n````"
    observer.record_text.assert_any_await(1, text)
    artifact = next(e for e in events if isinstance(e, dict) and e.get("type") == "artifact")
    assert artifact["language"] == "plan" and artifact["content"] == plan
    assert PLAN_MODE_MARKER in PLAN_MODE_BLOCK
