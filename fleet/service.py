"""Crew config and the fleet task tree.

A crew belongs to one user (personal crews only in v1). Every agent in it is
a taskboard conversation carrying a `fleet` block:

  role     cos | pm | worker | reviewer
  kind     plan | report | task | review
  state    queued -> running -> waiting | in_review | blocked -> accepted | done
           | failed | cancelled
  root_id / parent_id / depth   the tree (root has depth 1)

Agents grow the tree only through tools/fleet.py, which calls the tool_*
functions here. Caps are enforced here and in the dispatcher, never in the
prompt. Every run acts as the crew owner and keeps the parent's tool scope.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

ROLES = ("cos", "pm", "worker", "reviewer")
# Who may create whom. Reviewers and workers never grow the tree.
CHILD_ROLES = {"cos": ("pm", "worker"), "pm": ("worker",)}
TERMINAL = frozenset({"accepted", "done", "failed", "cancelled"})
OPEN = ("queued", "running", "waiting", "in_review", "blocked")
SOURCES = ("gmail", "calendar", "board", "slack", "linear")

DEFAULTS = {
    "state": "paused",
    "timezone": "Asia/Kolkata",
    "morning_time": "08:30",
    "evening_time": "19:00",
    "focus": "",
    # Gmail is read by default; nothing is ever sent without approval.
    "sources": {"gmail": True, "calendar": True, "board": True, "slack": False, "linear": False},
    "reports": {"inbox": True, "slack_dm": True},
    "max_concurrent": 5,
    "daily_usd": 25.0,
    "max_depth": 3,
    "max_fanout": 8,
    "max_rework": 2,
    "max_tasks_per_day": 40,
    "worker_model": "",
    "review_model": "",
}
# (min, max) for each numeric setting.
LIMITS = {
    "max_concurrent": (1, 8),
    "daily_usd": (1, 500),
    "max_depth": (2, 3),
    "max_fanout": (1, 12),
    "max_rework": (0, 3),
    "max_tasks_per_day": (1, 100),
}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RESULT_CHARS = 4000


class FleetError(ValueError):
    pass


def crews_enabled() -> bool:
    return os.getenv("LOMA_CREWS_ENABLED", "false").strip().lower() in ("1", "true", "on", "yes")


def now():
    return datetime.now(timezone.utc)


def _tz(crew: dict):
    try:
        return ZoneInfo(crew.get("timezone") or DEFAULTS["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULTS["timezone"])


def local_day(crew: dict, at: datetime | None = None) -> str:
    return (at or now()).astimezone(_tz(crew)).date().isoformat()


def day_start(crew: dict, at: datetime | None = None) -> datetime:
    local = (at or now()).astimezone(_tz(crew))
    return datetime.combine(local.date(), time(0), _tz(crew)).astimezone(timezone.utc)


def is_due(crew: dict, hhmm: str, at: datetime | None = None) -> bool:
    local = (at or now()).astimezone(_tz(crew))
    h, m = (int(x) for x in hhmm.split(":"))
    return (local.hour, local.minute) >= (h, m)


# ── Crew config ─────────────────────────────────────────────────────────────

def crew_view(crew: dict | None) -> dict:
    merged = json.loads(json.dumps(DEFAULTS))
    if crew:
        for key in DEFAULTS:
            if key in crew:
                if isinstance(DEFAULTS[key], dict):
                    merged[key].update(crew.get(key) or {})
                else:
                    merged[key] = crew[key]
        merged["crew_id"] = crew.get("crew_id")
        merged["exists"] = True
    else:
        merged["crew_id"] = None
        merged["exists"] = False
    return merged


def clean_settings(data: dict) -> dict:
    if not isinstance(data, dict):
        raise FleetError("Expected an object")
    out = {}
    for key in ("morning_time", "evening_time"):
        if key in data:
            if not isinstance(data[key], str) or not _TIME_RE.match(data[key]):
                raise FleetError(f"{key} must look like 08:30")
            out[key] = data[key]
    if "timezone" in data:
        try:
            ZoneInfo(str(data["timezone"]))
        except (ZoneInfoNotFoundError, ValueError):
            raise FleetError("Unknown timezone")
        out["timezone"] = str(data["timezone"])
    if "focus" in data:
        if not isinstance(data["focus"], str) or len(data["focus"]) > 4000:
            raise FleetError("focus must be text (max 4000 characters)")
        out["focus"] = data["focus"].strip()
    for key, (lo, hi) in LIMITS.items():
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise FleetError(f"{key} must be between {lo} and {hi}")
            out[key] = float(value) if key == "daily_usd" else int(value)
    if "sources" in data:
        src = data["sources"]
        if not isinstance(src, dict) or any(k not in SOURCES or not isinstance(v, bool) for k, v in src.items()):
            raise FleetError(f"sources must map {', '.join(SOURCES)} to true/false")
        out["sources"] = src
    if "reports" in data:
        rep = data["reports"]
        if not isinstance(rep, dict) or any(k not in ("inbox", "slack_dm") or not isinstance(v, bool)
                                            for k, v in rep.items()):
            raise FleetError("reports must map inbox / slack_dm to true/false")
        out["reports"] = rep
    for key in ("worker_model", "review_model"):
        if key in data:
            if not isinstance(data[key], str) or len(data[key]) > 200:
                raise FleetError(f"{key} must be a model id")
            out[key] = data[key].strip()
    return out


async def get_crew(db, owner: str) -> dict | None:
    return await db.crews.find_one({"owner": owner})


async def save_crew(db, owner: str, data: dict) -> dict:
    settings = clean_settings(data)
    existing = await get_crew(db, owner)
    if existing:
        for key in ("sources", "reports"):
            if key in settings:
                settings[key] = {**crew_view(existing)[key], **settings[key]}
        await db.crews.update_one({"_id": existing["_id"]}, {"$set": {**settings, "updated_at": now()}})
    else:
        doc = {**json.loads(json.dumps(DEFAULTS)), **settings, "_id": "crew:" + owner,
               "crew_id": str(uuid.uuid4()), "owner": owner, "created_at": now(), "updated_at": now()}
        for key in ("sources", "reports"):
            doc[key] = {**DEFAULTS[key], **settings.get(key, {})}
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
        # Turning the crew on never fires today's rituals that are already past.
        view, day = crew_view(crew), local_day(crew)
        for ritual in ("morning", "evening"):
            if is_due(crew, view[f"{ritual}_time"]):
                fields[f"last_{ritual}_day"] = day
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


# ── Tasks ───────────────────────────────────────────────────────────────────

# Bash and MCP calls a crew run may never make on its own (I4). The scope
# check in agent/agent_scope.py enforces this on every runtime.
WRITE_GUARD = {"write_guard": True, "agent_name": "Your crew"}


def fleet_tool_config(extra: dict | None = None) -> dict:
    return {**(extra or {}), "agent_scope": dict(WRITE_GUARD)}


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FleetError(f"{name} must be non-empty text (max {limit} characters)")
    return value.strip()


async def create_task(db, crew: dict, *, role: str, kind: str, title: str, brief: str,
                      acceptance: str = "", parent: dict | None = None, request_key: str,
                      depends_on: list[str] | None = None, on_board: bool = True,
                      review_of: str | None = None) -> tuple[dict, bool]:
    """Create a queued fleet task. Idempotent per (crew, parent, request_key) (I8)."""
    from api.task_service import create_staged_task
    owner = crew["owner"]
    pf = (parent or {}).get("fleet") or {}
    parent_id = parent["conversation_id"] if parent else None
    identifier = hashlib.sha256(json.dumps([crew["crew_id"], parent_id, request_key]).encode()).hexdigest()
    fleet = {
        "crew_id": crew["crew_id"], "owner": owner, "role": role, "kind": kind,
        "parent_id": parent_id, "root_id": pf.get("root_id") or parent_id,
        "depth": (pf.get("depth") or 0) + 1, "brief": brief, "acceptance": acceptance,
        "request_key": request_key, "depends_on": depends_on or [], "review_of": review_of,
        "state": "queued", "rework_round": 0, "reviews": [], "verdict": None,
        "result": "", "error": None, "notified": False,
        "created_at": now(), "updated_at": now(),
    }
    model = crew.get("review_model") if role == "reviewer" else crew.get("worker_model")
    fields = {"fleet": fleet, "tool_config": fleet_tool_config()}
    if not on_board:
        fields.update({"task_status": None, "task_lane": None})
    doc, created = await create_staged_task(
        db, owner, brief, title=title[:200], model=model or "",
        metadata={"source": "dashboard", "crew_id": crew["crew_id"]},
        document_id="fleet:" + identifier, fields=fields)
    if created and not parent_id:
        await db.conversations.update_one({"_id": doc["_id"]},
                                          {"$set": {"fleet.root_id": doc["conversation_id"]}})
        doc["fleet"]["root_id"] = doc["conversation_id"]
    return doc, created


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
        "role": f.get("role"), "kind": f.get("kind"), "state": f.get("state"),
        "depth": f.get("depth"), "parent_id": f.get("parent_id"), "root_id": f.get("root_id"),
        "depends_on": f.get("depends_on") or [], "review_of": f.get("review_of"),
        "rework_round": f.get("rework_round", 0), "verdict": f.get("verdict"),
        "reviews": f.get("reviews") or [], "error": f.get("error"),
        "result": (f.get("result") or "")[:RESULT_CHARS],
        "cost_usd": round(float(((doc.get("cost") or {}).get("total_cost_usd")) or 0), 4),
        "status": doc.get("status"), "pull_requests": doc.get("task_prs") or [],
        "created_at": f.get("created_at"), "updated_at": f.get("updated_at"),
    }


async def tool_create(db, owner: str, cid: str, args: dict) -> dict:
    parent, crew = await _caller(db, owner, cid)
    pf = parent["fleet"]
    if crew.get("state") != "active":
        raise FleetError("The crew is paused or stopped; no new tasks")
    if pf["state"] != "running":
        raise FleetError("Only a running task can create child tasks")
    if pf["kind"] not in ("plan", "task"):
        raise FleetError("Reports and reviews cannot create tasks")
    role = args.get("role")
    allowed = CHILD_ROLES.get(pf["role"], ())
    if role not in allowed:
        raise FleetError(f"A {pf['role']} can create: {', '.join(allowed) or 'nothing'}")
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
            {"fleet.parent_id": cid, "fleet.role": {"$ne": "reviewer"}})
        if children >= view["max_fanout"]:
            raise FleetError(f"Fan-out limit reached ({view['max_fanout']} child tasks)")
        if await created_today(db, crew) >= view["max_tasks_per_day"]:
            raise FleetError(f"Daily task limit reached ({view['max_tasks_per_day']})")
    doc, created = await create_task(db, crew, role=role, kind="task", title=title, brief=brief,
                                     acceptance=acceptance, parent=parent, request_key=key,
                                     depends_on=depends)
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
    if f["role"] != "reviewer" or f["state"] != "running":
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
    doc, _ = await _caller(db, owner, cid)
    if doc["fleet"]["state"] != "running":
        raise FleetError("Only a running task can escalate")
    kind = args.get("kind")
    if kind not in ("approval", "information"):
        raise FleetError("kind must be approval or information")
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
    "create": tool_create, "children": tool_children, "status": tool_status,
    "review": tool_review, "escalate": tool_escalate,
}


async def tool_run(db, owner: str, cid: str, command: str, args: dict) -> dict:
    fn = TOOL_COMMANDS.get(command)
    if not fn:
        raise FleetError(f"Unknown command {command}")
    try:
        if command in ("children", "status"):
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
