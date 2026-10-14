"""Starting a board draft saves the first message as the thread's prompt.

The drain queue is the seam (as in test_chat_agent_scope): it records exactly
what the run would have been started with, and what the thread stores.
"""
import uuid
from unittest.mock import patch

import pytest

import api.drain as drain
from api.routes import handle_chat

USER = "user@example.com"


class FakeRequest(dict):
    def __init__(self, body):
        super().__init__(user_email=USER, system_role="chatter")
        self._body = body
        self.remote = "127.0.0.1"
        self.can_read_body = True

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _draining():
    drain.set_draining(True, "test")
    yield
    drain.set_draining(False)


async def _db(**convo):
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"draft_prompt_{uuid.uuid4().hex}"]
    await db.conversations.insert_one({
        "conversation_id": "c1", "status": "completed", "source": "dashboard",
        "metadata": {"user_name": USER}, "messages": [{"role": "user", "content": "first"}], **convo,
    })
    return db


async def _send(db, message, **body):
    with patch("api.routes.get_db", return_value=db):
        response = await handle_chat(FakeRequest({"message": message, "conversation_id": "c1", **body}))
    assert response.status == 202
    return (await db.pending_runs.find_one({}),
            await db.conversations.find_one({"conversation_id": "c1"}))


@pytest.mark.asyncio
async def test_starting_an_empty_board_draft_saves_the_message_as_its_prompt():
    db = await _db(task_status="todo", status=None, prompt="", messages=[])
    _, convo = await _send(db, "Import these contacts into Apollo")
    assert convo["prompt"] == "Import these contacts into Apollo"


@pytest.mark.asyncio
async def test_a_started_thread_keeps_its_prompt():
    db = await _db(task_status="todo", status="completed", prompt="original ask")
    _, convo = await _send(db, "parked follow-up")
    assert convo["prompt"] == "original ask"
