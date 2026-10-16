"""Thinking: each runtime streams the model's (summarized) reasoning as
`thinking` events and saves it with the turn, so the chat can show it collapsed."""
import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ThinkingBlock

from tests.test_injected_message_once import FakePool as _BasePool


class FakePool(_BasePool):
    def status(self):
        return {"available": 1, "pool_size": 1, "warming": 0, "active_sessions": 1}


def _thinking(events):
    return [(e["text"], e["append"]) for e in events if isinstance(e, dict) and e.get("type") == "thinking"]


def _observer():
    obs = AsyncMock()
    obs.conversation_id = f"c-{uuid.uuid4().hex}"
    obs.metadata = {"source": "dashboard"}
    return obs


# ── Claude ─────────────────────────────────────────────────────────────────


def _stream(event):
    return StreamEvent(uuid="u", session_id="s", event=event)


class ThinkingClaude:
    """One turn: a streamed thinking block, then its AssistantMessage, then the answer."""

    def __init__(self, stream_thinking=True, thought="Check billing events first.\n\nThen join plans."):
        self.stream_thinking = stream_thinking
        self.thought = thought
        self.queries = []

    async def query(self, message):
        self.queries.append(message)

    async def set_permission_mode(self, mode):
        pass

    async def interrupt(self):
        pass

    async def receive_messages(self):
        if self.stream_thinking:
            yield _stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
            yield _stream({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Check billing events first."}})
            yield _stream({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "\n\nThen join plans."}})
        yield AssistantMessage(content=[ThinkingBlock(thinking=self.thought, signature="sig")], model="claude-test")
        yield AssistantMessage(content=[TextBlock(text="7 accounts churned.")], model="claude-test")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2, session_id="s")


async def _run_claude(monkeypatch, client, observer):
    import agent.client as agent_client

    monkeypatch.setattr(agent_client, "get_pool", lambda: FakePool(client))
    return [ev async for ev in agent_client._stream_agent(
        "start", observer=observer, source="dashboard", selected_model="claude-opus-5-5", include_steps=True)]


@pytest.mark.asyncio
async def test_claude_streams_thinking_live_and_saves_it_once(monkeypatch):
    observer = _observer()
    events = await asyncio.wait_for(_run_claude(monkeypatch, ThinkingClaude(), observer), 5)
    assert _thinking(events) == [("Check billing events first.", False), ("\n\nThen join plans.", True)]
    observer.record_thinking.assert_awaited_once_with(1, "Check billing events first.\n\nThen join plans.")
    # The thinking never leaks into the reply text.
    texts = "".join(e if isinstance(e, str) else e.get("text", "") for e in events
                    if isinstance(e, str) or e.get("type") == "text")
    assert "billing" not in texts and "7 accounts churned." in texts


@pytest.mark.asyncio
async def test_claude_thinking_without_partial_messages_is_sent_whole(monkeypatch):
    observer = _observer()
    events = await asyncio.wait_for(_run_claude(monkeypatch, ThinkingClaude(stream_thinking=False), observer), 5)
    assert _thinking(events) == [("Check billing events first.\n\nThen join plans.", False)]


@pytest.mark.asyncio
async def test_claude_omitted_thinking_shows_nothing(monkeypatch):
    observer = _observer()
    events = await asyncio.wait_for(
        _run_claude(monkeypatch, ThinkingClaude(stream_thinking=False, thought=""), observer), 5)
    assert _thinking(events) == []
    observer.record_thinking.assert_not_awaited()


@pytest.mark.parametrize("env,display", [(None, "summarized"), ("omitted", "omitted"), ("bogus", "summarized")])
def test_claude_thinking_config(monkeypatch, env, display):
    from agent.pool import claude_thinking_config

    if env is None:
        monkeypatch.delenv("CLAUDE_THINKING_DISPLAY", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_THINKING_DISPLAY", env)
    assert claude_thinking_config() == {"type": "adaptive", "display": display}


# ── OpenCode ───────────────────────────────────────────────────────────────


def _opencode(monkeypatch, events_for_session):
    import agent.opencode_runtime as ocr

    monkeypatch.setattr(ocr, "_opencode_session_cache", {})
    server = ocr._OpenCodeServer(config_hash="hash", config_home=Path("/tmp"), host="127.0.0.1", port=1, process=None)

    async def fake_ensure(user_mcp_overrides=None):
        return server

    async def fake_known(model_id):
        return True

    async def fake_create_session(title, *, base_url=None, permission=None):
        return "ses_1"

    async def fake_checkout(server, model_id):
        return None

    async def fake_iter(base_url, *, session_id, body, idle_timeout_seconds, request_timeout_seconds):
        for event in events_for_session(session_id):
            yield event

    monkeypatch.setattr(ocr, "_ensure_server_instance", fake_ensure)
    monkeypatch.setattr(ocr, "is_known_model", fake_known)
    monkeypatch.setattr(ocr, "_create_session", fake_create_session)
    monkeypatch.setattr(ocr, "_checkout_warm_session", fake_checkout)
    monkeypatch.setattr(ocr, "_schedule_prewarm", lambda server, model_id: None)
    monkeypatch.setattr(ocr, "_iter_opencode_turn_events", fake_iter)
    return ocr


def _part(session_id, part_id, type_, text, done=False):
    part = {"id": part_id, "type": type_, "text": text, "messageID": "m1", "sessionID": session_id}
    if done:
        part["time"] = {"end": 1}
    return {"type": "message.part.updated", "properties": {"part": part}}


def _delta(session_id, part_id, delta):
    return {"type": "message.part.delta", "properties": {
        "sessionID": session_id, "messageID": "m1", "partID": part_id, "field": "text", "delta": delta}}


def _assistant(session_id, done=False):
    info = {"id": "m1", "role": "assistant", "sessionID": session_id}
    if done:
        info.update({"time": {"completed": 1}, "finish": "stop", "tokens": {"input": 1, "output": 2}})
    return {"type": "message.updated", "properties": {"info": info}}


@pytest.mark.asyncio
async def test_opencode_reasoning_streams_as_thinking_and_is_saved(monkeypatch):
    ocr = _opencode(monkeypatch, lambda sid: [
        _assistant(sid),
        _part(sid, "r1", "reasoning", ""),
        _delta(sid, "r1", "Need billing events."),
        _delta(sid, "r1", " Then plans."),
        _part(sid, "r1", "reasoning", "Need billing events. Then plans. Done.", done=True),
        _part(sid, "t1", "text", "7 accounts churned.", done=True),
        _assistant(sid, done=True),
    ])
    observer = _observer()
    events = [e async for e in ocr.run_opencode_agent(
        full_prompt="hi", selected_model="opencode-go/glm-5.3-flash", observer=observer, include_steps=True)]
    assert _thinking(events) == [("Need billing events.", False), (" Then plans.", True), (" Done.", True)]
    observer.record_thinking.assert_awaited_once_with(1, "Need billing events. Then plans. Done.")


@pytest.mark.asyncio
async def test_opencode_reasoning_without_text_keeps_the_status_line(monkeypatch):
    ocr = _opencode(monkeypatch, lambda sid: [
        _assistant(sid),
        _part(sid, "r1", "reasoning", ""),
        _part(sid, "r1", "reasoning", "", done=True),
        _part(sid, "t1", "text", "ok", done=True),
        _assistant(sid, done=True),
    ])
    observer = _observer()
    events = [e async for e in ocr.run_opencode_agent(
        full_prompt="hi", selected_model="opencode-go/glm-5.3-flash", observer=observer, include_steps=True)]
    assert _thinking(events) == []
    assert any(e.get("type") == "status" and "reasoning" in e.get("message", "") for e in events if isinstance(e, dict))
    observer.record_thinking.assert_not_awaited()


@pytest.mark.asyncio
async def test_opencode_reasoning_is_saved_but_not_streamed_without_steps(monkeypatch):
    ocr = _opencode(monkeypatch, lambda sid: [
        _assistant(sid),
        _part(sid, "r1", "reasoning", ""),
        _delta(sid, "r1", "quiet"),
        _part(sid, "r1", "reasoning", "quiet thought", done=True),
        _part(sid, "t1", "text", "ok", done=True),
        _assistant(sid, done=True),
    ])
    observer = _observer()
    events = [e async for e in ocr.run_opencode_agent(
        full_prompt="hi", selected_model="opencode-go/glm-5.3-flash", observer=observer)]
    assert _thinking(events) == []
    observer.record_thinking.assert_awaited_once_with(1, "quiet thought")


# ── Codex ──────────────────────────────────────────────────────────────────


def test_codex_reasoning_events_are_normalized():
    from agent.codex_runtime import _normalize_v2_event

    tid = "t-1"
    assert _normalize_v2_event({"method": "item/reasoning/summaryTextDelta", "params": {
        "threadId": tid, "itemId": "r1", "summaryIndex": 0, "delta": "Plan"}}, tid) == [
        {"type": "reasoning_delta", "item_id": "r1", "delta": "Plan"}]
    assert _normalize_v2_event({"method": "item/reasoning/summaryTextDelta", "params": {
        "threadId": tid, "itemId": "r1", "delta": ""}}, tid) == []
    assert _normalize_v2_event({"method": "item/reasoning/summaryPartAdded", "params": {
        "threadId": tid, "itemId": "r1", "summaryIndex": 1}}, tid) == [
        {"type": "reasoning_part", "item_id": "r1", "index": 1}]
    done = {"method": "item/completed", "params": {"threadId": tid, "item": {
        "type": "reasoning", "id": "r1", "summary": ["First.", " ", "Second."], "content": ["raw"]}}}
    assert _normalize_v2_event(done, tid) == [{"type": "reasoning", "item_id": "r1", "text": "First.\n\nSecond."}]
    raw_only = {"method": "item/completed", "params": {"threadId": tid, "item": {
        "type": "reasoning", "id": "r2", "summary": [], "content": ["raw thought"]}}}
    assert _normalize_v2_event(raw_only, tid) == [{"type": "reasoning", "item_id": "r2", "text": "raw thought"}]
    empty = {"method": "item/completed", "params": {"threadId": tid, "item": {"type": "reasoning", "id": "r3"}}}
    assert _normalize_v2_event(empty, tid) == []


@pytest.mark.parametrize("env,value", [(None, "auto"), ("detailed", "detailed"), ("none", "none"), ("loud", "auto")])
def test_codex_reasoning_summary_setting(monkeypatch, env, value):
    from agent.codex_runtime import codex_reasoning_summary

    if env is None:
        monkeypatch.delenv("CODEX_REASONING_SUMMARY", raising=False)
    else:
        monkeypatch.setenv("CODEX_REASONING_SUMMARY", env)
    assert codex_reasoning_summary() == value


# ── Observer ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_observer_saves_thinking_on_the_turn():
    from mongomock_motor import AsyncMongoMockClient

    from observability.observer import ConversationObserver

    db = AsyncMongoMockClient()[f"thinking_{uuid.uuid4().hex}"]
    observer = ConversationObserver(db, {}, "c1")
    observer.turn_offset = 4
    await observer.record_thinking(1, "  Check billing first.  ")
    await observer.record_thinking(1, "Then plans.")
    await observer.record_thinking(1, "   ")
    await observer.record_text(1, "Answer")
    turn = await db.turns.find_one({"conversation_id": "c1", "turn_number": 5})
    assert [b["text"] for b in turn["thinking_blocks"]] == ["Check billing first.", "Then plans."]
    assert turn["text_blocks"][0]["text"] == "Answer"
