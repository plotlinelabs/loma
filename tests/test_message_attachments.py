"""A user message records which files were attached (names and types, never the data)."""

import uuid

import pytest
from mongomock_motor import AsyncMongoMockClient

import api.pending_runs as pending_runs
from observability.observer import ConversationObserver, attachment_fields

PNG = {"name": "shot.png", "mimetype": "image/png", "type": "image", "data": "iVBORw0KGgo="}
PDF = {"name": "spec.pdf", "mimetype": "application/pdf", "type": "binary", "data": "JVBERi0="}
PNG_META = {"name": "shot.png", "type": "image", "mimetype": "image/png"}
PDF_META = {"name": "spec.pdf", "type": "binary", "mimetype": "application/pdf"}


@pytest.fixture
def db():
    return AsyncMongoMockClient()[f"attach_{uuid.uuid4().hex}"]


def test_attachment_fields_keeps_metadata_and_drops_data():
    assert attachment_fields([PNG, PDF]) == {"files": [PNG_META, PDF_META]}


@pytest.mark.parametrize("files", [None, [], [{"data": "x"}], ["not-a-dict"]])
def test_attachment_fields_is_empty_without_named_files(files):
    assert attachment_fields(files) == {}


@pytest.mark.asyncio
async def test_start_records_files_on_the_first_message(db):
    observer = ConversationObserver(db, {"source": "dashboard", "prompt": "look at this"})
    await observer.start(files=[PNG])
    observer._stop_heartbeat()
    doc = await db.conversations.find_one({"conversation_id": observer.conversation_id})
    assert doc["messages"][0]["files"] == [PNG_META]


@pytest.mark.asyncio
async def test_resume_records_files_and_plain_messages_have_none(db):
    observer = ConversationObserver(db, {"source": "dashboard", "prompt": "first"})
    await observer.start()
    observer._stop_heartbeat()
    cid = observer.conversation_id

    follow_up = ConversationObserver(db, {"source": "dashboard", "prompt": "second"}, conversation_id=cid)
    await follow_up.resume(files=[PDF])
    follow_up._stop_heartbeat()
    doc = await db.conversations.find_one({"conversation_id": cid})
    assert "files" not in doc["messages"][0]
    assert doc["messages"][1]["files"] == [PDF_META]


@pytest.mark.asyncio
async def test_queued_message_records_files(db):
    await pending_runs.record_queued_message(
        db, "c1", "hi", sender="a@x", files=[PNG],
        new_conversation={"source": "dashboard", "prompt": "hi", "model": "m"})
    await pending_runs.record_queued_message(db, "c1", "more", sender="a@x")
    doc = await db.conversations.find_one({"conversation_id": "c1"})
    assert doc["messages"][0]["files"] == [PNG_META]
    assert "files" not in doc["messages"][1]
