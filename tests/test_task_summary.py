"""The one-line summary under each board task, and a running task's step."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from api import task_summary as ts
from api.task_attention import waiting_reason

CLARIFY = ":::clarify\n" + json.dumps({"questions": [
    {"question": "Which identity providers first?", "options": [{"label": "Okta"}]},
    {"question": "Enforced or optional?"},
]}) + "\n:::"


@pytest.mark.parametrize("text,expected", [
    ("## Done\n\nReviewed **5 PRs**: 3 good, 2 need [changes](https://x).", "Done"),
    ("```bash\nls\n```\nMade `today.pdf`, 1 page.", "Made today.pdf, 1 page."),
    ("| a | b |\n|---|---|\n\n- First point\n- Second", "First point"),
    ("---\n\n> Quoted note", "Quoted note"),
    ("Two things decide it:\n\n" + CLARIFY, "Two things decide it:"),
    ("", ""),
    (None, ""),
])
def test_first_line_is_plain_text(text, expected):
    assert ts.first_line(text) == expected


def test_long_lines_are_clipped():
    line = ts.first_line("word " * 100)
    assert len(line) == ts.MAX_LEN and line.endswith("…")


@pytest.mark.parametrize("text,reason,expected", [
    ("Before I plan:\n\n" + CLARIFY, "question", "Loma asks: Which identity providers first?"),
    ("Before I plan:\n\n" + CLARIFY, None, "Loma asks: Which identity providers first?"),
    (":::clarify\nnot json\n:::\nPick one.", "question", "Pick one."),
    ("Here is the plan:\n\n```plan\n# SSO for admins\n**Summary:** ...\n```", "plan", "Plan ready: SSO for admins"),
    ("```plan\nNo heading here\n```", "plan", "Plan ready: No heading here"),
    ("All done. The report is attached.", None, "All done. The report is attached."),
])
def test_summarize_reply(text, reason, expected):
    assert ts.summarize_reply(text, reason) == expected


def _task(**fields):
    return {"conversation_id": uuid.uuid4().hex, "task_status": "active", **fields}


@pytest.mark.parametrize("fields,expected", [
    ({"status": "completed", "human_task": {"state": "pending", "kind": "approval", "title": "Approve refund"}},
     "Approve refund"),
    ({"status": "error", "error": "Klaviyo API returned 401\nTraceback..."}, "Klaviyo API returned 401"),
    ({"status": "error", "error": ""}, "The run failed"),
    ({"status": "interrupted", "error": "Server restarted during chat"}, "Stopped: server restarted during chat"),
    ({"status": "interrupted", "error": "Stopped by user"}, "You stopped it"),
    ({"status": "completed", "task_summary": "Stored line", "final_response_head": "Other"}, "Stored line"),
    ({"status": "completed", "final_response_head": "# Report\nBody"}, "Report"),
    ({"status": "completed", "needs_you_reason": "question", "final_response_head": "Hm:\n" + CLARIFY},
     "Loma asks: Which identity providers first?"),
    ({"task_status": "todo", "status": None, "prompt": "Pull churned accounts\nand draft a note"}, "Pull churned accounts"),
    ({"status": "completed"}, None),
])
def test_task_summary_per_state(fields, expected):
    task = _task(**fields)
    assert ts.task_summary(task, waiting_reason(task)) == expected


@pytest.mark.parametrize("name,tool_input,expected", [
    ("Bash", json.dumps({"command": "ls", "description": "List the export files"}), "List the export files"),
    ("WebSearch", json.dumps({"query": "x"}), "Searching the web"),
    ("Read", {"file_path": "/a"}, "Reading a file"),
    ("mcp__linear__get_issue", "{}", "Using mcp__linear__get_issue"),
    ("Bash", "not json", "Running a command"),
    (None, None, "Working"),
])
def test_step_label(name, tool_input, expected):
    assert ts.step_label(name, tool_input) == expected


# ── Stored when a run ends, cleared when it fails ────────────────────────────

async def _db():
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"summary_{uuid.uuid4().hex}"]
    await db.conversations.insert_one({"conversation_id": "c1", "task_status": "active", "status": "running",
                                       "task_summary": "stale"})
    return db


def _observer(db):
    from observability.observer import ConversationObserver

    observer = ConversationObserver(db, {}, conversation_id="c1")
    observer._run_title_topic_enrichment = AsyncMock()
    observer._run_savings_estimation = AsyncMock()
    return observer


@pytest.mark.asyncio
@pytest.mark.parametrize("final,expected", [
    ("Here you go:\n\n" + CLARIFY, "Loma asks: Which identity providers first?"),
    ("Made the **PDF**.", "Made the PDF."),
    ("", None),
])
async def test_finish_stores_the_summary(final, expected):
    db = await _db()
    with patch("observability.push.fire_task_push"):
        await _observer(db).finish(final)
    assert (await db.conversations.find_one({"conversation_id": "c1"})).get("task_summary") == expected


@pytest.mark.asyncio
async def test_an_error_clears_the_stored_summary():
    db = await _db()
    with patch("observability.push.fire_task_push"):
        await _observer(db).record_error("boom")
    assert "task_summary" not in await db.conversations.find_one({"conversation_id": "c1"})


@pytest.mark.asyncio
async def test_running_tasks_show_their_latest_step():
    from mongomock_motor import AsyncMongoMockClient
    from api.task_routes import _attach_steps

    db = AsyncMongoMockClient()[f"summary_{uuid.uuid4().hex}"]
    await db.turns.insert_many([
        {"conversation_id": "r1", "turn_number": 1, "tool_calls": [{"tool_name": "Read", "input": "{}"}]},
        {"conversation_id": "r1", "turn_number": 2, "tool_calls": [
            {"tool_name": "Bash", "input": json.dumps({"description": "Clone the repo"})},
            {"tool_name": "Bash", "input": json.dumps({"description": "Run the tests"})}]},
        {"conversation_id": "r1", "turn_number": 3, "text_blocks": [{"text": "thinking"}]},
    ])
    views = [
        {"conversation_id": "r1", "column": "working", "status": "running", "step": None, "summary": None},
        {"conversation_id": "r2", "column": "working", "status": "running", "step": None, "summary": None},
        {"conversation_id": "q1", "column": "working", "status": "queued", "step": None, "summary": None},
        {"conversation_id": "d1", "column": "review", "status": "completed", "step": None, "summary": "Done."},
    ]
    await _attach_steps(db, views)
    assert [(v["step"], v["summary"]) for v in views] == [
        ("Run the tests", "Run the tests"), (None, None), (None, None), (None, "Done."),
    ]
