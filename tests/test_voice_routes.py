from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import voice_routes


def _db(conversation=None, board_lanes=None):
    conversations = SimpleNamespace(
        find_one=AsyncMock(return_value=conversation),
        insert_one=AsyncMock(),
        update_one=AsyncMock(),
    )
    users = SimpleNamespace(find_one=AsyncMock(return_value={
        "task_board": {
            "lanes": board_lanes or [{"id": "todo", "name": "Todo", "order": 0}],
            "tags": [],
        },
    }))
    return SimpleNamespace(conversations=conversations, users=users)


@pytest.mark.asyncio
async def test_create_task_draft_inserts_staged_doc(monkeypatch):
    db = _db()
    monkeypatch.setattr(voice_routes, "_auto_title_task", AsyncMock())

    ok, override = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "create_task", "prompt": "Review July invoices",
         "title": "July invoices", "start": False},
        refs={},
    )
    inserted = db.conversations.insert_one.await_args.args[0]

    assert ok and override is None
    assert inserted["task_status"] == "todo"
    assert inserted["status"] is None
    assert inserted["prompt"] == "Review July invoices"
    assert inserted["title"] == "July invoices"
    assert inserted["metadata"]["created_via"] == "voice"


@pytest.mark.asyncio
async def test_create_task_start_runs_headless(monkeypatch):
    db = _db()
    started = {}

    async def fake_headless(_db, cid, prompt, model, files, owner):
        started.update({"cid": cid, "prompt": prompt, "owner": owner})

    monkeypatch.setattr(voice_routes, "_run_task_headless", fake_headless)

    ok, _ = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "create_task", "prompt": "Do it now", "title": "Now", "start": True},
        refs={},
    )
    inserted = db.conversations.insert_one.await_args.args[0]
    # Let the fire-and-forget task run.
    import asyncio
    await asyncio.sleep(0)

    assert ok
    assert inserted["task_status"] == "active"
    assert started["cid"] == inserted["conversation_id"]
    assert started["owner"] == "owner@example.com"


@pytest.mark.asyncio
async def test_add_input_blocked_while_running():
    conversation = {
        "conversation_id": "task-1",
        "metadata": {"user_name": "owner@example.com"},
        "status": "running",
        "task_status": "active",
    }
    db = _db(conversation)

    ok, override = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "add_input", "ref": "task-1", "input": "also check mobile"},
        refs={"task-1": "task-1"},
    )

    assert not ok
    assert "still running" in override


@pytest.mark.asyncio
async def test_add_input_reactivates_done_task(monkeypatch):
    conversation = {
        "conversation_id": "task-1",
        "metadata": {"user_name": "owner@example.com"},
        "status": "completed",
        "task_status": "done",
        "messages": [],
    }
    db = _db(conversation)
    followup = AsyncMock()
    monkeypatch.setattr(voice_routes, "_run_followup_headless", followup)

    ok, override = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "add_input", "ref": "task-1", "input": "also check mobile"},
        refs={"task-1": "task-1"},
    )
    import asyncio
    await asyncio.sleep(0)

    assert ok and override is None
    set_args = db.conversations.update_one.await_args.args[1]["$set"]
    assert set_args["task_status"] == "active"
    followup.assert_awaited()


@pytest.mark.asyncio
async def test_mark_done_requires_started_task():
    conversation = {
        "conversation_id": "task-1",
        "metadata": {"user_name": "owner@example.com"},
        "status": None,
        "task_status": "todo",
    }
    db = _db(conversation)

    ok, override = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "mark_done", "ref": "task-1"},
        refs={"task-1": "task-1"},
    )

    assert not ok
    assert "hasn't started" in override


@pytest.mark.asyncio
async def test_unknown_ref_is_rejected():
    db = _db(None)

    ok, override = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "mark_done", "ref": "bogus"},
        refs={},
    )

    assert not ok
    assert "couldn't match" in override


@pytest.mark.asyncio
async def test_set_priority_validates_values():
    conversation = {
        "conversation_id": "task-1",
        "metadata": {"user_name": "owner@example.com"},
        "status": "completed",
        "task_status": "active",
    }
    db = _db(conversation)

    ok, override = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "set_priority", "ref": "task-1", "priority": "asap"},
        refs={"task-1": "task-1"},
    )
    assert not ok and "priority" in override

    ok, override = await voice_routes._execute_action(
        db, "owner@example.com",
        {"type": "set_priority", "ref": "task-1", "priority": "urgent"},
        refs={"task-1": "task-1"},
    )
    assert ok and override is None
    set_args = db.conversations.update_one.await_args.args[1]["$set"]
    assert set_args["task_priority"] == "urgent"


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

    def get(self, key, default=None):
        return {"user_email": "owner@example.com"}.get(key, default)


@pytest.mark.asyncio
async def test_handle_voice_command_executes_llm_action(monkeypatch):
    db = _db()
    db.conversations.find = lambda *a, **k: SimpleNamespace(
        sort=lambda *a2, **k2: SimpleNamespace(
            to_list=AsyncMock(return_value=[])))
    monkeypatch.setattr(voice_routes, "get_db", lambda: db)
    monkeypatch.setattr(voice_routes, "_auto_title_task", AsyncMock())
    monkeypatch.setattr(voice_routes, "_call_voice_llm", AsyncMock(return_value={
        "speech": "Created a draft for the invoice review.",
        "action": {"type": "create_task", "prompt": "Review invoices",
                   "title": "Invoice review", "start": False},
    }))

    response = await voice_routes.handle_voice_command(
        _FakeRequest({"text": "create a task to review invoices", "history": []}))

    assert response.status == 200
    import json
    payload = json.loads(response.text)
    assert payload["executed"] is True
    assert payload["speech"] == "Created a draft for the invoice review."
    db.conversations.insert_one.assert_awaited()


@pytest.mark.asyncio
async def test_handle_voice_command_coerces_unknown_action(monkeypatch):
    db = _db()
    db.conversations.find = lambda *a, **k: SimpleNamespace(
        sort=lambda *a2, **k2: SimpleNamespace(
            to_list=AsyncMock(return_value=[])))
    monkeypatch.setattr(voice_routes, "get_db", lambda: db)
    monkeypatch.setattr(voice_routes, "_call_voice_llm", AsyncMock(return_value={
        "speech": "Deleting everything.",
        "action": {"type": "delete_all_tasks"},
    }))

    response = await voice_routes.handle_voice_command(
        _FakeRequest({"text": "delete everything", "history": []}))

    import json
    payload = json.loads(response.text)
    assert payload["action"] == {"type": "none"}
    assert payload["executed"] is False
