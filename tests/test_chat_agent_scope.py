"""An agent chat runs on the agent's own scope, and the default agent can be reselected.

Regression coverage for the composer's Tools/Skills picks stacking on a selected
agent's scope, and for a conversation that stayed on its pinned agent after the
user switched back to the default agent. The drain queue is used as the seam:
it records exactly what the run would have been started with.
"""
import uuid
from unittest.mock import patch

import pytest

import api.drain as drain
from api.routes import handle_chat

USER = "user@example.com"
NARROW = {"enabled_skills": ["zoho-books"], "enabled_tools": ["gmail"]}


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


async def _db(*, pinned=False, tool_config=None):
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"agent_scope_{uuid.uuid4().hex}"]
    await db.agent_identities.insert_one({
        "agent_id": "ar", "name": "AR Agent", "description": "Collections",
        "skills": [], "tools": ["Zoho Books"], "status": "active",
        "visibility": "workspace", "created_by": USER,
    })
    metadata = {"user_name": USER}
    if pinned:
        metadata.update(agent_id="ar", agent_name="AR Agent")
    convo = {"conversation_id": "c1", "status": "completed", "source": "dashboard",
             "metadata": metadata, "messages": [{"role": "user", "content": "first"}]}
    if tool_config:
        convo["tool_config"] = tool_config
    await db.conversations.insert_one(convo)
    return db


async def _send(db, **body):
    with patch("api.routes.get_db", return_value=db):
        response = await handle_chat(FakeRequest({"message": "next", "conversation_id": "c1", **body}))
    assert response.status == 202
    return (await db.pending_runs.find_one({}),
            await db.conversations.find_one({"conversation_id": "c1"}))


@pytest.mark.asyncio
async def test_agent_chat_ignores_the_composers_picks_and_keeps_them_saved():
    db = await _db(tool_config=NARROW)
    run, convo = await _send(db, agent_id="ar", tool_config={"enabled_skills": [], "enabled_tools": ["slack"]})
    # Only the agent's own scope rides the run; the composer's picks don't.
    assert run["tool_config"]["enabled_tools"] is None
    assert run["tool_config"]["enabled_skills"] is None
    assert run["tool_config"]["agent_scope"]["agent_id"] == "ar"
    assert "## Active Agent: AR Agent" in run["conversation_context"]
    assert convo["tool_config"] == NARROW


@pytest.mark.asyncio
async def test_pinned_agent_chat_does_not_fall_back_to_saved_picks():
    db = await _db(pinned=True, tool_config=NARROW)
    run, _ = await _send(db)  # an older client: no agent_id, no tool_config
    assert run["metadata"]["agent_id"] == "ar"
    assert run["tool_config"]["enabled_tools"] is None
    assert run["tool_config"]["agent_scope"]["agent_id"] == "ar"


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [None, ""])
async def test_explicit_default_agent_unpins_and_restores_saved_picks(empty):
    db = await _db(pinned=True, tool_config=NARROW)
    run, convo = await _send(db, agent_id=empty)
    assert "agent_id" not in run["metadata"]
    assert "Active Agent" not in run["conversation_context"]
    assert "agent_id" not in convo["metadata"] and "agent_name" not in convo["metadata"]
    assert run["tool_config"] == NARROW


@pytest.mark.asyncio
async def test_default_agent_chat_still_applies_and_saves_picks():
    db = await _db()
    run, convo = await _send(db, agent_id=None, tool_config=NARROW)
    assert run["tool_config"] == NARROW
    assert convo["tool_config"] == NARROW
