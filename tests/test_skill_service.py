import pytest

from api import skill_service


def test_validate_skill_package_requires_skill_md():
    with pytest.raises(skill_service.SkillError, match="SKILL.md"):
        skill_service.validate_skill_package([
            skill_service.validate_text_file("notes.md", "hello"),
        ])


def test_normalize_file_path_rejects_traversal():
    with pytest.raises(skill_service.SkillError):
        skill_service.normalize_file_path("../secret.txt")
    with pytest.raises(skill_service.SkillError):
        skill_service.normalize_file_path("/tmp/secret.txt")


def test_validate_text_file_hashes_content():
    doc = skill_service.validate_text_file("SKILL.md", "---\ndescription: Test\n---\n\nBody")

    assert doc["kind"] == "inline_text"
    assert doc["path"] == "SKILL.md"
    assert doc["size_bytes"] > 0
    assert len(doc["content_hash"]) == 64


def test_store_asset_uses_local_asset_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_service, "LOMA_SKILL_ASSET_DIR", str(tmp_path))

    doc = skill_service.store_asset("demo", "assets/example.pdf", b"%PDF-1.4", "example.pdf")

    assert doc["kind"] == "local_asset"
    assert doc["asset_path"].startswith(str(tmp_path))
    assert doc["content_hash"] in doc["asset_path"]
    assert doc["size_bytes"] == len(b"%PDF-1.4")
