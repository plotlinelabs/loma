"""
Dashboard Chat Ingestion

Stores each dashboard chat message → agent response cycle as a change-stream
event so downstream semantic reasoning has visibility into support interactions
happening on the dashboard alongside Slack, Linear, GitHub, Grain, and Pylon.

Called fire-and-forget from handle_chat() after the agent stream completes.

For webhook-triggered conversations, uses an LLM to extract entity IDs
(pylon_issue_id, linear_issue, github_pr, etc.) from the prompt and stores
them as thread_refs for cross-tool linking.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from observability.db import get_db

logger = logging.getLogger(__name__)


def _content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _format_text(prompt: str, response: str | None) -> str:
    parts = [prompt]
    if response:
        parts.append("")
        parts.append("--- agent response ---")
        parts.append(response)
    return "\n".join(parts).strip()


async def _extract_thread_refs_llm(prompt: str) -> dict[str, list[str]]:
    """Use Haiku to extract entity IDs from a conversation prompt.

    Returns a dict of thread_refs where each value is an array (a single
    conversation may reference multiple PRs, tickets, etc.).
    Example: {"linear_issue": ["GO-123", "GO-456"], "github_pr": ["PR#789"]}
    Returns empty dict on any failure.
    """
    message = (
        "Extract entity IDs from this text. Do NOT fetch any URLs or use tools. "
        "Just parse the text and return a JSON object where each value is an ARRAY "
        "of strings. Only include keys where you found actual IDs:\n\n"
        "{\n"
        '  "pylon_issue_id": ["Pylon support issue UUIDs"],\n'
        '  "linear_issue": ["Linear issue identifiers like GO-123 or ISSUE-3141"],\n'
        '  "github_pr": ["GitHub references in owner/repo#number format like example-org/example-repo#65"],\n'
        '  "hubspot_deal_id": ["HubSpot deal IDs"],\n'
        '  "slack_thread_ts": ["Slack thread timestamps"],\n'
        '  "slack_channel_id": ["Slack channel IDs like C05JNL8SFJ8"],\n'
        '  "grain_recording_id": ["Grain recording UUIDs"]\n'
        "}\n\n"
        f"Text:\n{prompt[:2000]}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", message,
            "--model", "claude-haiku-4-5-20251001",
            "--max-turns", "1",
            "--output-format", "json",
            "--allowedTools", "",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            return {}

        output = stdout.decode().strip()
        try:
            envelope = json.loads(output)
            raw = envelope.get("result", output)
        except json.JSONDecodeError:
            raw = output

        # Parse the JSON from the LLM response.
        if isinstance(raw, str):
            # Strip markdown code fences if present.
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            refs = json.loads(raw)
        elif isinstance(raw, dict):
            refs = raw
        else:
            return {}

        # Normalize: ensure every value is a list of non-empty strings.
        result: dict[str, list[str]] = {}
        for k, v in refs.items():
            if isinstance(v, list):
                cleaned = [s for s in v if isinstance(s, str) and s.strip()]
                if cleaned:
                    result[k] = cleaned
            elif isinstance(v, str) and v.strip():
                result[k] = [v.strip()]
        return result

    except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:
        logger.debug("[DASHBOARD-INGESTION] LLM entity extraction failed: %s", e)
        return {}


def _build_thread_refs_from_metadata(metadata: dict, conversation_id: str) -> dict[str, list[str]]:
    """Build thread_refs from conversation metadata (no LLM needed).

    Every value is a list (a thread_ref key can have multiple values).
    """
    refs: dict[str, list[str]] = {"conversation_id": [conversation_id]}

    # Slack-originated conversations.
    slack_ts = metadata.get("slack_thread_ts")
    if slack_ts:
        refs["slack_thread_ts"] = [slack_ts]
    slack_ch = metadata.get("slack_channel_id")
    if slack_ch:
        refs["slack_channel_id"] = [slack_ch]

    # Webhook flow thread_id (generic — set by extract_thread_id).
    thread_id = metadata.get("thread_id")
    if thread_id:
        refs["webhook_thread_id"] = [thread_id]

    # Known tool-specific metadata keys (set by some webhook handlers).
    for meta_key, ref_key in [
        ("linear_issue_id", "linear_issue"),
        ("github_pr_number", "github_pr"),
    ]:
        val = metadata.get(meta_key)
        if val:
            refs[ref_key] = [str(val)]

    return refs


def _merge_refs(base: dict[str, list[str]], extra: dict[str, list[str]]) -> None:
    """Merge extra refs into base, deduplicating values per key."""
    for k, vals in extra.items():
        if k in base:
            existing = set(base[k])
            base[k] = list(existing | set(vals))
        else:
            base[k] = vals


async def ingest_dashboard_chat(
    conversation_id: str,
    prompt: str,
    user_email: str,
) -> None:
    """Read the conversation's final response and store a change-stream event."""
    try:
        db = get_db()
        if db is None:
            return

        # Fetch final_response + metadata + full messages from the conversation doc.
        conv = await db.conversations.find_one(
            {"conversation_id": conversation_id},
            {"final_response": 1, "started_at": 1, "title": 1, "topic": 1, "metadata": 1, "source": 1, "messages": 1},
        )
        final_response = (conv.get("final_response") or "") if conv else ""
        started_at = (conv.get("started_at") if conv else None) or datetime.now(timezone.utc)
        metadata = (conv.get("metadata") or {}) if conv else {}
        messages = (conv.get("messages") or []) if conv else []

        text = _format_text(prompt, final_response)

        # Build thread_refs based on conversation metadata.
        thread_refs = _build_thread_refs_from_metadata(metadata, conversation_id)

        # Inherit thread_refs from previous turns of the same conversation
        # (cheap DB lookup, no LLM call — carries forward refs from earlier turns).
        prev = await db.changestreams.find_one(
            {"source": "dashboard", "thread_refs.conversation_id": conversation_id},
            {"thread_refs": 1},
            sort=[("timestamp", -1)],
        )
        if prev and prev.get("thread_refs"):
            _merge_refs(thread_refs, prev["thread_refs"])

        # Build full conversation context for LLM extraction (not just the
        # current prompt — earlier turns may reference entity IDs that the
        # current "summarize the above" turn doesn't repeat).
        full_context_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = (msg.get("content") or "")[:500]
            if content:
                full_context_parts.append(f"{role}: {content}")
        full_context = "\n\n".join(full_context_parts) if full_context_parts else prompt

        # Use LLM to extract entity IDs from the full conversation context.
        llm_refs = await _extract_thread_refs_llm(full_context)
        if llm_refs:
            _merge_refs(thread_refs, llm_refs)
            logger.info("[DASHBOARD-INGESTION] LLM extracted refs conv=%s refs=%s", conversation_id, llm_refs)

        # Use the most specific thread ref as primary thread_ts for backward compat.
        thread_ts = (
            thread_refs.get("slack_thread_ts")
            or thread_refs.get("webhook_thread_id")
            or conversation_id
        )

        normalized = {
            "event_id": str(uuid.uuid4()),
            "source": "dashboard",
            "source_event_id": f"dashboard:{conversation_id}:{_content_hash(prompt)[:12]}",
            "event_type": "chat",
            "event_subtype": None,
            "timestamp": started_at,
            "ingested_at": datetime.now(timezone.utc),
            "channel_id": "dashboard",
            "channel_name": "Dashboard Chat",
            "thread_ts": thread_ts,
            "thread_refs": thread_refs,
            "user_id": user_email or None,
            "user_name": user_email or None,
            "is_bot": False,
            "text": text,
            "content_hash": _content_hash(text),
            "files": [],
            "reaction": None,
            "reaction_target_ts": None,
            "entities": [],
            "embedding": None,
            "raw_event": {
                "conversation_id": conversation_id,
                "prompt": prompt,
                "final_response": final_response[:5000],
                "user_email": user_email,
            },
            "processed": False,
            "processing_version": 1,
        }

        await db.changestreams.update_one(
            {"source_event_id": normalized["source_event_id"]},
            {"$setOnInsert": normalized},
            upsert=True,
        )
        logger.info(
            "[DASHBOARD-INGESTION] stored event conv=%s user=%s thread_refs=%s",
            conversation_id, user_email, list(thread_refs.keys()),
        )
    except Exception:
        logger.exception("[DASHBOARD-INGESTION] Failed to ingest conv=%s", conversation_id)
