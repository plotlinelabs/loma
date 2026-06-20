"""Slack channel workflow registry.

Two sources feed the per-channel agent automations:

1. A static, env/code-defined ``CHANNEL_CONFIGS`` map (empty by default in OSS).
2. **Slack-triggered flows** stored in MongoDB — a flow with ``trigger_type: "slack"``
   watches a channel and replies to each new top-level message in its thread.

``get_channel_config`` resolves both; the static map takes precedence.
"""

import logging

from observability.db import get_db

logger = logging.getLogger(__name__)

CHANNEL_CONFIGS: dict[str, dict] = {}

# Prepended to a Slack-triggered flow's prompt. The agent's final text reply is
# posted back into the message's thread automatically (see _stream_response).
SLACK_FLOW_PREAMBLE = (
    "{flow_prompt}\n\n"
    "---\n"
    "A new message was just posted in the Slack channel #{channel_name}. Your text "
    "reply will be posted back into that message's thread automatically — output only "
    "the reply content (no preamble or commentary).\n\n"
    "Message:\n"
)


def _config_from_slack_flow(flow: dict) -> dict:
    """Build a channel config dict from a Slack-triggered flow document."""
    channel_name = flow.get("channel_name") or flow.get("channel_id", "")
    return {
        "name": channel_name.lstrip("#") or flow.get("channel_id", ""),
        "source": "slack_flow",
        "flow_id": flow["flow_id"],
        "prompt_prefix": SLACK_FLOW_PREAMBLE.format(
            flow_prompt=flow.get("prompt", ""),
            channel_name=channel_name.lstrip("#"),
        ),
        "allow_bot_messages": bool(
            (flow.get("slack_config") or {}).get("allow_bot_messages", False)
        ),
    }


async def get_channel_config(channel_id: str) -> dict | None:
    """Return the agent config for a monitored channel, or None if not monitored.

    Checks the static ``CHANNEL_CONFIGS`` first, then an active Slack-triggered
    flow watching this channel (at most one — enforced at flow creation).
    """
    if not channel_id:
        return None

    static = CHANNEL_CONFIGS.get(channel_id)
    if static is not None:
        return static

    db = get_db()
    if db is None:
        return None

    try:
        flow = await db.flows.find_one({
            "trigger_type": "slack",
            "status": "active",
            "channel_id": channel_id,
        })
    except Exception:
        logger.exception("[SLACK] Failed to look up Slack flow for channel %s", channel_id)
        return None

    if flow is None:
        return None
    return _config_from_slack_flow(flow)
