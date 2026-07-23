from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import task_routes


class FakeRequest:
    match_info = {"conversation_id": "source-id"}

    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_fork_task_copies_context_and_resets_execution_state(monkeypatch):
    source = {
        "conversation_id": "source-id",
        "metadata": {"user_name": "owner@example.com"},
        "title": "Research",
        "prompt": "Investigate this",
        "model": "model-1",
        "status": "running",
        "messages": [{"role": "user", "content": "context"}],
        "total_turns": 3,
        "task_lane": "todo",
        "task_tag_ids": ["tag-1"],
        "task_priority": "high",
        "task_deadline": "2026-08-01",
    }
    conversations = SimpleNamespace(
        find_one=AsyncMock(return_value=source),
        insert_one=AsyncMock(),
    )
    users = SimpleNamespace(find_one=AsyncMock(return_value={
        "task_board": {
            "lanes": [{"id": "todo", "name": "Todo", "order": 0}],
            "tags": [],
        },
    }))
    monkeypatch.setattr(task_routes, "get_db", lambda: SimpleNamespace(
        conversations=conversations, users=users,
    ))
    monkeypatch.setattr(task_routes, "get_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(task_routes, "get_system_role", lambda _request: "admin")
    monkeypatch.setattr("api.routes._check_conversation_access", lambda *_args: True)

    response = await task_routes.handle_fork_task(FakeRequest())
    inserted = conversations.insert_one.await_args.args[0]

    assert response.status == 201
    assert inserted["conversation_id"] != "source-id"
    assert inserted["title"] == "Research (fork)"
    assert inserted["messages"] == source["messages"]
    assert inserted["messages"] is not source["messages"]
    assert inserted["status"] == "interrupted"
    assert inserted["task_status"] == "todo"
    assert inserted["task_started_at"] is None
    assert inserted["task_done_at"] is None
    assert inserted["cost"] is None
    assert inserted["forked_from_conversation_id"] == "source-id"


@pytest.mark.asyncio
async def test_fork_shared_task_uses_callers_lane_and_drops_foreign_tags(monkeypatch):
    source = {
        "conversation_id": "source-id",
        "metadata": {"user_name": "other@example.com"},
        "title": "Shared",
        "status": "completed",
        "task_lane": "foreign-lane",
        "task_tag_ids": ["foreign-tag"],
    }
    conversations = SimpleNamespace(
        find_one=AsyncMock(return_value=source),
        insert_one=AsyncMock(),
    )
    users = SimpleNamespace(find_one=AsyncMock(return_value={
        "task_board": {
            "lanes": [{"id": "mine", "name": "Mine", "order": 0}],
            "tags": [],
        },
    }))
    monkeypatch.setattr(task_routes, "get_db", lambda: SimpleNamespace(
        conversations=conversations, users=users,
    ))
    monkeypatch.setattr(task_routes, "get_user_email", lambda _request: "caller@example.com")
    monkeypatch.setattr(task_routes, "get_system_role", lambda _request: "admin")
    monkeypatch.setattr("api.routes._check_conversation_access", lambda *_args: True)

    response = await task_routes.handle_fork_task(FakeRequest())
    inserted = conversations.insert_one.await_args.args[0]

    assert response.status == 201
    assert inserted["metadata"]["user_name"] == "caller@example.com"
    assert inserted["task_lane"] == "mine"
    assert inserted["task_tag_ids"] == []
