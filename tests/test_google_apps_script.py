"""Offline tests for tools/google_apps_script.py get-content (no Google API calls)."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.google_apps_script as gas  # noqa: E402

BIG = "// big\n" + ("x" * (gas.MAX_FILE_SOURCE + 14000)) + "\nfunction end() {}\n"
SMALL = "function onOpen() {}\n"
PAGE = "<html><body>Hi</body></html>"
MANIFEST = '{"timeZone": "Etc/UTC", "runtimeVersion": "V8"}'

FILES = [
    {"name": "Code", "type": "SERVER_JS", "source": BIG},
    {"name": "Util", "type": "SERVER_JS", "source": SMALL},
    {"name": "Page", "type": "HTML", "source": PAGE},
    {"name": "appsscript", "type": "JSON", "source": MANIFEST},
]


@pytest.fixture
def service(monkeypatch):
    svc = MagicMock()
    svc.projects.return_value.getContent.return_value.execute.return_value = {
        "scriptId": "SID", "files": [dict(f) for f in FILES],
    }
    monkeypatch.setattr(gas, "_get_service", AsyncMock(return_value=svc))
    return svc


def run(**kwargs):
    return asyncio.run(gas.get_content("user@example.com", "SID", **kwargs))


def by_name(result):
    return {f["name"]: f for f in result["files"]}


def test_default_flags_truncated_files_and_adds_hint(service):
    result = run()
    files = by_name(result)

    assert result["truncated"] is True
    assert "--output-dir" in result["hint"] and "--file-name" in result["hint"]

    code = files["Code"]
    assert code["truncated"] is True
    assert code["originalChars"] == len(BIG)
    assert code["totalLength"] == len(BIG)
    assert code["source"].startswith(BIG[: gas.MAX_FILE_SOURCE])
    assert code["source"].endswith("[truncated — file too large]")

    for name in ("Util", "Page", "appsscript"):
        assert "truncated" not in files[name]
        assert "originalChars" not in files[name]
    assert files["Util"]["source"] == SMALL
    assert result["editorUrl"] == "https://script.google.com/d/SID/edit"


def test_default_without_large_files_is_unchanged(service):
    service.projects.return_value.getContent.return_value.execute.return_value = {
        "scriptId": "SID", "files": [dict(f) for f in FILES[1:]],
    }
    result = run()
    assert "truncated" not in result and "hint" not in result
    assert set(result) == {"scriptId", "files", "editorUrl"}
    assert by_name(result)["Util"] == {
        "name": "Util", "type": "SERVER_JS", "totalLength": len(SMALL), "source": SMALL,
    }


@pytest.mark.parametrize("name", ["Code", "Code.gs"])
def test_file_name_returns_full_source(service, name):
    result = run(file_name=name)
    assert len(result["files"]) == 1
    entry = result["files"][0]
    assert entry["name"] == "Code"
    assert entry["source"] == BIG
    assert "truncated" not in entry
    assert "truncated" not in result


def test_file_name_unknown_lists_available(service):
    with pytest.raises(ValueError) as exc:
        run(file_name="Missing")
    assert "Code" in str(exc.value) and "appsscript" in str(exc.value)


def test_output_dir_writes_full_files_and_summary_only(service, tmp_path):
    out = tmp_path / "script"
    result = run(output_dir=str(out))

    assert "truncated" not in result
    assert result["outputDir"] == os.path.realpath(out)
    expected = {
        "Code": ("Code.gs", BIG),
        "Util": ("Util.gs", SMALL),
        "Page": ("Page.html", PAGE),
        "appsscript": ("appsscript.json", MANIFEST),
    }
    for entry in result["files"]:
        assert set(entry) == {"name", "type", "chars", "path"}
        filename, source = expected[entry["name"]]
        assert entry["path"] == os.path.realpath(out / filename)
        assert entry["chars"] == len(source)
        assert Path(entry["path"]).read_text(encoding="utf-8") == source
    assert BIG not in json.dumps(result)


def test_output_dir_with_file_name_writes_one_file(service, tmp_path):
    result = run(output_dir=str(tmp_path), file_name="Page.html")
    assert [f["name"] for f in result["files"]] == ["Page"]
    assert sorted(os.listdir(tmp_path)) == ["Page.html"]


def test_output_dir_rejects_path_traversal(service, tmp_path):
    service.projects.return_value.getContent.return_value.execute.return_value = {
        "scriptId": "SID", "files": [{"name": "../evil", "type": "SERVER_JS", "source": "x"}],
    }
    with pytest.raises(ValueError):
        run(output_dir=str(tmp_path / "out"))
    assert not (tmp_path / "evil.gs").exists()


def test_cli_passes_new_flags(service, monkeypatch, tmp_path, capsys):
    import tools._auth_token as auth

    monkeypatch.setattr(auth, "verify_user_auth_token", lambda token, email: True)
    monkeypatch.setattr(sys, "argv", [
        "google_apps_script.py", "--auth-token", "t", "get-content",
        "--user-email", "user@example.com", "--script-id", "SID",
        "--output-dir", str(tmp_path),
    ])
    gas.main()
    printed = json.loads(capsys.readouterr().out)
    assert {f["name"] for f in printed["files"]} == {"Code", "Util", "Page", "appsscript"}
    assert (tmp_path / "Code.gs").read_text(encoding="utf-8") == BIG
