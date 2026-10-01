"""Offboarding requests (one per proposed plan) and their audit trail.

Every function takes the database handle so both the backend (get_db()) and the CLI
tool (its own motor client) share one implementation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

REQUESTS = "offboarding_requests"
AUDIT = "offboarding_audit"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db) -> None:
    await db[REQUESTS].create_index("request_id", unique=True)
    await db[REQUESTS].create_index("plan_id")
    await db[AUDIT].create_index([("created_at", -1)])
    await db[AUDIT].create_index("request_id")


async def audit(db, request_id: str, event: str, actor: str | None, **details: Any) -> None:
    await db[AUDIT].insert_one({
        "request_id": request_id,
        "event": event,
        "actor": actor,
        "details": details,
        "created_at": _now(),
    })


async def create_request(db, *, plan_id: str, requested_by: str, channel_id: str, thread_ts: str | None,
                         summary: dict[str, Any], expires_at: str | None) -> dict[str, Any]:
    doc = {
        "request_id": uuid.uuid4().hex,
        "plan_id": plan_id,
        "status": "pending",
        "requested_by": requested_by.lower(),
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "message_ts": None,
        "summary": summary,
        "plan_expires_at": expires_at,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db[REQUESTS].insert_one(doc)
    await audit(db, doc["request_id"], "proposed", requested_by.lower(), plan_id=plan_id)
    return doc


async def get_request(db, request_id: str) -> dict[str, Any] | None:
    return await db[REQUESTS].find_one({"request_id": request_id})


async def set_message_ts(db, request_id: str, message_ts: str) -> None:
    await db[REQUESTS].update_one({"request_id": request_id}, {"$set": {"message_ts": message_ts, "updated_at": _now()}})


async def transition(db, request_id: str, from_statuses: list[str], to_status: str, **fields: Any) -> dict[str, Any] | None:
    """Compare-and-set on status, so a double click can never apply or restore twice."""
    from pymongo import ReturnDocument
    return await db[REQUESTS].find_one_and_update(
        {"request_id": request_id, "status": {"$in": from_statuses}},
        {"$set": {"status": to_status, "updated_at": _now(), **fields}},
        return_document=ReturnDocument.AFTER,
    )
