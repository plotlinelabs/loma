"""Tests for webhooks/linear.py — thread-aware conversation continuity.

Covers:
- _build_conversation_context: message formatting
- handle_linear_webhook: comment trigger routing (resume vs new)
"""

import json
import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from webhooks.linear import (
    _build_conversation_context,
    _MAX_CONTEXT_MESSAGES,
    AGENT_COMMENT_MARKER,
)


# ---------------------------------------------------------------------------
# _build_conversation_context (shared with webhook_executor, tested here too)
# ---------------------------------------------------------------------------


class TestLinearBuildConversationContext:
    def test_empty_messages(self):
        assert _build_conversation_context([]) == ""

    def test_user_and_assistant(self):
        messages = [
            {"role": "user", "content": "Implement feature X"},
            {"role": "assistant", "content": "Done. PR created."},
        ]
        result = _build_conversation_context(messages)
        assert "**User**: Implement feature X" in result
        assert "**Assistant**: Done. PR created." in result

    def test_max_messages_cap(self):
        messages = [
            {"role": "user", "content": f"msg-{i}"}
            for i in range(30)
        ]
        result = _build_conversation_context(messages, max_messages=5)
        assert "msg-0" not in result
        assert "msg-29" in result


# ---------------------------------------------------------------------------
# handle_linear_webhook — comment trigger routing
# ---------------------------------------------------------------------------


def _make_signed_request(body_dict: dict, secret: str = "test-secret") -> tuple:
    """Create a signed request body and signature header."""
    raw_body = json.dumps(body_dict).encode()
    signature = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return raw_body, signature


def _make_comment_webhook(comment_body: str, issue_id: str = "issue-1",
                          issue_identifier: str = "GO-99",
                          issue_title: str = "Test Issue") -> dict:
    """Create a Linear comment webhook payload."""
    return {
        "action": "create",
        "type": "Comment",
        "webhookTimestamp": int(time.time() * 1000),
        "data": {
            "id": "comment-1",
            "body": comment_body,
            "issue": {
                "id": issue_id,
                "identifier": issue_identifier,
                "title": issue_title,
            },
        },
    }


class TestLinearWebhookCommentRouting:
    """Test that comment handling correctly routes to resume vs new."""

    @pytest.mark.asyncio
    async def test_comment_with_existing_conversation_resumes(self):
        """A 'loma' comment on an issue with a prior conversation should resume it."""
        existing = {
            "conversation_id": "existing-linear-convo",
            "status": "completed",
            "messages": [{"role": "user", "content": "Original"}],
        }

        webhook_body = _make_comment_webhook("loma - implement this")
        raw_body, signature = _make_signed_request(webhook_body)

        mock_db = MagicMock()
        mock_db.conversations.find_one = AsyncMock(return_value=existing)

        with patch("webhooks.linear.LINEAR_WEBHOOK_SECRET", "test-secret"), \
             patch("webhooks.linear.get_db", return_value=mock_db), \
             patch("webhooks.linear._acknowledge_comment", new_callable=AsyncMock), \
             patch("webhooks.linear._post_comment_tracking", new_callable=AsyncMock), \
             patch("webhooks.linear._process_linear_comment_on_existing", new_callable=AsyncMock) as mock_resume, \
             patch("asyncio.create_task") as mock_create_task:

            # Build a mock aiohttp request
            request = MagicMock()
            request.read = AsyncMock(return_value=raw_body)
            request.headers = {"linear-signature": signature}

            from webhooks.linear import handle_linear_webhook
            response = await handle_linear_webhook(request)

            # Check that create_task was called with _process_linear_comment_on_existing
            # The response should indicate resume trigger
            response_body = json.loads(response.body)
            assert response_body["trigger"] == "comment_resume"

    @pytest.mark.asyncio
    async def test_comment_without_existing_creates_new(self):
        """A 'loma' comment on an issue with NO prior conversation should create new."""
        webhook_body = _make_comment_webhook("loma - implement this")
        raw_body, signature = _make_signed_request(webhook_body)

        mock_db = MagicMock()
        mock_db.conversations.find_one = AsyncMock(return_value=None)

        with patch("webhooks.linear.LINEAR_WEBHOOK_SECRET", "test-secret"), \
             patch("webhooks.linear.get_db", return_value=mock_db), \
             patch("webhooks.linear._acknowledge_comment", new_callable=AsyncMock), \
             patch("webhooks.linear._post_comment_tracking", new_callable=AsyncMock), \
             patch("webhooks.linear._process_linear_issue", new_callable=AsyncMock) as mock_new, \
             patch("asyncio.create_task") as mock_create_task:

            request = MagicMock()
            request.read = AsyncMock(return_value=raw_body)
            request.headers = {"linear-signature": signature}

            from webhooks.linear import handle_linear_webhook
            response = await handle_linear_webhook(request)

            response_body = json.loads(response.body)
            assert response_body["trigger"] == "comment_detect_intent"

    @pytest.mark.asyncio
    async def test_comment_with_running_conversation_resumes(self):
        """A 'loma' comment should resume even a running conversation."""
        existing = {
            "conversation_id": "running-convo",
            "status": "running",
            "messages": [{"role": "user", "content": "In progress"}],
        }

        webhook_body = _make_comment_webhook("loma - what's the status?")
        raw_body, signature = _make_signed_request(webhook_body)

        mock_db = MagicMock()
        mock_db.conversations.find_one = AsyncMock(return_value=existing)

        with patch("webhooks.linear.LINEAR_WEBHOOK_SECRET", "test-secret"), \
             patch("webhooks.linear.get_db", return_value=mock_db), \
             patch("webhooks.linear._acknowledge_comment", new_callable=AsyncMock), \
             patch("webhooks.linear._post_comment_tracking", new_callable=AsyncMock), \
             patch("webhooks.linear._process_linear_comment_on_existing", new_callable=AsyncMock), \
             patch("asyncio.create_task"):

            request = MagicMock()
            request.read = AsyncMock(return_value=raw_body)
            request.headers = {"linear-signature": signature}

            from webhooks.linear import handle_linear_webhook
            response = await handle_linear_webhook(request)

            response_body = json.loads(response.body)
            assert response_body["trigger"] == "comment_resume"

    @pytest.mark.asyncio
    async def test_agent_comment_ignored(self):
        """Comments containing the agent marker should be ignored (loop prevention)."""
        webhook_body = _make_comment_webhook(f"{AGENT_COMMENT_MARKER} PR Created")
        raw_body, signature = _make_signed_request(webhook_body)

        with patch("webhooks.linear.LINEAR_WEBHOOK_SECRET", "test-secret"):
            request = MagicMock()
            request.read = AsyncMock(return_value=raw_body)
            request.headers = {"linear-signature": signature}

            from webhooks.linear import handle_linear_webhook
            response = await handle_linear_webhook(request)

            response_body = json.loads(response.body)
            assert response_body["reason"] == "self-comment"

    @pytest.mark.asyncio
    async def test_comment_without_trigger_phrase_ignored(self):
        """Comments without 'loma' should be ignored."""
        webhook_body = _make_comment_webhook("This is a regular comment")
        raw_body, signature = _make_signed_request(webhook_body)

        with patch("webhooks.linear.LINEAR_WEBHOOK_SECRET", "test-secret"):
            request = MagicMock()
            request.read = AsyncMock(return_value=raw_body)
            request.headers = {"linear-signature": signature}

            from webhooks.linear import handle_linear_webhook
            response = await handle_linear_webhook(request)

            response_body = json.loads(response.body)
            assert response_body["reason"] == "no_trigger_phrase"

    @pytest.mark.asyncio
    async def test_query_includes_running_and_completed(self):
        """The conversation lookup should match both running and completed status."""
        webhook_body = _make_comment_webhook("loma")
        raw_body, signature = _make_signed_request(webhook_body)

        mock_db = MagicMock()
        mock_db.conversations.find_one = AsyncMock(return_value=None)

        with patch("webhooks.linear.LINEAR_WEBHOOK_SECRET", "test-secret"), \
             patch("webhooks.linear.get_db", return_value=mock_db), \
             patch("webhooks.linear._acknowledge_comment", new_callable=AsyncMock), \
             patch("webhooks.linear._post_comment_tracking", new_callable=AsyncMock), \
             patch("webhooks.linear._process_linear_issue", new_callable=AsyncMock), \
             patch("asyncio.create_task"):

            request = MagicMock()
            request.read = AsyncMock(return_value=raw_body)
            request.headers = {"linear-signature": signature}

            from webhooks.linear import handle_linear_webhook
            await handle_linear_webhook(request)

            # Verify the query matches both running and completed
            call_args = mock_db.conversations.find_one.call_args
            query = call_args[0][0]
            assert query["status"] == {"$in": ["completed", "running"]}
            assert query["source"] == "linear_webhook"
