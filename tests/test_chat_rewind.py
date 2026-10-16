"""Edit, retry and branch: the stored thread is cut back, nothing is lost."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agent import active_streams
from api.chat_rewind import find_user_message, handle_branch, handle_rewind

OWNER = "owner@example.com"
OTHER = "other@example.com"
T0 = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class FakeRequest(dict):
    def __init__(self, body, cid="c1", user=OWNER, role="chatter"):
        super().__init__(user_email=user, system_role=role)
        self._body = body
        self.match_info = {"conversation_id": cid}

    async def json(self):
        return self._body


THREAD = [
    {"role": "user", "content": "first", "timestamp": at(0)},
    {"role": "assistant", "content": "reply one", "timestamp": at(1)},
    {"role": "user", "content": "second", "timestamp": at(2)},
    {"role": "assistant", "content": "reply two", "timestamp": at(3)},
    {"role": "user", "content": "third", "timestamp": at(4)},
    {"role": "assistant", "content": "reply three", "timestamp": at(5)},
]


async def _db(**convo):
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"rewind_{uuid.uuid4().hex}"]
    await db.conversations.insert_one({
        "conversation_id": "c1", "status": "completed", "source": "dashboard", "title": "Churn",
        "metadata": {"user_name": OWNER}, "messages": [dict(m) for m in THREAD],
        "final_response": "reply three", "task_summary": "old", **convo,
    })
    for n, minute in enumerate([0.5, 2.5, 4.5], start=1):
        await db.turns.insert_one({"conversation_id": "c1", "turn_number": n, "timestamp": at(minute),
                                   "text_blocks": [{"text": f"turn {n}"}]})
    await db.artifacts.insert_one({"conversation_id": "c1", "artifact_id": "a1", "timestamp": at(0.6)})
    await db.artifacts.insert_one({"conversation_id": "c1", "artifact_id": "a3", "timestamp": at(4.6)})
    return db


async def _call(handler, db, body, **request):
    with patch("api.chat_rewind.get_db", return_value=db):
        response = await handler(FakeRequest(body, **request))
    return response.status, json.loads(response.body)


def test_find_user_message_by_index_and_text():
    assert find_user_message(THREAD, 1, "second") == 2
    assert find_user_message(THREAD, 1, "  second ") == 2
    assert find_user_message(THREAD, 1, "nope") is None
    # Shown index off (repeated text hidden): nearest message with that text wins.
    assert find_user_message(THREAD, 0, "third") == 4
    ctx = [{"role": "user", "content": "[Context: board]\n\nhello"}]
    assert find_user_message(ctx, 0, "hello") == 0


@pytest.mark.asyncio
async def test_rewind_cuts_messages_turns_and_artifacts_from_that_message():
    db = await _db()
    status, body = await _call(handle_rewind, db, {"user_index": 1, "content": "second"})
    assert status == 200 and body["kept_messages"] == 2
    convo = await db.conversations.find_one({"conversation_id": "c1"})
    assert [m["content"] for m in convo["messages"]] == ["first", "reply one"]
    assert convo["final_response"] == "reply one"
    assert "task_summary" not in convo
    assert [t["turn_number"] for t in await db.turns.find({}).to_list(None)] == [1]
    assert [a["artifact_id"] for a in await db.artifacts.find({}).to_list(None)] == ["a1"]
    # Set aside, not deleted.
    assert [m["content"] for m in convo["rewinds"][0]["messages"]] == ["second", "reply two", "third", "reply three"]
    assert sorted(t["turn_number"] for t in await db.rewound_turns.find({}).to_list(None)) == [2, 3]
    assert [a["artifact_id"] for a in await db.rewound_artifacts.find({}).to_list(None)] == ["a3"]


@pytest.mark.asyncio
async def test_retry_last_reply_keeps_everything_before_the_last_message():
    db = await _db()
    status, _ = await _call(handle_rewind, db, {"user_index": 2, "content": "third"})
    assert status == 200
    convo = await db.conversations.find_one({"conversation_id": "c1"})
    assert [m["content"] for m in convo["messages"]] == ["first", "reply one", "second", "reply two"]
    assert sorted(t["turn_number"] for t in await db.turns.find({}).to_list(None)) == [1, 2]


@pytest.mark.asyncio
async def test_editing_the_first_message_empties_the_thread():
    db = await _db()
    status, body = await _call(handle_rewind, db, {"user_index": 0, "content": "first"})
    assert status == 200 and body["kept_messages"] == 0
    assert await db.turns.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status_value", ["running", "queued"])
async def test_rewind_refuses_while_running(status_value):
    db = await _db(status=status_value)
    status, _ = await _call(handle_rewind, db, {"user_index": 1, "content": "second"})
    assert status == 409
    assert len((await db.conversations.find_one({}))["messages"]) == 6


@pytest.mark.asyncio
async def test_rewind_refuses_while_a_run_holds_the_claim():
    db = await _db()
    claimed, done = asyncio.Event(), asyncio.Event()

    async def run():  # a run holds the claim from its own task
        await active_streams.try_claim("c1", "other-run")
        claimed.set()
        await done.wait()
        await active_streams.release_claim("c1", "other-run")

    holder = asyncio.create_task(run())
    await claimed.wait()
    try:
        status, _ = await _call(handle_rewind, db, {"user_index": 1, "content": "second"})
    finally:
        done.set()
        await holder
    assert status == 409
    assert len((await db.conversations.find_one({}))["messages"]) == 6


@pytest.mark.asyncio
async def test_rewind_releases_its_claim():
    db = await _db()
    await _call(handle_rewind, db, {"user_index": 2, "content": "third"})
    assert await active_streams.try_claim("c1", "next-run")
    await active_streams.release_claim("c1", "next-run")


@pytest.mark.asyncio
async def test_rewind_on_stale_view_changes_nothing():
    db = await _db()
    status, body = await _call(handle_rewind, db, {"user_index": 1, "content": "edited elsewhere"})
    assert status == 409 and "Reload" in body["error"]
    assert len((await db.conversations.find_one({}))["messages"]) == 6


@pytest.mark.asyncio
async def test_rewind_needs_a_timestamp_to_cut_at():
    db = await _db(messages=[{"role": "user", "content": "old"}, {"role": "assistant", "content": "x"}])
    status, _ = await _call(handle_rewind, db, {"user_index": 0, "content": "old"})
    assert status == 409


@pytest.mark.asyncio
async def test_only_the_owner_can_rewind_a_shared_chat():
    db = await _db(metadata={"user_name": OWNER, "visibility": "shared"})
    status, _ = await _call(handle_rewind, db, {"user_index": 1, "content": "second"}, user=OTHER)
    assert status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"user_index": "1", "content": "second"}, {"user_index": 1}])
async def test_rewind_validates_body(body):
    status, _ = await _call(handle_rewind, await _db(), body)
    assert status == 400


@pytest.mark.asyncio
async def test_rewind_missing_or_deleted_chat_is_404():
    db = await _db(deleted=True)
    status, _ = await _call(handle_rewind, db, {"user_index": 1, "content": "second"})
    assert status == 404


@pytest.mark.asyncio
async def test_branch_from_a_middle_reply_copies_up_to_it():
    db = await _db()
    status, body = await _call(handle_branch, db, {"before_user_index": 2, "content": "third"})
    assert status == 201 and body["title"] == "Churn (branch)"
    new = await db.conversations.find_one({"conversation_id": body["conversation_id"]})
    assert [m["content"] for m in new["messages"]] == ["first", "reply one", "second", "reply two"]
    assert new["metadata"]["user_name"] == OWNER and new["metadata"]["visibility"] == "private"
    assert new["branched_from_conversation_id"] == "c1" and new["final_response"] == "reply two"
    assert sorted(t["turn_number"] for t in await db.turns.find({"conversation_id": new["conversation_id"]}).to_list(None)) == [1, 2]
    assert [a["artifact_id"] for a in await db.artifacts.find({"conversation_id": new["conversation_id"]}).to_list(None)] == ["a1"]
    # The original is untouched.
    assert len((await db.conversations.find_one({"conversation_id": "c1"}))["messages"]) == 6
    assert await db.turns.count_documents({"conversation_id": "c1"}) == 3


@pytest.mark.asyncio
async def test_branch_from_the_last_reply_copies_the_whole_chat():
    db = await _db()
    status, body = await _call(handle_branch, db, {})
    assert status == 201
    new = await db.conversations.find_one({"conversation_id": body["conversation_id"]})
    assert len(new["messages"]) == 6 and new["total_turns"] == 3


@pytest.mark.asyncio
async def test_a_teammate_with_access_branches_into_their_own_chat():
    db = await _db(metadata={"user_name": OWNER, "visibility": "shared"})
    status, body = await _call(handle_branch, db, {}, user=OTHER)
    assert status == 201
    new = await db.conversations.find_one({"conversation_id": body["conversation_id"]})
    assert new["metadata"]["user_name"] == OTHER


@pytest.mark.asyncio
async def test_branch_without_access_is_404():
    db = await _db()
    with patch("api.task_routes.task_access", return_value=(False, False, None)):
        status, _ = await _call(handle_branch, db, {}, user=OTHER)
    assert status == 404


@pytest.mark.asyncio
async def test_branch_before_any_reply_is_refused():
    status, _ = await _call(handle_branch, await _db(), {"before_user_index": 0, "content": "first"})
    assert status == 400
