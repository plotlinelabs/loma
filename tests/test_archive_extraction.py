"""Tests for archive extraction logic in agent/client.py."""

import os
import shutil
import tarfile
import tempfile
import zipfile

import pytest

# Import the functions under test
from agent.client import (
    ARCHIVE_EXTENSIONS,
    MAX_FILE_COUNT,
    MAX_SINGLE_FILE_SIZE,
    MAX_UNCOMPRESSED_SIZE,
    _extract_archive,
    _human_size,
)


# -- Fixtures ---------------------------------------------------------------


@pytest.fixture
def extract_dir():
    """Create a temporary extraction directory, cleaned up after test."""
    d = tempfile.mkdtemp(prefix="test_extract_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def tmp_dir():
    """Create a temporary directory for building test archives."""
    d = tempfile.mkdtemp(prefix="test_archive_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_zip(tmp_dir, files, name="test.zip"):
    """Helper to create a zip file with given filename->content mapping."""
    zip_path = os.path.join(tmp_dir, name)
    with zipfile.ZipFile(zip_path, "w") as z:
        for fname, content in files.items():
            z.writestr(fname, content)
    return zip_path


def _make_tar(tmp_dir, files, name="test.tar"):
    """Helper to create a tar file with given filename->content mapping."""
    import io as _io

    tar_path = os.path.join(tmp_dir, name)
    with tarfile.open(tar_path, "w") as t:
        for fname, content in files.items():
            info = tarfile.TarInfo(name=fname)
            info.size = len(content)
            t.addfile(info, _io.BytesIO(content))
    return tar_path


def _make_tar_gz(tmp_dir, files, name="test.tar.gz"):
    """Helper to create a tar.gz file with given filename->content mapping."""
    import io as _io

    tar_path = os.path.join(tmp_dir, name)
    with tarfile.open(tar_path, "w:gz") as t:
        for fname, content in files.items():
            info = tarfile.TarInfo(name=fname)
            info.size = len(content)
            t.addfile(info, _io.BytesIO(content))
    return tar_path


# -- _human_size Tests -------------------------------------------------------


class TestHumanSize:
    def test_bytes(self):
        assert _human_size(500) == "500.0 B"

    def test_kilobytes(self):
        assert _human_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _human_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert _human_size(3 * 1024 * 1024 * 1024) == "3.0 GB"

    def test_zero(self):
        assert _human_size(0) == "0.0 B"


# -- ARCHIVE_EXTENSIONS Tests -----------------------------------------------


class TestArchiveExtensions:
    def test_zip_in_extensions(self):
        assert ".zip" in ARCHIVE_EXTENSIONS

    def test_tar_in_extensions(self):
        assert ".tar" in ARCHIVE_EXTENSIONS

    def test_gz_in_extensions(self):
        assert ".gz" in ARCHIVE_EXTENSIONS

    def test_tgz_in_extensions(self):
        assert ".tgz" in ARCHIVE_EXTENSIONS

    def test_7z_in_extensions(self):
        assert ".7z" in ARCHIVE_EXTENSIONS

    def test_rar_in_extensions(self):
        assert ".rar" in ARCHIVE_EXTENSIONS

    def test_non_archive_not_in_extensions(self):
        assert ".pdf" not in ARCHIVE_EXTENSIONS
        assert ".xlsx" not in ARCHIVE_EXTENSIONS


# -- _extract_archive: ZIP Tests --------------------------------------------


class TestExtractZip:
    def test_basic_zip(self, tmp_dir, extract_dir):
        """A simple zip with a few text files should extract correctly."""
        files = {
            "readme.txt": b"Hello world",
            "data.csv": b"a,b,c\n1,2,3",
        }
        zip_path = _make_zip(tmp_dir, files)

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        assert result["error"] is None
        assert len(result["files"]) == 2

        names = {f["name"] for f in result["files"]}
        assert names == {"readme.txt", "data.csv"}

        for f in result["files"]:
            assert os.path.exists(f["path"])
            assert f["size"] > 0

    def test_zip_with_subdirectories(self, tmp_dir, extract_dir):
        """Zip files with subdirectory structure should preserve paths."""
        files = {
            "dir1/file1.txt": b"content1",
            "dir1/dir2/file2.txt": b"content2",
            "root.txt": b"root content",
        }
        zip_path = _make_zip(tmp_dir, files)

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 3

    def test_empty_zip(self, tmp_dir, extract_dir):
        """An empty zip should extract successfully with 0 files."""
        zip_path = _make_zip(tmp_dir, {})

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 0

    def test_zip_bomb_protection(self, tmp_dir, extract_dir):
        """Zip files exceeding MAX_UNCOMPRESSED_SIZE should be rejected."""
        zip_path = os.path.join(tmp_dir, "bomb.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            large_content = b"A" * (MAX_UNCOMPRESSED_SIZE + 1024)
            z.writestr("large.bin", large_content)

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is False
        assert "too large" in result["error"].lower()

    def test_path_traversal_prevention(self, tmp_dir, extract_dir):
        """Files with path traversal patterns should be skipped."""
        zip_path = os.path.join(tmp_dir, "traversal.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("safe.txt", b"safe content")
            z.writestr("../etc/passwd", b"malicious")
            z.writestr("subdir/../../escape.txt", b"escape")

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        names = {f["name"] for f in result["files"]}
        assert "safe.txt" in names
        assert "../etc/passwd" not in names

    def test_absolute_path_prevention(self, tmp_dir, extract_dir):
        """Files with absolute paths should be skipped."""
        zip_path = os.path.join(tmp_dir, "absolute.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("safe.txt", b"safe")
            z.writestr("/etc/passwd", b"malicious")

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        names = {f["name"] for f in result["files"]}
        assert "safe.txt" in names
        assert "/etc/passwd" not in names

    def test_too_many_files(self, tmp_dir, extract_dir):
        """Zips with too many files should be rejected."""
        files = {f"file_{i}.txt": b"x" for i in range(MAX_FILE_COUNT + 1)}
        zip_path = _make_zip(tmp_dir, files)

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is False
        assert "too many" in result["error"].lower()

    def test_large_individual_file_skipped(self, tmp_dir, extract_dir):
        """Individual files exceeding MAX_SINGLE_FILE_SIZE should be skipped."""
        files = {
            "small.txt": b"small content",
            "huge.bin": b"x" * (MAX_SINGLE_FILE_SIZE + 1),
        }
        zip_path = _make_zip(tmp_dir, files)

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        names = {f["name"] for f in result["files"]}
        assert "small.txt" in names
        assert "huge.bin" not in names

    def test_corrupt_zip(self, tmp_dir, extract_dir):
        """A corrupt zip file should fail gracefully."""
        corrupt_path = os.path.join(tmp_dir, "corrupt.zip")
        with open(corrupt_path, "wb") as f:
            f.write(b"this is not a zip file")

        result = _extract_archive(corrupt_path, extract_dir)

        assert result["success"] is False
        assert result["error"] is not None


# -- _extract_archive: TAR Tests --------------------------------------------


class TestExtractTar:
    def test_basic_tar(self, tmp_dir, extract_dir):
        """A simple tar archive should extract correctly."""
        files = {
            "file1.txt": b"content one",
            "file2.txt": b"content two",
        }
        tar_path = _make_tar(tmp_dir, files)

        result = _extract_archive(tar_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 2

        names = {f["name"] for f in result["files"]}
        assert names == {"file1.txt", "file2.txt"}

    def test_tar_gz(self, tmp_dir, extract_dir):
        """A tar.gz archive should extract correctly."""
        files = {
            "report.csv": b"a,b,c\n1,2,3",
            "notes.md": b"# Notes\nSome content",
        }
        tar_path = _make_tar_gz(tmp_dir, files)

        result = _extract_archive(tar_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 2

    def test_tgz_extension(self, tmp_dir, extract_dir):
        """A .tgz archive should extract correctly."""
        files = {"data.json": b'{"key": "value"}'}
        tar_path = _make_tar_gz(tmp_dir, files, name="test.tgz")

        result = _extract_archive(tar_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 1

    def test_tar_path_traversal(self, tmp_dir, extract_dir):
        """Tar members with path traversal should be skipped."""
        import io as _io

        tar_path = os.path.join(tmp_dir, "traversal.tar")
        with tarfile.open(tar_path, "w") as t:
            info = tarfile.TarInfo(name="safe.txt")
            content = b"safe"
            info.size = len(content)
            t.addfile(info, _io.BytesIO(content))

            info = tarfile.TarInfo(name="../escape.txt")
            content = b"escape"
            info.size = len(content)
            t.addfile(info, _io.BytesIO(content))

        result = _extract_archive(tar_path, extract_dir)

        assert result["success"] is True
        names = {f["name"] for f in result["files"]}
        assert "safe.txt" in names
        assert "../escape.txt" not in names

    def test_corrupt_tar(self, tmp_dir, extract_dir):
        """A corrupt tar should fail gracefully."""
        corrupt_path = os.path.join(tmp_dir, "corrupt.tar")
        with open(corrupt_path, "wb") as f:
            f.write(b"not a tar file at all")

        result = _extract_archive(corrupt_path, extract_dir)

        assert result["success"] is False
        assert result["error"] is not None


# -- _extract_archive: Unsupported Formats ----------------------------------


class TestUnsupportedFormats:
    def test_7z_unsupported(self, tmp_dir, extract_dir):
        """7z files should return an unsupported format error."""
        fake_path = os.path.join(tmp_dir, "test.7z")
        with open(fake_path, "wb") as f:
            f.write(b"fake 7z content")

        result = _extract_archive(fake_path, extract_dir)

        assert result["success"] is False
        assert "unsupported" in result["error"].lower()

    def test_rar_unsupported(self, tmp_dir, extract_dir):
        """RAR files should return an unsupported format error."""
        fake_path = os.path.join(tmp_dir, "test.rar")
        with open(fake_path, "wb") as f:
            f.write(b"fake rar content")

        result = _extract_archive(fake_path, extract_dir)

        assert result["success"] is False
        assert "unsupported" in result["error"].lower()


# -- _extract_archive: Edge Cases -------------------------------------------


class TestEdgeCases:
    def test_zip_with_only_directories(self, tmp_dir, extract_dir):
        """A zip with only empty directories should extract with 0 files."""
        zip_path = os.path.join(tmp_dir, "dirs_only.zip")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.mkdir("empty_dir/")
            z.mkdir("another_dir/")

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 0

    def test_zip_with_mixed_file_types(self, tmp_dir, extract_dir):
        """Zip containing various file types should extract all."""
        files = {
            "image.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            "document.pdf": b"%PDF-1.4 fake pdf content",
            "data.json": b'{"key": "value"}',
            "script.py": b"print('hello')",
            "style.css": b"body { color: red; }",
        }
        zip_path = _make_zip(tmp_dir, files)

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 5

    def test_nested_zip_not_recursively_extracted(self, tmp_dir, extract_dir):
        """Nested zips should appear as files, not be recursively extracted."""
        inner_zip_path = os.path.join(tmp_dir, "inner.zip")
        with zipfile.ZipFile(inner_zip_path, "w") as z:
            z.writestr("inner_file.txt", b"inner content")

        with open(inner_zip_path, "rb") as f:
            inner_content = f.read()

        files = {
            "outer_file.txt": b"outer content",
            "inner.zip": inner_content,
        }
        zip_path = _make_zip(tmp_dir, files, name="outer.zip")

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        assert len(result["files"]) == 2
        names = {f["name"] for f in result["files"]}
        assert "outer_file.txt" in names
        assert "inner.zip" in names

    def test_extracted_files_have_correct_sizes(self, tmp_dir, extract_dir):
        """Extracted files should report their actual sizes."""
        content_a = b"Hello World"
        content_b = b"x" * 5000
        files = {
            "a.txt": content_a,
            "b.bin": content_b,
        }
        zip_path = _make_zip(tmp_dir, files)

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        size_map = {f["name"]: f["size"] for f in result["files"]}
        assert size_map["a.txt"] == len(content_a)
        assert size_map["b.bin"] == len(content_b)

    def test_extracted_file_content_matches(self, tmp_dir, extract_dir):
        """Extracted file contents should match the original data."""
        original = b"The quick brown fox jumps over the lazy dog"
        zip_path = _make_zip(tmp_dir, {"test.txt": original})

        result = _extract_archive(zip_path, extract_dir)

        assert result["success"] is True
        extracted_path = result["files"][0]["path"]
        with open(extracted_path, "rb") as f:
            assert f.read() == original
