from datetime import datetime, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

from agent import agent_scope
from fleet import dispatcher as d, service as svc

OWNER = "owner@example.com"


@pytest.fixture(autouse=True)
def no_slack(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)


async def setup(**settings):
    db = AsyncMongoMockClient().test
    await db.users.insert_one({"email": OWNER, "status": "active"})
    await svc.save_crew(db, OWNER, settings)
    await svc.set_state(db, OWNER, "active")
    # Keep today's rituals out of the way; test_rituals_* covers them.
    day = svc.local_day(await svc.get_crew(db, OWNER))
    await db.crews.update_one({"owner": OWNER}, {"$set": {"last_morning_day": day, "last_evening_day": day}})
    return db, await svc.get_crew(db, OWNER)


async def run_done(db, cid, reply="Done", status="completed"):
    """Stand-in for an agent turn: the observer records the reply and status."""
    await db.conversations.update_one({"conversation_id": cid},
                                      {"$set": {"status": status, "final_response": reply}})
    await d.finish(db, cid)


async def started(db):
    ids = []
    await d.tick(db, ids.append, {})
    return ids


async def fleet(db, cid):
    return (await db.conversations.find_one({"conversation_id": cid}))["fleet"]


def child(role="worker", key="k1", **kw):
    return {"role": role, "title": "Fix the bug", "brief": "Fix bug 12", "acceptance": "Tests pass",
            "request_key": key, **kw}


@pytest.mark.asyncio
async def test_defaults_read_gmail_and_cap_concurrency():
    db, crew = await setup()
    view = svc.crew_view(crew)
    assert view["sources"]["gmail"] is True and view["max_concurrent"] == 5
    assert view["max_depth"] == 3 and view["max_fanout"] == 8 and view["daily_usd"] == 25.0
    for bad in ({"max_concurrent": 9}, {"morning_time": "8am"}, {"timezone": "Mars/Base"},
                {"sources": {"gmail": "yes"}}, {"daily_usd": True}):
        with pytest.raises(svc.FleetError):
            await svc.save_crew(db, OWNER, bad)


@pytest.mark.asyncio
async def test_full_tree_plan_worker_review_and_wake():
    db, crew = await setup()
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    assert await started(db) == [pid]
    assert (await fleet(db, pid))["state"] == "running"

    made = await svc.tool_run(db, OWNER, pid, "create", child())
    again = await svc.tool_run(db, OWNER, pid, "create", child())
    assert made["created"] and not again["created"]
    wid = made["task"]["conversation_id"]
    await run_done(db, pid, "Morning brief")
    assert (await fleet(db, pid))["state"] == "waiting"
    assert await db.notifications.count_documents({"title": {"$regex": "Morning plan"}}) == 1

    assert await started(db) == [wid]
    await run_done(db, wid, "Fixed, PR opened")
    assert (await fleet(db, wid))["state"] == "in_review"
    review = await db.conversations.find_one({"fleet.review_of": wid})
    assert review["task_status"] is None  # Reviews stay off the board.

    # Rework once, then accept.
    assert await started(db) == [review["conversation_id"]]
    await svc.tool_run(db, OWNER, review["conversation_id"], "review",
                       {"verdict": "rework", "notes": "Add a test"})
    await run_done(db, review["conversation_id"])
    w = await fleet(db, wid)
    assert w["state"] == "queued" and w["rework_round"] == 1 and w["wake"]["notes"] == "Add a test"
    assert await started(db) == [wid]
    await run_done(db, wid, "Test added")
    review2 = await db.conversations.find_one({"fleet.review_of": wid, "fleet.state": "queued"})
    await started(db)
    await svc.tool_run(db, OWNER, review2["conversation_id"], "review",
                       {"verdict": "accept", "notes": "Looks right"})
    await run_done(db, review2["conversation_id"])
    assert (await fleet(db, wid))["state"] == "accepted"

    # The parent wakes with its children's results, then finishes.
    assert await started(db) == [pid]
    assert (await fleet(db, pid))["wake"]["type"] == "children_done"
    await run_done(db, pid, "All done")
    assert (await fleet(db, pid))["state"] == "done"


@pytest.mark.asyncio
async def test_roles_depth_and_fanout_are_enforced():
    db, crew = await setup(max_fanout=2, max_depth=2)
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    with pytest.raises(svc.FleetError, match="running"):
        await svc.tool_run(db, OWNER, pid, "create", child())
    await started(db)
    pm = (await svc.tool_run(db, OWNER, pid, "create", child("pm", "a")))["task"]["conversation_id"]
    await svc.tool_run(db, OWNER, pid, "create", child("worker", "b"))
    with pytest.raises(svc.FleetError, match="Fan-out"):
        await svc.tool_run(db, OWNER, pid, "create", child("worker", "c"))
    await db.conversations.update_one({"conversation_id": pm}, {"$set": {"fleet.state": "running"}})
    with pytest.raises(svc.FleetError, match="Depth"):
        await svc.tool_run(db, OWNER, pm, "create", child("worker", "d"))
    with pytest.raises(svc.FleetError, match="can create"):
        await svc.tool_run(db, OWNER, pm, "create", child("pm", "e"))
    with pytest.raises(svc.FleetError, match="different task"):
        await svc.tool_run(db, OWNER, pid, "create", {**child("pm", "a"), "brief": "other"})
    with pytest.raises(svc.FleetError, match="not part"):
        await svc.tool_run(db, "intruder@example.com", pid, "create", child("worker", "f"))


@pytest.mark.asyncio
async def test_concurrency_dependencies_and_budget():
    db, crew = await setup(max_concurrent=2)
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    await started(db)
    a = (await svc.tool_run(db, OWNER, pid, "create", child(key="a")))["task"]["conversation_id"]
    b = (await svc.tool_run(db, OWNER, pid, "create", child(key="b", depends_on=[a])))["task"]["conversation_id"]
    c = (await svc.tool_run(db, OWNER, pid, "create", child(key="c")))["task"]["conversation_id"]
    await run_done(db, pid)
    assert await started(db) == [a, c]  # b waits on a.
    assert await started(db) == []  # Both slots busy.
    await db.conversations.update_one({"conversation_id": a}, {"$set": {"fleet.state": "accepted"}})
    await db.conversations.update_one({"conversation_id": c}, {"$set": {"fleet.state": "accepted"}})
    await db.conversations.update_one({"conversation_id": pid},
                                      {"$set": {"cost": {"total_cost_usd": 30.0}}})
    assert await started(db) == []  # Budget reached.
    assert await db.notifications.count_documents({"title": {"$regex": "budget"}}) == 1
    await db.conversations.update_one({"conversation_id": pid}, {"$set": {"cost": {"total_cost_usd": 1}}})
    assert await started(db) == [b]


@pytest.mark.asyncio
async def test_rework_limit_escalates_to_owner_and_resumes():
    db, crew = await setup(max_rework=0)
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    await started(db)
    wid = (await svc.tool_run(db, OWNER, pid, "create", child()))["task"]["conversation_id"]
    await run_done(db, pid)
    await started(db)
    await run_done(db, wid)
    rid = (await started(db))[0]
    await svc.tool_run(db, OWNER, rid, "review", {"verdict": "rework", "notes": "Wrong file"})
    await run_done(db, rid)
    w = await fleet(db, wid)
    assert w["state"] == "blocked"
    task = await db.conversations.find_one({"conversation_id": w["escalation"]["human_task_id"]})
    assert task["human_task"]["kind"] == "information" and task["metadata"]["user_name"] == OWNER
    # The human-task worker resumed the worker with the answer.
    await db.conversations.update_one({"_id": task["_id"]}, {"$set": {"human_task.resume_state": "completed"}})
    await d.resolve_escalations(db)
    assert (await fleet(db, wid))["state"] == "in_review"


@pytest.mark.asyncio
async def test_failure_restart_and_stop():
    db, crew = await setup()
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    await started(db)
    w1 = (await svc.tool_run(db, OWNER, pid, "create", child(key="a")))["task"]["conversation_id"]
    await run_done(db, pid)
    await started(db)
    await run_done(db, w1, status="failed")
    assert (await fleet(db, w1))["state"] == "failed"
    assert await db.notifications.count_documents({"title": {"$regex": "failed"}}) == 1

    await db.conversations.update_one({"conversation_id": pid}, {"$set": {"fleet.state": "running"}})
    await d.recover(db)
    assert "restart" in (await fleet(db, pid))["error"]

    plan2, _ = await d.start_plan(db, crew)
    await svc.set_state(db, OWNER, "stopped")
    assert (await fleet(db, plan2["conversation_id"]))["state"] == "cancelled"
    assert await started(db) == []


@pytest.mark.asyncio
async def test_rituals_run_once_per_day():
    db, crew = await setup(morning_time="08:30", evening_time="19:00", timezone="UTC")
    early = datetime(2030, 1, 2, 8, 0, tzinfo=timezone.utc)
    await d.schedule_rituals(db, crew, early)
    assert await db.conversations.count_documents({"fleet.kind": "plan"}) == 0
    late = datetime(2030, 1, 2, 20, 0, tzinfo=timezone.utc)
    await db.crews.update_one({"owner": OWNER}, {"$unset": {"last_morning_day": 1, "last_evening_day": 1}})
    await d.schedule_rituals(db, await svc.get_crew(db, OWNER), early)
    assert await db.conversations.count_documents({"fleet.kind": "plan"}) == 0
    for _ in range(3):
        await d.schedule_rituals(db, await svc.get_crew(db, OWNER), late)
    assert await db.conversations.count_documents({"fleet.kind": "plan"}) == 1
    assert await db.conversations.count_documents({"fleet.kind": "report"}) == 1


@pytest.mark.parametrize("command,blocked", [
    ("python3 tools/gmail.py --user-email a --auth-token t send-email --to x", True),
    ("python3 tools/gmail.py --user-email a --auth-token t search --query x", False),
    ("python3 tools/gmail.py --user-email a --auth-token t create-draft --to x", False),
    ("python3 tools/slack_user.py --auth-token t send-message --channel C1 --text hi", True),
    ("python3 tools/google_calendar.py create-event --title x", True),
    ("gh pr merge 12 --squash", True),
    ("gh pr create --draft --title x", False),
    ("python3 tools/fleet.py --conversation-id c escalate --kind approval", False),
])
def test_write_guard_bash(command, blocked):
    scope = svc.fleet_tool_config()["agent_scope"]
    assert agent_scope.scope_is_enforced(scope)
    assert bool(agent_scope.check_tool_call(scope, "Bash", {"command": command})) is blocked
    assert bool(agent_scope.check_codex_command(scope, command)) is blocked


def test_write_guard_mcp_and_opencode():
    scope = svc.fleet_tool_config()["agent_scope"]
    assert agent_scope.check_tool_call(scope, "mcp__github__merge_pull_request", {})
    assert agent_scope.check_tool_call(scope, "mcp__claude_ai_Slack__slack_send_message", {})
    assert not agent_scope.check_tool_call(scope, "mcp__github__create_pull_request", {})
    assert not agent_scope.check_tool_call(scope, "Read", {"file_path": "/x"})
    rules = agent_scope.opencode_permission_rules(scope, ["github"])
    assert {"permission": "*_merge_pull_request", "pattern": "*", "action": "deny"} in rules
    # Scopes without the guard keep their old meaning.
    assert not agent_scope.check_write_guard({"enforce": True}, "Bash", {"command": "gh pr merge 1"})


@pytest.mark.asyncio
async def test_turning_on_late_skips_rituals_already_past():
    db = AsyncMongoMockClient().test
    await svc.save_crew(db, OWNER, {"morning_time": "00:00", "evening_time": "00:00"})
    crew = await svc.set_state(db, OWNER, "active")
    assert crew["last_morning_day"] == crew["last_evening_day"] == svc.local_day(crew)
    await d.schedule_rituals(db, crew)
    assert await db.conversations.count_documents({}) == 0


class _Observer:
    def __init__(self, db, **kwargs):
        self.metadata = kwargs["metadata"]
        self.conversation_id = kwargs["conversation_id"]

    async def resume(self, **kwargs):
        pass

    def _stop_heartbeat(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,guarded", [("approve", False), ("reject", True)])
async def test_only_owner_approval_lifts_write_guard(monkeypatch, decision, guarded):
    import agent.client
    import observability.observer
    from api import human_task_worker
    db, crew = await setup()
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    await started(db)
    out = await svc.tool_run(db, OWNER, pid, "escalate", {
        "kind": "approval", "title": "Send reply", "details": "Send this exact email", "request_key": "e1"})
    hid = out["human_task_id"]
    await db.conversations.update_one({"conversation_id": hid}, {"$set": {
        "human_task.decision": decision, "human_task.response": "ok", "human_task.responded_by": OWNER}})
    calls = []

    async def stream(**kwargs):
        calls.append(kwargs)
        yield "completed"
    monkeypatch.setattr(agent.client, "stream_agent", stream)
    monkeypatch.setattr(observability.observer, "ConversationObserver", _Observer)
    await db.conversations.update_one({"conversation_id": pid}, {"$set": {"status": "completed"}})
    source = await db.conversations.find_one({"conversation_id": pid})
    await human_task_worker.run_source(db, source, await db.conversations.find_one({"conversation_id": hid}))
    assert ("agent_scope" in calls[0]["tool_config"]) is guarded
    assert "## Crew role" in calls[0]["conversation_context"]


@pytest.mark.asyncio
async def test_run_task_uses_owner_guard_and_role_context(monkeypatch):
    import agent.client
    import observability.observer
    db, crew = await setup(focus="Ship the roadmap")
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    await started(db)
    calls = []

    async def stream(**kwargs):
        calls.append(kwargs)
        await db.conversations.update_one({"conversation_id": pid},
                                          {"$set": {"status": "completed", "final_response": "Brief"}})
        yield "completed"
    monkeypatch.setattr(agent.client, "stream_agent", stream)
    monkeypatch.setattr(observability.observer, "ConversationObserver", _Observer)
    await d.run_task(db, pid)
    call = calls[0]
    assert call["user_email"] == OWNER
    assert call["tool_config"]["agent_scope"]["write_guard"] is True
    assert "Chief of Staff" in call["conversation_context"] and "Ship the roadmap" in call["conversation_context"]
    assert "Gmail (read only" in call["conversation_context"]
    assert f"--conversation-id {pid}" in call["conversation_context"]
    assert (await fleet(db, pid))["state"] == "done"


@pytest.mark.asyncio
async def test_runtime_error_reply_retries_once_then_fails():
    db, crew = await setup()
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    await started(db)
    await db.conversations.update_one({"conversation_id": pid}, {"$set": {"fleet.last_prompt": "Plan it"}})
    await run_done(db, pid, "Failed to authenticate: OAuth session expired and could not be refreshed")
    f = await fleet(db, pid)
    assert f["state"] == "queued" and f["retries"] == 1 and f["wake"] == {"type": "retry", "prompt": "Plan it"}
    await started(db)
    await run_done(db, pid, "Failed to authenticate: OAuth session expired and could not be refreshed")
    assert (await fleet(db, pid))["state"] == "failed"


@pytest.mark.asyncio
async def test_mid_run_runtime_error_is_not_retried():
    db, crew = await setup()
    plan, _ = await d.start_plan(db, crew)
    pid = plan["conversation_id"]
    await started(db)
    await run_done(db, pid, "Sorry, I encountered an OpenCode error: boom")
    f = await fleet(db, pid)
    assert f["state"] == "failed" and "boom" in f["error"]
