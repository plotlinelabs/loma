"""Tests for file type classification in slack_app/utils.py."""

import base64
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from slack_app.utils import (
    ARCHIVE_MIMETYPES,
    BINARY_EXTENSIONS,
    DOCUMENT_MIMETYPES,
    IMAGE_MIMETYPES,
    TEXT_EXTENSIONS,
    download_slack_files,
)


# -- Constant Tests ---------------------------------------------------------


class TestArchiveMimetypes:
    def test_zip_mimetype(self):
        assert "application/zip" in ARCHIVE_MIMETYPES

    def test_zip_compressed_mimetype(self):
        assert "application/x-zip-compressed" in ARCHIVE_MIMETYPES

    def test_tar_mimetype(self):
        assert "application/x-tar" in ARCHIVE_MIMETYPES

    def test_gzip_mimetype(self):
        assert "application/gzip" in ARCHIVE_MIMETYPES

    def test_7z_mimetype(self):
        assert "application/x-7z-compressed" in ARCHIVE_MIMETYPES

    def test_rar_mimetype(self):
        assert "application/x-rar-compressed" in ARCHIVE_MIMETYPES

    def test_rar_vnd_mimetype(self):
        assert "application/vnd.rar" in ARCHIVE_MIMETYPES


class TestBinaryExtensions:
    def test_archive_extensions_present(self):
        for ext in [".zip", ".tar", ".gz", ".7z", ".rar", ".tgz"]:
            assert ext in BINARY_EXTENSIONS, f"{ext} missing from BINARY_EXTENSIONS"

    def test_spreadsheet_extensions_present(self):
        for ext in [".xlsx", ".xlsm", ".xls", ".pptx"]:
            assert ext in BINARY_EXTENSIONS, f"{ext} missing from BINARY_EXTENSIONS"

    def test_text_not_in_binary(self):
        assert ".txt" not in BINARY_EXTENSIONS
        assert ".py" not in BINARY_EXTENSIONS


# -- download_slack_files Tests ---------------------------------------------


def _make_mock_response(content, status=200):
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.read = AsyncMock(return_value=content)
    return resp


class TestDownloadSlackFiles:
    @pytest.mark.asyncio
    async def test_zip_file_as_binary(self):
        """A zip file should be downloaded as binary type."""
        fake_content = b"PK\x03\x04fake zip content"

        mock_resp = _make_mock_response(fake_content)

        with patch("slack_app.utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value = mock_session

            files = [{
                "url_private_download": "https://files.slack.com/test.zip",
                "name": "test.zip",
                "mimetype": "application/zip",
                "size": 1024,
            }]

            result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 1
        assert result[0]["type"] == "binary"
        assert result[0]["name"] == "test.zip"
        assert result[0]["mimetype"] == "application/zip"
        # Verify base64 encoding
        decoded = base64.standard_b64decode(result[0]["data"])
        assert decoded == fake_content

    @pytest.mark.asyncio
    async def test_xlsx_file_as_binary(self):
        """An xlsx file shared in Slack should now be downloaded as binary."""
        fake_content = b"PK\x03\x04fake xlsx content"

        mock_resp = _make_mock_response(fake_content)

        with patch("slack_app.utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value = mock_session

            files = [{
                "url_private_download": "https://files.slack.com/report.xlsx",
                "name": "report.xlsx",
                "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "size": 2048,
            }]

            result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 1
        assert result[0]["type"] == "binary"
        assert result[0]["name"] == "report.xlsx"

    @pytest.mark.asyncio
    async def test_tar_gz_by_mimetype(self):
        """A tar.gz file identified by mimetype should be binary."""
        fake_content = b"\x1f\x8b fake gzip"

        mock_resp = _make_mock_response(fake_content)

        with patch("slack_app.utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value = mock_session

            files = [{
                "url_private_download": "https://files.slack.com/archive.tar.gz",
                "name": "archive.tar.gz",
                "mimetype": "application/gzip",
                "size": 512,
            }]

            result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 1
        assert result[0]["type"] == "binary"

    @pytest.mark.asyncio
    async def test_zip_by_extension_unknown_mimetype(self):
        """A zip file with unknown mimetype but .zip extension should be binary."""
        fake_content = b"PK\x03\x04 zip"

        mock_resp = _make_mock_response(fake_content)

        with patch("slack_app.utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value = mock_session

            files = [{
                "url_private_download": "https://files.slack.com/data.zip",
                "name": "data.zip",
                "mimetype": "application/octet-stream",
                "size": 256,
            }]

            result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 1
        assert result[0]["type"] == "binary"

    @pytest.mark.asyncio
    async def test_image_still_works(self):
        """Image files should still be handled as images."""
        fake_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        mock_resp = _make_mock_response(fake_content)

        with patch("slack_app.utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value = mock_session

            files = [{
                "url_private_download": "https://files.slack.com/photo.png",
                "name": "photo.png",
                "mimetype": "image/png",
                "size": 500,
            }]

            result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 1
        assert result[0]["type"] == "image"

    @pytest.mark.asyncio
    async def test_text_file_still_works(self):
        """Text files should still be handled as text."""
        fake_content = b"Hello, world!"

        mock_resp = _make_mock_response(fake_content)

        with patch("slack_app.utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value = mock_session

            files = [{
                "url_private_download": "https://files.slack.com/notes.txt",
                "name": "notes.txt",
                "mimetype": "text/plain",
                "size": 13,
            }]

            result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["data"] == "Hello, world!"

    @pytest.mark.asyncio
    async def test_unsupported_file_skipped(self):
        """Truly unsupported files (e.g., .exe) should still be skipped."""
        fake_content = b"MZ\x90\x00"

        mock_resp = _make_mock_response(fake_content)

        with patch("slack_app.utils.aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value = mock_session

            files = [{
                "url_private_download": "https://files.slack.com/program.exe",
                "name": "program.exe",
                "mimetype": "application/x-msdownload",
                "size": 100,
            }]

            result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_file_too_large_skipped(self):
        """Files over 10MB should be skipped."""
        files = [{
            "url_private_download": "https://files.slack.com/huge.zip",
            "name": "huge.zip",
            "mimetype": "application/zip",
            "size": 11 * 1024 * 1024,
        }]

        result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_file_without_url_skipped(self):
        """Files without a download URL should be skipped."""
        files = [{
            "name": "no_url.zip",
            "mimetype": "application/zip",
            "size": 1024,
        }]

        result = await download_slack_files("xoxb-fake-token", files)

        assert len(result) == 0
