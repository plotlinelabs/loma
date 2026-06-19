import asyncio
import json
import logging
import os
import re as re_module

from agent.client import stream_agent
from api.dashboard_ingestion import ingest_dashboard_chat
from observability.db import get_db
from observability.observer import ConversationObserver
from slack_app.channels import get_channel_config
from slack_app.utils import (
    strip_bot_mention, truncate_for_slack, get_thread_context,
    get_dm_context, download_slack_files, BOT_MENTION_RE,
)
from draft_with_loma.models import create_draft, get_draft, update_draft, delete_draft
from draft_with_loma.blocks import (
    build_draft_context_modal,
    build_edit_context_modal,
    build_draft_review_blocks,
)
from draft_with_loma.auth import get_user_slack_token

logger = logging.getLogger(__name__)

THINKING_EMOJI = "hourglass_flowing_sand"

CONVERSATION_TRACKER_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:3001").rstrip("/") + "/conversations"


async def _stream_response(client, channel, thread_ts, react_ts, agent_stream):
    """
    Consume the agent stream and post only the final response.

    All intermediate text chunks are collected silently (the hourglass
    reaction stays on the original message as a progress indicator).
    Only the last chunk is posted to the thread.
    """
    last_text = ""

    async for text in agent_stream:
        last_text = text
        logger.info("[SLACK] Received chunk (%d chars), buffering...", len(text))

    # Remove the hourglass reaction
    try:
        await client.reactions_remove(
            name=THINKING_EMOJI,
            channel=channel,
            timestamp=react_ts,
        )
    except Exception as e:
        logger.warning("[SLACK] Failed to remove reaction: %s", e)

    if not last_text:
        await client.chat_postMessage(
            channel=channel,
            text="I didn't generate a response. Please try again.",
            thread_ts=thread_ts,
        )
        return

    # Post only the final response
    final_text = truncate_for_slack(last_text)
    logger.info("[SLACK] Posting final response (%d chars)", len(final_text))
    await client.chat_postMessage(
        channel=channel,
        text=final_text,
        thread_ts=thread_ts,
    )


async def _maybe_update_flow_memory(db, conversation, prompt):
    """If this conversation belongs to a flow, update its memory.

    Called after the agent finishes processing a reply in a flow output
    thread.  Uses a lightweight Haiku call to merge the feedback into
    the flow's rolling memory_state.
    """
    flow_id = (conversation.get("metadata") or {}).get("flow_id")
    if not flow_id:
        return

    logger.info("[MEMORY] Reply in flow thread detected (flow_id=%s), updating memory", flow_id)

    try:
        # Build a minimal feedback transcript from the conversation messages
        messages = conversation.get("messages", [])
        feedback_parts = []
        for msg in messages[-10:]:  # last 10 messages to keep context bounded
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            feedback_parts.append(f"{role}: {content[:1000]}")

        # Include the current user reply as the latest feedback
        feedback_parts.append(f"user: {prompt[:1000]}")
        feedback_conversation = "\n".join(feedback_parts)

        from scheduler.memory import update_flow_memory
        await update_flow_memory(db, flow_id, feedback_conversation)
    except Exception:
        logger.exception("[MEMORY] Failed to update flow memory for flow %s", flow_id)


async def _handle_agent_request(
    client, channel, thread_ts, event_ts, prompt, context, files, source, user_id,
):
    """Common flow: hourglass \u2192 observer \u2192 stream agent \u2192 post responses.

    Used by app_mention, DM, and monitored channel handlers to avoid duplication.
    """
    # Add hourglass reaction as acknowledgement
    logger.info("[SLACK] Adding hourglass reaction to message...")
    try:
        await client.reactions_add(
            name=THINKING_EMOJI,
            channel=channel,
            timestamp=event_ts,
        )
    except Exception as e:
        logger.warning("[SLACK] Failed to add reaction: %s", e)

    try:
        # Set up observability — reuse existing conversation for same Slack thread
        observer = None
        existing_convo = None
        db = get_db()
        if db is not None:
            existing_convo = await db.conversations.find_one(
                {
                    "metadata.slack_channel_id": channel,
                    "metadata.slack_thread_ts": thread_ts,
                },
            )
            # Resolve Slack user email for filtering
            user_email = None
            try:
                user_info = await client.users_info(user=user_id)
                user_email = user_info["user"]["profile"].get("email")
            except Exception as e:
                logger.warning("[SLACK] Failed to fetch user email: %s", e)

            metadata = {
                "source": source,
                "prompt": prompt,
                "model": os.environ.get("CLAUDE_MODEL", ""),
                "slack_user_id": user_id,
                "slack_channel_id": channel,
                "slack_thread_ts": thread_ts,
                "user_name": user_email or user_id,
            }
            if existing_convo:
                observer = ConversationObserver(
                    db, metadata=metadata,
                    conversation_id=existing_convo["conversation_id"],
                )
                await observer.resume()
            else:
                observer = ConversationObserver(db, metadata=metadata)
                await observer.start()

            # Only post the tracking link for NEW conversations (first message in thread)
            if not existing_convo:
                tracking_url = f"{CONVERSATION_TRACKER_BASE_URL}/{observer.conversation_id}"
                try:
                    await client.chat_postMessage(
                        channel=channel,
                        text=f"\u23f3 Working on it! Follow progress \u2192 {tracking_url}",
                        thread_ts=thread_ts,
                    )
                except Exception as e:
                    logger.warning("[SLACK] Failed to post tracking link: %s", e)

        # Stream the agent response
        logger.info("[AGENT] Starting streaming agent run...")
        agent_stream = stream_agent(
            prompt=prompt,
            conversation_context=context,
            files=files if files else None,
            observer=observer,
            user_email=user_email,
        )
        await _stream_response(client, channel, thread_ts, event_ts, agent_stream)
        logger.info("[SLACK] All responses posted successfully")

        # Fire-and-forget: ingest this conversation as a change-stream event.
        if observer:
            asyncio.create_task(ingest_dashboard_chat(
                observer.conversation_id, prompt, user_email or "",
            ))

        # --- Post-feedback memory update for scheduled task threads ---
        # If this reply was in a thread belonging to a scheduled task,
        # fire-and-forget a memory update so the next task run benefits
        # from the user's feedback.
        if existing_convo and db is not None:
            asyncio.create_task(
                _maybe_update_flow_memory(db, existing_convo, prompt)
            )

    except Exception as e:
        logger.exception("Error in agent request")
        try:
            await client.reactions_remove(
                name=THINKING_EMOJI,
                channel=channel,
                timestamp=event_ts,
            )
        except Exception:
            pass
        await client.chat_postMessage(
            channel=channel,
            text=f":x: Sorry, something went wrong: {e}",
            thread_ts=thread_ts,
        )


def register_handlers(app):
    """Register all Slack event handlers on the app."""

    @app.event("app_mention")
    async def handle_mention(event, client, say):
        """Handle @mentions of the bot in channels."""
        channel = event.get("channel")
        user = event.get("user")
        text = event.get("text", "")
        event_ts = event.get("ts")
        thread_ts = event.get("thread_ts") or event_ts

        logger.info("[SLACK] app_mention from user=%s channel=%s thread=%s", user, channel, thread_ts)
        logger.info("[SLACK] Raw text: %s", text)

        user_message = strip_bot_mention(text)
        logger.info("[SLACK] Cleaned message: %s", user_message)

        if not user_message:
            logger.info("[SLACK] Empty message after stripping mention, sending default reply")
            await say(
                text="Hey! How can I help? Ask me anything about your company.",
                thread_ts=thread_ts,
            )
            return

        # Gather thread context (including files from earlier messages)
        context = ""
        thread_raw_files = []
        if event.get("thread_ts"):
            logger.info("[SLACK] Fetching thread context for thread=%s", thread_ts)
            context, thread_raw_files = await get_thread_context(client, channel, thread_ts, current_ts=event_ts)
            logger.info("[SLACK] Thread context: %d chars, %d file(s) from thread", len(context), len(thread_raw_files))

        # Download files: current message files + files from earlier thread messages
        files = []
        raw_files = event.get("files", [])
        all_raw_files = raw_files + thread_raw_files
        if all_raw_files:
            logger.info("[SLACK] Downloading %d file(s) (%d current, %d from thread)...", len(all_raw_files), len(raw_files), len(thread_raw_files))
            files = await download_slack_files(client.token, all_raw_files)

        # Check if this is a monitored channel — prepend channel context if so
        channel_config = get_channel_config(channel)
        if channel_config:
            prompt = channel_config["prompt_prefix"] + user_message
            source = channel_config["source"]
            logger.info("[SLACK] Monitored channel #%s \u2014 using channel-specific prompt", channel_config["name"])
        else:
            prompt = user_message
            source = "slack_mention"

        await _handle_agent_request(
            client, channel, thread_ts, event_ts, prompt, context, files, source, user,
        )

    @app.event("message")
    async def handle_message(event, client, say):
        """Handle DMs and messages in monitored channels."""
        # Ignore bot's own messages and non-user subtypes
        # Allow "file_share" so messages with images/files are processed
        # Exception: monitored channels with allow_bot_messages=True (e.g., #alerts)
        subtype = event.get("subtype")
        is_bot = bool(event.get("bot_id"))
        if is_bot:
            channel_cfg = get_channel_config(event.get("channel", ""))
            if not (channel_cfg and channel_cfg.get("allow_bot_messages")):
                return
        if subtype and subtype not in ("file_share", "bot_message"):
            return

        channel = event.get("channel")
        user = event.get("user") or event.get("bot_id", "unknown_bot")
        text = event.get("text", "")
        event_ts = event.get("ts")
        thread_ts = event.get("thread_ts") or event_ts
        has_files = bool(event.get("files"))

        # --- Monitored channels (e.g., #bugs, #feature-requests-clients) ---
        channel_config = get_channel_config(channel)
        if channel_config:
            # Skip messages that contain a bot @mention — those are handled by app_mention
            if BOT_MENTION_RE.search(text):
                return

            # Skip thread replies — only auto-respond to new top-level messages.
            # Thread replies are handled by app_mention if the bot is @mentioned.
            if event.get("thread_ts"):
                return

            user_message = text.strip()
            if not user_message and not has_files:
                return

            logger.info(
                "[SLACK] New message in #%s from user=%s",
                channel_config["name"], user,
            )

            # Download any attached files (top-level only, no thread context)
            files = []
            raw_files = event.get("files", [])
            if raw_files:
                logger.info("[SLACK] Message has %d file(s), downloading...", len(raw_files))
                files = await download_slack_files(client.token, raw_files)

            prompt = channel_config["prompt_prefix"] + user_message
            await _handle_agent_request(
                client, channel, thread_ts, event_ts, prompt, "", files,
                channel_config["source"], user,
            )
            return

        # --- DMs ---
        if event.get("channel_type") != "im":
            return

        logger.info("[SLACK] DM from user=%s channel=%s thread=%s", user, channel, thread_ts)
        logger.info("[SLACK] Message: %s", text)

        if not text.strip() and not has_files:
            logger.info("[SLACK] Empty DM with no files, ignoring")
            return

        # Gather thread context (including files from earlier messages)
        context = ""
        thread_raw_files = []
        if event.get("thread_ts"):
            logger.info("[SLACK] Fetching thread context for thread=%s", thread_ts)
            context, thread_raw_files = await get_thread_context(client, channel, thread_ts, current_ts=event_ts)
            logger.info("[SLACK] Thread context: %d chars, %d file(s) from thread", len(context), len(thread_raw_files))
        else:
            logger.info("[SLACK] New top-level DM \u2014 no prior context")

        # Download files: current message files + files from earlier thread messages
        files = []
        raw_files = event.get("files", [])
        all_raw_files = raw_files + thread_raw_files
        if all_raw_files:
            logger.info("[SLACK] Downloading %d file(s) (%d current, %d from thread)...", len(all_raw_files), len(raw_files), len(thread_raw_files))
            files = await download_slack_files(client.token, all_raw_files)

        prompt = text or "What is in this file?"
        await _handle_agent_request(
            client, channel, thread_ts, event_ts, prompt, context, files,
            "slack_dm", user,
        )

    # ─── Draft with Loma: message shortcut ───────────────────────────

    @app.shortcut("draft_with_loma")
    async def handle_draft_shortcut(ack, shortcut, client):
        """User clicked '... → Draft with Loma' on a message."""
        await ack()
        try:
            await client.views_open(
                trigger_id=shortcut["trigger_id"],
                view=build_draft_context_modal(shortcut),
            )
        except Exception as e:
            logger.exception("[DRAFT] Failed to open context modal: %s", e)

    # ─── Draft with Loma: initial draft submission ───────────────────

    @app.view("draft_with_loma_submit")
    async def handle_draft_submit(ack, body, client, view):
        """User submitted the context modal — generate a draft."""
        await ack()

        meta = json.loads(view["private_metadata"])
        channel_id = meta["channel_id"]
        thread_ts = meta["thread_ts"]
        slack_user_id = body["user"]["id"]

        # Extract optional user context
        values = view["state"]["values"]
        user_context = (
            values.get("context_block", {})
            .get("user_context", {})
            .get("value") or ""
        )

        asyncio.create_task(
            _generate_and_post_draft(
                client, slack_user_id, channel_id, thread_ts, user_context,
            )
        )

    # ─── Draft with Loma: edit re-draft submission ───────────────────

    @app.view("draft_with_loma_edit_submit")
    async def handle_edit_submit(ack, body, client, view):
        """User submitted edit feedback — re-draft."""
        await ack()

        meta = json.loads(view["private_metadata"])
        draft_id = meta["draft_id"]
        slack_user_id = body["user"]["id"]

        edit_context = (
            view["state"]["values"]
            .get("edit_context_block", {})
            .get("edit_context", {})
            .get("value") or ""
        )

        asyncio.create_task(
            _redraft_and_post(client, slack_user_id, draft_id, edit_context)
        )

    # ─── Draft with Loma: action button handlers ─────────────────────

    @app.action(re_module.compile(r"^dwg_send_"))
    async def handle_dwg_send(ack, body, client):
        """Send the draft as the user in the original thread."""
        await ack()
        from slack_sdk.web.async_client import AsyncWebClient
        action_id = body["actions"][0]["action_id"]
        draft_id = action_id.replace("dwg_send_", "")
        slack_user_id = body["user"]["id"]

        try:
            draft = await get_draft(draft_id)
            if draft is None:
                logger.warning("[DRAFT] Draft %s not found for send", draft_id)
                return

            # Get user's OAuth token and send as them
            user_token = await get_user_slack_token(draft["user_email"])
            user_client = AsyncWebClient(token=user_token)
            await user_client.chat_postMessage(
                channel=draft["channel_id"],
                text=draft["draft_text"],
                thread_ts=draft["thread_ts"],
            )
            await update_draft(draft_id, status="sent")

            # Confirm via ephemeral
            await _post_ephemeral_safe(
                client, user_client, draft["channel_id"], slack_user_id,
                draft["thread_ts"],
                f":white_check_mark: Sent!\n>{draft['draft_text'][:500]}",
            )
        except Exception as e:
            logger.exception("[DRAFT] Failed to send draft %s: %s", draft_id, e)

    @app.action(re_module.compile(r"^dwg_edit_"))
    async def handle_dwg_edit(ack, body, client):
        """Open the edit modal for re-drafting."""
        await ack()
        action_id = body["actions"][0]["action_id"]
        draft_id = action_id.replace("dwg_edit_", "")

        try:
            draft = await get_draft(draft_id)
            if draft is None:
                logger.warning("[DRAFT] Draft %s not found for edit", draft_id)
                return

            await client.views_open(
                trigger_id=body["trigger_id"],
                view=build_edit_context_modal(draft_id, draft["draft_text"]),
            )
        except Exception as e:
            logger.exception("[DRAFT] Failed to open edit modal: %s", e)

    @app.action(re_module.compile(r"^dwg_dismiss_"))
    async def handle_dwg_dismiss(ack, body, client):
        """Dismiss the draft."""
        await ack()
        action_id = body["actions"][0]["action_id"]
        draft_id = action_id.replace("dwg_dismiss_", "")
        slack_user_id = body["user"]["id"]

        try:
            draft = await get_draft(draft_id)
            if draft is not None:
                await delete_draft(draft_id)
                await _post_ephemeral_safe(
                    client, None, draft["channel_id"], slack_user_id,
                    draft["thread_ts"], ":no_entry_sign: Draft dismissed.",
                )
        except Exception as e:
            logger.exception("[DRAFT] Failed to dismiss draft %s: %s", draft_id, e)


async def _post_ephemeral_safe(
    bot_client, user_client, channel_id: str, user_id: str, thread_ts: str,
    text: str, blocks: list | None = None,
):
    """Post an ephemeral message, falling back to user token if bot can't access the channel."""
    kwargs = {"channel": channel_id, "user": user_id, "thread_ts": thread_ts, "text": text}
    if blocks:
        kwargs["blocks"] = blocks

    # Try bot client first (ephemerals from bot look cleaner)
    try:
        await bot_client.chat_postEphemeral(**kwargs)
        return
    except Exception as e:
        logger.debug("[DRAFT] Bot ephemeral failed (%s), trying user token", e)

    # Fall back to user token
    if user_client is not None:
        try:
            await user_client.chat_postEphemeral(**kwargs)
            return
        except Exception as e:
            logger.warning("[DRAFT] User token ephemeral also failed: %s", e)

    logger.warning("[DRAFT] Could not post ephemeral to %s for user %s", channel_id, user_id)


async def _resolve_user_email(client, slack_user_id: str) -> str | None:
    """Resolve a Slack user ID to their email address."""
    try:
        info = await client.users_info(user=slack_user_id)
        return info["user"]["profile"].get("email")
    except Exception as e:
        logger.warning("[DRAFT] Failed to resolve email for %s: %s", slack_user_id, e)
        return None


async def _read_thread_with_user_token(user_token: str, channel_id: str, thread_ts: str) -> str:
    """Read a thread using the user's OAuth token and return formatted messages."""
    from slack_sdk.web.async_client import AsyncWebClient
    user_client = AsyncWebClient(token=user_token)

    result = await user_client.conversations_replies(
        channel=channel_id,
        ts=thread_ts,
        limit=50,
    )
    messages = result.get("messages", [])
    if not messages:
        return "(empty thread)"

    # Build a simple formatted view
    parts = []
    for msg in messages:
        user = msg.get("user", "unknown")
        text = msg.get("text", "")
        if msg.get("bot_id"):
            parts.append(f"[bot]: {text}")
        else:
            parts.append(f"[{user}]: {text}")
    return "\n".join(parts)


async def _generate_and_post_draft(
    client, slack_user_id: str, channel_id: str, thread_ts: str, user_context: str,
):
    """Read the thread, generate a draft via the agent, and post ephemeral."""
    from slack_sdk.web.async_client import AsyncWebClient

    try:
        user_email = await _resolve_user_email(client, slack_user_id)
        if not user_email:
            await _post_ephemeral_safe(
                client, None, channel_id, slack_user_id, thread_ts,
                ":x: Couldn't resolve your email. Please try again.",
            )
            return

        user_token = await get_user_slack_token(user_email)
        user_client = AsyncWebClient(token=user_token)

        # Read thread using user's token (works in private channels)
        thread_text = await _read_thread_with_user_token(user_token, channel_id, thread_ts)

        # Create draft record
        draft = await create_draft(
            user_email=user_email,
            slack_user_id=slack_user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_context=user_context,
        )

        # Generate draft via agent
        draft_text = await _call_agent_for_draft(
            thread_text=thread_text,
            user_context=user_context,
            user_email=user_email,
        )

        await update_draft(draft["draft_id"], draft_text=draft_text, status="drafted")

        # Post ephemeral with review buttons — try bot first, fall back to user token
        await _post_ephemeral_safe(
            client, user_client, channel_id, slack_user_id, thread_ts,
            draft_text,
            blocks=build_draft_review_blocks(draft["draft_id"], draft_text),
        )
    except Exception as e:
        logger.exception("[DRAFT] Failed to generate draft: %s", e)
        try:
            await _post_ephemeral_safe(
                client, None, channel_id, slack_user_id, thread_ts,
                f":x: Failed to generate draft: {e}",
            )
        except Exception:
            pass


async def _redraft_and_post(
    client, slack_user_id: str, draft_id: str, edit_context: str,
):
    """Re-read the thread, generate a new draft with edit feedback, post ephemeral."""
    from slack_sdk.web.async_client import AsyncWebClient
    draft = None
    try:
        draft = await get_draft(draft_id)
        if draft is None:
            logger.warning("[DRAFT] Draft %s not found for redraft", draft_id)
            return

        user_token = await get_user_slack_token(draft["user_email"])
        user_client = AsyncWebClient(token=user_token)
        thread_text = await _read_thread_with_user_token(
            user_token, draft["channel_id"], draft["thread_ts"],
        )

        # Generate new draft with edit context
        new_draft_text = await _call_agent_for_draft(
            thread_text=thread_text,
            user_context=draft.get("user_context", ""),
            user_email=draft["user_email"],
            previous_draft=draft["draft_text"],
            edit_feedback=edit_context,
        )

        await update_draft(draft_id, draft_text=new_draft_text, status="drafted")

        await _post_ephemeral_safe(
            client, user_client, draft["channel_id"], slack_user_id,
            draft["thread_ts"], new_draft_text,
            blocks=build_draft_review_blocks(draft_id, new_draft_text),
        )
    except Exception as e:
        logger.exception("[DRAFT] Failed to redraft %s: %s", draft_id, e)
        if draft:
            try:
                await _post_ephemeral_safe(
                    client, None, draft["channel_id"], slack_user_id,
                    draft["thread_ts"], f":x: Failed to re-draft: {e}",
                )
            except Exception:
                pass


async def _call_agent_for_draft(
    thread_text: str,
    user_context: str,
    user_email: str,
    previous_draft: str = "",
    edit_feedback: str = "",
) -> str:
    """Call the agent to generate a draft reply.

    Returns the draft text string.
    """
    parts = [
        "You are drafting a Slack reply on behalf of the user.",
        "Read the thread below. You may use tools to research context if needed.",
        "",
        "CRITICAL OUTPUT RULES:",
        "- Your FINAL output must contain ONLY the draft message text between <draft> and </draft> tags.",
        "- Do NOT include any narration, explanation, preamble, or commentary outside the tags.",
        "- Do NOT include phrases like 'Here is the draft' or 'Let me draft'.",
        "- The content inside <draft></draft> will be sent EXACTLY as-is as a Slack message.",
        "- Match the thread's tone and be concise.",
    ]

    if user_context:
        parts.append(f"\nUser's guidance: {user_context}")

    if previous_draft and edit_feedback:
        parts.append(f"\nPrevious draft:\n{previous_draft}")
        parts.append(f"\nUser's edit feedback: {edit_feedback}")
        parts.append("\nGenerate an updated draft incorporating the feedback.")

    parts.append(f"\n--- Thread ---\n{thread_text}\n--- End Thread ---")
    parts.append("\nNow output ONLY <draft>your message here</draft>:")

    prompt = "\n".join(parts)

    # Collect the full response from the agent stream
    last_text = ""
    async for text in stream_agent(
        prompt=prompt,
        source="draft_with_loma",
        user_email=user_email,
    ):
        last_text = text

    if not last_text:
        return "Sorry, I couldn't generate a draft. Please try again."

    return _extract_draft(last_text.strip())


def _extract_draft(text: str) -> str:
    """Extract the clean draft from agent output.

    Looks for <draft>...</draft> tags first. Falls back to stripping
    common preamble patterns if no tags found.
    """
    # Try <draft>...</draft> tags
    match = re_module.search(r"<draft>(.*?)</draft>", text, re_module.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback: strip common preamble patterns
    for marker in [
        "Here's the draft Slack reply:",
        "Here's the draft reply:",
        "Here's the draft:",
        "Here is the draft:",
        "Draft reply:",
        "Draft:",
    ]:
        idx = text.find(marker)
        if idx != -1:
            return text[idx + len(marker):].strip()

    # Last resort: return as-is
    return text
