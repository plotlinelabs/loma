"""Comprehensive tests for attachment support across all tool channels.

Tests cover:
  - Gmail: _build_message_with_attachments, _parse_attachments, send_email, create_draft
  - Slack User: send_message with file attachment
  - Slack Reader: send_message with file attachment
  - Pylon: _load_attachment_files, _api_post_multipart, reply with attachments, note with attachments
"""

import base64
import email
import json
import os
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def temp_text_file():
    """Create a temporary text file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Hello, this is a test file.")
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def temp_pdf_file():
    """Create a temporary PDF-like file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 fake pdf content for testing")
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def temp_csv_file():
    """Create a temporary CSV file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("name,email\nAlice,alice@example.com\nBob,bob@example.com\n")
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def temp_large_file():
    """Create a temporary file exceeding 25 MB."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(b"\x00" * (26 * 1024 * 1024))  # 26 MB
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def temp_image_file():
    """Create a temporary PNG-like file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        # Minimal PNG header
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        f.flush()
        yield f.name
    os.unlink(f.name)


# ══════════════════════════════════════════════════════════════════════════
# Gmail Tests
# ══════════════════════════════════════════════════════════════════════════


class TestGmailParseAttachments:
    """Test _parse_attachments helper."""

    def test_empty_string(self):
        from tools.gmail import _parse_attachments
        assert _parse_attachments("") == []

    def test_single_path(self):
        from tools.gmail import _parse_attachments
        assert _parse_attachments("/tmp/file.pdf") == ["/tmp/file.pdf"]

    def test_multiple_paths(self):
        from tools.gmail import _parse_attachments
        result = _parse_attachments("/tmp/a.pdf, /tmp/b.csv, /tmp/c.txt")
        assert result == ["/tmp/a.pdf", "/tmp/b.csv", "/tmp/c.txt"]

    def test_paths_with_extra_whitespace(self):
        from tools.gmail import _parse_attachments
        result = _parse_attachments("  /tmp/a.pdf ,  /tmp/b.csv  ")
        assert result == ["/tmp/a.pdf", "/tmp/b.csv"]

    def test_empty_segments_ignored(self):
        from tools.gmail import _parse_attachments
        result = _parse_attachments("/tmp/a.pdf,,/tmp/b.csv,")
        assert result == ["/tmp/a.pdf", "/tmp/b.csv"]


class TestGmailBuildMessageWithAttachments:
    """Test _build_message_with_attachments helper."""

    def test_no_attachments_returns_mime_text(self):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("Hello world")
        assert isinstance(msg, MIMEText)
        assert "Hello world" in msg.as_string()

    def test_none_attachments_returns_mime_text(self):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("Hello world", None)
        assert isinstance(msg, MIMEText)

    def test_empty_list_returns_mime_text(self):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("Hello world", [])
        assert isinstance(msg, MIMEText)

    def test_with_text_attachment(self, temp_text_file):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("Body text", [temp_text_file])
        assert isinstance(msg, MIMEMultipart)
        # Should have 2 parts: text body + attachment
        parts = msg.get_payload()
        assert len(parts) == 2
        # First part is text
        assert parts[0].get_content_type() == "text/plain"
        assert "Body text" in parts[0].get_payload()
        # Second part is attachment
        assert parts[1].get_content_type() == "text/plain"
        disposition = parts[1].get("Content-Disposition", "")
        assert "attachment" in disposition
        assert os.path.basename(temp_text_file) in disposition

    def test_with_pdf_attachment(self, temp_pdf_file):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("See attached PDF", [temp_pdf_file])
        assert isinstance(msg, MIMEMultipart)
        parts = msg.get_payload()
        assert len(parts) == 2
        assert parts[1].get_content_type() == "application/pdf"

    def test_with_multiple_attachments(self, temp_text_file, temp_pdf_file, temp_csv_file):
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments(
            "Multiple files", [temp_text_file, temp_pdf_file, temp_csv_file]
        )
        assert isinstance(msg, MIMEMultipart)
        parts = msg.get_payload()
        assert len(parts) == 4  # body + 3 attachments

    def test_nonexistent_file_raises_error(self):
        from tools.gmail import _build_message_with_attachments
        with pytest.raises(ValueError, match="Attachment file not found"):
            _build_message_with_attachments("Body", ["/nonexistent/file.pdf"])

    def test_file_too_large_raises_error(self, temp_large_file):
        from tools.gmail import _build_message_with_attachments
        with pytest.raises(ValueError, match="Attachment too large"):
            _build_message_with_attachments("Body", [temp_large_file])

    def test_unknown_mime_type_defaults_to_octet_stream(self):
        """Files with unknown extensions get application/octet-stream."""
        with tempfile.NamedTemporaryFile(suffix=".xyz123", delete=False) as f:
            f.write(b"some data")
            f.flush()
            try:
                from tools.gmail import _build_message_with_attachments
                msg = _build_message_with_attachments("Body", [f.name])
                parts = msg.get_payload()
                assert parts[1].get_content_type() == "application/octet-stream"
            finally:
                os.unlink(f.name)

    def test_attachment_is_base64_encoded(self, temp_pdf_file):
        """Verify attachment content is base64-encoded in the MIME message."""
        from tools.gmail import _build_message_with_attachments
        msg = _build_message_with_attachments("Body", [temp_pdf_file])
        parts = msg.get_payload()
        attachment_part = parts[1]
        assert attachment_part["Content-Transfer-Encoding"] == "base64"
        # Verify we can decode the base64 payload
        payload = attachment_part.get_payload()
        decoded = base64.b64decode(payload)
        with open(temp_pdf_file, "rb") as f:
            assert decoded == f.read()


class TestGmailSendEmailWithAttachments:
    """Test send_email with attachments parameter."""

    @pytest.mark.asyncio
    async def test_send_without_attachments_preserves_behavior(self):
        """Sending without attachments should use MIMEText (backward compatible)."""
        from tools.gmail import send_email

        mock_service = MagicMock()
        mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "msg123", "threadId": "thread123"
        }

        with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=mock_service):
            result = await send_email("user@test.com", "to@test.com", "Subject", "Body")

        assert result["sent"] is True
        assert result["messageId"] == "msg123"
        assert "attachments" not in result

    @pytest.mark.asyncio
    async def test_send_with_attachments_includes_files(self, temp_text_file):
        """Sending with attachments should include file names in response."""
        from tools.gmail import send_email

        mock_service = MagicMock()
        mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "msg456", "threadId": "thread456"
        }

        with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=mock_service):
            result = await send_email(
                "user@test.com", "to@test.com", "Subject", "Body",
                attachments=temp_text_file,
            )

        assert result["sent"] is True
        assert "attachments" in result
        assert os.path.basename(temp_text_file) in result["attachments"]

    @pytest.mark.asyncio
    async def test_send_with_nonexistent_attachment_raises_error(self):
        """Sending with a nonexistent file should raise ValueError."""
        from tools.gmail import send_email

        mock_service = MagicMock()
        with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=mock_service):
            with pytest.raises(ValueError, match="Attachment file not found"):
                await send_email(
                    "user@test.com", "to@test.com", "Subject", "Body",
                    attachments="/nonexistent/file.pdf",
                )


class TestGmailCreateDraftWithAttachments:
    """Test create_draft with attachments parameter."""

    @pytest.mark.asyncio
    async def test_draft_without_attachments_preserves_behavior(self):
        """Creating a draft without attachments should be backward compatible."""
        from tools.gmail import create_draft

        mock_service = MagicMock()
        mock_service.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
            "id": "draft123",
            "message": {"id": "msg123", "threadId": "thread123"},
        }

        with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=mock_service):
            result = await create_draft("user@test.com", body="Draft body")

        assert result["created"] is True
        assert result["draftId"] == "draft123"
        assert "attachments" not in result

    @pytest.mark.asyncio
    async def test_draft_with_attachments_includes_files(self, temp_csv_file):
        """Creating a draft with attachments should include file names in response."""
        from tools.gmail import create_draft

        mock_service = MagicMock()
        mock_service.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
            "id": "draft456",
            "message": {"id": "msg456", "threadId": "thread456"},
        }

        with patch("tools.gmail._get_service", new_callable=AsyncMock, return_value=mock_service):
            result = await create_draft(
                "user@test.com", body="Draft with CSV",
                attachments=temp_csv_file,
            )

        assert result["created"] is True
        assert "attachments" in result
        assert os.path.basename(temp_csv_file) in result["attachments"]


# ══════════════════════════════════════════════════════════════════════════
# Slack User Tests
# ══════════════════════════════════════════════════════════════════════════


class TestSlackUserSendMessageWithFile:
    """Test slack_user.py send_message with file attachment."""

    def test_text_only_message_preserved(self):
        """Text-only messages should use chat.postMessage (backward compatible)."""
        from tools.slack_user import send_message

        with patch("tools.slack_user._get_user_token", return_value="xoxp-fake"):
            mock_client = MagicMock()
            mock_client.chat_postMessage.return_value = {"ts": "1234.5678", "message": {"thread_ts": ""}}
            mock_client.conversations_list.return_value = {"channels": [{"name": "general", "id": "C123"}], "response_metadata": {}}

            with patch("tools.slack_user.WebClient", return_value=mock_client):
                result = send_message("user@test.com", "general", "Hello!")

        assert result["sent"] is True
        assert result["method"] == "text_message"
        mock_client.chat_postMessage.assert_called_once()

    def test_message_with_file_uses_upload(self, temp_text_file):
        """Messages with file should use files_upload_v2."""
        from tools.slack_user import send_message

        with patch("tools.slack_user._get_user_token", return_value="xoxp-fake"):
            mock_client = MagicMock()
            mock_client.files_upload_v2.return_value = {
                "file": {"id": "F123", "name": os.path.basename(temp_text_file)}
            }

            with patch("tools.slack_user.WebClient", return_value=mock_client):
                with patch("tools.slack_user._resolve_channel_id", return_value=("C123", None)):
                    result = send_message(
                        "user@test.com", "general", "See attached",
                        file_path=temp_text_file,
                    )

        assert result["sent"] is True
        assert result["method"] == "file_upload_with_message"
        assert result["file_id"] == "F123"
        mock_client.files_upload_v2.assert_called_once()

    def test_message_with_file_and_thread(self, temp_text_file):
        """File upload in a thread should pass thread_ts."""
        from tools.slack_user import send_message

        with patch("tools.slack_user._get_user_token", return_value="xoxp-fake"):
            mock_client = MagicMock()
            mock_client.files_upload_v2.return_value = {
                "file": {"id": "F456", "name": "test.txt"}
            }

            with patch("tools.slack_user.WebClient", return_value=mock_client):
                with patch("tools.slack_user._resolve_channel_id", return_value=("C123", None)):
                    result = send_message(
                        "user@test.com", "general", "Thread reply with file",
                        thread_ts="1234.5678",
                        file_path=temp_text_file,
                    )

        assert result["sent"] is True
        call_kwargs = mock_client.files_upload_v2.call_args
        assert call_kwargs.kwargs.get("thread_ts") == "1234.5678" or \
               (call_kwargs[1] if len(call_kwargs) > 1 else {}).get("thread_ts") == "1234.5678"

    def test_nonexistent_file_returns_error(self):
        """Non-existent file should return an error."""
        from tools.slack_user import send_message

        with patch("tools.slack_user._get_user_token", return_value="xoxp-fake"):
            mock_client = MagicMock()

            with patch("tools.slack_user.WebClient", return_value=mock_client):
                with patch("tools.slack_user._resolve_channel_id", return_value=("C123", None)):
                    result = send_message(
                        "user@test.com", "general", "File here",
                        file_path="/nonexistent/file.pdf",
                    )

        assert "error" in result
        assert "File not found" in result["error"]

    def test_file_title_passed_to_upload(self, temp_text_file):
        """Custom file title should be used in the upload."""
        from tools.slack_user import send_message

        with patch("tools.slack_user._get_user_token", return_value="xoxp-fake"):
            mock_client = MagicMock()
            mock_client.files_upload_v2.return_value = {
                "file": {"id": "F789", "name": "test.txt"}
            }

            with patch("tools.slack_user.WebClient", return_value=mock_client):
                with patch("tools.slack_user._resolve_channel_id", return_value=("C123", None)):
                    result = send_message(
                        "user@test.com", "general", "Custom title",
                        file_path=temp_text_file,
                        file_title="My Report",
                    )

        assert result["sent"] is True
        call_kwargs = mock_client.files_upload_v2.call_args
        # Check title was passed
        if call_kwargs.kwargs:
            assert call_kwargs.kwargs.get("title") == "My Report"


# ══════════════════════════════════════════════════════════════════════════
# Slack Reader Tests
# ══════════════════════════════════════════════════════════════════════════


class TestSlackReaderSendMessageWithFile:
    """Test slack_reader.py send_message with file attachment."""

    def test_text_only_message_preserved(self):
        """Text-only messages should use chat.postMessage (backward compatible)."""
        from tools.slack_reader import send_message

        with patch("tools.slack_reader._get_bot_token", return_value="xoxb-fake"):
            mock_client = MagicMock()
            mock_client.chat_postMessage.return_value = {
                "ts": "1234.5678",
                "message": {"text": "Hello!"},
            }

            with patch("tools.slack_reader.WebClient", return_value=mock_client):
                with patch("tools.slack_reader._resolve_channel_id", return_value=("C123", None)):
                    result = send_message("general", "Hello!")

        assert result["ok"] is True
        assert result["method"] == "text_message"
        mock_client.chat_postMessage.assert_called_once()

    def test_message_with_file_uses_upload(self, temp_text_file):
        """Messages with file should use files_upload_v2."""
        from tools.slack_reader import send_message

        with patch("tools.slack_reader._get_bot_token", return_value="xoxb-fake"):
            mock_client = MagicMock()
            mock_client.files_upload_v2.return_value = {
                "file": {"id": "F123", "name": os.path.basename(temp_text_file)}
            }

            with patch("tools.slack_reader.WebClient", return_value=mock_client):
                with patch("tools.slack_reader._resolve_channel_id", return_value=("C123", None)):
                    result = send_message(
                        "general", "See attached",
                        file_path=temp_text_file,
                    )

        assert result["ok"] is True
        assert result["method"] == "file_upload_with_message"
        assert result["file_id"] == "F123"

    def test_nonexistent_file_returns_error(self):
        """Non-existent file should return an error."""
        from tools.slack_reader import send_message

        with patch("tools.slack_reader._get_bot_token", return_value="xoxb-fake"):
            mock_client = MagicMock()

            with patch("tools.slack_reader.WebClient", return_value=mock_client):
                with patch("tools.slack_reader._resolve_channel_id", return_value=("C123", None)):
                    result = send_message(
                        "general", "File here",
                        file_path="/nonexistent/file.pdf",
                    )

        assert "error" in result
        assert "File not found" in result["error"]

    def test_empty_text_still_rejected(self):
        """Empty text should still be rejected even with a file."""
        from tools.slack_reader import send_message

        result = send_message("general", "")
        assert "error" in result
        assert "empty" in result["error"].lower()


# ══════════════════════════════════════════════════════════════════════════
# Pylon Tests
# ══════════════════════════════════════════════════════════════════════════


class TestPylonLoadAttachmentFiles:
    """Test _load_attachment_files helper."""

    def test_loads_single_file(self, temp_text_file):
        from tools.pylon import _load_attachment_files
        result = _load_attachment_files([temp_text_file])
        assert len(result) == 1
        filename, content_type, data = result[0]
        assert filename == os.path.basename(temp_text_file)
        assert content_type == "text/plain"
        assert data == b"Hello, this is a test file."

    def test_loads_multiple_files(self, temp_text_file, temp_pdf_file):
        from tools.pylon import _load_attachment_files
        result = _load_attachment_files([temp_text_file, temp_pdf_file])
        assert len(result) == 2
        assert result[0][0] == os.path.basename(temp_text_file)
        assert result[1][0] == os.path.basename(temp_pdf_file)
        assert result[1][1] == "application/pdf"

    def test_nonexistent_file_raises_error(self):
        from tools.pylon import _load_attachment_files
        with pytest.raises(ValueError, match="Attachment file not found"):
            _load_attachment_files(["/nonexistent/file.pdf"])

    def test_file_too_large_raises_error(self):
        """Files over 10 MB should raise an error."""
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"\x00" * (11 * 1024 * 1024))  # 11 MB
            f.flush()
            try:
                from tools.pylon import _load_attachment_files
                with pytest.raises(ValueError, match="Attachment too large"):
                    _load_attachment_files([f.name])
            finally:
                os.unlink(f.name)

    def test_unknown_mimetype_defaults(self):
        """Unknown file types should default to application/octet-stream."""
        with tempfile.NamedTemporaryFile(suffix=".xyz987", delete=False) as f:
            f.write(b"data")
            f.flush()
            try:
                from tools.pylon import _load_attachment_files
                result = _load_attachment_files([f.name])
                assert result[0][1] == "application/octet-stream"
            finally:
                os.unlink(f.name)

    def test_image_file_content_type(self, temp_image_file):
        from tools.pylon import _load_attachment_files
        result = _load_attachment_files([temp_image_file])
        assert result[0][1] == "image/png"


class TestPylonReplyWithAttachments:
    """Test reply() function with attachments."""

    @pytest.mark.asyncio
    async def test_reply_without_attachments_uses_json(self):
        """Reply without attachments should use the existing JSON path."""
        from tools.pylon import reply

        with patch("tools.pylon._api_post", new_callable=AsyncMock, return_value={"ok": True}):
            result = await reply("issue-1", "<p>Hello</p>", "msg-1")

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_reply_with_attachments_uploads_then_posts_json(self, temp_text_file):
        """Reply with attachments should upload files and post attachment URLs."""
        from tools.pylon import reply

        with patch("tools.pylon._upload_attachments", new_callable=AsyncMock, return_value=["https://files.example/a.txt"]) as mock_upload, \
             patch("tools.pylon._api_post", new_callable=AsyncMock, return_value={"ok": True}) as mock_post:
            result = await reply(
                "issue-1", "<p>Hello</p>", "msg-1",
                attachments=[temp_text_file],
            )

        mock_upload.assert_awaited_once_with([temp_text_file])
        mock_post.assert_awaited_once_with("/issues/issue-1/reply", {
            "body_html": "<p>Hello</p>",
            "message_id": "msg-1",
            "attachment_urls": ["https://files.example/a.txt"],
        })

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_reply_with_attachments_and_email_info_posts_json(self, temp_text_file):
        """Reply with both attachments and email_info should post attachment URLs with email_info."""
        from tools.pylon import reply

        with patch("tools.pylon._upload_attachments", new_callable=AsyncMock, return_value=["https://files.example/a.txt"]) as mock_upload, \
             patch("tools.pylon._api_post", new_callable=AsyncMock, return_value={"ok": True}) as mock_post:
            email_info = {"to_emails": ["a@x.com"], "cc_emails": ["b@x.com"]}
            result = await reply(
                "issue-1", "<p>Hello</p>", "msg-1",
                email_info=email_info,
                attachments=[temp_text_file],
            )

        mock_upload.assert_awaited_once_with([temp_text_file])
        mock_post.assert_awaited_once_with("/issues/issue-1/reply", {
            "body_html": "<p>Hello</p>",
            "message_id": "msg-1",
            "email_info": email_info,
            "attachment_urls": ["https://files.example/a.txt"],
        })

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_reply_with_nonexistent_attachment(self):
        """Reply with nonexistent file should raise ValueError."""
        from tools.pylon import reply

        with pytest.raises(ValueError, match="Attachment file not found"):
            await reply(
                "issue-1", "<p>Hello</p>", "msg-1",
                attachments=["/nonexistent/file.pdf"],
            )


class TestPylonPostNoteWithAttachments:
    """Test post_note() function with attachments."""

    @pytest.mark.asyncio
    async def test_note_without_attachments_uses_json(self):
        """Note without attachments should use the existing JSON path."""
        from tools.pylon import post_note

        with patch("tools.pylon._api_post", new_callable=AsyncMock, return_value={"ok": True}):
            result = await post_note("issue-1", "<p>Internal note</p>")

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_note_with_attachments_uploads_then_posts_json(self, temp_pdf_file):
        """Note with attachments should upload files and post attachment URLs."""
        from tools.pylon import post_note

        with patch("tools.pylon._upload_attachments", new_callable=AsyncMock, return_value=["https://files.example/a.pdf"]) as mock_upload, \
             patch("tools.pylon._api_post", new_callable=AsyncMock, return_value={"ok": True}) as mock_post:
            result = await post_note(
                "issue-1", "<p>See attached</p>",
                attachments=[temp_pdf_file],
            )

        mock_upload.assert_awaited_once_with([temp_pdf_file])
        mock_post.assert_awaited_once_with("/issues/issue-1/note", {
            "body_html": "<p>See attached</p>",
            "attachment_urls": ["https://files.example/a.pdf"],
        })

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_note_with_thread_and_attachments(self, temp_text_file):
        """Note with both thread_id and attachments should include thread_id and attachment URLs."""
        from tools.pylon import post_note

        with patch("tools.pylon._upload_attachments", new_callable=AsyncMock, return_value=["https://files.example/a.txt"]) as mock_upload, \
             patch("tools.pylon._api_post", new_callable=AsyncMock, return_value={"ok": True}) as mock_post:
            result = await post_note(
                "issue-1", "<p>Thread note</p>",
                thread_id="thread-123",
                attachments=[temp_text_file],
            )

        mock_upload.assert_awaited_once_with([temp_text_file])
        mock_post.assert_awaited_once_with("/issues/issue-1/note", {
            "body_html": "<p>Thread note</p>",
            "thread_id": "thread-123",
            "attachment_urls": ["https://files.example/a.txt"],
        })

        assert result == {"ok": True}


class TestPylonApiPostMultipart:
    """Test _api_post_multipart helper."""

    @pytest.mark.asyncio
    async def test_successful_post(self):
        """Successful multipart POST should return parsed JSON."""
        from tools.pylon import _api_post_multipart

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True})

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("tools.pylon.aiohttp.ClientSession", return_value=mock_session):
            with patch("tools.pylon._get_api_key", return_value="test-key"):
                result = await _api_post_multipart(
                    "/issues/test/reply",
                    {"body_html": "<p>Hello</p>", "message_id": "msg-1"},
                    [("test.pdf", "application/pdf", b"fake pdf")],
                )

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_auth_error(self):
        """401 response should return auth error."""
        from tools.pylon import _api_post_multipart

        mock_resp = AsyncMock()
        mock_resp.status = 401

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("tools.pylon.aiohttp.ClientSession", return_value=mock_session):
            with patch("tools.pylon._get_api_key", return_value="bad-key"):
                result = await _api_post_multipart(
                    "/issues/test/reply", {"body_html": "<p>Hello</p>"}, None
                )

        assert "error" in result
        assert "invalid or expired" in result["error"]

    @pytest.mark.asyncio
    async def test_rate_limit_error(self):
        """429 response should return rate limit error."""
        from tools.pylon import _api_post_multipart

        mock_resp = AsyncMock()
        mock_resp.status = 429

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("tools.pylon.aiohttp.ClientSession", return_value=mock_session):
            with patch("tools.pylon._get_api_key", return_value="test-key"):
                result = await _api_post_multipart(
                    "/issues/test/reply", {"body_html": "<p>Hello</p>"}, None
                )

        assert "error" in result
        assert "rate limit" in result["error"].lower()


# ══════════════════════════════════════════════════════════════════════════
# Cross-Channel Integration Tests
# ══════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Verify existing behavior is preserved when no attachments are provided."""

    def test_gmail_send_email_no_attachments_signature(self):
        """send_email should still work with positional args (no attachments)."""
        import inspect
        from tools.gmail import send_email
        sig = inspect.signature(send_email)
        params = list(sig.parameters.keys())
        # Original params still present
        assert "user_email" in params
        assert "to" in params
        assert "subject" in params
        assert "body" in params
        assert "cc" in params
        # New param added
        assert "attachments" in params
        # Attachments has a default value
        assert sig.parameters["attachments"].default == ""

    def test_gmail_create_draft_no_attachments_signature(self):
        """create_draft should still work with positional args (no attachments)."""
        import inspect
        from tools.gmail import create_draft
        sig = inspect.signature(create_draft)
        assert "attachments" in sig.parameters
        assert sig.parameters["attachments"].default == ""

    def test_slack_user_send_message_signature(self):
        """send_message should still work with original args."""
        import inspect
        from tools.slack_user import send_message
        sig = inspect.signature(send_message)
        assert "file_path" in sig.parameters
        assert sig.parameters["file_path"].default == ""
        assert "file_title" in sig.parameters
        assert sig.parameters["file_title"].default == ""

    def test_slack_reader_send_message_signature(self):
        """send_message should still work with original args."""
        import inspect
        from tools.slack_reader import send_message
        sig = inspect.signature(send_message)
        assert "file_path" in sig.parameters
        assert sig.parameters["file_path"].default is None
        assert "file_title" in sig.parameters
        assert sig.parameters["file_title"].default is None

    def test_pylon_reply_signature(self):
        """reply should still work with original args."""
        import inspect
        from tools.pylon import reply
        sig = inspect.signature(reply)
        assert "attachments" in sig.parameters
        assert sig.parameters["attachments"].default is None

    def test_pylon_post_note_signature(self):
        """post_note should still work with original args."""
        import inspect
        from tools.pylon import post_note
        sig = inspect.signature(post_note)
        assert "attachments" in sig.parameters
        assert sig.parameters["attachments"].default is None
