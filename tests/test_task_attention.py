"""Needs you vs Ready to review: why a stopped task waits, and who it pings."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api import task_attention as ta
from api.task_routes import _waiting_on, derive_column

USER = "user@example.com"


def _task(**fields):
    return {"conversation_id": uuid.uuid4().hex, "task_status": "active",
            "metadata": {"user_name": USER}, **fields}


CASES = [
    # (fields, waiting_reason, column)
    ({"status": "running"}, None, "working"),
    ({"status": "queued"}, None, "working"),
    ({"status": None}, None, "working"),
    ({"status": "completed"}, ta.FINISHED, "review"),
    ({"status": "completed", "needs_you_reason": "question"}, ta.QUESTION, "needs_input"),
    ({"status": "completed", "needs_you_reason": "plan"}, ta.PLAN, "needs_input"),
    ({"status": "completed", "needs_you_reason": "bogus"}, ta.FINISHED, "review"),
    ({"status": "error", "error": "Rate limited"}, ta.FAILED, "needs_input"),
    ({"status": "interrupted", "error": "Server restarted during chat"}, ta.STOPPED, "needs_input"),
    ({"status": "interrupted", "error": "Client disconnected"}, ta.STOPPED, "needs_input"),
    ({"status": "interrupted", "error": "Stopped by user"}, ta.STOPPED_BY_YOU, "review"),
    ({"status": "interrupted", "error": "Interrupted by user"}, ta.STOPPED_BY_YOU, "review"),
    ({"status": "completed", "human_task": {"state": "pending", "kind": "approval"}}, ta.APPROVAL, "needs_input"),
    ({"status": "completed", "human_task": {"state": "pending", "kind": "information"}}, ta.INFORMATION, "needs_input"),
    ({"status": "completed", "human_task": {"state": "answered", "kind": "approval"}}, ta.FINISHED, "review"),
]


@pytest.mark.parametrize("fields,reason,column", CASES)
def test_reason_and_column(fields, reason, column):
    task = _task(**fields)
    assert ta.waiting_reason(task) == reason
    assert derive_column(task, ["todo"]) == column


@pytest.mark.parametrize("task_status,column", [("todo", "todo"), ("done", "done")])
def test_staged_and_done_tasks_have_no_reason(task_status, column):
    task = _task(task_status=task_status, status="completed", needs_you_reason="question")
    assert ta.waiting_reason(task) is None
    assert derive_column(task, ["todo"]) == column


@pytest.mark.parametrize("text,reason", [
    ("Two things decide this:\n\n:::clarify\n{}\n:::", ta.QUESTION),
    ("Here is the plan:\n\n```plan\n# SSO\n```", ta.PLAN),
    ("Done. The report is attached.", None),
    ("", None),
    (None, None),
])
def test_reason_in_text(text, reason):
    assert ta.reason_in_text(text) == reason


@pytest.mark.asyncio
async def test_needs_you_query_matches_the_python_rule():
    """The Mongo filter behind counts, badges and pings agrees with waiting_reason."""
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"attention_{uuid.uuid4().hex}"]
    tasks = [_task(**fields) for fields, _, _ in CASES]
    tasks.append(_task(task_status="done", status="error"))
    tasks.append(_task(status="error", deleted=True))
    tasks.append({**_task(status="error"), "metadata": {"user_name": "someone@else.com"}})
    tasks.append({**_task(status="error"), "metadata": {"user_name": "someone@else.com"}, "task_assignee": USER})
    await db.conversations.insert_many(tasks)

    found = {d["conversation_id"] for d in await db.conversations.find(_waiting_on(USER)).to_list(None)}
    expected = {t["conversation_id"] for t in tasks
                if not t.get("deleted") and ta.needs_you(t)
                and USER in (t["metadata"]["user_name"], t.get("task_assignee"))}
    assert found == expected
    assert len(found) == 8  # 7 blocked cases from CASES + the one assigned to USER


@pytest.mark.asyncio
async def test_backfill_tags_only_tasks_that_ended_on_a_question_or_plan():
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"attention_{uuid.uuid4().hex}"]
    await db.conversations.insert_many([
        _task(conversation_id="q", status="completed", final_response="Which one?\n:::clarify\n{}\n:::"),
        _task(conversation_id="p", status="completed", final_response="```plan\n# Plan\n```"),
        _task(conversation_id="f", status="completed", final_response="All done."),
        _task(conversation_id="d", task_status="done", status="completed", final_response=":::clarify"),
        _task(conversation_id="r", status="running", final_response=":::clarify"),
        _task(conversation_id="k", status="completed", final_response=":::clarify", needs_you_reason="plan"),
    ])
    assert await ta.backfill_recorded_reasons(db) == 2
    reasons = {d["conversation_id"]: d.get("needs_you_reason") async for d in db.conversations.find({})}
    assert reasons == {"q": "question", "p": "plan", "f": None, "d": None, "r": None, "k": "plan"}
    assert await ta.backfill_recorded_reasons(db) == 0  # idempotent


# ── The observer records the reason when a run ends ──────────────────────────

async def _observer_db():
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"attention_{uuid.uuid4().hex}"]
    await db.conversations.insert_one({"conversation_id": "c1", "task_status": "active", "status": "running",
                                       "needs_you_reason": "question"})
    return db


def _observer(db):
    from observability.observer import ConversationObserver

    observer = ConversationObserver(db, {}, conversation_id="c1")
    observer._run_title_topic_enrichment = AsyncMock()
    observer._run_savings_estimation = AsyncMock()
    return observer


@pytest.mark.asyncio
@pytest.mark.parametrize("final,expected", [
    ("Which providers?\n:::clarify\n{\"questions\": []}\n:::", "question"),
    ("```plan\n# SSO\n```", "plan"),
    ("All done, report attached.", None),
])
async def test_finish_records_or_clears_the_reason(final, expected):
    db = await _observer_db()
    with patch("observability.push.fire_task_push"):
        await _observer(db).finish(final)
    doc = await db.conversations.find_one({"conversation_id": "c1"})
    assert doc["status"] == "completed" and doc.get("needs_you_reason") == expected


@pytest.mark.asyncio
async def test_a_question_asked_mid_run_counts_even_if_the_last_text_does_not():
    db = await _observer_db()
    observer = _observer(db)
    await observer.record_text(1, "Before I plan this:\n:::clarify\n{\"questions\": []}\n:::")
    with patch("observability.push.fire_task_push"):
        await observer.finish("")
    assert (await db.conversations.find_one({"conversation_id": "c1"}))["needs_you_reason"] == "question"


@pytest.mark.asyncio
async def test_a_plan_artifact_counts_as_a_plan():
    db = await _observer_db()
    observer = _observer(db)
    await observer.record_artifact({"artifact_id": "a1", "title": "Plan", "language": "plan",
                                    "version": 1, "type": "code", "content": "# Plan"})
    with patch("observability.push.fire_task_push"):
        await observer.finish("Plan is ready.")
    assert (await db.conversations.find_one({"conversation_id": "c1"}))["needs_you_reason"] == "plan"


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["error", "interrupted"])
async def test_errors_and_interruptions_clear_a_stale_reason(end):
    db = await _observer_db()
    observer = _observer(db)
    with patch("observability.push.fire_task_push"):
        if end == "error":
            await observer.record_error("boom")
        else:
            await observer.mark_interrupted("Server restarted during chat")
    doc = await db.conversations.find_one({"conversation_id": "c1"})
    assert "needs_you_reason" not in doc and doc["status"] == end


# ── Push titles say why ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("fields,title", [
    ({"status": "completed", "needs_you_reason": "question"}, "Loma has a question"),
    ({"status": "completed", "needs_you_reason": "plan"}, "A plan is ready for your review"),
    ({"status": "completed"}, "Task finished"),
    ({"status": "error", "error": "boom"}, "Task hit an error"),
    ({"status": "interrupted", "error": "Server restarted during chat"}, "Task stopped before finishing"),
    ({"status": "interrupted", "error": "Stopped by user"}, None),
])
async def test_push_title_follows_the_reason(fields, title, monkeypatch):
    from observability import push

    monkeypatch.setattr(push, "_vapid_config", lambda: ("key", "mailto:x@example.com"))
    sent = []
    monkeypatch.setattr(push, "_send_one", lambda sub, payload, *a: sent.append(payload))
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value={
        "task_status": "active", "title": "SSO", "metadata": {"user_name": USER}, **fields})
    subs = MagicMock()
    subs.to_list = AsyncMock(return_value=[{"subscription": {"endpoint": "https://push.example/1"}}])
    db.push_subscriptions.find = MagicMock(return_value=subs)

    await push.send_task_needs_input_push(db, "c1")

    if title is None:
        assert sent == []
    else:
        import json
        assert json.loads(sent[0])["title"] == title
