import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.task_routes import handle_create_task


class FakeRequest(dict):
    def __init__(self, *, user_email="owner@example.com", body=None):
        super().__init__(user_email=user_email, system_role="chatter")
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.body)


def _mock_db():
    db = MagicMock()
    db.conversations.insert_one = AsyncMock()
    return db


BOARD = {"lanes": [{"id": "todo", "name": "To do"}], "tags": []}


async def _create(body):
    db = _mock_db()
    with patch("api.task_routes.get_db", return_value=db), patch(
        "api.task_routes._get_board_config_for", AsyncMock(return_value=BOARD)
    ):
        response = await handle_create_task(FakeRequest(body=body))
    return db, response


@pytest.mark.asyncio
async def test_create_task_persists_valid_priority():
    db, response = await _create(
        {"title": "Ship it", "prompt": "do the thing", "task_priority": "urgent"}
    )
    assert response.status == 201
    assert response_json(response)["task"]["task_priority"] == "urgent"
    inserted = db.conversations.insert_one.await_args.args[0]
    assert inserted["task_priority"] == "urgent"


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_priority():
    db, response = await _create(
        {"title": "Ship it", "prompt": "do the thing", "task_priority": "sky-high"}
    )
    assert response.status == 400
    assert "task_priority" in response_json(response)["error"]
    db.conversations.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_task_defaults_priority_to_none_when_omitted():
    db, response = await _create({"title": "Ship it", "prompt": "do the thing"})
    assert response.status == 201
    assert response_json(response)["task"]["task_priority"] is None
    inserted = db.conversations.insert_one.await_args.args[0]
    assert inserted["task_priority"] is None
