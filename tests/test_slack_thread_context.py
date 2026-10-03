"""Thread context includes integration posts (attachments/blocks) and only
labels Loma's own replies as the assistant."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from slack_app import utils
from slack_app.utils import ASSISTANT_CONTEXT_LABEL, get_thread_context, message_text

PYLON_POST = {
    "ts": "1", "bot_id": "BPYLON", "subtype": "bot_message", "username": "Home Credit Indonesia",
    "text": "",
    "attachments": [{
        "fallback": "Hi Home Credit Indonesia - sharing a new invoice.",
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "  |  Home Credit Indonesia Issue 4085"}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": "<https://app.usepylon.com/issues?issueNumber=4085|View in Pylon>"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "Invoice No - 100004592-4, Due Date - 30 Nov 2026"}},
            {"type": "actions", "elements": [{
                "type": "static_select", "placeholder": {"type": "plain_text", "text": "Status"},
                "initial_option": {"text": {"type": "plain_text", "text": "New"}, "value": "open"},
            }]},
        ],
    }],
}


def test_message_text_reads_attachment_blocks():
    text = message_text(PYLON_POST)
    assert "Issue 4085" in text
    assert "Invoice No - 100004592-4" in text
    assert "[Status: New]" in text


def test_message_text_falls_back_to_attachment_fallback():
    msg = {"text": "", "attachments": [{"fallback": "Alert: CPU high"}]}
    assert message_text(msg) == "Alert: CPU high"


def test_message_text_skips_link_unfurls():
    msg = {"text": "see <https://x.io>", "attachments": [{"from_url": "https://x.io", "text": "X homepage"}]}
    assert message_text(msg) == "see <https://x.io>"


def test_message_text_reads_rich_text_when_text_empty():
    msg = {"text": "", "blocks": [{"type": "rich_text", "elements": [
        {"type": "rich_text_section", "elements": [{"type": "text", "text": "hello "}, {"type": "user", "user_id": "U1"}]},
    ]}]}
    assert message_text(msg) == "hello <@U1>"


@pytest.mark.asyncio
async def test_thread_context_keeps_integration_post_and_labels_only_loma():
    utils._own_bot_ids.clear()
    client = MagicMock()
    client.token = "xoxb-test"
    client.auth_test = AsyncMock(return_value={"bot_id": "BLOMA"})
    client.conversations_replies = AsyncMock(return_value={"messages": [
        PYLON_POST,
        {"ts": "2", "user": "U1", "text": "<@ULOMA> - AR agent - what do we need to do?"},
        {"ts": "3", "user": "ULOMA", "bot_id": "BLOMA", "text": "Need more context."},
        {"ts": "4", "user": "U1", "text": "current"},
    ]})
    context, _ = await get_thread_context(client, "C1", "1", current_ts="4")

    assert "**Bot (Home Credit Indonesia)**:" in context
    assert "Invoice No - 100004592-4" in context
    assert context.count(ASSISTANT_CONTEXT_LABEL) == 1
    assert f"{ASSISTANT_CONTEXT_LABEL}: Need more context." in context
    assert "current" not in context
