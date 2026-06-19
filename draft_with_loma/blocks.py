"""Slack Block Kit builders for the Draft with Loma feature."""

import json


def build_draft_context_modal(shortcut: dict) -> dict:
    """Build the initial modal that asks for optional drafting context.

    Args:
        shortcut: The Slack message shortcut payload.
    """
    message = shortcut["message"]
    channel_id = shortcut["channel"]["id"]
    message_ts = message["ts"]
    # If the message is in a thread, use the thread parent; otherwise use the message itself
    thread_ts = message.get("thread_ts", message_ts)
    preview = (message.get("text") or "")[:200]

    metadata = json.dumps({
        "channel_id": channel_id,
        "message_ts": message_ts,
        "thread_ts": thread_ts,
    })

    blocks = []

    if preview:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Replying to:*\n>{preview}",
            },
        })

    blocks.append({
        "type": "input",
        "block_id": "context_block",
        "optional": True,
        "element": {
            "type": "plain_text_input",
            "action_id": "user_context",
            "multiline": True,
            "placeholder": {
                "type": "plain_text",
                "text": "e.g., politely decline, ask for a deadline, say yes but suggest next week...",
            },
        },
        "label": {
            "type": "plain_text",
            "text": "Any context for the draft?",
        },
    })

    return {
        "type": "modal",
        "callback_id": "draft_with_loma_submit",
        "title": {"type": "plain_text", "text": "Draft with Loma"},
        "submit": {"type": "plain_text", "text": "Draft"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": metadata,
        "blocks": blocks,
    }


def build_edit_context_modal(draft_id: str, current_draft: str) -> dict:
    """Build the edit modal showing the current draft and asking for feedback.

    Args:
        draft_id: The draft record ID.
        current_draft: The current draft text to show as context.
    """
    metadata = json.dumps({"draft_id": draft_id})

    truncated = current_draft[:2900]  # Slack section text limit is 3000
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Current draft:*\n>{truncated}",
            },
        },
        {
            "type": "input",
            "block_id": "edit_context_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "edit_context",
                "multiline": True,
                "placeholder": {
                    "type": "plain_text",
                    "text": "e.g., make it shorter, add a thank you, be more formal...",
                },
            },
            "label": {
                "type": "plain_text",
                "text": "What should be different?",
            },
        },
    ]

    return {
        "type": "modal",
        "callback_id": "draft_with_loma_edit_submit",
        "title": {"type": "plain_text", "text": "Edit Draft"},
        "submit": {"type": "plain_text", "text": "Re-draft"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": metadata,
        "blocks": blocks,
    }


def build_draft_review_blocks(draft_id: str, draft_text: str) -> list:
    """Build Block Kit blocks for the ephemeral draft review message.

    Args:
        draft_id: The draft record ID (used in action_id values).
        draft_text: The drafted response text.
    """
    truncated = draft_text[:2900]
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Loma's draft:*\n{truncated}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Send"},
                    "style": "primary",
                    "action_id": f"dwg_send_{draft_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit"},
                    "action_id": f"dwg_edit_{draft_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss"},
                    "action_id": f"dwg_dismiss_{draft_id}",
                },
            ],
        },
    ]
