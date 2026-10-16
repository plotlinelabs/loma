"""Cmd+K chat search: your chats and tasks, by title and any message."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from api.chat_search import handle_search_chats, snippet

ME = "me@example.com"
T0 = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)


class FakeRequest(dict):
    def __init__(self, query, user=ME):
        super().__init__(user_email=user, system_role="chatter")
        self.query = query


async def _db():
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"search_{uuid.uuid4().hex}"]

    def chat(cid, minutes, **fields):
        return {"conversation_id": cid, "metadata": {"user_name": ME}, "started_at": T0,
                "last_heartbeat": T0 + timedelta(minutes=minutes), "messages": [], **fields}

    await db.conversations.insert_many([
        chat("churn", 50, title="Weekly churn summary", messages=[
            {"role": "user", "content": "Summarise churn"},
            {"role": "assistant", "content": "I couldn't reach Zendesk, so the ticket column is empty for now."}]),
        chat("macros", 40, title="Support macros cleanup", messages=[
            {"role": "user", "content": "Export every zendesk macro that hasn't been used since July"}]),
        chat("task", 30, title="Connect helpdesk", task_status="active", task_board_id="b1",
             final_response="The Zendesk OAuth app needs the tickets:read scope."),
        chat("personal-task", 20, title="Zendesk invoice", task_status="todo"),
        chat("old", 10, title="Release notes"),
        chat("deleted", 60, title="Zendesk deleted", deleted=True),
        chat("approval", 60, title="Zendesk approval", human_task={"state": "pending"}),
        {**chat("theirs", 70, title="Their Zendesk chat"), "metadata": {"user_name": "other@example.com"}},
    ])
    return db


async def _search(db, **query):
    with patch("api.chat_search.get_db", return_value=db):
        response = await handle_search_chats(FakeRequest(query))
    return response.status, json.loads(response.body)


def test_snippet_centres_on_the_match_and_collapses_whitespace():
    text = "a " * 100 + "Zendesk\n\nis down" + " b" * 100
    out = snippet(text, re.compile("zendesk", re.I))
    assert out.startswith("…") and out.endswith("…") and "Zendesk is down" in out
    assert snippet("short Zendesk", re.compile("zendesk", re.I)) == "short Zendesk"
    assert snippet("nothing", re.compile("zendesk", re.I)) is None


@pytest.mark.asyncio
async def test_finds_matches_in_titles_messages_and_replies_newest_first():
    status, body = await _search(await _db(), q="zendesk")
    assert status == 200
    ids = [r["conversation_id"] for r in body["results"]]
    assert ids == ["churn", "macros", "task", "personal-task"]
    by_id = {r["conversation_id"]: r for r in body["results"]}
    assert "Zendesk" in by_id["churn"]["snippet"] and by_id["churn"]["snippet_role"] == "assistant"
    assert by_id["macros"]["snippet_role"] == "user"
    assert "tickets:read" in by_id["task"]["snippet"]
    # A title match needs no snippet.
    assert by_id["personal-task"]["snippet"] is None


@pytest.mark.asyncio
async def test_task_results_say_which_board():
    _, body = await _search(await _db(), q="zendesk")
    by_id = {r["conversation_id"]: r for r in body["results"]}
    assert by_id["task"]["task_board_id"] == "b1"
    assert by_id["personal-task"]["task_board_id"] == "personal"
    assert by_id["churn"]["task_board_id"] is None


@pytest.mark.asyncio
async def test_never_returns_other_peoples_deleted_or_approval_chats():
    _, body = await _search(await _db(), q="zendesk")
    ids = {r["conversation_id"] for r in body["results"]}
    assert not ids & {"theirs", "deleted", "approval"}


@pytest.mark.asyncio
async def test_empty_query_lists_recent_chats():
    _, body = await _search(await _db(), limit="3")
    assert [r["conversation_id"] for r in body["results"]] == ["churn", "macros", "task"]
    assert all(r["snippet"] is None for r in body["results"])


@pytest.mark.asyncio
async def test_query_is_literal_not_a_regex():
    db = await _db()
    await db.conversations.insert_one({"conversation_id": "re", "metadata": {"user_name": ME},
                                       "title": "Costs (Q3) up 5.5%", "last_heartbeat": T0})
    _, body = await _search(db, q="(Q3) up 5.5")
    assert [r["conversation_id"] for r in body["results"]] == ["re"]
    _, body = await _search(db, q=".*")
    assert body["results"] == []


@pytest.mark.asyncio
async def test_bad_limit_falls_back():
    status, body = await _search(await _db(), q="zendesk", limit="lots")
    assert status == 200 and len(body["results"]) == 4
