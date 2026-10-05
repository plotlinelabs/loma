"""Private stars: a shared-board task bookmarked onto the starrer's own board."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import routes, task_routes, task_stars
from agent import active_streams
from tests.test_task_boards import EDITOR, OWNER, SHARED, STRANGER, VIEWER, FakeRequest

PERSONAL_LANES = [{"id": "today", "name": "Today", "order": 0}, {"id": "later", "name": "Later", "order": 1}]


def _matches(doc, query):
    for key, cond in query.items():
        if key == "$or":
            continue
        value = doc.get(key)
        if isinstance(cond, dict):
            if "$in" in cond and value not in cond["$in"]:
                return False
            if "$nin" in cond and value in cond["$nin"]:
                return False
            if "$ne" in cond and value == cond["$ne"]:
                return False
        elif value != cond:
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, _spec):
        return self

    def limit(self, _n):
        return self

    async def to_list(self, _length):
        return self.docs


class Collection:
    """A tiny in-memory stand-in for a Mongo collection."""

    def __init__(self, docs=()):
        self.docs = [dict(doc) for doc in docs]

    def find(self, query=None, _projection=None):
        return Cursor(doc for doc in self.docs if _matches(doc, query or {}))

    async def find_one(self, query, _projection=None):
        return next((doc for doc in self.docs if _matches(doc, query)), None)

    async def count_documents(self, query):
        return sum(1 for doc in self.docs if _matches(doc, query))

    async def update_one(self, query, update, upsert=False):
        doc = next((d for d in self.docs if _matches(d, query)), None)
        if doc is None:
            if not upsert:
                return
            doc = dict(update.get("$setOnInsert") or {})
            self.docs.append(doc)
        doc.update(update.get("$set") or {})
        for key in update.get("$unset") or {}:
            doc.pop(key, None)

    async def update_many(self, query, update):
        for doc in self.docs:
            if not _matches(doc, {k: v for k, v in query.items() if not isinstance(doc.get(k), list)}):
                continue
            for key, value in (update.get("$pull") or {}).items():
                if isinstance(doc.get(key), list):
                    doc[key] = [item for item in doc[key] if item != value]

    async def delete_one(self, query):
        doc = next((d for d in self.docs if _matches(d, query)), None)
        if doc is not None:
            self.docs.remove(doc)

    async def delete_many(self, query):
        self.docs = [d for d in self.docs if not _matches(d, query)]


def _task(cid="c1", **extra):
    return {"conversation_id": cid, "title": "Send SOW", "metadata": {"user_name": OWNER},
            "task_board_id": "deals1", "task_status": "active", "status": "completed", **extra}


def _db(tasks=None, boards=None, stars=()):
    return SimpleNamespace(
        conversations=Collection(tasks if tasks is not None else [_task()]),
        task_boards=Collection(boards if boards is not None else [SHARED]),
        task_cards=Collection([{"card_id": "card1", "board_id": "deals1", "title": "ACME"}]),
        task_stars=Collection(stars),
        users=SimpleNamespace(find_one=AsyncMock(return_value={"task_board": {"lanes": PERSONAL_LANES}})),
    )


def _as(monkeypatch, db, email):
    monkeypatch.setattr(task_routes, "get_db", lambda: db)
    monkeypatch.setattr(task_routes, "get_user_email", lambda _r: email)
    monkeypatch.setattr(task_routes, "get_system_role", lambda _r: "member")
    monkeypatch.setattr("api.routes._check_conversation_access", lambda conv, who, role: who == OWNER)


def _req(method, body=None, cid="c1", query=None):
    request = FakeRequest(body, query=query, match_info={"conversation_id": cid})
    request.method = method
    return request


async def _board(email_board=None):
    response = await task_routes.handle_list_tasks(FakeRequest(query={"board": email_board} if email_board else {}))
    assert response.status == 200
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_viewer_stars_a_task_into_their_own_board(monkeypatch):
    db = _db(tasks=[_task(task_card_id="card1")])
    _as(monkeypatch, db, VIEWER)
    assert (await task_stars.handle_star_task(_req("PUT"))).status == 200
    assert (await task_stars.handle_star_task(_req("PUT"))).status == 200  # idempotent
    assert len(db.task_stars.docs) == 1

    mine = await _board()
    [card] = mine["tasks"]
    assert card["conversation_id"] == "c1" and card["starred"] is True
    assert card["column"] == "today"  # first personal lane, not the shared board's column
    assert card["star"] == {
        "lane": "today", "done": False, "role": "viewer", "board_id": "deals1", "board_name": "Deals",
        "board_emoji": card["star"]["board_emoji"], "card_title": "ACME", "source_column": "needs_input",
    }
    assert mine["counts"]["today"] == 1


@pytest.mark.asyncio
async def test_star_moves_and_done_never_touch_the_shared_task(monkeypatch):
    db = _db()
    _as(monkeypatch, db, EDITOR)
    await task_stars.handle_star_task(_req("PUT"))
    before = dict(db.conversations.docs[0])

    assert (await task_stars.handle_update_star(_req("PATCH", {"lane": "later", "rank": 3}))).status == 200
    assert (await _board())["tasks"][0]["column"] == "later"
    assert (await task_stars.handle_update_star(_req("PATCH", {"done": True}))).status == 200
    card = (await _board())["tasks"][0]
    assert card["column"] == "done" and card["star"]["done"] is True
    # The real task is unchanged: still open on the shared board.
    assert db.conversations.docs[0] == before
    assert card["task_status"] == "active" and card["star"]["source_column"] == "needs_input"
    shared = (await _board("deals1"))["tasks"][0]
    assert shared["column"] == "needs_input" and shared["starred"] is True

    assert (await task_stars.handle_update_star(_req("PATCH", {"lane": "lead"}))).status == 400  # not my lane
    assert (await task_stars.handle_update_star(_req("PATCH", {"done": "yes"}))).status == 400
    assert (await task_stars.handle_update_star(_req("PATCH", {}))).status == 400


@pytest.mark.asyncio
async def test_done_on_the_board_leaves_my_card_where_i_put_it(monkeypatch):
    db = _db()
    _as(monkeypatch, db, EDITOR)
    await task_stars.handle_star_task(_req("PUT"))
    db.conversations.docs[0].update({"task_status": "done", "task_done_at": datetime.now(timezone.utc)})
    card = (await _board())["tasks"][0]
    assert card["column"] == "today" and card["star"]["source_column"] == "done"


@pytest.mark.asyncio
async def test_stars_are_private(monkeypatch):
    db = _db()
    _as(monkeypatch, db, EDITOR)
    await task_stars.handle_star_task(_req("PUT"))

    _as(monkeypatch, db, VIEWER)
    assert (await _board("deals1"))["tasks"][0]["starred"] is False
    assert (await _board())["tasks"] == []
    # Nothing about the star is stored on the task.
    assert not any("star" in key for key in db.conversations.docs[0])
    # Another person can't move my star.
    assert (await task_stars.handle_update_star(_req("PATCH", {"done": True}))).status == 404
    assert db.task_stars.docs[0]["done"] is False


@pytest.mark.asyncio
async def test_unstar_removes_it_from_my_board(monkeypatch):
    db = _db()
    _as(monkeypatch, db, EDITOR)
    await task_stars.handle_star_task(_req("PUT"))
    assert (await task_stars.handle_unstar_task(_req("DELETE"))).status == 200
    assert db.task_stars.docs == []
    assert (await _board())["tasks"] == []
    assert (await _board("deals1"))["tasks"][0]["starred"] is False


@pytest.mark.asyncio
async def test_who_can_star(monkeypatch):
    db = _db(tasks=[_task(), _task("p1", task_board_id=None)])
    _as(monkeypatch, db, STRANGER)
    assert (await task_stars.handle_star_task(_req("PUT"))).status == 404  # not on the board
    _as(monkeypatch, db, OWNER)
    assert (await task_stars.handle_star_task(_req("PUT", cid="p1"))).status == 400  # personal task
    assert (await task_stars.handle_star_task(_req("PUT", cid="missing"))).status == 404
    assert db.task_stars.docs == []


@pytest.mark.asyncio
async def test_star_is_dropped_when_task_is_gone_or_access_is_lost(monkeypatch):
    star = {"user_email": EDITOR, "lane": "today", "done": False, "rank": 0}
    db = _db(
        tasks=[_task("gone", deleted=True), _task("unshared", task_board_id=None), _task("kept")],
        stars=[{**star, "conversation_id": cid} for cid in ("gone", "unshared", "kept", "missing")],
    )
    _as(monkeypatch, db, EDITOR)
    assert [t["conversation_id"] for t in (await _board())["tasks"]] == ["kept"]
    assert [s["conversation_id"] for s in db.task_stars.docs] == ["kept"]

    # Removed from the board: the star goes too.
    db.task_boards.docs[0]["members"] = [m for m in SHARED["members"] if m["email"] != EDITOR]
    assert (await _board())["tasks"] == []
    assert db.task_stars.docs == []


@pytest.mark.asyncio
async def test_search_does_not_drop_stars(monkeypatch):
    db = _db(tasks=[], stars=[{"user_email": EDITOR, "conversation_id": "c1", "lane": "today", "rank": 0}])
    _as(monkeypatch, db, EDITOR)
    response = await task_routes.handle_list_tasks(FakeRequest(query={"q": "nothing matches"}))
    assert json.loads(response.body)["tasks"] == []
    assert len(db.task_stars.docs) == 1


# ── Board owners and editors can stop a teammate's run ──────────────────────

def _interrupt_request(db, email, monkeypatch):
    monkeypatch.setattr(routes, "get_user_email", lambda _r: email)
    monkeypatch.setattr(routes, "get_system_role", lambda _r: "member")
    return SimpleNamespace(match_info={"conversation_id": "c1"}, app={"db": db})


@pytest.mark.asyncio
async def test_editor_can_stop_a_teammates_run_but_viewer_cannot(monkeypatch):
    db = _db(tasks=[_task(status="running")])
    client = SimpleNamespace(interrupt=AsyncMock())
    stream = await active_streams.register("c1", client, OWNER)
    try:
        for outsider in (VIEWER, STRANGER):
            response = await routes.handle_interrupt_agent(_interrupt_request(db, outsider, monkeypatch))
            assert response.status == 404
        client.interrupt.assert_not_awaited()

        response = await routes.handle_interrupt_agent(_interrupt_request(db, EDITOR, monkeypatch))
        assert response.status == 200
        client.interrupt.assert_awaited_once()
        assert stream.stopped
    finally:
        await active_streams.unregister("c1", stream)


@pytest.mark.asyncio
async def test_editor_cannot_stop_a_teammates_personal_run(monkeypatch):
    db = _db(tasks=[_task(status="running", task_board_id=None)])
    client = SimpleNamespace(interrupt=AsyncMock())
    stream = await active_streams.register("c1", client, OWNER)
    try:
        response = await routes.handle_interrupt_agent(_interrupt_request(db, EDITOR, monkeypatch))
        assert response.status == 404
        client.interrupt.assert_not_awaited()
    finally:
        await active_streams.unregister("c1", stream)


MY_TAGS = [{"id": "t-mine", "name": "Follow up", "color": "blue"},
           {"id": "t-two", "name": "Waiting", "color": "red"}]


def _db_with_my_tags(**kwargs):
    db = _db(**kwargs)
    db.users = SimpleNamespace(
        find_one=AsyncMock(return_value={"task_board": {"lanes": PERSONAL_LANES, "tags": list(MY_TAGS)}}),
        update_one=AsyncMock(),
    )
    return db


@pytest.mark.asyncio
async def test_my_tags_priority_and_deadline_start_blank_and_stay_mine(monkeypatch):
    shared_values = {"task_priority": "urgent", "task_deadline": "2026-10-10", "task_tag_ids": ["t-shared"]}
    db = _db_with_my_tags(tasks=[_task(**shared_values)])
    _as(monkeypatch, db, EDITOR)
    await task_stars.handle_star_task(_req("PUT"))

    # Starts blank, not copied from the shared task.
    card = (await _board())["tasks"][0]
    assert (card["task_tag_ids"], card["task_priority"], card["task_deadline"]) == ([], None, None)

    body = {"tag_ids": ["t-mine"], "priority": "high", "deadline": "2026-10-20"}
    response = await task_stars.handle_update_star(_req("PATCH", body))
    assert response.status == 200
    assert json.loads(response.body)["star"]["priority"] == "high"
    card = (await _board())["tasks"][0]
    assert (card["task_tag_ids"], card["task_priority"], card["task_deadline"]) == (["t-mine"], "high", "2026-10-20")

    # The shared task keeps its own values, and the shared board shows those.
    for key, value in shared_values.items():
        assert db.conversations.docs[0][key] == value
    shared = (await _board("deals1"))["tasks"][0]
    assert (shared["task_priority"], shared["task_deadline"]) == ("urgent", "2026-10-10")

    # Later changes on the shared task don't sync into my copy.
    db.conversations.docs[0].update({"task_priority": "low", "task_deadline": "2026-12-01"})
    card = (await _board())["tasks"][0]
    assert (card["task_priority"], card["task_deadline"]) == ("high", "2026-10-20")

    # Clearing works too.
    body = {"tag_ids": [], "priority": None, "deadline": None}
    assert (await task_stars.handle_update_star(_req("PATCH", body))).status == 200
    card = (await _board())["tasks"][0]
    assert (card["task_tag_ids"], card["task_priority"], card["task_deadline"]) == ([], None, None)


@pytest.mark.asyncio
async def test_bad_star_tags_priority_or_deadline_are_rejected(monkeypatch):
    db = _db_with_my_tags()
    _as(monkeypatch, db, VIEWER)
    await task_stars.handle_star_task(_req("PUT"))
    for body in (
        {"tag_ids": ["t-shared"]},           # the shared board's tag, not mine
        {"tag_ids": ["t-mine", "t-mine"]},   # duplicate
        {"tag_ids": "t-mine"},               # not a list
        {"priority": "asap"},
        {"deadline": "20/10/2026"},
        {"deadline": "2026-02-30"},
    ):
        response = await task_stars.handle_update_star(_req("PATCH", body))
        assert response.status == 400, body
    assert db.task_stars.docs[0]["tag_ids"] == [] and db.task_stars.docs[0]["priority"] is None
    # A viewer of the shared board can still label their own copy.
    assert (await task_stars.handle_update_star(_req("PATCH", {"tag_ids": ["t-two"]}))).status == 200


@pytest.mark.asyncio
async def test_deleting_my_tag_drops_it_from_my_stars(monkeypatch):
    db = _db_with_my_tags()
    _as(monkeypatch, db, EDITOR)
    await task_stars.handle_star_task(_req("PUT"))
    await task_stars.handle_update_star(_req("PATCH", {"tag_ids": ["t-mine", "t-two"]}))

    request = FakeRequest(match_info={"tag_id": "t-mine"})
    response = await task_routes.handle_delete_tag(request)
    assert response.status == 200
    assert db.task_stars.docs[0]["tag_ids"] == ["t-two"]

    # A tag missing from my board never shows on the card, even if a star still lists it.
    db.task_stars.docs[0]["tag_ids"] = ["t-gone", "t-two"]
    assert (await _board())["tasks"][0]["task_tag_ids"] == ["t-two"]
