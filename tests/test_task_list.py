from types import SimpleNamespace
from unittest.mock import AsyncMock

import json

import pytest

from api import task_routes


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self.sort_spec = None
        self.limit_n = None

    def sort(self, spec):
        self.sort_spec = spec
        key_field = spec[0][0]
        self._docs.sort(key=lambda d: d.get(key_field) or "", reverse=spec[0][1] == -1)
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    async def to_list(self, length):
        docs = self._docs
        if self.limit_n is not None:
            docs = docs[: self.limit_n]
        if length is not None:
            docs = docs[:length]
        return docs


class FakeConversations:
    def __init__(self, docs):
        self.docs = docs
        self.cursors = []

    def find(self, query, _projection):
        status = query["task_status"]
        allowed = set(status["$in"]) if isinstance(status, dict) else {status}
        cursor = FakeCursor(d for d in self.docs if d["task_status"] in allowed)
        self.cursors.append((query, cursor))
        return cursor


def _doc(i, status, done_at=None):
    return {
        "conversation_id": f"{status}-{i}",
        "title": f"{status} {i}",
        "status": "completed",
        "task_status": status,
        "task_lane": "todo",
        "task_created_at": f"2026-01-01T00:00:{i % 60:02d}+00:00",
        "task_done_at": done_at,
    }


class FakeRequest:
    def __init__(self, q="", headers=None, **query):
        self.query = {"q": q, **query} if q else dict(query)
        self.headers = headers or {}


def _patch(monkeypatch, docs):
    conversations = FakeConversations(docs)
    users = SimpleNamespace(find_one=AsyncMock(return_value={
        "task_board": {"lanes": [{"id": "todo", "name": "Todo", "order": 0}], "tags": []},
    }))
    stars = SimpleNamespace(find=lambda *_a, **_k: FakeCursor([]))
    monkeypatch.setattr(task_routes, "get_db", lambda: SimpleNamespace(
        conversations=conversations, users=users, task_stars=stars,
    ))
    monkeypatch.setattr(task_routes, "get_user_email", lambda _r: "owner@example.com")
    return conversations


@pytest.mark.asyncio
async def test_active_tasks_not_dropped_when_done_exceeds_cap(monkeypatch):
    limit = task_routes.DONE_TASKS_PAGE
    done = [_doc(i, "done", f"2026-01-{1 + i % 28:02d}T{i % 24:02d}:00:00+00:00") for i in range(limit + 50)]
    live = [_doc(i, "todo") for i in range(3)] + [_doc(i, "active") for i in range(2)]
    # Done docs first, mimicking natural (oldest-first) order that used to fill the cap.
    conversations = _patch(monkeypatch, done + live)

    import json
    response = await task_routes.handle_list_tasks(FakeRequest())
    body = json.loads(response.body)
    ids = {t["conversation_id"] for t in body["tasks"]}

    for doc in live:
        assert doc["conversation_id"] in ids
    assert body["counts"]["done"] == limit
    assert body["done_has_more"] is True
    done_cursor = next(c for q, c in conversations.cursors if q["task_status"] == "done")
    assert done_cursor.sort_spec[0] == ("task_done_at", -1)
    assert done_cursor.limit_n == limit + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("raw, expected", [
    (None, task_routes.DONE_TASKS_PAGE),
    ("120", 120),
    ("0", 1),
    ("99999", task_routes.DONE_TASKS_LIMIT),
    ("abc", task_routes.DONE_TASKS_PAGE),
])
async def test_done_limit_param(monkeypatch, raw, expected):
    done = [_doc(i, "done", f"2026-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}+00:00") for i in range(600)]
    _patch(monkeypatch, done)
    query = {"done_limit": raw} if raw is not None else {}
    body = json.loads((await task_routes.handle_list_tasks(FakeRequest(**query))).body)
    assert body["counts"]["done"] == expected
    assert body["done_has_more"] is (expected < 600)


@pytest.mark.asyncio
async def test_done_has_more_false_when_all_loaded(monkeypatch):
    _patch(monkeypatch, [_doc(i, "done", f"2026-01-0{1 + i}") for i in range(3)])
    body = json.loads((await task_routes.handle_list_tasks(FakeRequest())).body)
    assert body["counts"]["done"] == 3
    assert body["done_has_more"] is False


@pytest.mark.asyncio
async def test_unchanged_board_returns_304(monkeypatch):
    _patch(monkeypatch, [_doc(1, "todo")])
    first = await task_routes.handle_list_tasks(FakeRequest())
    etag = first.headers["ETag"]
    assert first.status == 200 and etag
    again = await task_routes.handle_list_tasks(FakeRequest(headers={"If-None-Match": etag}))
    assert again.status == 304 and again.body is None
    stale = await task_routes.handle_list_tasks(FakeRequest(headers={"If-None-Match": '"old"'}))
    assert stale.status == 200


def test_projection_trims_prompt_in_mongo():
    assert task_routes._TASK_PROJECTION["prompt"] == {
        "$substrCP": [{"$ifNull": ["$prompt", ""]}, 0, 200]}


@pytest.mark.asyncio
async def test_search_filter_applies_to_both_queries(monkeypatch):
    conversations = _patch(monkeypatch, [_doc(1, "todo"), _doc(2, "done", "2026-01-02")])
    await task_routes.handle_list_tasks(FakeRequest(q="Sim"))
    assert len(conversations.cursors) == 2
    for query, _ in conversations.cursors:
        assert "$or" in query
        assert query["metadata.user_name"] == "owner@example.com"
