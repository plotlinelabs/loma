"""Personal crews: user-defined members, links, permissions and schedules."""

from datetime import datetime, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

from agent import agent_scope
from fleet import dispatcher as d, service as svc

OWNER = "owner@example.com"


def team(**overrides):
    """An arbitrary team shape: a lead hands work to a dev and a writer; qa reviews the dev."""
    members = [
        {"id": "lead", "name": "Lead", "description": "Splits goals", "instructions": "Be brief.",
         "delegates_to": ["dev", "writer"]},
        {"id": "dev", "name": "Dev", "reviewed_by": "qa"},
        {"id": "writer", "name": "Writer", "writes": "deny", "can_escalate": False},
        {"id": "qa", "name": "QA", "description": "Checks work"},
    ]
    for m in members:
        m.update(overrides.get(m["id"], {}))
    return members


@pytest.fixture(autouse=True)
def no_slack(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOMA_CREWS_ALLOW_UNATTENDED_WRITES", raising=False)


async def setup(members=None, **settings):
    db = AsyncMongoMockClient().test
    await db.users.insert_one({"email": OWNER, "status": "active"})
    await svc.save_crew(db, OWNER, {"members": members or team(), **settings})
    await svc.set_state(db, OWNER, "active")
    return db, await svc.get_crew(db, OWNER)


async def give(db, member_id="lead", title="Ship it"):
    doc = await svc.owner_task(db, OWNER, {"member_id": member_id, "title": title, "brief": "Do it"})
    return doc["conversation_id"]


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


def child(to="dev", key="k1", **kw):
    return {"to": to, "title": "Fix the bug", "brief": "Fix bug 12", "acceptance": "Tests pass",
            "request_key": key, **kw}


@pytest.mark.asyncio
async def test_defaults_and_no_builtin_roles():
    db = AsyncMongoMockClient().test
    crew = await svc.save_crew(db, OWNER, {})
    view = svc.crew_view(crew)
    assert view["members"] == [] and view["schedules"] == []
    assert view["max_concurrent"] == 5 and view["max_depth"] == 3 and view["daily_usd"] == 25.0
    m = svc.clean_members([{"id": "x", "name": "X"}])[0]
    assert m["writes"] == "ask" and m["can_escalate"] is True and m["reviewed_by"] is None


@pytest.mark.parametrize("members,match", [
    ([{"id": "A", "name": "A"}], "ids"),
    ([{"id": "a", "name": "A"}, {"id": "a", "name": "B"}], "share"),
    ([{"id": "a", "name": ""}], "name"),
    ([{"id": "a", "name": "A", "delegates_to": ["a"]}], "itself"),
    ([{"id": "a", "name": "A", "delegates_to": ["ghost"]}], "unknown"),
    ([{"id": "a", "name": "A", "reviewed_by": "a"}], "another member"),
    ([{"id": "a", "name": "A", "writes": "allow"}], "UNATTENDED"),
    ([{"id": "a", "name": "A", "writes": "maybe"}], "ask, deny or allow"),
    ([{"id": f"m{i}", "name": "M"} for i in range(13)], "at most"),
])
def test_member_validation(members, match):
    with pytest.raises(svc.FleetError, match=match):
        svc.clean_members(members)


def test_allow_needs_server_flag(monkeypatch):
    monkeypatch.setenv("LOMA_CREWS_ALLOW_UNATTENDED_WRITES", "true")
    assert svc.clean_members([{"id": "a", "name": "A", "writes": "allow"}])[0]["writes"] == "allow"


@pytest.mark.asyncio
async def test_schedule_validation_and_unknown_agent():
    db, _ = await setup()
    for bad, match in (
        ({"name": "S", "member_id": "ghost", "time": "08:00", "prompt": "p"}, "member"),
        ({"name": "S", "member_id": "lead", "time": "8am", "prompt": "p"}, "time"),
        ({"name": "S", "member_id": "lead", "time": "08:00", "prompt": "p", "days": [7]}, "weekdays"),
        ({"name": "S", "member_id": "lead", "time": "08:00", "prompt": ""}, "prompt"),
    ):
        with pytest.raises(svc.FleetError, match=match):
            await svc.save_crew(db, OWNER, {"schedules": [bad]})
    # Removing a member a schedule points at is refused.
    await svc.save_crew(db, OWNER, {"schedules": [
        {"name": "S", "member_id": "writer", "time": "08:00", "prompt": "p"}]})
    with pytest.raises(svc.FleetError, match="member"):
        await svc.save_crew(db, OWNER, {"members": [{"id": "lead", "name": "Lead"}]})
    with pytest.raises(svc.FleetError, match="not available"):
        await svc.save_crew(db, OWNER, {"members": [{"id": "a", "name": "A", "agent_id": "nope"}], "schedules": []})


@pytest.mark.asyncio
async def test_links_drive_the_tree_review_rework_and_wake():
    db, _ = await setup()
    lid = await give(db)
    assert await started(db) == [lid]
    made = await svc.tool_run(db, OWNER, lid, "create", child())
    again = await svc.tool_run(db, OWNER, lid, "create", child())
    assert made["created"] and not again["created"]
    wid = made["task"]["conversation_id"]
    assert made["task"]["member_name"] == "Dev"
    await run_done(db, lid, "Handed out")
    assert (await fleet(db, lid))["state"] == "waiting"

    assert await started(db) == [wid]
    await run_done(db, wid, "Fixed, PR opened")
    assert (await fleet(db, wid))["state"] == "in_review"
    review = await db.conversations.find_one({"fleet.review_of": wid})
    assert review["fleet"]["member_id"] == "qa" and review["task_status"] is None

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

    # The lead wakes with its children's results, finishes, and the owner gets the reply.
    assert await started(db) == [lid]
    assert (await fleet(db, lid))["wake"]["type"] == "children_done"
    await run_done(db, lid, "All done")
    assert (await fleet(db, lid))["state"] == "done"
    # Only the finished result is delivered: the hand-off turn is not.
    assert await db.notifications.count_documents({"title": "Lead: Ship it"}) == 1


@pytest.mark.asyncio
async def test_schedule_can_also_send_updates_and_reviewed_work_waits_for_accept():
    sched = [{"id": "plan", "name": "Plan", "member_id": "lead", "time": "00:00", "prompt": "Plan",
              "deliver": {"inbox": True, "updates": True}},
             {"id": "fix", "name": "Fix", "member_id": "dev", "time": "00:00", "prompt": "Fix"}]
    db, crew = await setup(schedules=sched)
    plan, _ = await d.start_schedule(db, crew, crew["schedules"][0])
    await started(db)
    await svc.tool_run(db, OWNER, plan["conversation_id"], "create", child())
    await run_done(db, plan["conversation_id"], "Morning brief")
    assert await db.notifications.count_documents({"body": "Morning brief"}) == 1
    fix, _ = await d.start_schedule(db, crew, crew["schedules"][1])
    await db.conversations.update_one({"conversation_id": fix["conversation_id"]},
                                      {"$set": {"fleet.state": "running"}})
    await run_done(db, fix["conversation_id"], "Fixed")
    assert await db.notifications.count_documents({"body": "Fixed"}) == 0  # In review.
    rid = (await db.conversations.find_one({"fleet.review_of": fix["conversation_id"]}))["conversation_id"]
    await db.conversations.update_one({"conversation_id": rid}, {"$set": {"fleet.state": "running"}})
    await svc.tool_run(db, OWNER, rid, "review", {"verdict": "accept", "notes": "ok"})
    await run_done(db, rid)
    assert await db.notifications.count_documents({"body": "Fixed"}) == 1


@pytest.mark.asyncio
async def test_only_linked_members_can_receive_work():
    db, _ = await setup(max_fanout=2, max_depth=2)
    lid = await give(db)
    with pytest.raises(svc.FleetError, match="running"):
        await svc.tool_run(db, OWNER, lid, "create", child())
    await started(db)
    assert [m["id"] for m in (await svc.tool_run(db, OWNER, lid, "members", {}))["can_hand_work_to"]] \
        == ["dev", "writer"]
    with pytest.raises(svc.FleetError, match="can hand work to: dev, writer"):
        await svc.tool_run(db, OWNER, lid, "create", child("qa", "x"))
    dev = (await svc.tool_run(db, OWNER, lid, "create", child("dev", "a")))["task"]["conversation_id"]
    await svc.tool_run(db, OWNER, lid, "create", child("writer", "b"))
    with pytest.raises(svc.FleetError, match="Fan-out"):
        await svc.tool_run(db, OWNER, lid, "create", child("dev", "c"))
    await db.conversations.update_one({"conversation_id": dev}, {"$set": {"fleet.state": "running"}})
    with pytest.raises(svc.FleetError, match="nobody"):
        await svc.tool_run(db, OWNER, dev, "create", child("writer", "d"))
    with pytest.raises(svc.FleetError, match="different task"):
        await svc.tool_run(db, OWNER, lid, "create", {**child("dev", "a"), "brief": "other"})
    with pytest.raises(svc.FleetError, match="not part"):
        await svc.tool_run(db, "intruder@example.com", lid, "create", child("dev", "f"))


@pytest.mark.asyncio
async def test_rewiring_applies_to_running_members_and_depth_cap():
    members = [{"id": "a", "name": "A", "delegates_to": ["b"]},
               {"id": "b", "name": "B", "delegates_to": ["a"]}]  # Cycles are fine; depth bounds them.
    db, _ = await setup(members, max_depth=2)
    aid = await give(db, "a")
    await started(db)
    bid = (await svc.tool_run(db, OWNER, aid, "create", child("b")))["task"]["conversation_id"]
    await db.conversations.update_one({"conversation_id": bid}, {"$set": {"fleet.state": "running"}})
    with pytest.raises(svc.FleetError, match="Depth"):
        await svc.tool_run(db, OWNER, bid, "create", child("a", "k2"))
    await svc.save_crew(db, OWNER, {"members": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
    with pytest.raises(svc.FleetError, match="nobody"):
        await svc.tool_run(db, OWNER, aid, "create", child("b", "k3"))


@pytest.mark.asyncio
async def test_concurrency_dependencies_and_budget():
    db, _ = await setup(team(dev={"reviewed_by": None}), max_concurrent=2)
    lid = await give(db)
    await started(db)
    a = (await svc.tool_run(db, OWNER, lid, "create", child(key="a")))["task"]["conversation_id"]
    b = (await svc.tool_run(db, OWNER, lid, "create", child(key="b", depends_on=[a])))["task"]["conversation_id"]
    c = (await svc.tool_run(db, OWNER, lid, "create", child(key="c")))["task"]["conversation_id"]
    await run_done(db, lid)
    assert await started(db) == [a, c]  # b waits on a.
    assert await started(db) == []  # Both slots busy.
    await run_done(db, a)
    assert (await fleet(db, a))["state"] == "done"  # No reviewer linked: done straight away.
    await run_done(db, c)
    await db.conversations.update_one({"conversation_id": lid}, {"$set": {"cost": {"total_cost_usd": 30.0}}})
    assert await started(db) == []  # Budget reached.
    assert await db.notifications.count_documents({"title": {"$regex": "budget"}}) == 1
    await db.conversations.update_one({"conversation_id": lid}, {"$set": {"cost": {"total_cost_usd": 1}}})
    assert await started(db) == [b]


@pytest.mark.asyncio
async def test_rework_limit_escalates_to_owner_and_resumes():
    db, _ = await setup(max_rework=0)
    wid = await give(db, "dev")
    await started(db)
    await run_done(db, wid)
    rid = (await started(db))[0]
    await svc.tool_run(db, OWNER, rid, "review", {"verdict": "rework", "notes": "Wrong file"})
    await run_done(db, rid)
    w = await fleet(db, wid)
    assert w["state"] == "blocked"
    task = await db.conversations.find_one({"conversation_id": w["escalation"]["human_task_id"]})
    assert task["human_task"]["kind"] == "information" and "QA could not accept" in task["human_task"]["details"]
    await db.conversations.update_one({"_id": task["_id"]}, {"$set": {"human_task.resume_state": "completed"}})
    await d.resolve_escalations(db)
    assert (await fleet(db, wid))["state"] == "in_review"


@pytest.mark.asyncio
async def test_failure_restart_stop_and_removed_member():
    db, _ = await setup()
    lid = await give(db)
    await started(db)
    w1 = (await svc.tool_run(db, OWNER, lid, "create", child(key="a")))["task"]["conversation_id"]
    await run_done(db, lid)
    await started(db)
    await run_done(db, w1, status="failed")
    assert (await fleet(db, w1))["state"] == "failed"
    assert await db.notifications.count_documents({"title": {"$regex": "failed"}}) == 1

    await db.conversations.update_one({"conversation_id": lid}, {"$set": {"fleet.state": "running"}})
    await d.recover(db)
    assert "restart" in (await fleet(db, lid))["error"]

    wid = await give(db, "writer")
    await svc.save_crew(db, OWNER, {"members": [{"id": "lead", "name": "Lead"}], "schedules": []})
    await started(db)
    await d.run_task(db, wid)
    assert "removed" in (await fleet(db, wid))["error"]

    other = await give(db, "lead", "Other")
    await svc.set_state(db, OWNER, "stopped")
    assert (await fleet(db, other))["state"] == "cancelled"
    assert await started(db) == []


@pytest.mark.asyncio
async def test_schedules_weekdays_once_per_day_and_activity():
    sched = [{"id": "standup", "name": "Standup", "member_id": "lead", "time": "08:30",
              "days": [0, 1, 2, 3, 4], "prompt": "Plan the day", "deliver": {"inbox": True}},
             {"id": "wrap", "name": "Wrap-up", "member_id": "qa", "time": "19:00",
              "prompt": "Summarise", "include_activity": True}]
    db, _ = await setup(schedules=sched, timezone="UTC")
    await db.crews.update_one({"owner": OWNER}, {"$set": {"last_run": {}}})
    wed_early = datetime(2030, 1, 2, 8, 0, tzinfo=timezone.utc)
    wed_late = datetime(2030, 1, 2, 20, 0, tzinfo=timezone.utc)
    sat = datetime(2030, 1, 5, 9, 0, tzinfo=timezone.utc)
    await d.run_schedules(db, await svc.get_crew(db, OWNER), wed_early)
    assert await db.conversations.count_documents({}) == 0
    await d.run_schedules(db, await svc.get_crew(db, OWNER), sat)
    assert await db.conversations.count_documents({}) == 0  # Weekdays only.
    for _ in range(3):
        await d.run_schedules(db, await svc.get_crew(db, OWNER), wed_late)
    standup = await db.conversations.find_one({"fleet.schedule_id": "standup"})
    assert standup["fleet"]["member_id"] == "lead" and standup["prompt"] == "Plan the day"
    assert await db.conversations.count_documents({"fleet.schedule_id": "standup"}) == 1
    wrap = await db.conversations.find_one({"fleet.schedule_id": "wrap"})
    assert wrap["fleet"]["member_id"] == "qa" and "Standup" in wrap["prompt"]
    assert wrap["fleet"]["deliver"] == {"inbox": True, "slack_dm": False, "updates": False}
    # On an idle day the activity schedule skips, but Run now always runs.
    db2, _ = await setup(schedules=sched, timezone="UTC")
    await db2.crews.update_one({"owner": OWNER}, {"$set": {"last_run": {"standup": "2030-01-02"}}})
    await d.run_schedules(db2, await svc.get_crew(db2, OWNER), wed_late)
    assert await db2.conversations.count_documents({}) == 0
    crew = await svc.get_crew(db2, OWNER)
    doc, _ = await d.start_schedule(db2, crew, crew["schedules"][1])
    assert "Today's crew activity" in doc["prompt"]


@pytest.mark.asyncio
async def test_turning_on_late_skips_schedules_already_past():
    db = AsyncMongoMockClient().test
    await svc.save_crew(db, OWNER, {"members": [{"id": "a", "name": "A"}], "schedules": [
        {"id": "s1", "name": "S", "member_id": "a", "time": "00:00", "prompt": "p"}]})
    crew = await svc.set_state(db, OWNER, "active")
    assert crew["last_run"]["s1"] == svc.local_day(crew)
    await d.run_schedules(db, crew)
    assert await db.conversations.count_documents({}) == 0


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
    scope = {"agent_name": "Dev", "write_guard": True, "write_policy": "ask"}
    assert agent_scope.scope_is_enforced(scope)
    assert bool(agent_scope.check_tool_call(scope, "Bash", {"command": command})) is blocked
    assert bool(agent_scope.check_codex_command(scope, command)) is blocked


def test_write_guard_mcp_opencode_and_messages():
    scope = {"agent_name": "Dev", "write_guard": True, "write_policy": "ask"}
    assert agent_scope.check_tool_call(scope, "mcp__github__merge_pull_request", {})
    assert agent_scope.check_tool_call(scope, "mcp__claude_ai_Slack__slack_send_message", {})
    assert not agent_scope.check_tool_call(scope, "mcp__github__create_pull_request", {})
    assert not agent_scope.check_tool_call(scope, "Read", {"file_path": "/x"})
    rules = agent_scope.opencode_permission_rules(scope, ["github"])
    assert {"permission": "*_merge_pull_request", "pattern": "*", "action": "deny"} in rules
    assert "approval" in agent_scope.check_write_guard(scope, "Bash", {"command": "gh pr merge 1"})["reason"]
    deny = {**scope, "write_policy": "deny"}
    assert "never" in agent_scope.check_write_guard(deny, "Bash", {"command": "gh pr merge 1"})["reason"]
    assert not agent_scope.check_write_guard({"enforce": True}, "Bash", {"command": "gh pr merge 1"})


@pytest.mark.asyncio
async def test_member_permissions_resolve_to_scope(monkeypatch):
    db, crew = await setup()
    ask = await svc.member_tool_config(db, crew, svc.member(crew, "dev"))
    assert ask["agent_scope"]["write_guard"] and ask["agent_scope"]["write_policy"] == "ask"
    deny = await svc.member_tool_config(db, crew, svc.member(crew, "writer"))
    assert deny["agent_scope"]["write_policy"] == "deny"
    monkeypatch.setenv("LOMA_CREWS_ALLOW_UNATTENDED_WRITES", "true")
    free = await svc.member_tool_config(db, crew, {"id": "x", "name": "X", "writes": "allow"})
    assert "agent_scope" not in free
    monkeypatch.delenv("LOMA_CREWS_ALLOW_UNATTENDED_WRITES")
    # The server gate turned off later: the member falls back to asking.
    back = await svc.member_tool_config(db, crew, {"id": "x", "name": "X", "writes": "allow"})
    assert back["agent_scope"]["write_policy"] == "ask"


@pytest.mark.asyncio
async def test_escalation_permissions():
    db, _ = await setup()
    wid = await give(db, "writer")
    await started(db)
    with pytest.raises(svc.FleetError, match="not allowed to ask"):
        await svc.tool_run(db, OWNER, wid, "escalate", {
            "kind": "information", "title": "t", "details": "d", "request_key": "e"})
    db2, _ = await setup(team(writer={"can_escalate": True}))
    wid2 = await give(db2, "writer")
    await started(db2)
    with pytest.raises(svc.FleetError, match="even with approval"):
        await svc.tool_run(db2, OWNER, wid2, "escalate", {
            "kind": "approval", "title": "t", "details": "d", "request_key": "e"})


@pytest.mark.asyncio
async def test_agent_backed_member_keeps_agent_scope():
    db = AsyncMongoMockClient().test
    await db.agent_identities.insert_one({
        "agent_id": "ag1", "name": "Researcher", "description": "Finds things", "created_by": OWNER,
        "visibility": "private", "status": "active", "tools": ["github"], "skills": ["grain"],
        "identity_prompt": "Cite sources.", "default_model": "claude-x"})
    await svc.save_crew(db, OWNER, {"members": [{"id": "r", "name": "R", "agent_id": "ag1"}]})
    crew = await svc.set_state(db, OWNER, "active")
    cfg = await svc.member_tool_config(db, crew, svc.member(crew, "r"))
    scope = cfg["agent_scope"]
    assert scope["tools_restricted"] and scope["skills"] == ["grain"] and scope["write_guard"]
    assert scope["agent_name"] == "R"


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
    db, _ = await setup()
    lid = await give(db)
    await started(db)
    await db.conversations.update_one({"conversation_id": lid}, {"$set": {"tool_config": {
        "agent_scope": {"agent_name": "Lead", "enforce": True, "tools_restricted": True,
                        "mcp_servers": ["github"], "write_guard": True, "write_policy": "ask"}}}})
    out = await svc.tool_run(db, OWNER, lid, "escalate", {
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
    await db.conversations.update_one({"conversation_id": lid}, {"$set": {"status": "completed"}})
    source = await db.conversations.find_one({"conversation_id": lid})
    await human_task_worker.run_source(db, source, await db.conversations.find_one({"conversation_id": hid}))
    scope = calls[0]["tool_config"]["agent_scope"]
    assert bool(scope.get("write_guard")) is guarded
    assert scope["tools_restricted"] and scope["mcp_servers"] == ["github"]  # Tool limits stay.
    assert "## Your place on the crew" in calls[0]["conversation_context"]


@pytest.mark.asyncio
async def test_run_task_context_comes_from_member_config(monkeypatch):
    import agent.client
    import observability.observer
    db, _ = await setup(notes="Ship the roadmap")
    lid = await give(db)
    await started(db)
    calls = []

    async def stream(**kwargs):
        calls.append(kwargs)
        await db.conversations.update_one({"conversation_id": lid},
                                          {"$set": {"status": "completed", "final_response": "Brief"}})
        yield "completed"
    monkeypatch.setattr(agent.client, "stream_agent", stream)
    monkeypatch.setattr(observability.observer, "ConversationObserver", _Observer)
    await d.run_task(db, lid)
    call = calls[0]
    ctx = call["conversation_context"]
    assert call["user_email"] == OWNER
    assert call["tool_config"]["agent_scope"]["write_guard"] is True
    assert '"Lead"' in ctx and "Be brief." in ctx and "Ship the roadmap" in ctx
    assert "`dev` Dev" in ctx and "`writer` Writer" in ctx and "create --to <member-id>" in ctx
    assert f"--conversation-id {lid}" in ctx
    for hardcoded in ("Chief of Staff", "PM review", "worker"):
        assert hardcoded not in ctx
    assert (await fleet(db, lid))["state"] == "done"


@pytest.mark.asyncio
async def test_runtime_error_reply_retries_once_then_fails():
    db, _ = await setup()
    lid = await give(db)
    await started(db)
    await db.conversations.update_one({"conversation_id": lid}, {"$set": {"fleet.last_prompt": "Plan it"}})
    await run_done(db, lid, "Failed to authenticate: OAuth session expired and could not be refreshed")
    f = await fleet(db, lid)
    assert f["state"] == "queued" and f["retries"] == 1 and f["wake"] == {"type": "retry", "prompt": "Plan it"}
    await started(db)
    await run_done(db, lid, "Failed to authenticate: OAuth session expired and could not be refreshed")
    assert (await fleet(db, lid))["state"] == "failed"


@pytest.mark.asyncio
async def test_mid_run_runtime_error_is_not_retried():
    db, _ = await setup()
    lid = await give(db)
    await started(db)
    await run_done(db, lid, "Sorry, I encountered an OpenCode error: boom")
    f = await fleet(db, lid)
    assert f["state"] == "failed" and "boom" in f["error"]
