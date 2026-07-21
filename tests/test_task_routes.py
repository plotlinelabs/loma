import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.task_routes import handle_create_task


class FakeRequest(dict):
    """Minimal aiohttp-request stand-in: dict-based auth + a JSON body."""

    def __init__(self, *, user_email="owner@example.com", body=None):
        super().__init__(user_email=user_email, system_role="chatter")
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.body)


def _db():
    """A DB whose user has the default board (single 'todo' lane)."""
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value=None)  # -> DEFAULT_BOARD
    db.conversations.insert_one = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_create_task_persists_valid_priority():
    db = _db()
    with patch("api.task_routes.get_db", return_value=db):
        res = await handle_create_task(FakeRequest(
            body={"title": "Ship it", "prompt": "do the thing", "task_priority": "high"},
        ))

    assert res.status == 201
    # Stored on the draft doc...
    stored = db.conversations.insert_one.await_args.args[0]
    assert stored["task_priority"] == "high"
    # ...and reflected in the returned card view.
    assert response_json(res)["task"]["task_priority"] == "high"


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_priority():
    db = _db()
    with patch("api.task_routes.get_db", return_value=db):
        res = await handle_create_task(FakeRequest(
            body={"title": "Bad", "prompt": "x", "task_priority": "sometime"},
        ))

    assert res.status == 400
    assert "task_priority" in response_json(res)["error"]
    db.conversations.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_task_defaults_priority_to_none():
    db = _db()
    with patch("api.task_routes.get_db", return_value=db):
        res = await handle_create_task(FakeRequest(
            body={"title": "No priority", "prompt": "x"},
        ))

    assert res.status == 201
    stored = db.conversations.insert_one.await_args.args[0]
    assert stored["task_priority"] is None
    assert response_json(res)["task"]["task_priority"] is None
