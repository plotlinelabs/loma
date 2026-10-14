"""Tests for replying inside an existing Gmail thread and sending saved drafts."""

import base64
from email import message_from_bytes
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.gmail import delete_draft, list_drafts, send_draft, send_email


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


@pytest.mark.asyncio
async def test_delete_draft_calls_drafts_delete():
    svc = MagicMock()
    with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=svc):
        result = await delete_draft("u@test.com", " r-123 ")
    svc.users.return_value.drafts.return_value.delete.assert_called_once_with(
        userId="me", id="r-123",
    )
    svc.users.return_value.messages.return_value.delete.assert_not_called()
    assert result == {"deleted": True, "draftId": "r-123"}


@pytest.mark.asyncio
async def test_delete_draft_rejects_empty_id():
    with patch("tools.gmail._get_service", new_callable=AsyncMock) as get_service:
        with pytest.raises(ValueError):
            await delete_draft("u@test.com", "  ")
    get_service.assert_not_called()


@pytest.mark.asyncio
async def test_list_drafts_returns_ids_and_subjects():
    svc = MagicMock()
    drafts = svc.users.return_value.drafts.return_value
    drafts.list.return_value.execute.return_value = {"drafts": [{"id": "r-1"}]}
    drafts.get.return_value.execute.return_value = {
        "id": "r-1",
        "message": {"id": "m1", "threadId": "t1", "snippet": "hi", "payload": {"headers": [
            {"name": "Subject", "value": "[Loma test] x"}, {"name": "To", "value": "a@test.com"},
        ]}},
    }
    with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=svc):
        result = await list_drafts("u@test.com", limit=5, query="subject:test")
    drafts.list.assert_called_once_with(userId="me", maxResults=5, q="subject:test")
    assert result["total"] == 1
    assert result["drafts"][0]["draftId"] == "r-1"
    assert result["drafts"][0]["subject"] == "[Loma test] x"


class TestPlaintextToHtml:
    """Plain-text bodies get an HTML part so Gmail reflows instead of hard-wrapping."""

    def test_paragraphs_and_line_breaks(self):
        from tools.gmail import _plaintext_to_html
        out = _plaintext_to_html("Hi Sunil,\n\nLine one\nLine two")
        assert "<div>Hi Sunil,</div>" in out
        assert "<div><br></div>" in out
        assert "Line one<br>\nLine two" in out

    def test_long_paragraph_has_no_inserted_breaks(self):
        from tools.gmail import _plaintext_to_html
        para = "word " * 60
        out = _plaintext_to_html(para.strip())
        assert "<br>" not in out
        assert para.strip() in out

    def test_escapes_special_characters(self):
        from tools.gmail import _plaintext_to_html
        out = _plaintext_to_html("A & B <tag> \"q\"")
        assert "A &amp; B &lt;tag&gt; &quot;q&quot;" in out

    def test_crlf_normalized(self):
        from tools.gmail import _plaintext_to_html
        assert _plaintext_to_html("a\r\n\r\nb") == _plaintext_to_html("a\n\nb")

    def test_explicit_html_body_is_kept(self):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("plain", None, "<p>custom</p>")
        html = msg.get_payload()[1].get_payload(decode=True).decode()
        assert html == "<p>custom</p>"

    def test_non_ascii_body_utf8(self):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("Thanks — ₹100", None)
        for part in msg.get_payload():
            assert part.get_content_charset() == "utf-8"
            assert "₹100" in part.get_payload(decode=True).decode()
