"""Crew config and the fleet task tree.

A crew belongs to one user (personal crews only in v1) and is entirely
defined by its owner:

  members    up to 12 agents. Each has a name, description, instructions,
             optional model, and is optionally backed by a Loma agent identity
             (its skills and tools then limit what the member can use).
  links      member.delegates_to: members it may hand work to (a graph, any
             shape). member.reviewed_by: a member that checks its results
             before they count as done (never itself).
  permissions  member.writes: ask | deny | allow for external writes (send
             email/Slack, change calendar, merge PRs). member.can_escalate:
             whether it may ask the owner.
  schedules  start a task for a member at a time of day on chosen weekdays,
             optionally with today's activity and delivered to inbox/Slack
             (the finished result, plus in-between replies if asked).

Every agent run is a taskboard conversation carrying a `fleet` block:

  member_id / member_name   who does it (snapshot of the name at creation)
  kind     task | review
  state    queued -> running -> waiting | in_review | blocked -> accepted | done
           | failed | cancelled
  root_id / parent_id / depth   the tree (root has depth 1)

Agents grow the tree only through tools/fleet.py, which calls the tool_*
functions here. Links, caps and permissions are enforced here, in the
dispatcher and in agent/agent_scope.py, never in the prompt. Every run acts as
the crew owner.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

TERMINAL = frozenset({"accepted", "done", "failed", "cancelled"})
OPEN = ("queued", "running", "waiting", "in_review", "blocked")
WRITE_POLICIES = ("ask", "deny", "allow")
# Where a schedule's reply goes. updates: also send replies from turns that
# handed work out (not only the finished result).
DELIVER_KEYS = ("inbox", "slack_dm", "updates")
MAX_MEMBERS = 12
MAX_SCHEDULES = 20

DEFAULTS = {
    "state": "paused",
    "timezone": "Asia/Kolkata",
    "notes": "",
    "members": [],
    "schedules": [],
    "max_concurrent": 5,
    "daily_usd": 25.0,
    "max_depth": 3,
    "max_fanout": 8,
    "max_rework": 2,
    "max_tasks_per_day": 40,
}
# (min, max) for each numeric setting.
LIMITS = {
    "max_concurrent": (1, 8),
    "daily_usd": (1, 500),
    "max_depth": (1, 5),
    "max_fanout": (1, 12),
    "max_rework": (0, 3),
    "max_tasks_per_day": (1, 100),
}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_SCHEDULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESULT_CHARS = 4000


class FleetError(ValueError):
    pass


def crews_enabled() -> bool:
    return _flag("LOMA_CREWS_ENABLED")


def unattended_writes_allowed() -> bool:
    """Server-level gate for members with writes=allow (off by default)."""
    return _flag("LOMA_CREWS_ALLOW_UNATTENDED_WRITES")


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "on", "yes")


def now():
    return datetime.now(timezone.utc)


def _tz(crew: dict):
    try:
        return ZoneInfo(crew.get("timezone") or DEFAULTS["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULTS["timezone"])


def local_now(crew: dict, at: datetime | None = None) -> datetime:
    return (at or now()).astimezone(_tz(crew))


def local_day(crew: dict, at: datetime | None = None) -> str:
    return local_now(crew, at).date().isoformat()


def day_start(crew: dict, at: datetime | None = None) -> datetime:
    local = local_now(crew, at)
    return datetime.combine(local.date(), time(0), _tz(crew)).astimezone(timezone.utc)


def schedule_due(crew: dict, schedule: dict, at: datetime | None = None) -> bool:
    """Due today (right weekday) and the time of day has passed."""
    local = local_now(crew, at)
    if local.weekday() not in (schedule.get("days") or range(7)):
        return False
    h, m = (int(x) for x in schedule["time"].split(":"))
    return (local.hour, local.minute) >= (h, m)


# ── Crew config ─────────────────────────────────────────────────────────────

def crew_view(crew: dict | None) -> dict:
    merged = json.loads(json.dumps(DEFAULTS))
    if crew:
        for key in DEFAULTS:
            if key in crew:
                merged[key] = crew[key]
        merged["crew_id"] = crew.get("crew_id")
        merged["exists"] = True
    else:
        merged["crew_id"] = None
        merged["exists"] = False
    merged["unattended_writes_allowed"] = unattended_writes_allowed()
    return merged


def member(crew: dict, member_id: str | None) -> dict | None:
    return next((m for m in crew.get("members") or [] if m["id"] == member_id), None)


def _str(data: dict, key: str, limit: int, *, required=False, default="") -> str:
    value = data.get(key, default)
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > limit:
        raise FleetError(f"{key} must be text (max {limit} characters)")
    value = value.strip()
    if required and not value:
        raise FleetError(f"{key} is required")
    return value


def clean_members(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > MAX_MEMBERS:
        raise FleetError(f"members must be a list of at most {MAX_MEMBERS}")
    out, ids = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise FleetError("Each member must be an object")
        mid = item.get("id")
        if not isinstance(mid, str) or not _ID_RE.match(mid):
            raise FleetError("Member ids must be lowercase letters, digits and dashes (max 32)")
        if mid in ids:
            raise FleetError(f"Two members share the id {mid}")
        ids.add(mid)
        writes = item.get("writes", "ask")
        if writes not in WRITE_POLICIES:
            raise FleetError("writes must be ask, deny or allow")
        if writes == "allow" and not unattended_writes_allowed():
            raise FleetError("This server does not allow members to send or merge without your "
                             "approval (LOMA_CREWS_ALLOW_UNATTENDED_WRITES)")
        delegates = item.get("delegates_to") or []
        if not isinstance(delegates, list) or not all(isinstance(x, str) for x in delegates):
            raise FleetError("delegates_to must be a list of member ids")
        can_escalate = item.get("can_escalate", True)
        if not isinstance(can_escalate, bool):
            raise FleetError("can_escalate must be true or false")
        out.append({
            "id": mid,
            "name": _str(item, "name", 60, required=True),
            "description": _str(item, "description", 300),
            "instructions": _str(item, "instructions", 8000),
            "agent_id": _str(item, "agent_id", 100) or None,
            "model": _str(item, "model", 200),
            "delegates_to": list(dict.fromkeys(delegates)),
            "reviewed_by": _str(item, "reviewed_by", 32) or None,
            "writes": writes,
            "can_escalate": can_escalate,
        })
    for m in out:
        bad = [d for d in m["delegates_to"] if d not in ids or d == m["id"]]
        if bad:
            raise FleetError(f"{m['name']} hands work to unknown members (or itself): {', '.join(bad)}")
        if m["reviewed_by"] and (m["reviewed_by"] not in ids or m["reviewed_by"] == m["id"]):
            raise FleetError(f"{m['name']} must be reviewed by another member of the crew")
    return out


def clean_schedules(raw, member_ids: set[str]) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > MAX_SCHEDULES:
        raise FleetError(f"schedules must be a list of at most {MAX_SCHEDULES}")
    out, ids = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise FleetError("Each schedule must be an object")
        sid = item.get("id") or uuid.uuid4().hex[:8]
        if not isinstance(sid, str) or not _SCHEDULE_ID_RE.match(sid):
            raise FleetError("Schedule ids must be lowercase letters, digits and dashes")
        if sid in ids:
            raise FleetError(f"Two schedules share the id {sid}")
        ids.add(sid)
        if item.get("member_id") not in member_ids:
            raise FleetError("Each schedule must start a member of the crew")
        t = item.get("time")
        if not isinstance(t, str) or not _TIME_RE.match(t):
            raise FleetError("Schedule time must look like 08:30")
        days = item.get("days", list(range(7)))
        if (not isinstance(days, list) or not days
                or any(isinstance(d, bool) or d not in range(7) for d in days)):
            raise FleetError("days must list weekdays 0 (Monday) to 6 (Sunday)")
        deliver = item.get("deliver") or {}
        if not isinstance(deliver, dict) or any(k not in DELIVER_KEYS or not isinstance(v, bool)
                                                for k, v in deliver.items()):
            raise FleetError("deliver must map inbox / slack_dm / updates to true/false")
        flags = {}
        for key, default in (("enabled", True), ("include_activity", False)):
            flags[key] = item.get(key, default)
            if not isinstance(flags[key], bool):
                raise FleetError(f"{key} must be true or false")
        out.append({
            "id": sid, "name": _str(item, "name", 80, required=True), "member_id": item["member_id"],
            "time": t, "days": sorted(set(days)), "prompt": _str(item, "prompt", 8000, required=True),
            "deliver": {"inbox": deliver.get("inbox", True), "slack_dm": deliver.get("slack_dm", False),
                        "updates": deliver.get("updates", False)},
            **flags,
        })
    return out


def clean_settings(data: dict, current: dict | None = None) -> dict:
    if not isinstance(data, dict):
        raise FleetError("Expected an object")
    out = {}
    if "timezone" in data:
        try:
            ZoneInfo(str(data["timezone"]))
        except (ZoneInfoNotFoundError, ValueError):
            raise FleetError("Unknown timezone")
        out["timezone"] = str(data["timezone"])
    if "notes" in data:
        out["notes"] = _str(data, "notes", 4000)
    for key, (lo, hi) in LIMITS.items():
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise FleetError(f"{key} must be between {lo} and {hi}")
            out[key] = float(value) if key == "daily_usd" else int(value)
    if "members" in data:
        out["members"] = clean_members(data["members"])
    members = out.get("members", (current or {}).get("members") or [])
    if "schedules" in data or "members" in data:
        raw = data.get("schedules", (current or {}).get("schedules") or [])
        out["schedules"] = clean_schedules(raw, {m["id"] for m in members})
    return out


async def check_agents(db, owner: str, members: list[dict]):
    """Members can only be backed by agent identities the owner can use."""
    from api.agent_identity_routes import resolve_agent_for_chat
    for m in members:
        if m.get("agent_id") and not await resolve_agent_for_chat(db, m["agent_id"], owner):
            raise FleetError(f"{m['name']}: that agent is not available to you")


async def get_crew(db, owner: str) -> dict | None:
    return await db.crews.find_one({"owner": owner})


async def save_crew(db, owner: str, data: dict) -> dict:
    existing = await get_crew(db, owner)
    settings = clean_settings(data, existing)
    if "members" in settings:
        await check_agents(db, owner, settings["members"])
    if existing:
        await db.crews.update_one({"_id": existing["_id"]}, {"$set": {**settings, "updated_at": now()}})
    else:
        doc = {**json.loads(json.dumps(DEFAULTS)), **settings, "_id": "crew:" + owner,
               "crew_id": str(uuid.uuid4()), "owner": owner, "last_run": {},
               "created_at": now(), "updated_at": now()}
        from pymongo.errors import DuplicateKeyError
        try:
            await db.crews.insert_one(doc)
        except DuplicateKeyError:
            return await save_crew(db, owner, data)
    return await get_crew(db, owner)


async def set_state(db, owner: str, state: str) -> dict:
    """active / paused / stopped. Stop also cancels every open task (I9)."""
    if state not in ("active", "paused", "stopped"):
        raise FleetError("Unknown crew state")
    crew = await get_crew(db, owner)
    if not crew:
        raise FleetError("Set up your crew first")
    fields = {"state": state, "updated_at": now()}
    if state == "active" and crew.get("state") != "active":
        # Turning the crew on never fires today's schedules that are already past.
        day = local_day(crew)
        for s in crew.get("schedules") or []:
            if schedule_due(crew, s):
                fields[f"last_run.{s['id']}"] = day
    await db.crews.update_one({"_id": crew["_id"]}, {"$set": fields})
    if state == "stopped":
        await cancel_open(db, crew["crew_id"], "Crew stopped")
    return await get_crew(db, owner)


async def cancel_open(db, crew_id: str, reason: str) -> list[str]:
    docs = await db.conversations.find(
        {"fleet.crew_id": crew_id, "fleet.state": {"$in": list(OPEN)}},
        {"conversation_id": 1, "fleet.state": 1}).to_list(1000)
    ids = [d["conversation_id"] for d in docs]
    if ids:
        await db.conversations.update_many(
            {"conversation_id": {"$in": ids}, "fleet.state": {"$in": list(OPEN)}},
            {"$set": {"fleet.state": "cancelled", "fleet.error": reason, "fleet.updated_at": now()}})
    from agent.active_streams import get_stream, request_pending_stop
    for d in docs:
        if d["fleet"]["state"] == "running":
            stream = await get_stream(d["conversation_id"])
            try:
                if stream:
                    await stream.interrupt()
                else:
                    await request_pending_stop(d["conversation_id"])
            except Exception:
                pass
    return ids


async def spend_today(db, crew: dict) -> float:
    rows = await db.conversations.aggregate([
        {"$match": {"fleet.crew_id": crew["crew_id"], "fleet.created_at": {"$gte": day_start(crew)}}},
        {"$group": {"_id": None, "usd": {"$sum": {"$ifNull": ["$cost.total_cost_usd", 0]}}}},
    ]).to_list(1)
    return round(float(rows[0]["usd"]) if rows else 0.0, 4)


async def created_today(db, crew: dict) -> int:
    return await db.conversations.count_documents(
        {"fleet.crew_id": crew["crew_id"], "fleet.created_at": {"$gte": day_start(crew)}})


# ── Permissions ─────────────────────────────────────────────────────────────

async def member_tool_config(db, crew: dict, m: dict, base: dict | None = None) -> dict:
    """Tool scope for one member's run, resolved fresh so edits apply at once.

    A member backed by an agent identity gets that agent's tool and skill
    limits. Unless the member may write unattended, the write guard blocks
    sends and merges (agent/agent_scope.py); with writes=ask an owner approval
    lifts it for one turn (api/human_task_worker.py).
    """
    out = {k: v for k, v in (base or {}).items() if k != "agent_scope"}
    scope: dict = {"agent_name": m["name"]}
    if m.get("agent_id"):
        from agent.agent_scope import build_agent_scope
        from api.agent_identity_routes import resolve_agent_for_chat
        agent = await resolve_agent_for_chat(db, m["agent_id"], crew["owner"])
        if not agent:
            raise FleetError(f"The agent behind {m['name']} is no longer available")
        scope = await build_agent_scope(db, agent)
        scope["agent_name"] = m["name"]
        if not scope.get("enforce"):
            scope.update(tools_restricted=False, skills_restricted=False)
    writes = m.get("writes", "ask")
    if writes == "allow" and not unattended_writes_allowed():
        writes = "ask"  # The server gate was turned off after the member was saved.
    if writes != "allow":
        scope.update(write_guard=True, write_policy=writes)
    if scope.get("write_guard") or scope.get("enforce"):
        out["agent_scope"] = scope
    return out


# ── Tasks ───────────────────────────────────────────────────────────────────

def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FleetError(f"{name} must be non-empty text (max {limit} characters)")
    return value.strip()


async def create_task(db, crew: dict, *, member_id: str, kind: str, title: str, brief: str,
                      acceptance: str = "", parent: dict | None = None, request_key: str,
                      depends_on: list[str] | None = None, on_board: bool = True,
                      review_of: str | None = None, schedule_id: str | None = None,
                      deliver: dict | None = None) -> tuple[dict, bool]:
    """Create a queued fleet task. Idempotent per (crew, parent, request_key) (I8)."""
    from api.task_service import create_staged_task
    m = member(crew, member_id)
    if not m:
        raise FleetError(f"No crew member with id {member_id}")
    owner = crew["owner"]
    pf = (parent or {}).get("fleet") or {}
    parent_id = parent["conversation_id"] if parent else None
    identifier = hashlib.sha256(json.dumps([crew["crew_id"], parent_id, request_key]).encode()).hexdigest()
    fleet = {
        "crew_id": crew["crew_id"], "owner": owner, "member_id": m["id"], "member_name": m["name"],
        "kind": kind, "parent_id": parent_id, "root_id": pf.get("root_id") or parent_id,
        "depth": (pf.get("depth") or 0) + 1, "brief": brief, "acceptance": acceptance,
        "request_key": request_key, "depends_on": depends_on or [], "review_of": review_of,
        "schedule_id": schedule_id, "deliver": deliver or None,
        "state": "queued", "rework_round": 0, "reviews": [], "verdict": None,
        "result": "", "error": None, "delivered_turns": 0,
        "created_at": now(), "updated_at": now(),
    }
    fields = {"fleet": fleet, "tool_config": {}}
    if not on_board:
        fields.update({"task_status": None, "task_lane": None})
    doc, created = await create_staged_task(
        db, owner, brief, title=title[:200], model=m.get("model") or "",
        metadata={"source": "dashboard", "crew_id": crew["crew_id"]},
        document_id="fleet:" + identifier, fields=fields)
    if created and not parent_id:
        await db.conversations.update_one({"_id": doc["_id"]},
                                          {"$set": {"fleet.root_id": doc["conversation_id"]}})
        doc["fleet"]["root_id"] = doc["conversation_id"]
    return doc, created


async def owner_task(db, owner: str, data: dict) -> dict:
    """The owner hands a task straight to any member."""
    crew = await get_crew(db, owner)
    if not crew or crew.get("state") != "active":
        raise FleetError("Turn the crew on first")
    m = member(crew, data.get("member_id"))
    if not m:
        raise FleetError("Pick a member of your crew")
    title = _text(data.get("title"), "title", 200)
    brief = _text(data.get("brief"), "brief", 10000)
    acceptance = _str(data, "acceptance", 4000)
    doc, _ = await create_task(db, crew, member_id=m["id"], kind="task", title=title, brief=brief,
                               acceptance=acceptance, request_key="owner:" + uuid.uuid4().hex,
                               deliver={"inbox": True, "slack_dm": False, "updates": False})
    return doc


async def _caller(db, owner: str, cid: str) -> tuple[dict, dict]:
    doc = await db.conversations.find_one({"conversation_id": cid, "deleted": {"$ne": True}})
    if not doc or not doc.get("fleet") or doc["fleet"].get("owner") != owner:
        raise FleetError("This conversation is not part of your crew")
    crew = await db.crews.find_one({"crew_id": doc["fleet"]["crew_id"], "owner": owner})
    if not crew:
        raise FleetError("Crew not found")
    return doc, crew


def task_view(doc: dict) -> dict:
    f = doc.get("fleet") or {}
    return {
        "conversation_id": doc["conversation_id"], "title": doc.get("title"),
        "member_id": f.get("member_id"), "member_name": f.get("member_name"),
        "kind": f.get("kind"), "state": f.get("state"),
        "depth": f.get("depth"), "parent_id": f.get("parent_id"), "root_id": f.get("root_id"),
        "depends_on": f.get("depends_on") or [], "review_of": f.get("review_of"),
        "schedule_id": f.get("schedule_id"),
        "rework_round": f.get("rework_round", 0), "verdict": f.get("verdict"),
        "reviews": f.get("reviews") or [], "error": f.get("error"),
        "result": (f.get("result") or "")[:RESULT_CHARS],
        "cost_usd": round(float(((doc.get("cost") or {}).get("total_cost_usd")) or 0), 4),
        "status": doc.get("status"), "pull_requests": doc.get("task_prs") or [],
        "created_at": f.get("created_at"), "updated_at": f.get("updated_at"),
    }


def member_card(crew: dict, m: dict) -> dict:
    reviewer = member(crew, m.get("reviewed_by"))
    return {"id": m["id"], "name": m["name"], "description": m.get("description") or "",
            "reviewed_by": reviewer["name"] if reviewer else None}


async def tool_members(db, owner: str, cid: str) -> dict:
    doc, crew = await _caller(db, owner, cid)
    me = member(crew, doc["fleet"]["member_id"])
    targets = [member(crew, i) for i in (me or {}).get("delegates_to") or []]
    return {"you": doc["fleet"]["member_id"],
            "can_hand_work_to": [member_card(crew, m) for m in targets if m]}


async def tool_create(db, owner: str, cid: str, args: dict) -> dict:
    parent, crew = await _caller(db, owner, cid)
    pf = parent["fleet"]
    if crew.get("state") != "active":
        raise FleetError("The crew is paused or stopped; no new tasks")
    if pf["state"] != "running":
        raise FleetError("Only a running task can create child tasks")
    if pf["kind"] != "task":
        raise FleetError("Reviews cannot create tasks")
    me = member(crew, pf["member_id"])
    if not me:
        raise FleetError("You were removed from the crew; you cannot hand out work")
    target = args.get("to")
    if target not in me.get("delegates_to") or not member(crew, target):
        allowed = ", ".join(me.get("delegates_to") or []) or "nobody"
        raise FleetError(f"{me['name']} can hand work to: {allowed}")
    view = crew_view(crew)
    if pf["depth"] + 1 > view["max_depth"]:
        raise FleetError(f"Depth limit reached ({view['max_depth']})")
    title = _text(args.get("title"), "title", 200)
    brief = _text(args.get("brief"), "brief", 10000)
    acceptance = _text(args.get("acceptance"), "acceptance", 4000)
    key = _text(args.get("request_key"), "request_key", 200)
    depends = [d.strip() for d in (args.get("depends_on") or []) if d and d.strip()]
    if depends:
        siblings = await db.conversations.count_documents(
            {"conversation_id": {"$in": depends}, "fleet.parent_id": cid})
        if siblings != len(set(depends)):
            raise FleetError("depends_on must list sibling tasks created by this task")
    existing = await db.conversations.find_one(
        {"fleet.crew_id": crew["crew_id"], "fleet.parent_id": cid, "fleet.request_key": key})
    if not existing:
        children = await db.conversations.count_documents(
            {"fleet.parent_id": cid, "fleet.kind": "task"})
        if children >= view["max_fanout"]:
            raise FleetError(f"Fan-out limit reached ({view['max_fanout']} child tasks)")
        if await created_today(db, crew) >= view["max_tasks_per_day"]:
            raise FleetError(f"Daily task limit reached ({view['max_tasks_per_day']})")
    doc, created = await create_task(db, crew, member_id=target, kind="task", title=title,
                                     brief=brief, acceptance=acceptance, parent=parent,
                                     request_key=key, depends_on=depends)
    if doc["fleet"]["brief"] != brief:
        raise FleetError("request_key already used for a different task")
    return {"task": task_view(doc), "created": created}


async def tool_children(db, owner: str, cid: str) -> dict:
    await _caller(db, owner, cid)
    docs = await db.conversations.find({"fleet.parent_id": cid}).sort("fleet.created_at", 1).to_list(100)
    return {"children": [task_view(d) for d in docs]}


async def tool_status(db, owner: str, cid: str) -> dict:
    doc, crew = await _caller(db, owner, cid)
    view = crew_view(crew)
    return {"task": task_view(doc), "crew": {
        "state": crew.get("state"), "spend_today_usd": await spend_today(db, crew),
        "daily_usd": view["daily_usd"], "tasks_today": await created_today(db, crew),
        "max_tasks_per_day": view["max_tasks_per_day"], "max_depth": view["max_depth"],
        "max_fanout": view["max_fanout"]}}


async def tool_review(db, owner: str, cid: str, args: dict) -> dict:
    doc, _ = await _caller(db, owner, cid)
    f = doc["fleet"]
    if f["kind"] != "review" or f["state"] != "running":
        raise FleetError("Only a running review can record a verdict")
    verdict = args.get("verdict")
    if verdict not in ("accept", "rework", "escalate"):
        raise FleetError("verdict must be accept, rework or escalate")
    notes = _text(args.get("notes"), "notes", 4000)
    await db.conversations.update_one(
        {"_id": doc["_id"], "fleet.state": "running"},
        {"$set": {"fleet.verdict": {"verdict": verdict, "notes": notes, "at": now()},
                  "fleet.updated_at": now()}})
    return {"recorded": verdict}


async def tool_escalate(db, owner: str, cid: str, args: dict) -> dict:
    """Ask the owner for an approval or information through a human task."""
    from api import human_tasks
    doc, crew = await _caller(db, owner, cid)
    if doc["fleet"]["state"] != "running":
        raise FleetError("Only a running task can escalate")
    me = member(crew, doc["fleet"]["member_id"]) or {}
    if not me.get("can_escalate", True):
        raise FleetError("This member is not allowed to ask the owner. Report the blocker in your result.")
    kind = args.get("kind")
    if kind not in ("approval", "information"):
        raise FleetError("kind must be approval or information")
    if kind == "approval" and me.get("writes") == "deny":
        raise FleetError("This member may not send or merge, even with approval. "
                         "Put the draft in your result instead.")
    title = _text(args.get("title"), "title", 200)
    details = _text(args.get("details"), "details", 10000)
    key = _text(args.get("request_key"), "request_key", 200)
    task, created = await human_tasks.create(db, owner, {
        "source_conversation_id": cid, "assignee": owner, "kind": kind, "title": title,
        "details": details, "request_key": "fleet:" + key})
    await db.conversations.update_one(
        {"_id": doc["_id"]},
        {"$set": {"fleet.escalation": {"human_task_id": task["conversation_id"], "kind": kind},
                  "fleet.updated_at": now()}})
    return {"human_task_id": task["conversation_id"], "created": created,
            "note": "Finish your turn now and say you are waiting for the owner's answer."}


TOOL_COMMANDS = {
    "members": tool_members, "create": tool_create, "children": tool_children,
    "status": tool_status, "review": tool_review, "escalate": tool_escalate,
}
NO_ARGS = ("members", "children", "status")


async def tool_run(db, owner: str, cid: str, command: str, args: dict) -> dict:
    fn = TOOL_COMMANDS.get(command)
    if not fn:
        raise FleetError(f"Unknown command {command}")
    try:
        if command in NO_ARGS:
            return await fn(db, owner, cid)
        return await fn(db, owner, cid, args)
    except web.HTTPException as e:
        raise FleetError(e.text or e.reason)


# ── Fleet view ──────────────────────────────────────────────────────────────

async def tree(db, owner: str, days: int = 3) -> list[dict]:
    crew = await get_crew(db, owner)
    if not crew:
        return []
    since = day_start(crew) - timedelta(days=max(0, days - 1))
    docs = await db.conversations.find(
        {"fleet.crew_id": crew["crew_id"], "fleet.owner": owner, "deleted": {"$ne": True},
         "fleet.created_at": {"$gte": since}},
        {"conversation_id": 1, "title": 1, "fleet": 1, "cost": 1, "status": 1, "task_prs": 1},
    ).sort("fleet.created_at", 1).to_list(1000)
    nodes = {d["conversation_id"]: {**task_view(d), "children": []} for d in docs}
    roots = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"]) if node["parent_id"] else None
        (parent["children"] if parent else roots).append(node)
    roots.reverse()
    return roots
