import asyncio
from types import SimpleNamespace


from tools import loma_skills


def test_get_notes_that_it_returns_skill_md_only(monkeypatch, capsys):
    async def fake_get_skill(db, slug):
        return {
            "slug": slug,
            "name": "Demo",
            "description": "Demo description",
            "content": "Main instructions",
        }

    monkeypatch.setattr(loma_skills, "_connect_db", lambda: (SimpleNamespace(close=lambda: None), object()))
    monkeypatch.setattr(loma_skills.skill_service, "get_skill", fake_get_skill)

    status = asyncio.run(loma_skills._run(SimpleNamespace(command="get", slug="demo")))

    assert status == 0
    output = capsys.readouterr().out
    assert '"content": "Main instructions"' in output
    assert "get returns metadata plus SKILL.md content only; use dump for all inline text files" in output


def test_dump_prints_markdown(monkeypatch, capsys):
    async def fake_get_skill(db, slug):
        return {"slug": slug, "name": "Demo", "files": []}

    monkeypatch.setattr(loma_skills, "_connect_db", lambda: (SimpleNamespace(close=lambda: None), object()))
    monkeypatch.setattr(loma_skills.skill_service, "get_skill", fake_get_skill)
    monkeypatch.setattr(loma_skills.skill_service, "format_skill_dump_markdown", lambda skill: "# Demo\n")

    status = asyncio.run(loma_skills._run(SimpleNamespace(command="dump", slug="demo")))

    assert status == 0
    assert capsys.readouterr().out == "# Demo\n"
