"""Agent switch events on a conversation.

A thread can change agents part-way through (agent picker, "Loma", or naming an
agent in Slack). Each change is kept in `conversations.agent_events` so the run
view can show where it happened and who did it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_AGENT_NAME = "Loma"


def _ref(agent: dict | None) -> tuple[str | None, str]:
    if not agent:
        return None, DEFAULT_AGENT_NAME
    return agent.get("agent_id"), agent.get("name") or agent.get("agent_name") or DEFAULT_AGENT_NAME


async def record_agent_switch(db, conversation_id: str, from_agent: dict | None,
                              to_agent: dict | None, switched_by: str | None,
                              source: str = "dashboard") -> dict | None:
    """Append an agent_switch event. No-op when the agent did not change."""
    from_id, from_name = _ref(from_agent)
    to_id, to_name = _ref(to_agent)
    if from_id == to_id or db is None or not conversation_id:
        return None
    event = {
        "type": "agent_switch",
        "from_agent_id": from_id,
        "from_agent_name": from_name,
        "to_agent_id": to_id,
        "to_agent_name": to_name,
        "switched_by": switched_by,
        "source": source,
        "timestamp": datetime.now(timezone.utc),
    }
    if to_agent and to_agent.get("config_version"):
        event["to_agent_config_version"] = int(to_agent["config_version"])
    try:
        await db.conversations.update_one(
            {"conversation_id": conversation_id}, {"$push": {"agent_events": event}},
        )
    except Exception as e:
        logger.warning("Failed to record agent switch: %s", e)
        return None
    return event
