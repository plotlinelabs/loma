"""Pinned agent configuration for self-owned, private scheduled work.

This reuses flow execution authority, NOT a tool permission boundary. Shared
agent edits never change a saved job's instructions without a new job/review.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from api.agent_identity_routes import resolve_agent_for_chat, build_agent_context_block
from scheduler.engine import _fix_crontab_dow
from scheduler.run_identity import require_execution_account


async def validate_agent_work(db, flow):
    if flow.get("trigger_type", "scheduled") != "scheduled":
        raise ValueError("Agent work currently supports scheduled triggers only")
    if flow.get("visibility") != "private":
        raise ValueError("Agent schedules and their results must stay private")
    owner = (flow.get("created_by", {}).get("source") or "").lower()
    if not owner or flow.get("run_as") != owner:
        raise ValueError("Agent schedules must run using their creator's own account")
    await require_execution_account(db, flow)
    agent_id = flow.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("Select an active agent")
    agent = await resolve_agent_for_chat(db, agent_id, owner)
    if not agent:
        raise ValueError("Agent is unavailable, disabled, or no longer shared with this account")
    snapshot = flow.get("agent_snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("context"):
        raise ValueError("Agent configuration is missing; create a new schedule")
    if flow.get("schedule_type") != "recurring":
        raise ValueError("Agent work currently supports recurring schedules only")
    if flow.get("status") not in ("active", "paused"):
        raise ValueError("Agent schedule status must be active or paused")
    try:
        CronTrigger.from_crontab(_fix_crontab_dow(flow.get("cron")), timezone=ZoneInfo(flow.get("timezone")))
    except (ValueError, TypeError, AttributeError, ZoneInfoNotFoundError):
        raise ValueError("Choose a valid cron schedule and IANA timezone") from None
    return agent


async def prepare_agent_work(db, data, requester):
    data = dict(data)
    if data.get("status") != "paused":
        raise ValueError("Save agent schedules paused, then explicitly enable them")
    if data.get("run_as") != requester:
        raise ValueError("Agent schedules must run using your own account")
    if not isinstance(data.get("agent_id"), str):
        raise ValueError("Select an active agent")
    agent = await resolve_agent_for_chat(db, data["agent_id"], requester)
    if not agent:
        raise ValueError("Agent is unavailable, disabled, or no longer shared with this account")
    for key in ("name", "prompt"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError("Give this job a name and instructions")
    data["agent_snapshot"] = {
        "name": agent["name"],
        "context": await build_agent_context_block(db, agent),
        "captured_at": datetime.now(timezone.utc),
        "configuration_updated_at": agent.get("updated_at"),
    }
    data["model"] = data.get("model") or agent.get("default_model")
    await validate_agent_work(db, data)
    return data
