"""Human handoffs on the existing conversation/taskboard, not autonomous tasks.

Only the signed dashboard route may record a decision. Personal agent tools can
create, inspect and reassign requests but cannot approve them.
"""
import hashlib
import json
from datetime import datetime, timezone

from aiohttp import web
from api.task_service import create_staged_task


def now():
    return datetime.now(timezone.utc)


def text(value, name, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a non-empty string (max {limit} characters)")
    return value.strip()


async def active_user(db, email):
    user = await db.users.find_one({"email": email})
    if not user or user.get("deleted") or user.get("status", "active") != "active":
        raise web.HTTPForbidden(text="An active Loma account is required")
    return user


def source_owner(source):
    metadata = source.get("metadata") or {}
    return metadata.get("run_as") or metadata.get("user_name")


async def source_for(db, actor, cid):
    source = await db.conversations.find_one({"conversation_id": cid, "deleted": {"$ne": True}})
    # Read access to shared conversations is not authority to resume them.
    if not source or source_owner(source) != actor or source.get("human_task"):
        raise web.HTTPNotFound(text="Source conversation not found")
    return source


async def get(db, actor, cid):
    await active_user(db, actor)
    cid = text(cid, "task ID", 100)
    doc = await db.conversations.find_one({"conversation_id": cid, "deleted": {"$ne": True}})
    h = (doc or {}).get("human_task") or {}
    if not h or actor not in (h["requested_by"], doc["metadata"]["user_name"]):
        raise web.HTTPNotFound(text="Human task not found")
    return doc


def view(doc):
    return {"conversation_id": doc["conversation_id"], "title": doc["title"],
            "assignee": doc["metadata"]["user_name"], **doc["human_task"]}


async def create(db, actor, data):
    await active_user(db, actor)
    cid = text(data.get("source_conversation_id"), "source_conversation_id", 100)
    await source_for(db, actor, cid)
    assignee = text(data.get("assignee"), "assignee", 254).lower()
    await active_user(db, assignee)
    kind = data.get("kind")
    if kind not in ("approval", "information"):
        raise ValueError("kind must be approval or information")
    title = text(data.get("title"), "title", 200)
    details = text(data.get("details"), "details")
    key = text(data.get("request_key"), "request_key", 200)
    ticket = data.get("ticket_url") or ""
    if ticket:
        from urllib.parse import urlparse
        parsed = urlparse(text(ticket, "ticket_url", 2000))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("ticket_url must be an HTTPS URL without credentials")
    # Stable across retries, scoped to the originating user and conversation.
    identifier = hashlib.sha256(json.dumps([actor, cid, key]).encode()).hexdigest()
    spec = {"kind": kind, "title": title, "details": details, "ticket_url": ticket}
    fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    h = {**spec, "source_conversation_id": cid, "requested_by": actor,
         "request_key": key, "fingerprint": fingerprint, "state": "pending",
         "version": 1, "decision": None, "response": None, "responded_by": None,
         "responded_at": None, "resume_state": "waiting", "assignment_notice": "pending"}
    doc, created = await create_staged_task(
        db, assignee, details, title=title, document_id="human-task:" + identifier,
        fields={"human_task": h, "task_status": "active", "status": "completed"})
    if doc.get("deleted") or doc["human_task"]["fingerprint"] != fingerprint:
        raise web.HTTPConflict(text="Request key already used; use a new key for a different request")
    return doc, created


async def assign(db, actor, cid, assignee, version):
    if type(version) is not int or version < 1:
        raise ValueError("version must be a positive integer")
    doc = await get(db, actor, cid)
    h = doc["human_task"]
    if actor != h["requested_by"]:
        raise web.HTTPForbidden(text="Only the requester can reassign")
    assignee = text(assignee, "assignee", 254).lower()
    await active_user(db, assignee)
    from api.task_routes import _get_board_config_for
    board = await _get_board_config_for(db, assignee)
    result = await db.conversations.update_one(
        {"_id": doc["_id"], "human_task.state": "pending", "human_task.version": version},
        {"$set": {"metadata.user_name": assignee, "task_lane": board["lanes"][0]["id"],
                  "human_task.assignment_notice": "pending"}, "$inc": {"human_task.version": 1}})
    if not result.modified_count:
        raise web.HTTPConflict(text="Task changed or already answered; refresh")
    return await get(db, actor, cid)


async def respond(db, actor, cid, data):
    if type(data.get("version")) is not int or data["version"] < 1:
        raise ValueError("version must be a positive integer")
    doc = await get(db, actor, cid)
    h = doc["human_task"]
    if actor != doc["metadata"]["user_name"]:
        raise web.HTTPForbidden(text="Only the assigned human can respond")
    decision = data.get("decision")
    allowed = ("approve", "reject", "provide_information") if h["kind"] == "approval" else ("provide_information",)
    if decision not in allowed:
        raise ValueError("Invalid decision for this request")
    response = text(data.get("response"), "response")
    # Decisions are immutable and tied to the exact request and assignment version.
    if h["state"] == "answered":
        if h["decision"] == decision and h["response"] == response and h["responded_by"] == actor:
            return doc
        raise web.HTTPConflict(text="A different decision is already recorded")
    result = await db.conversations.update_one(
        {"_id": doc["_id"], "human_task.state": "pending", "human_task.version": data.get("version"),
         "metadata.user_name": actor},
        {"$set": {"human_task.state": "answered", "human_task.decision": decision,
                  "human_task.response": response, "human_task.responded_by": actor,
                  "human_task.responded_at": now(), "human_task.resume_state": "queued",
                  "task_status": "done", "task_done_at": now()},
         "$inc": {"human_task.version": 1}})
    if not result.modified_count:
        raise web.HTTPConflict(text="Task changed; refresh before responding")
    return await get(db, actor, cid)
