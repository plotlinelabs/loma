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


def test_format_skill_dump_markdown_includes_all_inline_files_and_assets():
    skill = {
        "slug": "demo-skill",
        "name": "Demo Skill",
        "description": "Demo description",
        "tags": ["demo", "test"],
        "files": [
            skill_service.validate_text_file("SKILL.md", "Main instructions"),
            skill_service.validate_text_file("notes/extra.md", "Extra context"),
            skill_service.validate_asset_file("assets/example.pdf", b"%PDF-1.4", "example.pdf"),
        ],
    }

    rendered = skill_service.format_skill_dump_markdown(skill)

    assert "# Demo Skill" in rendered
    assert "Slug: `demo-skill`" in rendered
    assert "Description: Demo description" in rendered
    assert "Tags: demo, test" in rendered
    assert "## SKILL.md\n\nMain instructions" in rendered
    assert "## notes/extra.md\n\nExtra context" in rendered
    assert "## Asset files" in rendered
    assert "`assets/example.pdf` (application/pdf, 8 bytes)" in rendered
