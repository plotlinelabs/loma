"""Saved views on card boards: validation, visibility and who can change them."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api import task_routes, task_views

OWNER = "owner@example.com"
EDITOR = "editor@example.com"
VIEWER = "viewer@example.com"
STRANGER = "stranger@example.com"

CARD_BOARD = {
    "board_id": "pilots1",
    "name": "Pilots",
    "owner": OWNER,
    "card_mode": True,
    "members": [{"email": EDITOR, "role": "editor"}, {"email": VIEWER, "role": "viewer"}],
    "task_board": {"lanes": [{"id": "disc", "name": "Discovery", "order": 0}], "fields": []},
}


class FakeRequest:
    def __init__(self, body=None, query=None, match_info=None):
        self._body = body if body is not None else {}
        self.query = {"board": "pilots1", **(query or {})}
        self.match_info = match_info or {}

    async def json(self):
        return self._body


def _db(board=CARD_BOARD, view=None, owned=0, listed=None):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=listed or [])
    views = SimpleNamespace(
        find=MagicMock(return_value=cursor),
        find_one=AsyncMock(return_value=view),
        insert_one=AsyncMock(),
        update_one=AsyncMock(),
        delete_one=AsyncMock(),
        count_documents=AsyncMock(return_value=owned),
    )
    return SimpleNamespace(task_boards=SimpleNamespace(find_one=AsyncMock(return_value=board)),
                           task_board_views=views)


def _as(monkeypatch, db, email):
    monkeypatch.setattr(task_routes, "get_db", lambda: db)
    monkeypatch.setattr(task_routes, "get_user_email", lambda _r: email)


def _body(response):
    return json.loads(response.body)


VIEW_BODY = {
    "name": " Big deals ",
    "match": "all",
    "filters": [
        {"id": "f1", "field": "value", "op": "gt", "value": 50000},
        {"field": "product", "op": "any_of", "value": ["Nudges", "Stories"]},
    ],
    "search": "bank",
    "assigned_to_me": True,
}


@pytest.mark.asyncio
async def test_create_personal_view(monkeypatch):
    db = _db()
    _as(monkeypatch, db, VIEWER)  # view-only members can still save personal views
    response = await task_views.handle_create_view(FakeRequest(VIEW_BODY))
    assert response.status == 201
    doc = db.task_board_views.insert_one.await_args.args[0]
    assert doc["name"] == "Big deals"
    assert doc["board_id"] == "pilots1" and doc["owner"] == VIEWER
    assert doc["shared"] is False and doc["assigned_to_me"] is True
    assert doc["filters"][0] == {"id": "f1", "field": "value", "op": "gt", "value": 50000}
    assert doc["filters"][1]["id"]  # minted when missing
    view = _body(response)["view"]
    assert view["mine"] and view["can_edit"] and view["search"] == "bank"


@pytest.mark.asyncio
async def test_viewer_cannot_share_a_view(monkeypatch):
    db = _db()
    _as(monkeypatch, db, VIEWER)
    response = await task_views.handle_create_view(FakeRequest({**VIEW_BODY, "shared": True}))
    assert response.status == 403
    db.task_board_views.insert_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"name": ""},
    {"name": "x" * 61},
    {"name": "ok", "filters": [{"field": "value", "op": "drop table", "value": 1}]},
    {"name": "ok", "filters": [{"op": "gt", "value": 1}]},
    {"name": "ok", "filters": [{"field": "v", "op": "gt", "value": {"$ne": 1}}]},
    {"name": "ok", "filters": [{"field": "v", "op": "eq", "value": 1}] * 21},
    {"name": "ok", "match": "some"},
    {"name": "ok", "shared": "yes"},
])
async def test_create_view_validation(monkeypatch, body):
    db = _db()
    _as(monkeypatch, db, OWNER)
    response = await task_views.handle_create_view(FakeRequest(body))
    assert response.status == 400


@pytest.mark.asyncio
async def test_view_limit_per_board(monkeypatch):
    db = _db(owned=task_views.MAX_VIEWS_PER_BOARD)
    _as(monkeypatch, db, OWNER)
    response = await task_views.handle_create_view(FakeRequest(VIEW_BODY))
    assert response.status == 400


@pytest.mark.asyncio
async def test_non_members_and_task_boards_have_no_views(monkeypatch):
    db = _db()
    _as(monkeypatch, db, STRANGER)
    assert (await task_views.handle_list_views(FakeRequest())).status == 404
    db = _db(board={**CARD_BOARD, "card_mode": False})
    _as(monkeypatch, db, OWNER)
    assert (await task_views.handle_list_views(FakeRequest())).status == 400


@pytest.mark.asyncio
async def test_list_shows_own_and_shared_views(monkeypatch):
    listed = [
        {"view_id": "v1", "board_id": "pilots1", "owner": EDITOR, "name": "Mine", "shared": False},
        {"view_id": "v2", "board_id": "pilots1", "owner": OWNER, "name": "Team", "shared": True},
    ]
    db = _db(listed=listed)
    _as(monkeypatch, db, EDITOR)
    response = await task_views.handle_list_views(FakeRequest())
    query = db.task_board_views.find.call_args.args[0]
    assert query == {"board_id": "pilots1", "$or": [{"owner": EDITOR}, {"shared": True}]}
    views = _body(response)["views"]
    assert [(v["id"], v["mine"], v["can_edit"]) for v in views] == [("v1", True, True), ("v2", False, False)]


@pytest.mark.asyncio
async def test_update_view(monkeypatch):
    view = {"view_id": "v1", "board_id": "pilots1", "owner": EDITOR, "name": "Old", "shared": False}
    db = _db(view=view)
    _as(monkeypatch, db, EDITOR)
    response = await task_views.handle_update_view(FakeRequest(
        {"name": "New", "filters": [], "shared": True}, match_info={"view_id": "v1"}))
    assert response.status == 200
    updates = db.task_board_views.update_one.await_args.args[1]["$set"]
    assert updates["name"] == "New" and updates["filters"] == [] and updates["shared"] is True
    assert "search" not in updates  # partial update
    assert _body(response)["view"]["shared"] is True


@pytest.mark.asyncio
async def test_someone_elses_views(monkeypatch):
    personal = {"view_id": "v1", "board_id": "pilots1", "owner": EDITOR, "name": "P", "shared": False}
    shared = {**personal, "shared": True}
    # Another member's personal view is invisible, even to a board owner.
    db = _db(view=personal)
    _as(monkeypatch, db, OWNER)
    response = await task_views.handle_delete_view(FakeRequest(match_info={"view_id": "v1"}))
    assert response.status == 404
    # Shared views: other members can't change them...
    db = _db(view=shared)
    _as(monkeypatch, db, VIEWER)
    response = await task_views.handle_update_view(FakeRequest({"name": "x"}, match_info={"view_id": "v1"}))
    assert response.status == 403
    # ...but board owners can.
    db = _db(view=shared)
    _as(monkeypatch, db, OWNER)
    response = await task_views.handle_delete_view(FakeRequest(match_info={"view_id": "v1"}))
    assert response.status == 200
    db.task_board_views.delete_one.assert_awaited_once_with({"view_id": "v1"})
