"""Slash commands and /plan as handle_chat starts a run.

The drain queue is the seam (as in test_chat_agent_scope): it records exactly
what the run would have been started with, and what the thread stores.
"""
import uuid
from unittest.mock import patch

import pytest

import api.drain as drain
from agent.plan_mode import PLAN_MODE_MARKER
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

    db = AsyncMongoMockClient()[f"chat_commands_{uuid.uuid4().hex}"]
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
async def test_slash_command_runs_its_template_but_the_thread_keeps_what_was_typed():
    run, convo = await _send(await _db(), "/review https://github.com/o/r/pull/9")
    assert "https://github.com/o/r/pull/9" in run["prompt"] and "code-review" in run["prompt"]
    assert convo["messages"][-1]["content"] == "/review https://github.com/o/r/pull/9"


@pytest.mark.asyncio
async def test_users_own_template_is_expanded():
    db = await _db()
    await db.prompt_templates.insert_one({"owner": USER, "name": "standup", "body": "Standup from {input}"})
    run, _ = await _send(db, "/standup yesterday")
    assert run["prompt"] == "Standup from yesterday"


@pytest.mark.asyncio
async def test_plan_command_turns_on_plan_mode():
    run, _ = await _send(await _db(), "/plan add SSO")
    assert run["prompt"] == "add SSO"
    assert PLAN_MODE_MARKER in run["conversation_context"]


@pytest.mark.asyncio
async def test_plain_message_is_not_in_plan_mode():
    run, _ = await _send(await _db(), "hello")
    assert run["prompt"] == "hello"
    assert PLAN_MODE_MARKER not in (run["conversation_context"] or "")
