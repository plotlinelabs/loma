import logging
import os
from datetime import datetime, timezone

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from agent.client import stream_agent
from agent.pool import ClientPool
from observability.db import get_db
from observability.observer import ConversationObserver
from scheduler.engine import get_next_run_time, remove_flow_from_scheduler
from slack_app.utils import truncate_for_slack

logger = logging.getLogger(__name__)

EMPTY_OUTPUT_SENTINEL = "__EMPTY__"


class SlackPostError(RuntimeError):
    """Raised when a scheduled flow cannot post its output to Slack."""

FLOW_PREAMBLE = """You are executing a scheduled flow. IMPORTANT:
- Your text output will be AUTOMATICALLY posted to the Slack channel #{channel_name}.
- Do NOT try to post to Slack yourself (no curl, no Slack API calls, no webhooks).
- Just produce the final message content as your response.
- Format your output for Slack mrkdwn (use *bold* for headers, no # headings).
- If the flow requires querying data (databases, APIs, etc.), do that first, then produce the final formatted output.

Flow name: {flow_name}
---

"""


def _flow_model(flow: dict) -> str:
    """Return the provider/model id configured for this flow."""
    configured = (flow.get("model") or "").strip()
    if configured:
        return configured
    return f"anthropic/{ClientPool.default_model()}"


async def execute_flow(flow_id: str):
    """Execute a single scheduled flow.

    Called by APScheduler when a flow's trigger fires.
    Creates a new agent conversation, runs it, and posts to Slack.
    """
    db = get_db()
    if db is None:
        logger.error("[SCHEDULER] DB not available, skipping flow %s", flow_id)
        return

    flow = await db.flows.find_one({"flow_id": flow_id})
    if flow is None or flow["status"] != "active":
        logger.info("[SCHEDULER] Flow %s not active or not found, skipping", flow_id)
        return

    logger.info("[SCHEDULER] Executing flow: %s (%s)", flow["name"], flow_id)

    # For private flows, tag the conversation with the creator's email
    # so existing conversation isolation gives them access.
    visibility = flow.get("visibility", "shared")
    creator_email = (
        flow.get("created_by", {}).get("source", "")
        or flow.get("created_by", {}).get("user_name", "")
    )

    selected_model = _flow_model(flow)

    metadata = {
        "source": "flow",
        "prompt": flow["prompt"],
        "model": selected_model,
        "flow_id": flow_id,
        "flow_name": flow["name"],
        "slack_channel_id": flow["channel_id"],
        "visibility": visibility,
    }
    if visibility == "private" and creator_email:
        metadata["user_name"] = creator_email

    observer = ConversationObserver(db, metadata=metadata)
    await observer.start()

    # Build the full prompt: preamble + memory context (if any) + flow prompt
    memory_state = flow.get("memory_state", "")
    if memory_state:
        memory_context = (
            "\n\n## Memory from previous runs\n"
            "The following is a summary of feedback and preferences from previous "
            "executions of this flow. Use it to refine your output:\n"
            f"{memory_state}\n\n"
        )
    else:
        memory_context = ""

    full_prompt = (
        FLOW_PREAMBLE.format(
            channel_name=flow.get("channel_name", flow["channel_id"]),
            flow_name=flow["name"],
        )
        + memory_context
        + flow["prompt"]
    )

    last_text = ""
    try:
        async for chunk in stream_agent(
            prompt=full_prompt,
            observer=observer,
            source="slack",
            selected_model=selected_model,
            raise_on_opencode_error=True,
            user_email=creator_email if "@" in creator_email else None,
        ):
            if isinstance(chunk, str):
                last_text = chunk
            elif isinstance(chunk, dict) and chunk.get("type") == "text":
                text = str(chunk.get("text") or "")
                if chunk.get("append"):
                    last_text += text
                else:
                    last_text = text
    except Exception as exc:
        logger.exception("[SCHEDULER] Flow %s execution failed", flow_id)
        error_message = f"Flow execution failed: {exc}"
        await observer.record_error(error_message)
        await db.flows.update_one(
            {"flow_id": flow_id},
            {"$set": {
                "last_run_at": datetime.now(timezone.utc),
                "last_error": error_message[:1000],
            }},
        )
        return

    await observer.finish(last_text)

    # Sizing-flow post-processor: parse the per-ticket Slack bullets the rubric
    # emits and write eng_sizing_log audit docs from Python. The agent itself
    # cannot write to Mongo (MCP is read-only on loma_observability), so
    # this is the only path that captures per-ticket history for sweep runs.
    # Gated on the flow's `labels` so it only fires for sizing flows.
    if "sizing" in (flow.get("labels") or []):
        try:
            from metrics.sizing_audit import parse_sweep_output, write_sweep_audit_docs
            items = parse_sweep_output(last_text)
            if items:
                n = await write_sweep_audit_docs(
                    db, items, conversation_id=observer.conversation_id,
                )
                logger.info(
                    "[SCHEDULER] Sizing flow %s: parsed %d items, wrote %d audit docs",
                    flow_id, len(items), n,
                )
            else:
                logger.info(
                    "[SCHEDULER] Sizing flow %s: no parseable items in output (likely __EMPTY__)",
                    flow_id,
                )
        except Exception:
            logger.exception(
                "[SCHEDULER] Sizing audit post-processor failed for flow %s", flow_id,
            )

    # Post to Slack channel and capture the thread_ts for reply handling.
    # "__EMPTY__" is the flow convention for "do the work, but stay silent".
    slack_thread_ts = None
    slack_post_error = None
    should_post_to_slack = bool(last_text.strip()) and last_text.strip() != EMPTY_OUTPUT_SENTINEL
    if should_post_to_slack:
        try:
            slack_thread_ts = await _post_to_slack_channel(
                channel_id=flow["channel_id"],
                text=last_text,
                flow_name=flow["name"],
            )
        except SlackPostError as exc:
            slack_post_error = str(exc)
            logger.exception("[SCHEDULER] Flow %s failed to post to Slack", flow_id)
            try:
                await db.conversations.update_one(
                    {"conversation_id": observer.conversation_id},
                    {"$set": {"metadata.slack_post_error": slack_post_error}},
                )
            except Exception:
                logger.exception(
                    "[SCHEDULER] Failed to record Slack post error for flow %s", flow_id
                )

    # Wire the Slack thread to the conversation record so that user replies
    # in the flow output thread are picked up by the existing reply handler.
    if slack_thread_ts:
        try:
            await db.conversations.update_one(
                {"conversation_id": observer.conversation_id},
                {"$set": {
                    "metadata.slack_thread_ts": slack_thread_ts,
                    "metadata.slack_channel_id": flow["channel_id"],
                    "metadata.flow_id": flow_id,
                }},
            )
            logger.info(
                "[SCHEDULER] Wired thread_ts=%s to conversation %s for flow %s",
                slack_thread_ts, observer.conversation_id, flow_id,
            )
        except Exception:
            logger.exception(
                "[SCHEDULER] Failed to wire thread_ts for flow %s", flow_id
            )

    # Update flow metadata
    now = datetime.now(timezone.utc)
    update_fields: dict = {
        "last_run_at": now,
        "last_run_conversation_id": observer.conversation_id,
        "last_error": slack_post_error,
    }

    if flow["schedule_type"] == "once":
        update_fields["status"] = "completed"
        await remove_flow_from_scheduler(flow_id)
    else:
        next_run = get_next_run_time(flow_id)
        if next_run:
            update_fields["next_run_at"] = next_run

    await db.flows.update_one(
        {"flow_id": flow_id},
        {"$set": update_fields, "$inc": {"run_count": 1}},
    )
    if slack_post_error:
        logger.warning("[SCHEDULER] Flow %s completed but Slack post failed", flow_id)
    elif should_post_to_slack:
        logger.info("[SCHEDULER] Flow %s completed, posted to %s", flow_id, flow["channel_id"])
    else:
        logger.info("[SCHEDULER] Flow %s completed with empty output; skipped Slack post", flow_id)


async def _post_to_slack_channel(
    channel_id: str, text: str, flow_name: str,
) -> str | None:
    """Post flow output to a Slack channel.

    Returns the message timestamp (thread_ts) on success, or None on failure.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise SlackPostError("SLACK_BOT_TOKEN not set")

    client = AsyncWebClient(token=token)

    footer = f"\n\n_via flow: {flow_name}_"
    full_text = truncate_for_slack(text, max_length=40000 - len(footer)) + footer

    try:
        result = await client.chat_postMessage(channel=channel_id, text=full_text)
        return result["ts"]
    except SlackApiError as exc:
        slack_error = exc.response.get("error", str(exc)) if exc.response else str(exc)
        raise SlackPostError(f"Slack API error posting to {channel_id}: {slack_error}") from exc
    except Exception as exc:
        raise SlackPostError(f"Failed to post to {channel_id}: {exc}") from exc
