import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


from api.task_routes import handle_create_task


class FakeRequest(dict):
    def __init__(self, body):
        super().__init__(user_email="owner@example.com")
        self._body = body

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.body)


def fake_db():
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value=None)
    db.conversations.insert_one = AsyncMock()
    return db


def test_create_task_stores_valid_priority():
    db = fake_db()
    with patch("api.task_routes.get_db", return_value=db):
        response = asyncio.run(handle_create_task(FakeRequest({
            "title": "Important task", "task_priority": "high",
        })))

    assert response.status == 201
    assert db.conversations.insert_one.await_args.args[0]["task_priority"] == "high"


def test_create_task_rejects_invalid_priority():
    db = fake_db()
    with patch("api.task_routes.get_db", return_value=db):
        response = asyncio.run(handle_create_task(FakeRequest({
            "title": "Important task", "task_priority": "critical",
        })))

    assert response.status == 400
    assert response_json(response)["error"] == (
        "task_priority must be low, medium, high, urgent or null"
    )
    db.conversations.insert_one.assert_not_awaited()


def test_create_task_defaults_priority_to_none():
    db = fake_db()
    with patch("api.task_routes.get_db", return_value=db):
        response = asyncio.run(handle_create_task(FakeRequest({"title": "Normal task"})))

    assert response.status == 201
    assert db.conversations.insert_one.await_args.args[0]["task_priority"] is None
