"""Soft second pass that compresses over-long Slack replies.

The brevity rules are prompt-driven, so a reply can still come back as a
multi-section report. Rather than hard-cutting the text (which can hide a
warning at the end), this asks a small model to rewrite it as a short Slack
reply that keeps the answer, numbers, links and any blocker or warning.

Every failure path returns the original text unchanged: the pass is an
improvement, never a gate.
"""

import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Replies at or under this length are posted as-is. 0 disables the pass.
SLACK_COMPRESS_THRESHOLD = int(os.environ.get("LOMA_SLACK_COMPRESS_THRESHOLD", "600"))
SLACK_COMPRESS_MODEL = os.environ.get("LOMA_SLACK_COMPRESS_MODEL", "claude-haiku-4-5-20251001")
# A haiku rewrite of a ~1.5K-char reply measured ~27s through the bundled CLI.
SLACK_COMPRESS_TIMEOUT = int(os.environ.get("LOMA_SLACK_COMPRESS_TIMEOUT", "45"))

# The user explicitly asked for a long-form answer; leave it alone.
DETAIL_REQUEST_RE = re.compile(
    r"\b(detailed|in detail|full|complete|comprehensive|step[- ]by[- ]step|"
    r"write[- ]?up|report|walk ?through|everything|all the details|long)\b",
    re.IGNORECASE,
)
# Fenced code means the deliverable is code or a command.
CODE_BLOCK_RE = re.compile(r"```")

_COMPRESS_PROMPT = (
    "Rewrite the Slack reply below so it fits Slack thread etiquette: at most 3 short "
    "lines by default (up to 5 if the request was to explain or summarize). Keep the "
    "direct answer, the outcome, every number, link, ID, and any blocker, warning or "
    "uncertainty. Drop section headers, investigation narration, evidence dumps, "
    "repeated context, and closing offers such as \"Want me to...?\". Use Slack mrkdwn "
    "only (single-asterisk bold, `-` bullets, backticks for code); no Markdown headings "
    "or tables. Reply with ONLY the rewritten message.\n\n"
    "User request:\n{request}\n\n"
    "Reply to rewrite:\n{reply}"
)


def should_compress(text: str, request: str = "", threshold: int | None = None) -> bool:
    """Decide whether a Slack reply is a candidate for the compress pass."""
    limit = SLACK_COMPRESS_THRESHOLD if threshold is None else threshold
    if limit <= 0 or not text or len(text) <= limit:
        return False
    if CODE_BLOCK_RE.search(text):
        return False
    if request and DETAIL_REQUEST_RE.search(request):
        return False
    return True


async def _run_compress_model(message: str) -> str:
    """Run the compress prompt through the claude CLI (same pattern as title generation)."""
    from agent.pool import background_cli_env

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", message,
        "--model", SLACK_COMPRESS_MODEL,
        "--max-turns", "1",
        "--output-format", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=background_cli_env(),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SLACK_COMPRESS_TIMEOUT)
    if proc.returncode != 0:
        detail = stderr.decode()[:300].strip() or stdout.decode()[:300].strip()
        raise RuntimeError(f"claude CLI failed (rc={proc.returncode}): {detail}")
    output = stdout.decode().strip()
    try:
        return str(json.loads(output).get("result", output))
    except (json.JSONDecodeError, AttributeError):
        return output


async def maybe_compress_slack_reply(text: str, request: str = "") -> str:
    """Return a shorter version of ``text`` for Slack, or ``text`` itself on any doubt."""
    # Only the user's own words matter for detecting "give me detail"; a Slack-flow
    # preamble or channel workflow prefix sits before them, so judge the tail.
    request_tail = (request or "")[-1000:]
    if not should_compress(text, request_tail):
        return text

    try:
        rewritten = (await _run_compress_model(
            _COMPRESS_PROMPT.format(request=request_tail, reply=text)
        )).strip()
    except asyncio.TimeoutError:
        logger.warning("[SLACK] Compress pass timed out; posting original reply")
        return text
    except Exception as e:
        logger.warning("[SLACK] Compress pass failed (%s); posting original reply", e)
        return text

    if not rewritten or len(rewritten) >= len(text):
        logger.info("[SLACK] Compress pass produced no shorter reply; posting original")
        return text

    logger.info("[SLACK] Compressed reply %d -> %d chars", len(text), len(rewritten))
    return rewritten
