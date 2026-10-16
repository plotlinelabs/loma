import pytest

from api import task_prs
from api.task_routes import _task_view
from observability.observer import ConversationObserver


def test_extract_prs_dedupes_and_keeps_order():
    text = (
        "Opened https://github.com/acme/widgets/pull/295 and updated "
        "https://github.com/acme/mobile-sdk/pull/12/files, see also "
        "https://github.com/Acme/Widgets/pull/295#discussion_r1 and "
        "https://github.com/acme/mobile-sdk/issues/9"
    )
    assert task_prs.extract_prs(text) == [
        {"repo": "acme/widgets", "number": 295, "url": "https://github.com/acme/widgets/pull/295"},
        {"repo": "acme/mobile-sdk", "number": 12, "url": "https://github.com/acme/mobile-sdk/pull/12"},
    ]
    assert task_prs.extract_prs(None) == []
    assert task_prs.extract_prs({"not": "text"}) == []


def test_is_pr_create_call():
    assert task_prs.is_pr_create_call("mcp__github__create_pull_request", "{}")
    assert task_prs.is_pr_create_call("Bash", '{"command": "gh pr create --draft"}')
    assert not task_prs.is_pr_create_call("Bash", '{"command": "gh pr view 12"}')
    assert not task_prs.is_pr_create_call("mcp__github__list_pull_requests", "{}")


def test_task_view_merges_stored_and_reply_prs():
    doc = {
        "conversation_id": "c1", "task_status": "done", "status": "completed",
        "task_prs": [{"repo": "o/r", "number": 1, "url": "x"}],
        "reply_pr_matches": [
            {"match": "...", "captures": ["o/r", "1"]},
            {"match": "...", "captures": ["o/other", "7"]},
            {"match": "...", "captures": [None, None]},
        ],
    }
    assert [(p["repo"], p["number"]) for p in _task_view(doc, ["todo"])["pull_requests"]] == [
        ("o/r", 1), ("o/other", 7)]
    assert _task_view({"conversation_id": "c2", "task_status": "todo"}, ["todo"])["pull_requests"] == []


def test_merge_caps_list():
    many = [{"repo": "o/r", "number": n} for n in range(50)]
    assert len(task_prs.merge_prs(many)) == task_prs.MAX_TASK_PRS


class FakeColl:
    def __init__(self):
        self.updates = []

    async def update_one(self, flt, update, upsert=False):
        self.updates.append(update)


class FakeDB:
    def __init__(self):
        self.conversations = FakeColl()
        self.turns = FakeColl()


@pytest.mark.asyncio
async def test_observer_links_created_pr_only_from_create_calls():
    db = FakeDB()
    obs = ConversationObserver(db, {}, conversation_id="c1")
    await obs.record_tool_call(1, "Bash", "t1", {"command": "gh pr create --draft --title x"})
    await obs.record_tool_call(1, "mcp__github__list_pull_requests", "t2", {})
    await obs.record_tool_result("t2", False, "https://github.com/o/r/pull/5")
    await obs.record_tool_result("t1", False, "https://github.com/o/r/pull/9\n")
    added = [u["$addToSet"]["task_prs"]["$each"] for u in db.conversations.updates if "$addToSet" in u]
    assert added == [[{"repo": "o/r", "number": 9, "url": "https://github.com/o/r/pull/9"}]]


@pytest.mark.asyncio
async def test_observer_failed_create_links_nothing():
    db = FakeDB()
    obs = ConversationObserver(db, {}, conversation_id="c1")
    await obs.record_tool_call(1, "mcp__github__create_pull_request", "t1", {})
    await obs.record_tool_result("t1", True, "error, see https://github.com/o/r/pull/3")
    assert not any("$addToSet" in u for u in db.conversations.updates)
