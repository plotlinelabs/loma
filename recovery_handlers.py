"""Response delivery handlers for recovered conversations.

Provides a unified interface to post a response back to the original
channel (Slack, Linear, Pylon) after a conversation is recovered from
an interrupted state.
"""

import logging
import os

import aiohttp
from slack_sdk.web.async_client import AsyncWebClient

from webhooks.linear_api import post_comment, AGENT_COMMENT_MARKER

logger = logging.getLogger(__name__)


async def post_recovery_response(source: str, metadata: dict, response_text: str):
    """Route a recovered response to the correct destination based on source.

    Args:
        source: The conversation source (e.g., 'slack_mention', 'slack_dm',
                'linear_webhook', 'pylon_webhook', or a channel monitor source).
        metadata: Conversation metadata containing channel/thread/issue IDs.
        response_text: The agent's response to deliver.
    """
    try:
        if source in ("slack_mention", "slack_dm") or source.startswith("slack_channel_"):
            await _post_to_slack(metadata, response_text)
        elif source == "linear_webhook":
            await _post_to_linear(metadata, response_text)
        elif source == "pylon_webhook":
            await _post_to_pylon(metadata, response_text)
        else:
            logger.warning(
                "[RECOVERY] Unknown source '%s' — cannot deliver response", source
            )
    except Exception:
        logger.exception(
            "[RECOVERY] Failed to deliver response for source=%s", source
        )


async def _post_to_slack(metadata: dict, response_text: str):
    """Post a recovered response to a Slack thread."""
    channel = metadata.get("slack_channel_id")
    thread_ts = metadata.get("slack_thread_ts")

    if not channel or not thread_ts:
        logger.warning(
            "[RECOVERY] Missing Slack channel/thread in metadata: %s", metadata
        )
        return

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        logger.error("[RECOVERY] SLACK_BOT_TOKEN not set — cannot post to Slack")
        return

    client = AsyncWebClient(token=token)

    # Truncate to Slack's limit
    max_len = 40000
    if len(response_text) > max_len:
        response_text = response_text[:max_len - 100] + "\n\n_(response truncated due to length)_"

    await client.chat_postMessage(
        channel=channel,
        text=response_text,
        thread_ts=thread_ts,
    )
    logger.info(
        "[RECOVERY] Posted recovered response to Slack channel=%s thread=%s",
        channel, thread_ts,
    )


async def _post_to_linear(metadata: dict, response_text: str):
    """Post a recovered response as a comment on a Linear issue."""
    issue_id = metadata.get("linear_issue_id")
    if not issue_id:
        logger.warning(
            "[RECOVERY] Missing linear_issue_id in metadata: %s", metadata
        )
        return

    # For Linear webhook conversations, the agent's output is typically
    # the PR comment itself. We don't need to re-wrap it — just post as-is
    # with the agent marker to prevent loops.
    body = f"{AGENT_COMMENT_MARKER}\n{response_text}"
    comment_id = await post_comment(issue_id, body)
    if comment_id:
        logger.info(
            "[RECOVERY] Posted recovered response to Linear issue=%s (comment=%s)",
            issue_id, comment_id,
        )
    else:
        logger.warning(
            "[RECOVERY] Failed to post recovered response to Linear issue=%s",
            issue_id,
        )


async def _post_to_pylon(metadata: dict, response_text: str):
    """Post a recovered response as an internal note on a Pylon issue."""
    issue_id = metadata.get("pylon_issue_id")
    if not issue_id:
        logger.warning(
            "[RECOVERY] Missing pylon_issue_id in metadata: %s", metadata
        )
        return

    pylon_api_key = os.environ.get("PYLON_API_KEY", "")
    if not pylon_api_key:
        logger.error("[RECOVERY] PYLON_API_KEY not set — cannot post to Pylon")
        return

    url = f"https://api.usepylon.com/issues/{issue_id}/notes"
    headers = {
        "Authorization": f"Bearer {pylon_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"body": response_text}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status in (200, 201):
                logger.info(
                    "[RECOVERY] Posted recovered response to Pylon issue=%s",
                    issue_id,
                )
            else:
                body = await resp.text()
                logger.warning(
                    "[RECOVERY] Failed to post to Pylon issue=%s — HTTP %d: %s",
                    issue_id, resp.status, body[:500],
                )
