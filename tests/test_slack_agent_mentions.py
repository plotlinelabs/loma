"""Naming a Loma agent at the start of a Slack message picks and pins it."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient

from slack_app.agent_mentions import (
    agent_handle, format_agent_list, is_list_agents_command, match_agent_reference,
)

AR = {"agent_id": "ag-ar", "name": "AR Agent", "description": "Billing and invoices"}
AR_PRO = {"agent_id": "ag-pro", "name": "AR Agent Pro", "description": "More"}
FIN = {"agent_id": "ag-fin", "name": "Finance", "description": "Finance help"}
AGENTS = [AR, AR_PRO, FIN]


@pytest.mark.parametrize("text, agent, rest", [
    ("AR Agent: check invoice 12", AR, "check invoice 12"),
    ("ar agent, check invoice 12", AR, "check invoice 12"),
    ("@AR-Agent: check invoice 12", AR, "check invoice 12"),
    ("ar-agent check invoice 12", AR, "check invoice 12"),
    ("AR Agent Pro: hi", AR_PRO, "hi"),
    ("finance: what is due?", FIN, "what is due?"),
    # Ordinary sentences never switch agents.
    ("Finance team asked about invoice 12", None, "Finance team asked about invoice 12"),
    ("ar agent check invoice 12", None, "ar agent check invoice 12"),
    ("check invoice 12 with AR Agent: now", None, "check invoice 12 with AR Agent: now"),
    ("ar-agents are cool", None, "ar-agents are cool"),
    ("", None, ""),
])
def test_match_agent_reference(text, agent, rest):
    assert match_agent_reference(text, AGENTS) == (agent, rest)


def test_list_command_and_format():
    assert is_list_agents_command(" Agents? ")
    assert not is_list_agents_command("agents are broken")
    assert agent_handle("AR  Agent") == "ar-agent"
    out = format_agent_list([AR])
    assert "*AR Agent* (`ar-agent`): Billing and invoices" in out
    assert "@Loma AR Agent: your question" in out
    assert "No agents" in format_agent_list([])


async def _events(*chunks):
    for chunk in chunks:
        yield chunk


async def _run(db, user_message, prompt=None, email="a@x.so"):
    from slack_app import handlers

    slack = MagicMock()
    slack.reactions_add = AsyncMock()
    slack.chat_postMessage = AsyncMock()
    slack.users_info = AsyncMock(return_value={"user": {"profile": {"email": email}}})
    observer = MagicMock(conversation_id="c1", start=AsyncMock(), resume=AsyncMock())
    agent = MagicMock(return_value=_events("ok"))
    with patch.object(handlers, "is_draining", return_value=False), \
            patch.object(handlers, "get_db", return_value=db), \
            patch.object(handlers, "ConversationObserver", return_value=observer) as obs_cls, \
            patch.object(handlers, "stream_agent", agent), \
            patch.object(handlers, "_stream_response", AsyncMock()), \
            patch.object(handlers, "ingest_dashboard_chat", AsyncMock()):
        await handlers._handle_agent_request(
            slack, "C1", "1.0", "2.0", prompt or user_message, "thread ctx", [],
            "slack_mention", "U1", user_message=user_message,
        )
    return agent.call_args.kwargs, obs_cls.call_args.kwargs["metadata"], slack


@pytest.fixture
def db():
    database = AsyncMongoMockClient()["loma_test"]
    return database


async def _seed(db):
    await db.agent_identities.insert_many([
        {**AR, "status": "active", "visibility": "workspace", "created_by": "o@x.so"},
        {"agent_id": "ag-private", "name": "Secret Agent", "description": "x",
         "status": "active", "visibility": "private", "created_by": "o@x.so"},
    ])


@pytest.mark.asyncio
async def test_named_agent_answers_and_is_pinned(db):
    await _seed(db)
    kwargs, metadata, slack = await _run(db, "AR Agent: check invoice 12",
                                         prompt="PREFIX: AR Agent: check invoice 12")
    assert kwargs["prompt"] == "PREFIX: check invoice 12"
    assert kwargs["conversation_context"].startswith("## Active Agent: AR Agent")
    assert kwargs["conversation_context"].endswith("thread ctx")
    assert kwargs["user_email"] == "a@x.so"  # still the sender's own identity
    assert metadata["agent_id"] == "ag-ar" and metadata["agent_name"] == "AR Agent"
    assert "(AR Agent)" in slack.chat_postMessage.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_thread_keeps_pinned_agent_and_switch_updates_it(db):
    await _seed(db)
    await db.conversations.insert_one({
        "conversation_id": "c1",
        "metadata": {"slack_channel_id": "C1", "slack_thread_ts": "1.0", "agent_id": "ag-ar"},
    })
    kwargs, metadata, _ = await _run(db, "any update?")
    assert kwargs["prompt"] == "any update?"
    assert kwargs["conversation_context"].startswith("## Active Agent: AR Agent")
    assert metadata["agent_id"] == "ag-ar"

    # Someone who cannot see the pinned agent gets plain Loma.
    await db.conversations.update_one({"conversation_id": "c1"}, {"$set": {"metadata.agent_id": "ag-private"}})
    kwargs, metadata, _ = await _run(db, "any update?")
    assert kwargs["conversation_context"] == "thread ctx"
    assert "agent_id" not in metadata

    # Naming a different agent switches the thread and says so.
    kwargs, _, slack = await _run(db, "ar-agent take over")
    assert kwargs["prompt"] == "take over"
    convo = await db.conversations.find_one({"conversation_id": "c1"})
    assert convo["metadata"]["agent_id"] == "ag-ar"
    assert "Switched this thread to *AR Agent*" in slack.chat_postMessage.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_private_agent_of_someone_else_does_not_match(db):
    await _seed(db)
    kwargs, metadata, _ = await _run(db, "Secret Agent: hi")
    assert kwargs["prompt"] == "Secret Agent: hi"
    assert "agent_id" not in metadata
    # Its owner can use it.
    kwargs, metadata, _ = await _run(db, "Secret Agent: hi", email="o@x.so")
    assert kwargs["prompt"] == "hi" and metadata["agent_id"] == "ag-private"
