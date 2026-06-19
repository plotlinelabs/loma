import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid

from aiohttp import web
from dotenv import load_dotenv

from agent.client import stream_agent
from metrics.sizing_audit import parse_single_result, write_audit_doc
from observability.db import get_db
from prompts.estimation_rubric import AGENT_COMMENT_MARKER, build_sizing_prompt
from tools.linear import RELEASED_DONE_STATE, RELEASED_STATE
from webhooks.linear_ingestion import ingest_linear_event
from observability.observer import ConversationObserver
from webhooks.linear_api import (
    post_acknowledgment_comment,
    post_comment_tracking_acknowledgment,
    react_to_comment,
)

load_dotenv()

logger = logging.getLogger(__name__)

_LINEAR_WEBHOOK_SECRET_ENV = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
LINEAR_WEBHOOK_SECRET = _LINEAR_WEBHOOK_SECRET_ENV

# Cached DB webhook secret (loaded lazily)
_db_webhook_secret: str | None = None
_db_secret_loaded = False

# AGENT_COMMENT_MARKER is the canonical loop-prevention marker for any comment
# the agent writes to Linear. Defined in prompts.estimation_rubric so the
# rubric prompt and this webhook share one literal value (no drift). Imported
# at module load above.

# Label that can be added to re-trigger implementation on an existing issue
TRIGGER_LABEL = "loma"

# Phrase in comments that triggers auto-implementation or PR modification
TRIGGER_PHRASE = "loma"

# Maximum number of prior messages to include as conversation context on resume.
_MAX_CONTEXT_MESSAGES = 20


async def _get_webhook_secret() -> str:
    """Get Linear webhook secret from DB integration, falling back to env var."""
    global _db_webhook_secret, _db_secret_loaded
    if not _db_secret_loaded:
        try:
            from observability.db import get_db
            from api.oauth_helpers import decrypt_token
            db = get_db()
            if db is not None:
                integration = await db.integrations.find_one(
                    {"provider": "linear", "status": "active"}
                )
                if integration and integration.get("webhook_secret_encrypted"):
                    _db_webhook_secret = decrypt_token(integration["webhook_secret_encrypted"])
        except Exception:
            logger.exception("[LINEAR-WEBHOOK] Failed to load webhook secret from DB")
        _db_secret_loaded = True
    return _db_webhook_secret or LINEAR_WEBHOOK_SECRET


def _verify_signature_sync(secret: str, signature_header: str | None, raw_body: bytes) -> bool:
    """Verify the Linear webhook signature using HMAC-SHA256."""
    if not secret or not signature_header:
        return False

    try:
        header_sig = bytes.fromhex(signature_header)
    except ValueError:
        return False

    computed_sig = hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).digest()

    return hmac.compare_digest(computed_sig, header_sig)


# Maximum age (in seconds) for a webhook timestamp to be considered valid.
# Rejects replayed webhooks older than this threshold.
_MAX_WEBHOOK_AGE_SECONDS = 60


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


async def handle_linear_webhook(request: web.Request) -> web.Response:
    """Handle incoming Linear webhook notifications."""
    # Read the raw body once for both signature verification and JSON parsing
    raw_body = await request.read()

    # Verify HMAC-SHA256 signature (secret from DB integration or env var fallback)
    signature = request.headers.get("linear-signature")
    secret = await _get_webhook_secret()
    if not _verify_signature_sync(secret, signature, raw_body):
        logger.warning("[LINEAR-WEBHOOK] Invalid signature \u2014 rejecting request")
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        body = json.loads(raw_body)
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    # Reject stale webhooks to prevent replay attacks
    webhook_ts = body.get("webhookTimestamp")
    if webhook_ts is not None:
        age_seconds = abs(time.time() * 1000 - webhook_ts) / 1000
        if age_seconds > _MAX_WEBHOOK_AGE_SECONDS:
            logger.warning(
                "[LINEAR-WEBHOOK] Stale webhook (age=%.1fs) \u2014 rejecting to prevent replay",
                age_seconds,
            )
            return web.json_response({"error": "Unauthorized"}, status=401)

    action = body.get("action", "")
    event_type = body.get("type", "")
    data = body.get("data", {})

    logger.info("[LINEAR-WEBHOOK] Received: type=%s, action=%s", event_type, action)

    # Fire-and-forget: ingest every webhook event for observability
    asyncio.create_task(ingest_linear_event(body))

    # --- Trigger 1: 'loma' label added to existing issue (explicit trigger) ---
    if event_type == "Issue" and action == "update":
        updated_from = body.get("updatedFrom", {})
        old_label_ids = set(updated_from.get("labelIds", []))
        new_label_ids = set(data.get("labelIds", []))
        added_label_ids = new_label_ids - old_label_ids

        if added_label_ids:
            labels = data.get("labels", [])
            added_label_names = [
                lbl.get("name", "").lower()
                for lbl in labels
                if lbl.get("id", "") in added_label_ids
            ]
            if TRIGGER_LABEL in added_label_names:
                issue_id = data.get("id", "")
                issue_identifier = data.get("identifier", "")
                issue_title = data.get("title", "")
                issue_url = data.get("url", "")
                logger.info(
                    "[LINEAR-WEBHOOK] Trigger: '%s' label added to issue \u2014 %s: %s (force re-process)",
                    TRIGGER_LABEL, issue_identifier, issue_title,
                )

                # Generate conversation_id upfront for tracking link
                conversation_id = str(uuid.uuid4())

                # Acknowledge the re-triggered issue immediately (includes tracking link)
                asyncio.create_task(
                    _acknowledge_new_issue(issue_id, issue_identifier, conversation_id)
                )

                asyncio.create_task(
                    _process_linear_issue(
                        issue_id, issue_identifier, issue_title, issue_url,
                        skip_duplicate_check=True,
                        conversation_id=conversation_id,
                    )
                )
                return web.json_response({"status": "accepted", "trigger": "label_added"})

        # --- Trigger 1b: State transition to Released to Production / Done → size if unsized ---
        # Sizes the ticket just-in-time before the metrics pipeline reads it. Re-fires are
        # safe in three ways:
        #   - The estimate==0 check here suppresses re-fires for any later state hop on a
        #     ticket the sizer already touched (sizer writes a non-zero estimate).
        #   - The rubric prompt itself re-checks the estimate before save_issue, in case
        #     a human edited the ticket between webhook receive and agent execution.
        #   - The save_issue call below WILL fire an Issue update webhook (with
        #     updatedFrom.estimate present), but since the new estimate is non-zero the
        #     guard above filters it out — no loop.
        state_changed = "stateId" in updated_from
        new_state_name = (data.get("state") or {}).get("name", "")
        current_estimate = data.get("estimate") or 0
        if (state_changed
                and new_state_name in (RELEASED_STATE, RELEASED_DONE_STATE)
                and current_estimate == 0):
            issue_id = data.get("id", "")
            issue_identifier = data.get("identifier", "")
            if issue_identifier:
                # Generate conversation_id upfront so the run is findable via the
                # observer + tracking links (parity with _process_linear_issue).
                sizing_conversation_id = str(uuid.uuid4())
                logger.info(
                    "[LINEAR-WEBHOOK] Trigger: state→%s on unsized %s — invoking sizer (conv=%s)",
                    new_state_name, issue_identifier, sizing_conversation_id,
                )
                asyncio.create_task(
                    _size_issue_on_release(
                        issue_id=issue_id,
                        issue_identifier=issue_identifier,
                        entered_state=new_state_name,
                        conversation_id=sizing_conversation_id,
                    )
                )
                return web.json_response({
                    "status": "accepted",
                    "trigger": "state_transition_sizing",
                    "conversation_id": sizing_conversation_id,
                })

    # --- Trigger 2: Comment containing 'loma' ---
    if event_type == "Comment" and action == "create":
        comment_body = data.get("body", "")
        comment_id = data.get("id", "")

        # Ignore our own comments (loop prevention)
        if AGENT_COMMENT_MARKER in comment_body:
            logger.info("[LINEAR-WEBHOOK] Ignoring own agent comment (loop prevention)")
            return web.json_response({"status": "ignored", "reason": "self-comment"})

        # Check for trigger phrase (case-insensitive)
        if re.search(TRIGGER_PHRASE, comment_body, re.IGNORECASE):
            issue_data = data.get("issue", {})
            issue_id = issue_data.get("id", "")
            issue_identifier = issue_data.get("identifier", "")
            issue_title = issue_data.get("title", "")
            # Linear comment webhooks don't include issue URL, construct it
            issue_url = f"https://linear.app/issue/{issue_identifier}" if issue_identifier else ""

            # React to the comment with \U0001f440 emoji and post tracking link
            if comment_id:
                asyncio.create_task(
                    _acknowledge_comment(comment_id, issue_identifier)
                )

            # --- Thread-aware conversation continuity (GO-90) ---
            # Look up ANY existing conversation for this issue (completed or running),
            # not just ones with a PR.  This unifies the comment handling path:
            # follow-up questions, modification requests, and new implementations
            # all resume the same conversation thread.
            existing_conversation = None
            db = get_db()
            if db is not None:
                existing_conversation = await db.conversations.find_one(
                    {
                        "metadata.linear_issue_id": issue_id,
                        "source": "linear_webhook",
                        "status": {"$in": ["completed", "running"]},
                    },
                    sort=[("finished_at", -1)],
                )

            if existing_conversation:
                existing_conversation_id = existing_conversation.get("conversation_id", "")
                logger.info(
                    "[LINEAR-WEBHOOK] Trigger: comment on issue with existing conversation %s \u2014 %s: %s (resume)",
                    existing_conversation_id, issue_identifier, issue_title,
                )

                # Generate conversation_id for tracking — but we'll resume the existing one
                conversation_id = existing_conversation_id

                # Post tracking link pointing to the existing conversation
                if issue_id:
                    asyncio.create_task(
                        _post_comment_tracking(issue_id, issue_identifier, conversation_id)
                    )

                asyncio.create_task(
                    _process_linear_comment_on_existing(
                        issue_id, issue_identifier, issue_title, issue_url,
                        comment_body=comment_body,
                        existing_conversation=existing_conversation,
                    )
                )
                return web.json_response({"status": "accepted", "trigger": "comment_resume"})
            else:
                # No existing conversation — detect intent (question vs implementation)
                logger.info(
                    "[LINEAR-WEBHOOK] Trigger: comment contains '%s', no existing conversation \u2014 %s: %s (detect intent)",
                    TRIGGER_PHRASE, issue_identifier, issue_title,
                )

                # Generate conversation_id upfront for tracking link
                conversation_id = str(uuid.uuid4())

                if issue_id:
                    asyncio.create_task(
                        _post_comment_tracking(issue_id, issue_identifier, conversation_id)
                    )

                asyncio.create_task(
                    _process_linear_issue(
                        issue_id, issue_identifier, issue_title, issue_url,
                        trigger_comment=comment_body,
                        conversation_id=conversation_id,
                    )
                )
                return web.json_response({"status": "accepted", "trigger": "comment_detect_intent"})
        else:
            logger.info("[LINEAR-WEBHOOK] Comment without trigger phrase \u2014 ignoring")
            return web.json_response({"status": "ignored", "reason": "no_trigger_phrase"})

    logger.info("[LINEAR-WEBHOOK] Unhandled event type=%s action=%s \u2014 ignoring", event_type, action)
    return web.json_response({"status": "ignored", "reason": "unhandled_event"})


async def _acknowledge_new_issue(
    issue_id: str, issue_identifier: str, conversation_id: str,
):
    """Post an acknowledgment comment on a newly created Linear issue."""
    try:
        comment_id = await post_acknowledgment_comment(issue_id, conversation_id)
        if comment_id:
            logger.info(
                "[LINEAR-WEBHOOK] Acknowledged new issue %s with comment %s",
                issue_identifier, comment_id,
            )
        else:
            logger.warning(
                "[LINEAR-WEBHOOK] Failed to acknowledge new issue %s",
                issue_identifier,
            )
    except Exception:
        logger.exception(
            "[LINEAR-WEBHOOK] Error acknowledging new issue %s", issue_identifier
        )


async def _acknowledge_comment(comment_id: str, issue_identifier: str):
    """React to a Linear comment with an emoji to acknowledge it."""
    try:
        success = await react_to_comment(comment_id, "\U0001f440")
        if success:
            logger.info(
                "[LINEAR-WEBHOOK] Reacted to comment %s on issue %s",
                comment_id, issue_identifier,
            )
        else:
            logger.warning(
                "[LINEAR-WEBHOOK] Failed to react to comment %s on issue %s",
                comment_id, issue_identifier,
            )
    except Exception:
        logger.exception(
            "[LINEAR-WEBHOOK] Error reacting to comment %s on issue %s",
            comment_id, issue_identifier,
        )


async def _post_comment_tracking(
    issue_id: str, issue_identifier: str, conversation_id: str,
):
    """Post a tracking link comment for comment-triggered processing."""
    try:
        comment_id = await post_comment_tracking_acknowledgment(
            issue_id, conversation_id,
        )
        if comment_id:
            logger.info(
                "[LINEAR-WEBHOOK] Posted tracking link comment on issue %s (comment_id=%s)",
                issue_identifier, comment_id,
            )
        else:
            logger.warning(
                "[LINEAR-WEBHOOK] Failed to post tracking link comment on issue %s",
                issue_identifier,
            )
    except Exception:
        logger.exception(
            "[LINEAR-WEBHOOK] Error posting tracking link comment on issue %s",
            issue_identifier,
        )


async def _process_linear_issue(
    issue_id: str,
    issue_identifier: str,
    issue_title: str,
    issue_url: str,
    trigger_comment: str | None = None,
    skip_duplicate_check: bool = False,
    conversation_id: str | None = None,
):
    """Run the agent to handle a Linear ticket \u2014 either answer a question or implement and create a PR."""
    logger.info(
        "[LINEAR-WEBHOOK] Processing issue %s (%s): %s",
        issue_identifier, issue_id, issue_title,
    )

    # Check for duplicate processing (skip when re-triggered via label)
    db = get_db()
    if not skip_duplicate_check and db is not None:
        existing = await db.conversations.find_one({
            "metadata.linear_issue_id": issue_id,
            "source": "linear_webhook",
            "status": {"$in": ["running", "completed"]},
        })
        if existing:
            logger.info(
                "[LINEAR-WEBHOOK] Issue %s already processed (conversation: %s) \u2014 skipping",
                issue_identifier, existing.get("conversation_id"),
            )
            return

    # Build the prompt for the agent with intent detection
    prompt_parts = [
        f"A comment was posted on Linear ticket {issue_identifier} that mentions 'loma'.",
        f"- Linear Issue ID: {issue_identifier}",
        f"- Issue URL: {issue_url}",
        f"- Title: {issue_title}",
    ]
    if trigger_comment:
        prompt_parts.append(f"- Comment text: {trigger_comment}")

    prompt_parts.extend([
        "",
        "## Intent Detection",
        "",
        "First, read the comment and the ticket carefully. Determine the intent:",
        "",
        "**Option A \u2014 Question / Analysis / Discussion:**",
        "If the comment is asking a question, requesting analysis, seeking advice,",
        "or discussing approach (e.g., 'loma \u2014 what's the best way to implement this?',",
        "'loma can you check if this is feasible?', 'loma what do you think about X?'),",
        "then:",
        f"1. Read the full Linear ticket details for {issue_identifier} using Linear MCP tools",
        "2. Research the question \u2014 search the codebase, docs, or other resources as needed",
        "3. Post your answer as a comment on the Linear ticket",
        f"   Use `mcp__linear__create_comment` with the issue ID for {issue_identifier}",
        "   The comment body MUST:",
        f"   - Start with `{AGENT_COMMENT_MARKER}` (invisible marker for loop prevention)",
        "   - Contain your analysis/answer in clear markdown",
        "   - Be concise and actionable",
        "   Do NOT create a PR in this case.",
        "",
        "**Option B \u2014 Implementation Request:**",
        "If the comment is requesting code changes, implementation, or just says 'loma'",
        "without additional context (implying 'implement this ticket'), then:",
        "1. Load the `implement-ticket` skill",
        f"2. Read the full Linear ticket details for {issue_identifier} using Linear MCP tools",
        "3. Follow the implement-ticket playbook (Steps 1-7) but with these modifications:",
        "   - Skip Step 4 (present plan) \u2014 this is an automated webhook flow, no human approval needed",
        "   - After creating the draft PR, extract the PR URL from the result",
        f"4. After creating the PR, comment on the Linear ticket {issue_identifier} with the PR details",
        f"   Use the Linear MCP tool `mcp__linear__create_comment` with the issue ID",
        "   The comment body MUST:",
        f"   - Start with `{AGENT_COMMENT_MARKER}` (invisible marker for loop prevention)",
        "   - Include a summary of changes made",
        "   - Include the PR URL",
        "   - List the files changed",
        "",
        "   Example comment format:",
        f"   {AGENT_COMMENT_MARKER}",
        "   **PR Created** :white_check_mark:",
        "",
        "   [View Draft PR](pr-url-here)",
        "",
        "   **Changes:**",
        "   - `path/to/file1.ts` \u2014 description",
        "   - `path/to/file2.ts` \u2014 description",
        "",
        "   _This PR was auto-generated by Loma. Please review before merging._",
    ])

    prompt = "\n".join(prompt_parts)

    # Set up observability \u2014 use the pre-generated conversation_id
    observer = None
    if db is not None:
        observer = ConversationObserver(db, metadata={
            "source": "linear_webhook",
            "prompt": prompt,
            "model": os.environ.get("CLAUDE_MODEL", ""),
            "linear_issue_id": issue_id,
            "linear_issue_identifier": issue_identifier,
            "linear_issue_title": issue_title,
            "linear_issue_url": issue_url,
            "trigger_type": "comment" if trigger_comment else "label_added",
        }, conversation_id=conversation_id)
        await observer.start()

    try:
        async for text in stream_agent(prompt=prompt, observer=observer):
            logger.info("[LINEAR-WEBHOOK] Agent output: %.500s", text)
        logger.info("[LINEAR-WEBHOOK] Agent processing complete for issue %s", issue_identifier)
    except Exception:
        logger.exception("[LINEAR-WEBHOOK] Agent processing failed for issue %s", issue_identifier)


async def _size_issue_on_release(
    issue_id: str,
    issue_identifier: str,
    entered_state: str,
    conversation_id: str | None = None,
):
    """Size one Linear ticket using the Fibonacci rubric.

    Triggered when a ticket transitions into `Released to Production` or `Done`
    with `estimate=0/null`. The prompt itself re-checks `estimate` before writing
    so racing or duplicate webhooks are no-ops (won't overwrite a real value).

    `conversation_id` is passed by the caller so the resulting agent run is
    findable via tracking-comment links and conversation-resume logic, mirroring
    the pattern used by `_process_linear_issue`.

    Audit doc to loma_observability.eng_sizing_log is written from here
    (Python side) after the agent finishes, because the agent's MongoDB MCP is
    read-only by policy. The agent's final output line carries the chosen
    estimate + one-line reasoning in a deterministic format the regex above
    parses.
    """
    logger.info(
        "[SIZER] Sizing %s on entry to '%s' (conv=%s)",
        issue_identifier, entered_state, conversation_id,
    )

    prompt = build_sizing_prompt(identifier=issue_identifier)

    db = get_db()
    observer = None
    if db is not None:
        observer = ConversationObserver(db, metadata={
            "source": "linear_webhook",
            "prompt": prompt,
            "model": os.environ.get("CLAUDE_MODEL", ""),
            "linear_issue_id": issue_id,
            "linear_issue_identifier": issue_identifier,
            "trigger_type": "state_transition_sizing",
            "entered_state": entered_state,
        }, conversation_id=conversation_id)
        await observer.start()

    last_text = ""
    try:
        async for text in stream_agent(prompt=prompt, observer=observer):
            if isinstance(text, str):
                last_text = text
                logger.info("[SIZER] Agent output: %.300s", text)
        logger.info("[SIZER] Done sizing %s", issue_identifier)
    except Exception:
        logger.exception("[SIZER] Failed to size %s", issue_identifier)
        return

    # Parse the agent's final line and write the audit doc from Python.
    parsed = parse_single_result(last_text)
    if parsed is None:
        logger.warning(
            "[SIZER] No parseable result line for %s — audit doc not written. "
            "Last text was %.200s",
            issue_identifier, last_text,
        )
        return
    if parsed.get("skipped"):
        logger.info("[SIZER] Idempotency guard tripped for %s — no audit doc", issue_identifier)
        return
    await write_audit_doc(
        db,
        identifier=issue_identifier,
        linear_issue_id=issue_id,
        estimate_set=parsed["estimate"],
        prior_estimate=0,  # we only get here when the webhook check saw estimate==0
        reasoning=parsed["reasoning"],
        trigger="webhook",
        conversation_id=conversation_id,
    )


async def _process_linear_comment_on_existing(
    issue_id: str,
    issue_identifier: str,
    issue_title: str,
    issue_url: str,
    comment_body: str,
    existing_conversation: dict,
):
    """Handle a follow-up comment on a Linear ticket that already has a conversation.

    Resumes the existing conversation and passes prior message context so the
    agent is aware of all previous work on this issue.  Detects whether the
    comment is a question, a PR modification request, or a new implementation.
    """
    existing_conversation_id = existing_conversation.get("conversation_id", "")
    logger.info(
        "[LINEAR-WEBHOOK] Resuming conversation %s for issue %s (%s): comment=%.200s",
        existing_conversation_id, issue_identifier, issue_id, comment_body,
    )

    # Build conversation context from prior messages
    prior_messages = existing_conversation.get("messages", [])
    conversation_context = _build_conversation_context(prior_messages)

    # Build the prompt with intent detection
    prompt_parts = [
        f"A follow-up comment was posted on Linear ticket {issue_identifier}.",
        f"- Linear Issue ID: {issue_identifier}",
        f"- Issue URL: {issue_url}",
        f"- Title: {issue_title}",
        f"- Comment text: {comment_body}",
        "",
        "This ticket has a prior conversation with the agent (context provided above).",
        "",
        "## Intent Detection",
        "",
        "First, read the comment carefully. Determine the intent:",
        "",
        "**Option A \u2014 Question / Analysis / Discussion:**",
        "If the comment is asking a question about previous work, requesting a review,",
        "seeking clarification, or discussing the implementation approach",
        "(e.g., 'loma \u2014 why did you use this approach?', 'loma can you explain this change?'),",
        "then:",
        f"1. Read the full Linear ticket details for {issue_identifier} using Linear MCP tools",
        "2. Find and read the existing draft PR (if one exists) to understand the current changes",
        "3. Research and formulate your answer",
        "4. Post your answer as a comment on the Linear ticket",
        f"   Use `mcp__linear__create_comment` with the issue ID for {issue_identifier}",
        "   The comment body MUST:",
        f"   - Start with `{AGENT_COMMENT_MARKER}` (invisible marker for loop prevention)",
        "   - Contain your analysis/answer in clear markdown",
        "   - Reference the PR if relevant",
        "   Do NOT modify the PR in this case.",
        "",
        "**Option B \u2014 Implementation / PR Modification Request:**",
        "If the comment is requesting code changes, fixes, modifications to an existing PR,",
        "or a new implementation (e.g., 'loma \u2014 implement this', 'loma \u2014 also add tests',",
        "'loma fix the linting errors', 'loma \u2014 change the approach to use X instead'), then:",
        "1. Load the `implement-ticket` skill",
        f"2. Read the full Linear ticket details for {issue_identifier} using Linear MCP tools",
        "3. Check if a draft PR already exists for this ticket on GitHub:",
        f"   - Search for open PRs in the relevant repo with the ticket ID '{issue_identifier}' in the title or branch name",
        "   - Use `mcp__github__list_pull_requests` or `mcp__github__search_pull_requests` to find it",
        "4. If a PR exists:",
        "   - Read the comment above carefully and determine what changes are requested",
        "   - Read the current file contents from the PR branch",
        "   - Push additional commits to the same PR branch with the requested changes",
        "   - Use `mcp__github__push_files` to push to the existing branch",
        "   - Keep the commit message descriptive, referencing the Linear ticket",
        "   - Update the PR description if the changes are significant enough to warrant it",
        "5. If no PR exists:",
        "   - Follow the implement-ticket playbook (Steps 1-7) but with these modifications:",
        "     - Skip Step 4 (present plan) \u2014 this is an automated webhook flow, no human approval needed",
        "     - After creating the draft PR, extract the PR URL from the result",
        f"6. Comment on the Linear ticket {issue_identifier} confirming the changes were made",
        f"   Use `mcp__linear__create_comment` with the issue ID",
        "   The comment body MUST:",
        f"   - Start with `{AGENT_COMMENT_MARKER}` (invisible marker for loop prevention)",
        "   - Summarize what was changed based on the comment",
        "   - Include the PR URL",
        "   - List the files modified",
        "",
        "   Example comment format:",
        f"   {AGENT_COMMENT_MARKER}",
        "   **PR Updated** :pencil:",
        "",
        "   [View Draft PR](pr-url-here)",
        "",
        "   **Changes based on your comment:**",
        "   - `path/to/file1.ts` \u2014 description of change",
        "",
        "   _This update was auto-generated by Loma. Please review before merging._",
    ]

    prompt = "\n".join(prompt_parts)

    # Set up observability \u2014 resume the existing conversation
    db = get_db()
    observer = None
    if db is not None:
        observer = ConversationObserver(db, metadata={
            "source": "linear_webhook",
            "prompt": prompt,
            "model": os.environ.get("CLAUDE_MODEL", ""),
            "linear_issue_id": issue_id,
            "linear_issue_identifier": issue_identifier,
            "linear_issue_title": issue_title,
            "linear_issue_url": issue_url,
            "trigger_type": "comment_resume",
            "trigger_comment": comment_body[:2000],
        }, conversation_id=existing_conversation_id)
        await observer.resume()

    try:
        async for text in stream_agent(
            prompt=prompt,
            conversation_context=conversation_context,
            observer=observer,
        ):
            logger.info("[LINEAR-WEBHOOK] Agent output (resume): %.500s", text)
        logger.info("[LINEAR-WEBHOOK] Agent handling complete for issue %s", issue_identifier)
    except Exception:
        logger.exception("[LINEAR-WEBHOOK] Agent handling failed for issue %s", issue_identifier)


def setup_linear_webhook_routes(app: web.Application):
    """Register Linear webhook routes on the aiohttp app."""
    app.router.add_post("/webhooks/linear", handle_linear_webhook)
