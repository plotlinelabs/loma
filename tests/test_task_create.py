from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api import task_routes


class FakeRequest:
    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


def _setup(monkeypatch):
    conversations = SimpleNamespace(insert_one=AsyncMock())
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
    scheduled = MagicMock()
    monkeypatch.setattr(task_routes.asyncio, "create_task", scheduled)
    return conversations, scheduled


@pytest.mark.asyncio
async def test_create_empty_draft_leaves_title_open_for_auto_naming(monkeypatch):
    conversations, scheduled = _setup(monkeypatch)

    response = await task_routes.handle_create_task(
        FakeRequest({"prompt": "", "lane": "todo"}))
    inserted = conversations.insert_one.await_args.args[0]

    assert response.status == 201
    assert inserted["title"] is None
    assert inserted["title_edited"] is False
    assert inserted["task_status"] == "todo"
    # No prompt yet — finish-time enrichment names the task, not creation.
    scheduled.assert_not_called()


@pytest.mark.asyncio
async def test_quick_add_with_prompt_schedules_auto_title(monkeypatch):
    conversations, scheduled = _setup(monkeypatch)

    response = await task_routes.handle_create_task(
        FakeRequest({"prompt": "Investigate the flaky deploy"}))
    inserted = conversations.insert_one.await_args.args[0]

    assert response.status == 201
    assert inserted["title"] is None
    assert inserted["title_edited"] is False
    assert scheduled.called
    for call in scheduled.call_args_list:
        call.args[0].close()  # discard un-run coroutines from the mock


@pytest.mark.asyncio
async def test_user_supplied_title_is_marked_edited(monkeypatch):
    conversations, scheduled = _setup(monkeypatch)

    response = await task_routes.handle_create_task(
        FakeRequest({"title": "Ship the release", "prompt": ""}))
    inserted = conversations.insert_one.await_args.args[0]

    assert response.status == 201
    assert inserted["title"] == "Ship the release"
    assert inserted["title_edited"] is True
    scheduled.assert_not_called()


@pytest.mark.asyncio
async def test_start_still_requires_prompt(monkeypatch):
    conversations, _scheduled = _setup(monkeypatch)

    response = await task_routes.handle_create_task(
        FakeRequest({"prompt": "", "start": True}))

    assert response.status == 400
    conversations.insert_one.assert_not_called()
