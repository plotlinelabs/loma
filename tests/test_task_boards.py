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
    method = "POST"

    def __init__(self, body=None, query=None, match_info=None):
        self._body = body or {}
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self):
        return self._body


def _db(task_board=SHARED, conversation=None, users_find=None, card=None):
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
    cards_cursor = MagicMock()
    cards_cursor.to_list = AsyncMock(return_value=[card] if card else [])
    task_cards = SimpleNamespace(
        find_one=AsyncMock(return_value=card),
        find=MagicMock(return_value=cards_cursor),
        insert_one=AsyncMock(),
        update_one=AsyncMock(),
        update_many=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
        delete_one=AsyncMock(),
        delete_many=AsyncMock(),
        count_documents=AsyncMock(return_value=0),
    )
    task_board_views = SimpleNamespace(delete_many=AsyncMock())
    return SimpleNamespace(task_boards=task_boards, conversations=conversations, users=users,
                           task_cards=task_cards, task_board_views=task_board_views)


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
    assert update == {"task_board_id": "deals1", "task_lane": "lead", "task_tag_ids": [],
                      "task_assignee": None}


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
    assert update["$unset"] == {"task_board_id": "", "task_card_id": ""}
    db.task_cards.delete_many.assert_awaited_once_with({"board_id": "deals1"})
    db.task_board_views.delete_many.assert_awaited_once_with({"board_id": "deals1"})
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


# ── Card boards: custom fields, cards, tasks inside cards ────────────────────

FIELDS = [
    {"id": "val", "name": "Value", "type": "number", "options": [], "show_on_card": True},
    {"id": "reg", "name": "Region", "type": "select", "options": ["India", "GCC"], "show_on_card": False},
    {"id": "due", "name": "Close date", "type": "date", "options": [], "show_on_card": True},
    {"id": "url", "name": "Link", "type": "link", "options": [], "show_on_card": False},
]
CARD_BOARD = {**SHARED, "card_mode": True,
              "task_board": {**SHARED["task_board"], "fields": FIELDS}}
CARD = {"card_id": "card1", "board_id": "deals1", "title": "IndiGo", "lane": "lead", "rank": 1.0,
        "fields": {"val": 150000, "reg": "GCC"}, "notes": "Needs GCC hosting."}


@pytest.mark.asyncio
async def test_create_card_board_from_template(monkeypatch):
    db = _db()
    _as(monkeypatch, db, OWNER)
    response = await task_routes.handle_create_board(
        FakeRequest({"name": "Deals", "card_mode": True, "template": "deals"}))
    assert response.status == 201
    doc = db.task_boards.insert_one.await_args.args[0]
    assert doc["card_mode"] is True
    assert [lane["name"] for lane in doc["task_board"]["lanes"]][:2] == ["Prospecting", "Demo"]
    fields = doc["task_board"]["fields"]
    assert fields[0]["name"] == "Value" and fields[0]["type"] == "number" and fields[0]["id"]
    assert json.loads(response.text)["board"]["card_mode"] is True

    unknown = await task_routes.handle_create_board(
        FakeRequest({"name": "X", "card_mode": True, "template": "nope"}))
    assert unknown.status == 400


def test_field_definitions_are_validated():
    fields, error = task_routes._clean_fields([
        {"name": " Stage ", "type": "select", "options": ["A", "A", " B "]},
        {"id": "keep", "name": "Amount", "type": "number", "show_on_card": True},
    ])
    assert error is None
    assert fields[0]["name"] == "Stage" and fields[0]["options"] == ["A", "B"] and fields[0]["id"]
    assert fields[1] == {"id": "keep", "name": "Amount", "type": "number", "options": [], "show_on_card": True}
    assert task_routes._clean_fields([{"name": "X", "type": "rocket"}])[1]
    assert task_routes._clean_fields([{"name": "X", "type": "select", "options": []}])[1]
    assert task_routes._clean_fields([{"name": "a", "type": "text"}, {"name": "A", "type": "text"}])[1]
    assert task_routes._clean_fields([{"id": "a.b", "name": "X", "type": "text"}])[1]


@pytest.mark.parametrize("field_id,value,ok", [
    ("val", 12.5, True), ("val", "12", False), ("val", True, False),
    ("reg", "GCC", True), ("reg", "Mars", False),
    ("due", "2026-10-31", True), ("due", "2026-13-01", False),
    ("url", "https://example.com/x", True), ("url", "javascript:alert(1)", False),
])
def test_field_values_are_validated(field_id, value, ok):
    field = next(f for f in FIELDS if f["id"] == field_id)
    cleaned, error = task_routes._clean_field_value(field, value)
    assert (error is None) == ok
    if ok:
        assert cleaned == value


@pytest.mark.asyncio
async def test_editor_creates_and_updates_card(monkeypatch):
    db = _db(task_board=CARD_BOARD, card=CARD)
    _as(monkeypatch, db, EDITOR)
    created = await task_routes.handle_create_card(
        FakeRequest({"board": "deals1", "title": " Kenz'up ", "lane": "won", "fields": {"val": 42000}}))
    assert created.status == 201
    doc = db.task_cards.insert_one.await_args.args[0]
    assert doc["title"] == "Kenz'up" and doc["lane"] == "won" and doc["fields"] == {"val": 42000}
    assert doc["created_by"] == EDITOR

    request = FakeRequest({"lane": "won", "fields": {"reg": None, "due": "2026-10-31"}, "notes": "n"},
                          match_info={"card_id": "card1"})
    request.method = "PATCH"
    updated = await task_routes.handle_update_card(request)
    assert updated.status == 200
    saved = db.task_cards.update_one.await_args.args[1]["$set"]
    # Partial update: untouched values stay, null clears.
    assert saved["fields"] == {"val": 150000, "due": "2026-10-31"}
    assert saved["lane"] == "won" and saved["notes"] == "n"

    bad = FakeRequest({"fields": {"val": "lots"}}, match_info={"card_id": "card1"})
    bad.method = "PATCH"
    assert (await task_routes.handle_update_card(bad)).status == 400


@pytest.mark.asyncio
async def test_viewer_cannot_change_cards(monkeypatch):
    db = _db(task_board=CARD_BOARD, card=CARD)
    _as(monkeypatch, db, VIEWER)
    assert (await task_routes.handle_create_card(
        FakeRequest({"board": "deals1", "title": "X"}))).status == 403
    request = FakeRequest(match_info={"card_id": "card1"})
    request.method = "DELETE"
    assert (await task_routes.handle_delete_card(request)).status == 403
    db.task_cards.delete_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_cards_only_exist_on_card_boards(monkeypatch):
    db = _db()  # plain shared board
    _as(monkeypatch, db, OWNER)
    assert (await task_routes.handle_create_card(
        FakeRequest({"board": "deals1", "title": "X"}))).status == 404
    assert (await task_routes.handle_create_task(
        FakeRequest({"title": "t", "board": "deals1", "card": "card1"}))).status == 400


@pytest.mark.asyncio
async def test_tasks_on_card_board_need_a_card(monkeypatch):
    db = _db(task_board=CARD_BOARD, card=CARD)
    _as(monkeypatch, db, EDITOR)
    missing = await task_routes.handle_create_task(FakeRequest({"title": "t", "board": "deals1"}))
    assert missing.status == 400
    created = await task_routes.handle_create_task(
        FakeRequest({"title": "Send proposal", "board": "deals1", "card": "card1"}))
    assert created.status == 201
    doc = db.conversations.insert_one.await_args.args[0]
    assert doc["task_card_id"] == "card1" and doc["task_board_id"] == "deals1"


@pytest.mark.asyncio
async def test_card_task_can_be_ticked_off_without_running(monkeypatch):
    task = {"conversation_id": "c1", "metadata": {"user_name": OWNER}, "task_board_id": "deals1",
            "task_card_id": "card1", "task_status": "todo", "task_lane": "lead", "status": None}
    db = _db(task_board=CARD_BOARD, conversation=task, card=CARD)
    monkeypatch.setattr("api.routes._check_conversation_access", lambda conv, email, role: email == OWNER)
    _as(monkeypatch, db, EDITOR)
    response = await task_routes.handle_update_task(
        FakeRequest({"task_status": "done"}, match_info={"conversation_id": "c1"}))
    assert response.status == 200

    # Outside a card the old rule still holds.
    db = _db(conversation={**task, "task_card_id": None})
    _as(monkeypatch, db, EDITOR)
    response = await task_routes.handle_update_task(
        FakeRequest({"task_status": "done"}, match_info={"conversation_id": "c1"}))
    assert response.status == 400


@pytest.mark.asyncio
async def test_card_fields_and_notes_reach_the_agent_context(monkeypatch):
    db = _db(task_board=CARD_BOARD, card=CARD)
    monkeypatch.setattr(task_routes, "get_prompt_setting", lambda _key: "")
    context = await task_routes.build_board_context(db, OWNER, "deals1", "card1")
    assert "Deals context" in context
    assert "### Card: IndiGo" in context and 'in the "Lead" column' in context
    assert "- Value: 150000" in context and "- Region: GCC" in context
    assert "Needs GCC hosting." in context
    assert "Close date" not in context  # empty fields are left out


@pytest.mark.asyncio
async def test_delete_card_returns_tasks_to_creators(monkeypatch):
    db = _db(task_board=CARD_BOARD, card=CARD)
    _as(monkeypatch, db, OWNER)
    request = FakeRequest(match_info={"card_id": "card1"})
    request.method = "DELETE"
    response = await task_routes.handle_delete_card(request)
    assert response.status == 200
    query, update = db.conversations.update_many.await_args.args
    assert query == {"task_card_id": "card1"}
    assert update["$unset"] == {"task_board_id": "", "task_card_id": ""}
    db.task_cards.delete_one.assert_awaited_once_with({"card_id": "card1"})


@pytest.mark.asyncio
async def test_removing_a_field_clears_its_values(monkeypatch):
    db = _db(task_board=CARD_BOARD, card=CARD)
    _as(monkeypatch, db, EDITOR)
    lanes = CARD_BOARD["task_board"]["lanes"]
    response = await task_routes.handle_put_board_settings(FakeRequest(
        {"prompt": "p", "lanes": lanes, "fields": [f for f in FIELDS if f["id"] != "reg"]},
        query={"board": "deals1"}))
    assert response.status == 200
    saved = db.task_boards.update_one.await_args.args[1]["$set"]["task_board.fields"]
    assert [f["id"] for f in saved] == ["val", "due", "url"]
    assert db.task_cards.update_many.await_args.args[1] == {"$unset": {"fields.reg": ""}}


@pytest.mark.asyncio
async def test_list_boards_counts_tasks_waiting_on_caller(monkeypatch):
    db = _db()
    boards_cursor = MagicMock()
    boards_cursor.sort.return_value.to_list = AsyncMock(return_value=[SHARED])
    db.task_boards.find = MagicMock(return_value=boards_cursor)
    waiting_cursor = MagicMock()
    waiting_cursor.to_list = AsyncMock(return_value=[
        {"_id": "personal", "count": 1}, {"_id": "deals1", "count": 3}, {"_id": "gone", "count": 5},
    ])
    db.conversations.aggregate = MagicMock(return_value=waiting_cursor)
    _as(monkeypatch, db, EDITOR)

    response = await task_routes.handle_list_boards(FakeRequest())

    boards = json.loads(response.body)["boards"]
    assert [(b["id"], b["needs_you"]) for b in boards] == [("personal", 1), ("deals1", 3)]
    match = db.conversations.aggregate.call_args.args[0][0]["$match"]
    assert match["$or"] == [{"metadata.user_name": EDITOR}, {"task_assignee": EDITOR}]
    assert match["task_status"] == "active"
