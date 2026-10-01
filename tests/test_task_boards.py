"""Shared task boards: access roles, board-scoped tasks, sharing and deletion."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api import task_routes

OWNER = "owner@example.com"
EDITOR = "editor@example.com"
VIEWER = "viewer@example.com"
STRANGER = "stranger@example.com"

SHARED = {
    "board_id": "deals1",
    "name": "Deals",
    "owner": OWNER,
    "members": [{"email": EDITOR, "role": "editor"}, {"email": VIEWER, "role": "viewer"}],
    "task_board": {
        "prompt": "Deals context",
        "lanes": [{"id": "lead", "name": "Lead", "order": 0}, {"id": "won", "name": "Won", "order": 1}],
        "tags": [{"id": "hot", "name": "Hot", "color": "red"}],
    },
}


class FakeRequest:
    def __init__(self, body=None, query=None, match_info=None):
        self._body = body or {}
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self):
        return self._body


def _db(task_board=SHARED, conversation=None, users_find=None):
    task_boards = SimpleNamespace(
        find_one=AsyncMock(return_value=task_board),
        insert_one=AsyncMock(),
        update_one=AsyncMock(),
        delete_one=AsyncMock(),
        count_documents=AsyncMock(return_value=0),
    )
    conversations = SimpleNamespace(
        find_one=AsyncMock(return_value=conversation),
        insert_one=AsyncMock(),
        update_one=AsyncMock(),
        update_many=AsyncMock(return_value=SimpleNamespace(modified_count=2)),
    )
    users_cursor = MagicMock()
    users_cursor.to_list = AsyncMock(return_value=users_find or [])
    users = SimpleNamespace(
        find_one=AsyncMock(return_value={"task_board": {"prompt": "Personal", "show_agent_work": True}}),
        update_one=AsyncMock(),
        find=MagicMock(return_value=users_cursor),
    )
    return SimpleNamespace(task_boards=task_boards, conversations=conversations, users=users)


def _as(monkeypatch, db, email):
    monkeypatch.setattr(task_routes, "get_db", lambda: db)
    monkeypatch.setattr(task_routes, "get_user_email", lambda _r: email)
    monkeypatch.setattr(task_routes, "get_system_role", lambda _r: "member")


@pytest.mark.asyncio
async def test_resolve_board_roles():
    db = _db()
    assert (await task_routes.resolve_board(db, OWNER, "deals1"))["role"] == "owner"
    assert (await task_routes.resolve_board(db, EDITOR, "deals1"))["role"] == "editor"
    assert (await task_routes.resolve_board(db, VIEWER, "deals1"))["role"] == "viewer"
    assert await task_routes.resolve_board(db, STRANGER, "deals1") is None
    personal = await task_routes.resolve_board(db, STRANGER, "personal")
    assert personal["task_filter"] == {"metadata.user_name": STRANGER, "task_board_id": None}


@pytest.mark.asyncio
async def test_viewer_cannot_create_tasks(monkeypatch):
    db = _db()
    _as(monkeypatch, db, VIEWER)
    response = await task_routes.handle_create_task(FakeRequest({"prompt": "x", "board": "deals1"}))
    assert response.status == 403
    db.conversations.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_editor_creates_task_on_shared_board(monkeypatch):
    db = _db()
    _as(monkeypatch, db, EDITOR)
    response = await task_routes.handle_create_task(FakeRequest({"title": "ACME", "board": "deals1"}))
    assert response.status == 201
    doc = db.conversations.insert_one.await_args.args[0]
    assert doc["task_board_id"] == "deals1"
    assert doc["task_lane"] == "lead"
    assert doc["metadata"]["user_name"] == EDITOR  # runs use the creator's identity


@pytest.mark.asyncio
async def test_stranger_cannot_list_shared_board(monkeypatch):
    db = _db()
    _as(monkeypatch, db, STRANGER)
    response = await task_routes.handle_list_tasks(FakeRequest(query={"board": "deals1"}))
    assert response.status == 404


@pytest.mark.asyncio
async def test_editor_can_annotate_but_not_change_what_runs(monkeypatch):
    task = {"conversation_id": "c1", "metadata": {"user_name": OWNER}, "task_board_id": "deals1",
            "task_status": "todo", "task_lane": "lead", "status": None}
    db = _db(conversation=task)
    monkeypatch.setattr("api.routes._check_conversation_access", lambda conv, email, role: email == OWNER)
    _as(monkeypatch, db, EDITOR)

    blocked = await task_routes.handle_update_task(
        FakeRequest({"prompt": "do something else"}, match_info={"conversation_id": "c1"}))
    assert blocked.status == 403

    allowed = await task_routes.handle_update_task(
        FakeRequest({"task_lane": "won", "task_tag_ids": ["hot"]}, match_info={"conversation_id": "c1"}))
    assert allowed.status == 200
    assert db.conversations.update_one.await_args.args[1]["$set"]["task_lane"] == "won"


@pytest.mark.asyncio
async def test_viewer_cannot_move_tasks(monkeypatch):
    task = {"conversation_id": "c1", "metadata": {"user_name": OWNER}, "task_board_id": "deals1",
            "task_status": "todo", "task_lane": "lead"}
    db = _db(conversation=task)
    monkeypatch.setattr("api.routes._check_conversation_access", lambda conv, email, role: email == OWNER)
    _as(monkeypatch, db, VIEWER)
    response = await task_routes.handle_update_task(
        FakeRequest({"task_lane": "won"}, match_info={"conversation_id": "c1"}))
    assert response.status == 403


@pytest.mark.asyncio
async def test_creator_moves_task_to_shared_board(monkeypatch):
    task = {"conversation_id": "c1", "metadata": {"user_name": EDITOR},
            "task_status": "todo", "task_lane": "todo", "task_tag_ids": ["mine"]}
    db = _db(conversation=task)
    monkeypatch.setattr("api.routes._check_conversation_access", lambda conv, email, role: email == EDITOR)
    _as(monkeypatch, db, EDITOR)
    response = await task_routes.handle_update_task(
        FakeRequest({"task_board_id": "deals1"}, match_info={"conversation_id": "c1"}))
    assert response.status == 200
    update = db.conversations.update_one.await_args.args[1]["$set"]
    assert update == {"task_board_id": "deals1", "task_lane": "lead", "task_tag_ids": []}


@pytest.mark.asyncio
async def test_only_owner_manages_members(monkeypatch):
    db = _db()
    _as(monkeypatch, db, EDITOR)
    response = await task_routes.handle_update_board(
        FakeRequest({"members": []}, match_info={"board_id": "deals1"}))
    assert response.status == 403


@pytest.mark.asyncio
async def test_members_must_be_loma_users(monkeypatch):
    db = _db(users_find=[{"email": EDITOR}])
    _as(monkeypatch, db, OWNER)
    response = await task_routes.handle_update_board(FakeRequest(
        {"members": [{"email": "Editor@Example.com", "role": "editor"},
                     {"email": "nobody@example.com", "role": "viewer"}]},
        match_info={"board_id": "deals1"}))
    assert response.status == 400
    assert "nobody@example.com" in json.loads(response.text)["error"]
    db.task_boards.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_sets_members(monkeypatch):
    db = _db(users_find=[{"email": EDITOR}])
    _as(monkeypatch, db, OWNER)
    response = await task_routes.handle_update_board(FakeRequest(
        {"name": "Deals 2026", "members": [{"email": "Editor@Example.com", "role": "editor"},
                                            {"email": OWNER, "role": "viewer"}]},
        match_info={"board_id": "deals1"}))
    assert response.status == 200
    saved = db.task_boards.update_one.await_args.args[1]["$set"]
    assert saved["members"] == [{"email": EDITOR, "role": "editor"}]  # owner is never a member
    assert saved["name"] == "Deals 2026"


@pytest.mark.asyncio
async def test_delete_board_returns_tasks_to_creators(monkeypatch):
    db = _db()
    _as(monkeypatch, db, OWNER)
    response = await task_routes.handle_delete_board(FakeRequest(match_info={"board_id": "deals1"}))
    assert response.status == 200
    query, update = db.conversations.update_many.await_args.args
    assert query == {"task_board_id": "deals1"}
    assert update["$unset"] == {"task_board_id": ""}
    db.task_boards.delete_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_board(monkeypatch):
    db = _db()
    _as(monkeypatch, db, OWNER)
    response = await task_routes.handle_create_board(FakeRequest({"name": "  Hiring "}))
    assert response.status == 201
    doc = db.task_boards.insert_one.await_args.args[0]
    assert doc["name"] == "Hiring" and doc["owner"] == OWNER and doc["members"] == []
    assert doc["task_board"]["lanes"][0]["id"] == "todo"


@pytest.mark.asyncio
async def test_shared_board_context_replaces_personal_prompt(monkeypatch):
    db = _db()
    monkeypatch.setattr(task_routes, "get_prompt_setting", lambda _key: "")
    assert "Deals context" in await task_routes.build_board_context(db, OWNER, "deals1")
    assert "Personal" in await task_routes.build_board_context(db, OWNER)


@pytest.mark.asyncio
async def test_member_can_view_task_conversation(monkeypatch):
    db = _db()
    monkeypatch.setattr("api.routes._check_conversation_access", lambda *_a: False)
    task = {"metadata": {"user_name": OWNER}, "task_board_id": "deals1", "task_status": "active"}
    assert await task_routes.task_access(db, task, VIEWER, "member") == (True, False, False)
    assert await task_routes.task_access(db, task, EDITOR, "member") == (True, True, False)
    assert await task_routes.task_access(db, task, STRANGER, "member") == (False, False, False)
