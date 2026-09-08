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


# ── Board context (global default + personal) ──────────────────────────────

def test_render_board_default_context_fills_placeholders():
    doc = {"email": "adarsh@example.com", "name": "Adarsh"}
    out = task_routes.render_board_default_context(
        "Assistant for {{user_name}}. Use --user-email {{ user_email }}.", doc, "adarsh@example.com")
    assert out == "Assistant for Adarsh. Use --user-email adarsh@example.com."


def test_render_board_default_context_falls_back_to_email_local_part():
    out = task_routes.render_board_default_context(
        "Hi {{user_name}} <{{user_email}}>", None, "jane.doe@example.com")
    assert out == "Hi jane.doe <jane.doe@example.com>"


def test_merge_board_context_default_then_personal():
    out = task_routes.merge_board_context("DEFAULT", "PERSONAL")
    assert out.startswith(task_routes.BOARD_CONTEXT_HEADING)
    assert out.index("DEFAULT") < out.index(task_routes.BOARD_PERSONAL_HEADING) < out.index("PERSONAL")


def test_merge_board_context_personal_only_keeps_legacy_shape():
    assert task_routes.merge_board_context("", " PERSONAL ") == (
        f"{task_routes.BOARD_CONTEXT_HEADING}\nPERSONAL")
    assert task_routes.merge_board_context("", "") == ""


@pytest.mark.asyncio
async def test_build_board_context_uses_default_setting_and_owner_doc(monkeypatch):
    from agent.prompt import set_prompt_settings_cache

    set_prompt_settings_cache({
        "task_board_default_context": "Global rules for {{user_name}} ({{user_email}}).",
    })
    users = SimpleNamespace(find_one=AsyncMock(return_value={
        "email": "owner@example.com", "name": "Owner",
        "task_board": {"prompt": "My personal notes"},
    }))
    try:
        out = await task_routes.build_board_context(SimpleNamespace(users=users), "owner@example.com")
    finally:
        set_prompt_settings_cache({})

    assert out == (
        f"{task_routes.BOARD_CONTEXT_HEADING}\n"
        "Global rules for Owner (owner@example.com).\n"
        f"\n{task_routes.BOARD_PERSONAL_HEADING}\n"
        "My personal notes"
    )
    users.find_one.assert_awaited_once_with(
        {"email": "owner@example.com"}, {"task_board": 1, "name": 1, "email": 1})
