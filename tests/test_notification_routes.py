"""Tests for the notification inbox routes and creation helper."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.notification_routes import (
    handle_dismiss,
    handle_list_notifications,
    handle_mark_read,
    handle_read_all,
    handle_unread_count,
)
from observability.notifications import create_notification


class FakeRequest(dict):
    def __init__(self, *, user_email="", role="chatter", query=None,
                 notification_id="notif-1"):
        super().__init__(user_email=user_email, system_role=role)
        self.match_info = {"notification_id": notification_id}
        self.query = query or {}


def response_json(response):
    return json.loads(response.body)


def _db_with_find(docs):
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    db.notifications.find.return_value = cursor
    return db


# ── List ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_auth():
    with patch("api.notification_routes.get_db", return_value=MagicMock()):
        response = await handle_list_notifications(FakeRequest(user_email=""))
    assert response.status == 401


@pytest.mark.asyncio
async def test_list_scopes_to_user_and_excludes_dismissed():
    db = _db_with_find([
        {"_id": "x", "notification_id": "n1", "user_email": "user@example.com",
         "title": "Hello", "read": False, "dismissed": False},
    ])
    with patch("api.notification_routes.get_db", return_value=db):
        response = await handle_list_notifications(FakeRequest(user_email="user@example.com"))
    assert response.status == 200
    query = db.notifications.find.call_args[0][0]
    assert query["user_email"] == "user@example.com"
    assert query["dismissed"] == {"$ne": True}
    body = response_json(response)
    assert body["notifications"][0]["notification_id"] == "n1"
    assert "_id" not in body["notifications"][0]


@pytest.mark.asyncio
async def test_list_can_include_dismissed():
    db = _db_with_find([])
    with patch("api.notification_routes.get_db", return_value=db):
        response = await handle_list_notifications(
            FakeRequest(user_email="user@example.com", query={"include_dismissed": "1"}),
        )
    assert response.status == 200
    query = db.notifications.find.call_args[0][0]
    assert "dismissed" not in query


# ── Unread count ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unread_count_scopes_to_user():
    db = MagicMock()
    db.notifications.count_documents = AsyncMock(return_value=3)
    with patch("api.notification_routes.get_db", return_value=db):
        response = await handle_unread_count(FakeRequest(user_email="user@example.com"))
    assert response.status == 200
    assert response_json(response)["count"] == 3
    query = db.notifications.count_documents.call_args[0][0]
    assert query["user_email"] == "user@example.com"
    assert query["read"] == {"$ne": True}
    assert query["dismissed"] == {"$ne": True}


@pytest.mark.asyncio
async def test_unread_count_zero_without_db():
    with patch("api.notification_routes.get_db", return_value=None):
        response = await handle_unread_count(FakeRequest(user_email="user@example.com"))
    assert response.status == 200
    assert response_json(response)["count"] == 0


# ── Mark read / dismiss ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_read_updates_own_notification():
    db = MagicMock()
    db.notifications.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    with patch("api.notification_routes.get_db", return_value=db):
        response = await handle_mark_read(
            FakeRequest(user_email="user@example.com", notification_id="n1"),
        )
    assert response.status == 200
    query, updates = db.notifications.update_one.call_args[0]
    assert query == {"notification_id": "n1", "user_email": "user@example.com"}
    assert updates["$set"]["read"] is True


@pytest.mark.asyncio
async def test_mark_read_404_for_other_users_notification():
    db = MagicMock()
    db.notifications.update_one = AsyncMock(return_value=MagicMock(matched_count=0))
    with patch("api.notification_routes.get_db", return_value=db):
        response = await handle_mark_read(
            FakeRequest(user_email="intruder@example.com", notification_id="n1"),
        )
    assert response.status == 404


@pytest.mark.asyncio
async def test_dismiss_sets_dismissed_and_read():
    db = MagicMock()
    db.notifications.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    with patch("api.notification_routes.get_db", return_value=db):
        response = await handle_dismiss(
            FakeRequest(user_email="user@example.com", notification_id="n1"),
        )
    assert response.status == 200
    updates = db.notifications.update_one.call_args[0][1]["$set"]
    assert updates["dismissed"] is True
    assert updates["read"] is True


@pytest.mark.asyncio
async def test_read_all_scopes_to_user():
    db = MagicMock()
    db.notifications.update_many = AsyncMock(return_value=MagicMock(modified_count=4))
    with patch("api.notification_routes.get_db", return_value=db):
        response = await handle_read_all(FakeRequest(user_email="user@example.com"))
    assert response.status == 200
    assert response_json(response)["updated"] == 4
    query = db.notifications.update_many.call_args[0][0]
    assert query["user_email"] == "user@example.com"


# ── create_notification helper ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_notification_inserts_doc_and_fires_push():
    db = MagicMock()
    db.notifications.insert_one = AsyncMock()
    with patch("observability.notifications.fire_user_push") as fire:
        doc = await create_notification(
            db,
            user_email="User@Example.com",
            title="Flow finished",
            body="3 critical tickets",
            conversation_id="conv-42",
            source="flow",
        )
    assert doc["user_email"] == "user@example.com"
    assert doc["title"] == "Flow finished"
    assert doc["conversation_id"] == "conv-42"
    assert doc["read"] is False
    assert doc["dismissed"] is False
    db.notifications.insert_one.assert_awaited_once()
    fire.assert_called_once()
    # Push clicks land on the notifications inbox, never directly in a chat
    assert fire.call_args.kwargs["url"].endswith("/notifications")
    assert "conv-42" not in fire.call_args.kwargs["url"]


@pytest.mark.asyncio
async def test_create_notification_requires_title_and_email():
    db = MagicMock()
    db.notifications.insert_one = AsyncMock()
    with pytest.raises(ValueError):
        await create_notification(db, user_email="user@example.com", title="  ")
    with pytest.raises(ValueError):
        await create_notification(db, user_email="not-an-email", title="Hi")


@pytest.mark.asyncio
async def test_create_notification_can_skip_push():
    db = MagicMock()
    db.notifications.insert_one = AsyncMock()
    with patch("observability.notifications.fire_user_push") as fire:
        await create_notification(
            db, user_email="user@example.com", title="Quiet", fire_push=False,
        )
    fire.assert_not_called()
