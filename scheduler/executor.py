import logging
from datetime import datetime, timezone

from agent.client import stream_agent
from agent.pool import ClientPool
from observability.db import get_db
from observability.observer import ConversationObserver
from scheduler.engine import get_next_run_time, remove_flow_from_scheduler

logger = logging.getLogger(__name__)

FLOW_PREAMBLE = """You are executing a scheduled flow. IMPORTANT:
- Your text output is NOT posted anywhere — use MCP tools to take actions.
- If the flow should notify a channel (Slack, etc.), do it yourself via the
  appropriate MCP tool; nothing is sent automatically.
- If the flow requires querying data (databases, APIs, etc.), do that first,
  then take the actions the flow describes.

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

    Called by APScheduler when a flow's trigger fires. Creates a new agent
    conversation and runs it. Like webhook flows, the agent acts via MCP
    tools — its text output is not posted anywhere.
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
        "visibility": visibility,
    }
    if visibility == "private" and creator_email:
        metadata["user_name"] = creator_email

    observer = ConversationObserver(db, metadata=metadata)
    await observer.start()

    full_prompt = (
        FLOW_PREAMBLE.format(flow_name=flow["name"])
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

    # Sizing-flow post-processor: parse the per-ticket bullets the rubric emits
    # and write eng_sizing_log audit docs from Python. The agent itself cannot
    # write to Mongo (MCP is read-only on loma_observability), so this is the
    # only path that captures per-ticket history for sweep runs. Gated on the
    # flow's `labels` so it only fires for sizing flows.
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
                    "[SCHEDULER] Sizing flow %s: no parseable items in output", flow_id,
                )
        except Exception:
            logger.exception(
                "[SCHEDULER] Sizing audit post-processor failed for flow %s", flow_id,
            )

    # Update flow metadata
    now = datetime.now(timezone.utc)
    update_fields: dict = {
        "last_run_at": now,
        "last_run_conversation_id": observer.conversation_id,
        "last_error": None,
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
    logger.info("[SCHEDULER] Flow %s completed (conversation: %s)", flow_id, observer.conversation_id)
