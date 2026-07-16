import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes import (
    _check_conversation_access,
    handle_share_conversation,
)


def conversation(owner="owner@plotline.so", visibility="private"):
    return {
        "conversation_id": "conv-1",
        "source": "dashboard",
        "metadata": {"user_name": owner, "visibility": visibility},
    }


class FakeRequest(dict):
    def __init__(self, *, user_email, role="chatter", body=None):
        super().__init__(user_email=user_email, system_role=role)
        self.match_info = {"conversation_id": "conv-1"}
        self._body = body

    async def json(self):
        return self._body


def response_json(response):
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_owner_can_share_and_unshare_conversation():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=conversation())
    db.conversations.update_one = AsyncMock()

    with patch("api.routes.get_db", return_value=db):
        shared = await handle_share_conversation(FakeRequest(
            user_email="owner@plotline.so", body={"shared": True},
        ))
        private = await handle_share_conversation(FakeRequest(
            user_email="owner@plotline.so", body={"shared": False},
        ))

    assert shared.status == 200
    assert response_json(shared)["shared"] is True
    assert private.status == 200
    assert response_json(private)["shared"] is False
    assert db.conversations.update_one.await_args_list[0].args[1] == {
        "$set": {"metadata.visibility": "shared"}
    }
    assert db.conversations.update_one.await_args_list[1].args[1] == {
        "$set": {"metadata.visibility": "private"}
    }


@pytest.mark.asyncio
async def test_non_owner_cannot_change_sharing_even_if_admin():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=conversation())
    db.conversations.update_one = AsyncMock()

    with patch("api.routes.get_db", return_value=db):
        response = await handle_share_conversation(FakeRequest(
            user_email="admin@plotline.so", role="admin", body={"shared": True},
        ))

    assert response.status == 403
    db.conversations.update_one.assert_not_awaited()


@pytest.mark.parametrize("role", ["chatter", "analyst", "operator", "maintainer", "admin"])
def test_shared_conversation_is_accessible_to_every_authenticated_role(role):
    assert _check_conversation_access(
        conversation(visibility="shared"), "teammate@plotline.so", role
    )


@pytest.mark.parametrize("role", ["chatter", "analyst", "operator", "maintainer"])
def test_private_dashboard_conversation_remains_owner_only(role):
    assert not _check_conversation_access(
        conversation(visibility="private"), "teammate@plotline.so", role
    )
    assert _check_conversation_access(
        conversation(visibility="private"), "owner@plotline.so", role
    )


@pytest.mark.asyncio
async def test_unauthenticated_user_cannot_change_sharing():
    db = MagicMock()
    with patch("api.routes.get_db", return_value=db):
        response = await handle_share_conversation(FakeRequest(
            user_email="", body={"shared": True},
        ))
    assert response.status == 401
