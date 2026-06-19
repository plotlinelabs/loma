"""Rolling memory state management for scheduled flows.

After a user replies in a flow output Slack thread, the feedback is
summarized via a lightweight Haiku call and merged into the flow's
`memory_state` field.  This keeps the memory at a fixed ~500 token
budget (O(1) prompt size) regardless of how many feedback rounds occur.

Memory is updated ONLY on user feedback — silent runs leave memory
unchanged.
"""

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

MEMORY_SUMMARIZE_PROMPT = """You are a memory manager for a recurring scheduled flow.

Given the OLD memory state and TODAY's feedback conversation between the
user and the assistant, produce an UPDATED memory JSON object.

Rules:
- Merge new feedback into existing preferences (don't just append).
- Track completed items so they are not repeated in future runs.
- Preserve all refinements and user preferences.
- Drop stale details that are no longer relevant.
- Keep the output under 500 tokens.
- If OLD MEMORY is empty, create a fresh memory object from the feedback.

OLD MEMORY:
{old_memory}

TODAY'S FEEDBACK CONVERSATION:
{feedback_conversation}

Return ONLY the updated memory JSON — no explanation, no markdown fences."""


async def update_flow_memory(
    db,
    flow_id: str,
    feedback_conversation: str,
) -> str | None:
    """Summarize feedback and update the flow's rolling memory_state.

    Args:
        db: MongoDB database handle.
        flow_id: The scheduled flow's ID.
        feedback_conversation: The user \u2194 assistant exchange from the
            Slack thread (plain text).

    Returns:
        The new memory_state string, or None on failure.
    """
    flow = await db.flows.find_one({"flow_id": flow_id})
    if flow is None:
        logger.warning("[MEMORY] Flow %s not found, skipping memory update", flow_id)
        return None

    old_memory = flow.get("memory_state", "")

    new_memory = await _summarize_with_haiku(old_memory, feedback_conversation)
    if new_memory is None:
        logger.warning("[MEMORY] Haiku summarization failed for flow %s", flow_id)
        return None

    await db.flows.update_one(
        {"flow_id": flow_id},
        {"$set": {"memory_state": new_memory}},
    )
    logger.info(
        "[MEMORY] Updated memory_state for flow %s (%d chars)",
        flow_id, len(new_memory),
    )
    return new_memory


async def _summarize_with_haiku(
    old_memory: str,
    feedback_conversation: str,
) -> str | None:
    """Call Claude Haiku via the CLI to produce an updated memory summary.

    Uses the same CLI-based pattern as observability/confidence.py so it
    works with both API keys and Claude Max subscriptions.
    """
    message = MEMORY_SUMMARIZE_PROMPT.format(
        old_memory=old_memory or "(empty \u2014 first feedback)",
        feedback_conversation=feedback_conversation[:6000],
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", message,
            "--model", "claude-haiku-4-5-20251001",
            "--max-turns", "1",
            "--output-format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            err = stderr.decode().strip() if stderr else "unknown error"
            logger.warning("[MEMORY] Haiku CLI failed (rc=%d): %s", proc.returncode, err)
            return None

        output = stdout.decode().strip()

        # claude --output-format json wraps the response in a JSON envelope
        try:
            envelope = json.loads(output)
            raw = envelope.get("result", output)
        except json.JSONDecodeError:
            raw = output

        # Strip markdown code fences if the model wrapped the JSON
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        # Validate that the output is parseable JSON (we store it as a string)
        json.loads(raw)

        return raw

    except asyncio.TimeoutError:
        logger.warning("[MEMORY] Haiku summarization timed out (30s)")
        return None
    except json.JSONDecodeError:
        logger.warning("[MEMORY] Haiku returned non-JSON output: %.200s", raw)
        # Still usable as free-text memory — store it anyway
        return raw if raw else None
    except FileNotFoundError:
        logger.warning("[MEMORY] claude CLI not found — memory update skipped")
        return None
    except Exception as e:
        logger.warning("[MEMORY] Haiku summarization failed: %s", e)
        return None
