"""Share links are read-only; viewers fork a shared chat to continue it."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api import routes, task_routes

OWNER = "owner@example.com"
VIEWER = "viewer@example.com"
TOKEN = "eyJlbWFpbCI6ICJvd25lckBleGFtcGxlLmNvbSJ9.c2lnbmF0dXJl"


def chat(visibility="shared", **extra):
    return {
        "conversation_id": "source-id",
        "source": "dashboard",
        "metadata": {"user_name": OWNER, "visibility": visibility, "agent_id": "agent-1"},
        "title": "Debug login",
        "prompt": f"[Personal Tools Auth Token: {TOKEN}]\nwhy is login broken",
        "status": "completed",
        "final_response": "Fixed.",
        "messages": [
            {"role": "user", "content": f"run --user-email {OWNER} --auth-token {TOKEN} now"},
            {"role": "assistant", "content": "Done", "tool_calls": [{"input": f"token={TOKEN}"}]},
        ],
        "draft_files": [{"name": "secret.png"}],
        **extra,
    }


class ForkRequest:
    match_info = {"conversation_id": "source-id"}

    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


async def fork_as(monkeypatch, source, email, body=None, role="chatter"):
    db = SimpleNamespace(
        conversations=SimpleNamespace(find_one=AsyncMock(return_value=source), insert_one=AsyncMock()),
        users=SimpleNamespace(find_one=AsyncMock(return_value={
            "task_board": {"lanes": [{"id": "todo", "name": "Todo", "order": 0}], "tags": []},
        })),
    )
    monkeypatch.setattr(task_routes, "get_db", lambda: db)
    monkeypatch.setattr(task_routes, "get_user_email", lambda _r: email)
    monkeypatch.setattr(task_routes, "get_system_role", lambda _r: role)
    response = await task_routes.handle_fork_task(ForkRequest(body))
    insert = db.conversations.insert_one
    return response, (insert.await_args.args[0] if insert.await_count else None)


class ChatRequest(dict):
    def __init__(self, body, user_email):
        super().__init__(user_email=user_email, system_role="chatter")
        self._body = body
        self.app = {}
        self.match_info = {"conversation_id": "source-id"}

    async def json(self):
        return self._body


# -- access ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_share_link_gives_view_but_not_message_access():
    assert await task_routes.task_access(None, chat(), VIEWER, "chatter") == (True, False, False)


@pytest.mark.asyncio
async def test_owner_and_admin_keep_full_access_to_a_shared_chat():
    assert await task_routes.task_access(None, chat(), OWNER, "chatter") == (True, True, True)
    assert await task_routes.task_access(None, chat(), "admin@example.com", "admin") == (True, True, True)


@pytest.mark.asyncio
async def test_private_chat_is_hidden_from_others():
    assert await task_routes.task_access(None, chat("private"), VIEWER, "chatter") == (False, False, False)


@pytest.mark.asyncio
async def test_share_link_viewer_cannot_message_the_owners_chat():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=chat())
    db.conversations.update_one = AsyncMock()
    with patch.object(routes, "get_db", return_value=db):
        response = await routes.handle_chat(ChatRequest(
            {"message": "hi", "conversation_id": "source-id"}, VIEWER))
    assert response.status == 403
    assert "Fork" in json.loads(response.body)["error"]
    db.conversations.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_conversation_tells_viewers_they_cannot_message():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=chat())
    db.turns.find.return_value.sort.return_value.to_list = AsyncMock(return_value=[])
    db.artifacts.find.return_value.to_list = AsyncMock(return_value=[])
    with patch.object(routes, "get_db", return_value=db):
        viewer = json.loads((await routes.handle_get_conversation(ChatRequest({}, VIEWER))).body)
        owner = json.loads((await routes.handle_get_conversation(ChatRequest({}, OWNER))).body)
    assert viewer["can_message"] is False
    assert owner["can_message"] is True


# -- fork --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_viewer_forks_shared_chat_onto_their_board_without_owner_settings(monkeypatch):
    response, inserted = await fork_as(monkeypatch, chat(), VIEWER, {"start": True})

    assert response.status == 201
    assert inserted["metadata"] == {"user_name": VIEWER}
    assert "task_board_id" not in inserted  # their personal "My tasks" board
    assert inserted["task_status"] == "active"
    assert inserted["task_started_at"] is not None
    assert "draft_files" not in inserted
    assert inserted["forked_from_conversation_id"] == "source-id"
    assert inserted["title"] == "Debug login (fork)"


@pytest.mark.asyncio
async def test_viewer_fork_scrubs_credentials_from_the_transcript(monkeypatch):
    _, inserted = await fork_as(monkeypatch, chat(), VIEWER)

    dumped = json.dumps([inserted["messages"], inserted["prompt"]], default=str)
    assert TOKEN not in dumped
    assert "--auth-token [REDACTED]" in inserted["messages"][0]["content"]
    assert "why is login broken" in inserted["prompt"]
    assert inserted["messages"][1]["content"] == "Done"


@pytest.mark.asyncio
async def test_private_chat_cannot_be_forked_by_others(monkeypatch):
    response, inserted = await fork_as(monkeypatch, chat("private"), VIEWER)
    assert response.status == 404
    assert inserted is None


@pytest.mark.asyncio
async def test_owner_fork_keeps_settings_but_starts_private(monkeypatch):
    response, inserted = await fork_as(monkeypatch, chat(), OWNER)

    assert response.status == 201
    assert inserted["metadata"] == {"user_name": OWNER, "agent_id": "agent-1"}
    assert inserted["task_status"] == "todo"
    assert inserted["draft_files"] == [{"name": "secret.png"}]
