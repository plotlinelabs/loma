"""Executor for webhook-triggered flows.

When a webhook hits /webhook?flowId=XYZ, this module renders the flow's
prompt_template with the incoming payload, runs the agent, and records
the execution in observability.  Unlike scheduled flows, webhook flows
do NOT post output to Slack — they act via MCP tools.
"""

import json
import logging
import os
from datetime import datetime, timezone

import asyncio

from agent.client import stream_agent
from agent.pool import ClientPool
from observability.db import get_db
from observability.observer import ConversationObserver
from api.dashboard_ingestion import ingest_dashboard_chat

logger = logging.getLogger(__name__)

WEBHOOK_PREAMBLE = """You are executing a webhook-triggered flow.
IMPORTANT:
- Your text output is NOT posted anywhere — use MCP tools to take actions.
- The raw webhook payload is included below for context.

Flow name: {flow_name}
---

"""

# Maximum number of prior messages to include as conversation context on resume.
# Keeps token usage bounded for long-running conversations.
_MAX_CONTEXT_MESSAGES = 20


def _flow_model(flow: dict) -> str:
    """Return the provider/model id configured for this flow."""
    configured = (flow.get("model") or "").strip()
    if configured:
        return configured
    return f"anthropic/{ClientPool.default_model()}"


def _resolve_jsonpath(obj: dict, path: str):
    """Resolve a dot-separated path against a nested dict.

    Returns None if any segment is missing.

    >>> _resolve_jsonpath({"a": {"b": 1}}, "a.b")
    1
    """
    parts = path.split(".")
    current = obj
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def extract_thread_id(flow: dict, payload: dict) -> str | None:
    """Extract a thread identifier from the webhook payload.

    Checks flow-level config first (``webhook_config.thread_id_path``),
    then auto-detects from well-known payload shapes (Pylon, etc.).
    """
    # 1. Explicit config — flow author specified a JSONPath
    thread_id_path = flow.get("webhook_config", {}).get("thread_id_path")
    if thread_id_path:
        value = _resolve_jsonpath(payload, thread_id_path)
        if value is not None:
            return str(value)

    # 2. Auto-detect for known webhook sources
    for path in ("data.issue_id", "issue.id", "issue_id", "data.id"):
        value = _resolve_jsonpath(payload, path)
        if value is not None:
            return str(value)

    return None


def _build_conversation_context(messages: list[dict], max_messages: int = _MAX_CONTEXT_MESSAGES) -> str:
    """Build a conversation context string from stored messages.

    Takes the last *max_messages* messages and formats them as a readable
    conversation transcript the agent can use as prior context.
    """
    if not messages:
        return ""

    recent = messages[-max_messages:]
    parts = []
    for msg in recent:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role == "user":
            parts.append(f"**User**: {content}")
        elif role == "assistant":
            parts.append(f"**Assistant**: {content}")
    return "\n".join(parts)


async def execute_webhook_flow(
    flow: dict,
    raw_body: bytes,
    headers: dict,
    log_id: str,
) -> str | None:
    """Execute a webhook-triggered flow.

    Args:
        flow: The flow document from MongoDB.
        raw_body: Raw bytes of the webhook request body.
        headers: Request headers dict.
        log_id: The webhook_logs document ID for status updates.

    Returns:
        The conversation_id on success, None on failure.
    """
    db = get_db()
    if db is None:
        logger.error("[WEBHOOK-EXEC] DB not available, skipping flow %s", flow["flow_id"])
        return None

    flow_id = flow["flow_id"]

    if flow["status"] != "active":
        logger.info("[WEBHOOK-EXEC] Flow %s is %s, skipping", flow_id, flow["status"])
        return None

    # Parse payload for template rendering
    try:
        payload = json.loads(raw_body)
        payload_pretty = json.dumps(payload, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
        payload_pretty = raw_body.decode("utf-8", errors="replace")

    # Render prompt: replace {{payload}} with the pretty-printed JSON
    template = flow.get("prompt_template") or flow.get("prompt", "")
    rendered_prompt = template.replace("{{payload}}", payload_pretty)

    full_prompt = (
        WEBHOOK_PREAMBLE.format(flow_name=flow["name"])
        + rendered_prompt
    )

    # Mark log as running
    await db.webhook_logs.update_one(
        {"log_id": log_id},
        {"$set": {"execution_status": "running"}},
    )

    # Set up observability
    visibility = flow.get("visibility", "shared")
    creator_email = (
        flow.get("created_by", {}).get("source", "")
        or flow.get("created_by", {}).get("user_name", "")
    )

    selected_model = _flow_model(flow)

    metadata = {
        "source": "webhook",
        "prompt": rendered_prompt,
        "model": selected_model,
        "flow_id": flow_id,
        "flow_name": flow["name"],
        "trigger_type": "webhook",
        "webhook_log_id": log_id,
        "visibility": visibility,
    }
    if visibility == "private" and creator_email:
        metadata["user_name"] = creator_email

    # --- Thread-aware conversation continuity (GO-90) ---
    # Extract a thread identifier from the payload to enable resuming
    # prior conversations for the same thread (e.g. same Pylon issue).
    thread_id = extract_thread_id(flow, payload)
    existing_convo = None
    conversation_context = ""

    if thread_id:
        metadata["thread_id"] = thread_id
        try:
            existing_convo = await db.conversations.find_one(
                {
                    "metadata.thread_id": thread_id,
                    "metadata.flow_id": flow_id,
                    "status": {"$in": ["completed", "running"]},
                },
                sort=[("finished_at", -1)],
            )
        except Exception:
            logger.exception(
                "[WEBHOOK-EXEC] Failed to look up existing conversation for thread %s",
                thread_id,
            )

    if existing_convo:
        existing_id = existing_convo.get("conversation_id", "")
        logger.info(
            "[WEBHOOK-EXEC] Resuming conversation %s for thread %s (flow %s)",
            existing_id, thread_id, flow_id,
        )
        # Build conversation context from prior messages
        prior_messages = existing_convo.get("messages", [])
        conversation_context = _build_conversation_context(prior_messages)

        observer = ConversationObserver(
            db, metadata=metadata, conversation_id=existing_id,
        )
        await observer.resume()
    else:
        if thread_id:
            logger.info(
                "[WEBHOOK-EXEC] New conversation for thread %s (flow %s)",
                thread_id, flow_id,
            )
        observer = ConversationObserver(db, metadata=metadata)
        await observer.start()

    last_text = ""
    try:
        async for chunk in stream_agent(
            prompt=full_prompt,
            conversation_context=conversation_context,
            observer=observer,
            source="slack",
            selected_model=selected_model,
            raise_on_opencode_error=True,
        ):
            if isinstance(chunk, str):
                last_text = chunk
    except Exception as exc:
        logger.exception("[WEBHOOK-EXEC] Flow %s execution failed", flow_id)
        error_message = f"Flow execution failed: {exc}"
        await observer.record_error(error_message)
        await db.webhook_logs.update_one(
            {"log_id": log_id},
            {"$set": {
                "execution_status": "error",
                "error": error_message[:1000],
                "conversation_id": observer.conversation_id,
            }},
        )
        await db.flows.update_one(
            {"flow_id": flow_id},
            {"$set": {
                "last_run_at": datetime.now(timezone.utc),
                "last_error": error_message[:1000],
            }},
        )
        return observer.conversation_id

    # Update webhook log
    await db.webhook_logs.update_one(
        {"log_id": log_id},
        {"$set": {
            "execution_status": "completed",
            "conversation_id": observer.conversation_id,
        }},
    )

    # Update flow metadata
    now = datetime.now(timezone.utc)
    await db.flows.update_one(
        {"flow_id": flow_id},
        {
            "$set": {
                "last_run_at": now,
                "last_run_conversation_id": observer.conversation_id,
                "last_error": None,
            },
            "$inc": {"run_count": 1},
        },
    )

    # Fire-and-forget: ingest this conversation as a change-stream event.
    asyncio.create_task(ingest_dashboard_chat(
        observer.conversation_id, full_prompt, metadata.get("user_name", ""),
    ))

    # Fire-and-forget: pre-send gate SHADOW (decision-only, no behaviour change).
    # Scoped to the Pylon first-response flow; the recorder itself skips runs that
    # did not post a customer-facing reply. Reconstructs reply + evidence from turns.
    from gate.shadow import is_enabled as _gate_shadow_on, is_pylon_flow
    if _gate_shadow_on() and is_pylon_flow(flow_id):
        from gate.shadow import record_gate_shadow
        asyncio.create_task(record_gate_shadow(
            db,
            conversation_id=observer.conversation_id,
            flow_id=flow_id,
            issue_id=(payload.get("pylonIssueId") or metadata.get("thread_id")),
        ))

    logger.info(
        "[WEBHOOK-EXEC] Flow %s completed (conversation: %s)",
        flow_id, observer.conversation_id,
    )
    return observer.conversation_id
