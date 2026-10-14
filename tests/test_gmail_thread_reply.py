"""Tests for replying inside an existing Gmail thread and sending saved drafts."""

import base64
from email import message_from_bytes
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.gmail import send_draft, send_email


def _sent_body(mock_service):
    return mock_service.users.return_value.messages.return_value.send.call_args.kwargs["body"]


def _service():
    svc = MagicMock()
    svc.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "m1", "threadId": "t1",
    }
    return svc


@pytest.mark.asyncio
async def test_send_email_reply_sets_thread_and_headers():
    svc = _service()
    with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=svc):
        result = await send_email(
            "u@test.com", "a@test.com", "Re: Hello", "Hi", cc="b@test.com",
            thread_id="t1", in_reply_to="<orig@mail.gmail.com>",
        )
    body = _sent_body(svc)
    assert body["threadId"] == "t1"
    msg = message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
    assert msg["In-Reply-To"] == "<orig@mail.gmail.com>"
    assert msg["References"] == "<orig@mail.gmail.com>"
    assert msg["cc"] == "b@test.com"
    assert result == {"sent": True, "messageId": "m1", "threadId": "t1"}


@pytest.mark.asyncio
async def test_send_email_without_thread_is_unchanged():
    svc = _service()
    with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=svc):
        await send_email("u@test.com", "a@test.com", "Hello", "Hi")
    body = _sent_body(svc)
    assert "threadId" not in body
    msg = message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
    assert msg["In-Reply-To"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["orig@mail.gmail.com", "<a@b>\nBcc: x@evil.com", "<a b@c>"])
async def test_send_email_rejects_bad_in_reply_to(bad):
    with patch("tools.gmail._get_service", new_callable=AsyncMock) as get_service:
        with pytest.raises(ValueError, match="Invalid --in-reply-to"):
            await send_email("u@test.com", "a@test.com", "Re: x", "Hi", in_reply_to=bad)
    get_service.assert_not_called()


@pytest.mark.asyncio
async def test_send_draft_sends_by_id():
    svc = MagicMock()
    svc.users.return_value.drafts.return_value.send.return_value.execute.return_value = {
        "id": "m9", "threadId": "t9",
    }
    with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=svc):
        result = await send_draft("u@test.com", "r-123")
    svc.users.return_value.drafts.return_value.send.assert_called_once_with(
        userId="me", body={"id": "r-123"},
    )
    assert result == {"sent": True, "messageId": "m9", "threadId": "t9"}
