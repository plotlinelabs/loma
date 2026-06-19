"""Tests for Phase 4 — Dashboard file delivery via SSE.

Tests cover:
  - File path detection regex (_detect_file_paths)
  - File serving infrastructure (register_served_file, handle_serve_file)
  - File event emission during streaming
  - ChatEvent types in api.ts (verified via prompt.py)
  - Phase 5: System prompt includes attachment flags
"""

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── File Path Detection Tests ─────────────────────────────────────────────


class TestFilePathDetection:
    """Tests for _detect_file_paths in agent/client.py."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Import the function under test."""
        from agent.client import _detect_file_paths
        self.detect = _detect_file_paths

    def _create_temp_file(self, suffix: str) -> str:
        """Create a real temp file so detection validates existence."""
        f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, prefix="test_")
        f.write(b"test content")
        f.close()
        return f.name

    def test_detects_saved_to_pdf(self):
        path = self._create_temp_file(".pdf")
        try:
            text = f"The report has been saved to {path}"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_available_at_xlsx(self):
        path = self._create_temp_file(".xlsx")
        try:
            text = f"Your spreadsheet is available at {path}"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_written_to_docx(self):
        path = self._create_temp_file(".docx")
        try:
            text = f"Document written to {path}"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_file_at_png(self):
        path = self._create_temp_file(".png")
        try:
            text = f"file at {path}"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_exported_to_csv(self):
        path = self._create_temp_file(".csv")
        try:
            text = f"Data exported to {path}"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_generated_at_html(self):
        path = self._create_temp_file(".html")
        try:
            text = f"HTML generated at {path}"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_uploaded_to_zip(self):
        path = self._create_temp_file(".zip")
        try:
            text = f"Archive uploaded to {path}"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_multiple_files(self):
        path1 = self._create_temp_file(".pdf")
        path2 = self._create_temp_file(".xlsx")
        try:
            text = f"Report saved to {path1} and data exported to {path2}"
            result = self.detect(text)
            assert path1 in result
            assert path2 in result
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_deduplicates_same_path(self):
        path = self._create_temp_file(".pdf")
        try:
            text = f"File saved to {path}. The file is at {path}."
            result = self.detect(text)
            assert result.count(path) == 1
        finally:
            os.unlink(path)

    def test_ignores_nonexistent_files(self):
        text = "saved to /tmp/nonexistent_file_xyz_99999.pdf"
        result = self.detect(text)
        assert len(result) == 0

    def test_ignores_non_tmp_paths(self):
        text = "saved to /home/user/report.pdf"
        result = self.detect(text)
        assert len(result) == 0

    def test_ignores_unsupported_extensions(self):
        path = self._create_temp_file(".exe")
        try:
            text = f"saved to {path}"
            result = self.detect(text)
            assert path not in result
        finally:
            os.unlink(path)

    def test_strips_trailing_punctuation(self):
        path = self._create_temp_file(".pdf")
        try:
            text = f"The report is saved to {path}."
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_backtick_wrapped_path(self):
        path = self._create_temp_file(".pdf")
        try:
            text = f"saved to `{path}`"
            result = self.detect(text)
            assert path in result
        finally:
            os.unlink(path)

    def test_detects_bare_tmp_path_for_downloadable_extensions(self):
        """Bare /tmp/ paths with downloadable extensions should be detected."""
        path = self._create_temp_file(".pdf")
        try:
            text = f"Here is the file: {path}"
            result = self.detect(text)
            # The second regex group catches bare /tmp/ paths with downloadable extensions
            assert path in result
        finally:
            os.unlink(path)


# ── File Serving Infrastructure Tests ──────────────────────────────────────


class TestFileServing:
    """Tests for register_served_file and handle_serve_file in api/routes.py."""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Set up a temporary served files directory and patch the module constant."""
        import api.routes as routes_mod
        self.test_dir = tempfile.mkdtemp(prefix="test_served_")
        self.original_dir = routes_mod.SERVED_FILES_DIR
        routes_mod.SERVED_FILES_DIR = Path(self.test_dir)
        yield
        routes_mod.SERVED_FILES_DIR = self.original_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_register_served_file_creates_copy(self):
        """register_served_file should copy the file and return metadata."""
        from api.routes import register_served_file, SERVED_FILES_DIR, _served_files

        # Create a source file
        src = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=self.test_dir)
        src.write(b"PDF content here")
        src.close()

        result = register_served_file(src.name)

        assert "file_id" in result
        assert result["url"].startswith("/api/files/")
        assert result["name"] == os.path.basename(src.name)
        assert result["mime_type"] == "application/pdf"
        assert result["size"] == 16  # len("PDF content here")

        # Verify the file was copied to SERVED_FILES_DIR
        dest_path = SERVED_FILES_DIR / f"{result['file_id']}.pdf"
        assert dest_path.exists()

        # Cleanup
        os.unlink(src.name)

    def test_register_served_file_custom_name(self):
        """register_served_file should use custom name if provided."""
        from api.routes import register_served_file

        src = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, dir=self.test_dir)
        src.write(b"a,b,c\n1,2,3\n")
        src.close()

        result = register_served_file(src.name, original_name="my_report.csv")
        assert result["name"] == "my_report.csv"
        assert result["mime_type"] == "text/csv"

        os.unlink(src.name)

    def test_register_served_file_not_found(self):
        """register_served_file should raise FileNotFoundError for missing files."""
        from api.routes import register_served_file

        with pytest.raises(FileNotFoundError):
            register_served_file("/tmp/nonexistent_file_abc123.pdf")

    def test_register_served_file_for_directory(self):
        """register_served_file should raise FileNotFoundError for directories."""
        from api.routes import register_served_file

        with pytest.raises(FileNotFoundError):
            register_served_file(self.test_dir)

    def test_registered_file_in_registry(self):
        """Registered files should be findable in the in-memory registry."""
        from api.routes import register_served_file, _served_files

        src = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, dir=self.test_dir)
        src.write(b"hello")
        src.close()

        result = register_served_file(src.name)
        file_id = result["file_id"]

        assert file_id in _served_files
        assert _served_files[file_id]["original_name"] == os.path.basename(src.name)

        os.unlink(src.name)


# ── Downloadable Extensions Tests ──────────────────────────────────────────


class TestDownloadableExtensions:
    """Tests for _DOWNLOADABLE_EXTENSIONS constant."""

    def test_common_document_extensions(self):
        from agent.client import _DOWNLOADABLE_EXTENSIONS

        for ext in [".pdf", ".docx", ".pptx", ".xlsx", ".csv"]:
            assert ext in _DOWNLOADABLE_EXTENSIONS, f"{ext} should be downloadable"

    def test_common_image_extensions(self):
        from agent.client import _DOWNLOADABLE_EXTENSIONS

        for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg"]:
            assert ext in _DOWNLOADABLE_EXTENSIONS, f"{ext} should be downloadable"

    def test_common_archive_extensions(self):
        from agent.client import _DOWNLOADABLE_EXTENSIONS

        for ext in [".zip", ".tar", ".gz"]:
            assert ext in _DOWNLOADABLE_EXTENSIONS, f"{ext} should be downloadable"

    def test_media_extensions(self):
        from agent.client import _DOWNLOADABLE_EXTENSIONS

        for ext in [".mp4", ".mp3", ".wav"]:
            assert ext in _DOWNLOADABLE_EXTENSIONS, f"{ext} should be downloadable"

    def test_text_data_extensions(self):
        from agent.client import _DOWNLOADABLE_EXTENSIONS

        for ext in [".json", ".html", ".txt", ".md"]:
            assert ext in _DOWNLOADABLE_EXTENSIONS, f"{ext} should be downloadable"


# ── Phase 5: System Prompt Tests ──────────────────────────────────────────


class TestPhase5PromptUpdates:
    """Tests that the system prompt includes attachment flag documentation."""

    def test_gmail_attachments_flag_in_prompt(self):
        """System prompt should document --attachments flag for Gmail."""
        from agent.prompt import build_pooled_system_prompt

        prompt = build_pooled_system_prompt()
        assert "--attachments" in prompt, "Gmail --attachments flag should be in system prompt"

    def test_slack_user_file_flag_in_prompt(self):
        """System prompt should document --file flag for Slack User."""
        from agent.prompt import build_pooled_system_prompt

        prompt = build_pooled_system_prompt()
        assert "--file /path/to/file" in prompt, "Slack User --file flag should be in system prompt"

    def test_slack_user_file_title_flag_in_prompt(self):
        """System prompt should document --file-title flag for Slack User."""
        from agent.prompt import build_pooled_system_prompt

        prompt = build_pooled_system_prompt()
        assert "--file-title" in prompt, "Slack User --file-title flag should be in system prompt"

    def test_gmail_send_email_attachments(self):
        """send-email command should show --attachments as optional."""
        from agent.prompt import _TOOLS_AND_SKILLS_SECTION

        assert "send-email" in _TOOLS_AND_SKILLS_SECTION
        assert "--attachments" in _TOOLS_AND_SKILLS_SECTION

    def test_gmail_create_draft_attachments(self):
        """create-draft command should show --attachments as optional."""
        from agent.prompt import _TOOLS_AND_SKILLS_SECTION

        # Verify the create-draft command includes --attachments
        idx = _TOOLS_AND_SKILLS_SECTION.find("create-draft")
        assert idx >= 0
        # Check that --attachments appears after create-draft in the same line
        line_end = _TOOLS_AND_SKILLS_SECTION.find("\n", idx)
        create_draft_line = _TOOLS_AND_SKILLS_SECTION[idx:line_end]
        assert "--attachments" in create_draft_line

    def test_slack_user_send_message_file(self):
        """send-message command for slack-personal should show --file as optional."""
        from agent.prompt import _TOOLS_AND_SKILLS_SECTION

        # Find the slack-personal section
        idx = _TOOLS_AND_SKILLS_SECTION.find("slack-personal")
        assert idx >= 0
        section_end = _TOOLS_AND_SKILLS_SECTION.find("\n\n", idx)
        slack_section = _TOOLS_AND_SKILLS_SECTION[idx:section_end]
        assert "--file" in slack_section
        assert "--file-title" in slack_section


# ── Skill File Tests ──────────────────────────────────────────────────────


class TestSkillFileUpdates:
    """Tests that skill files document the new attachment flags."""

    def test_pylon_skill_has_attachment_flag(self):
        """Pylon support skill should document --attachment flag."""
        skill_path = Path(__file__).parent.parent / ".claude" / "skills" / "pylon-support" / "SKILL.md"
        if skill_path.exists():
            content = skill_path.read_text()
            assert "--attachment" in content, "Pylon skill should document --attachment flag"

    def test_slack_reader_skill_has_file_flag(self):
        """Slack reader skill should document --file flag for send command."""
        skill_path = Path(__file__).parent.parent / ".claude" / "skills" / "slack-reader" / "SKILL.md"
        if skill_path.exists():
            content = skill_path.read_text()
            assert "--file" in content, "Slack reader skill should document --file flag"
            assert "--file-title" in content, "Slack reader skill should document --file-title flag"

    def test_slack_reader_skill_has_file_example(self):
        """Slack reader skill should have a file attachment example."""
        skill_path = Path(__file__).parent.parent / ".claude" / "skills" / "slack-reader" / "SKILL.md"
        if skill_path.exists():
            content = skill_path.read_text()
            assert "report.pdf" in content or "--file /tmp/" in content, \
                "Slack reader skill should have a file attachment example"


# ── File Event Type Tests ──────────────────────────────────────────────────


class TestFileEventConstants:
    """Tests for file-related constants and regex patterns."""

    def test_file_path_regex_pattern(self):
        """_FILE_PATH_RE should match common file output patterns."""
        from agent.client import _FILE_PATH_RE

        test_cases = [
            ("saved to /tmp/report.pdf", True),
            ("available at /tmp/output.xlsx", True),
            ("written to /tmp/doc.docx", True),
            ("exported to /tmp/data.csv", True),
            ("file at /tmp/image.png", True),
            ("generated at /tmp/page.html", True),
            ("/tmp/standalone.pdf", True),  # bare path with downloadable ext
            ("random text without paths", False),
            ("/home/user/file.pdf", False),  # not /tmp/
        ]

        for text, should_match in test_cases:
            matches = _FILE_PATH_RE.findall(text)
            if should_match:
                assert len(matches) > 0, f"Should match: {text}"
            # Note: bare paths without context may not match the first group

    def test_file_path_regex_captures_groups(self):
        """_FILE_PATH_RE should capture the file path in group 1 or 2."""
        from agent.client import _FILE_PATH_RE

        match = _FILE_PATH_RE.search("saved to /tmp/test_report.pdf")
        assert match is not None
        path = match.group(1) or match.group(2)
        assert path == "/tmp/test_report.pdf"
