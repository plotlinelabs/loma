import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.task_routes import handle_create_task


BOARD = {"lanes": [{"id": "todo", "name": "To do"}], "tags": []}


class FakeRequest:
    """Minimal aiohttp request stand-in exposing an async json() body."""

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.body)


@contextlib.contextmanager
def _env():
    db = MagicMock()
    db.conversations.insert_one = AsyncMock()
    with patch("api.task_routes.get_db", return_value=db), \
         patch("api.task_routes.get_user_email", return_value="owner@example.com"), \
         patch("api.task_routes._get_board_config_for", AsyncMock(return_value=BOARD)):
        yield db


@pytest.mark.asyncio
async def test_create_task_persists_valid_priority():
    with _env() as db:
        resp = await handle_create_task(
            FakeRequest({"title": "T", "prompt": "do it", "task_priority": "high"}))
    assert resp.status == 201
    assert response_json(resp)["task"]["task_priority"] == "high"
    doc = db.conversations.insert_one.await_args.args[0]
    assert doc["task_priority"] == "high"


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_priority():
    with _env() as db:
        resp = await handle_create_task(
            FakeRequest({"title": "T", "prompt": "do it", "task_priority": "sky-high"}))
    assert resp.status == 400
    assert "task_priority" in response_json(resp)["error"]
    db.conversations.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_task_defaults_priority_to_none_when_omitted():
    with _env() as db:
        resp = await handle_create_task(
            FakeRequest({"title": "T", "prompt": "do it"}))
    assert resp.status == 201
    doc = db.conversations.insert_one.await_args.args[0]
    assert doc["task_priority"] is None
