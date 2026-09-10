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
    conversations = SimpleNamespace(insert_one=AsyncMock(), update_one=AsyncMock())
    users = SimpleNamespace(find_one=AsyncMock(return_value={
        "task_board": {
            "lanes": [{"id": "todo", "name": "Todo", "order": 0},
                      {"id": "ideas", "name": "Ideas", "order": 1}],
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
async def test_scratch_task_keeps_input_name_without_running(monkeypatch):
    conversations, scheduled = _setup(monkeypatch)

    response = await task_routes.handle_create_task(
        FakeRequest({"prompt": "Investigate the flaky deploy"}))
    inserted = conversations.insert_one.await_args.args[0]

    assert response.status == 201
    assert inserted["title"] == "Investigate the flaky deploy"
    assert inserted["title_edited"] is False
    assert inserted["started_at"] is None
    assert inserted["messages"] == []
    scheduled.assert_not_called()


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


@pytest.mark.asyncio
async def test_scratch_respects_lane_and_preserves_details_and_files(monkeypatch):
    conversations, scheduled = _setup(monkeypatch)
    prompt = "Check loading times " * 20
    files = [{"name": "notes.txt", "data": "dGVzdA==", "type": "text/plain"}]
    response = await task_routes.handle_create_task(FakeRequest({
        "prompt": prompt, "lane": "ideas", "start": False, "files": files,
        "tool_config": {"enabled_tools": []},
    }))
    doc = conversations.insert_one.await_args.args[0]
    assert response.status == 201
    assert doc["task_lane"] == "ideas"
    assert doc["task_status"] == "todo"
    assert doc["prompt"] == prompt.strip()
    assert len(doc["title"]) <= 80
    assert doc["draft_files"] == files
    assert doc["tool_config"] == {"enabled_tools": []}
    scheduled.assert_not_called()


@pytest.mark.asyncio
async def test_add_and_start_has_immediate_name_and_schedules_both_jobs(monkeypatch):
    conversations, scheduled = _setup(monkeypatch)
    response = await task_routes.handle_create_task(FakeRequest({
        "prompt": "Investigate\n  slow dashboard", "lane": "ideas", "start": True,
    }))
    doc = conversations.insert_one.await_args.args[0]
    assert response.status == 201
    assert doc["title"] == "Investigate slow dashboard"
    assert doc["title_edited"] is False
    assert doc["started_at"] is not None
    assert doc["task_status"] == "active"
    assert doc["task_lane"] == "ideas"
    assert scheduled.call_count == 2
    jobs = [call.args[0] for call in scheduled.call_args_list]
    assert {job.cr_code.co_name for job in jobs} == {"_auto_title_task", "_run_task_headless"}
    for job in jobs:
        job.close()


@pytest.mark.asyncio
async def test_unknown_lane_does_not_create_task(monkeypatch):
    conversations, scheduled = _setup(monkeypatch)
    response = await task_routes.handle_create_task(FakeRequest({"prompt": "Test", "lane": "missing"}))
    assert response.status == 400
    conversations.insert_one.assert_not_called()
    scheduled.assert_not_called()


@pytest.mark.asyncio
async def test_auto_naming_is_atomic_and_protects_manual_and_enriched_titles(monkeypatch):
    from api import routes
    generate = AsyncMock(return_value="Investigate slowness")
    monkeypatch.setattr(routes, "_generate_title_llm", generate)
    conversations = SimpleNamespace(update_one=AsyncMock())
    await task_routes._auto_title_task(SimpleNamespace(conversations=conversations), "test-id", "Check slow dashboard")
    conversations.update_one.assert_awaited_once_with(
        {"conversation_id": "test-id", "title_edited": {"$ne": True},
         "title": {"$in": [None, "Check slow dashboard"]}},
        {"$set": {"title": "Investigate slowness"}},
    )


@pytest.mark.asyncio
async def test_failed_naming_leaves_fallback_untouched(monkeypatch):
    from api import routes
    monkeypatch.setattr(routes, "_generate_title_llm", AsyncMock(side_effect=RuntimeError("offline")))
    conversations = SimpleNamespace(update_one=AsyncMock())
    await task_routes._auto_title_task(SimpleNamespace(conversations=conversations), "test-id", "Keep this name")
    conversations.update_one.assert_not_called()
