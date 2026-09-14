"""Offline contract, permission, fencing and recovery tests. No production DB/OAuth."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from api import skill_service as skills, skill_sync_service as sync
from integrations.google_docs_skill_source import (
    SourceError, read_tab, build_requests, parse_url, utf16, parse_markdown, block_key,
)


def document(text="Hello\n", tab_id="t1"):
    paragraphs = []
    index = 1
    for line in text.splitlines(keepends=True):
        end = index + utf16(line)
        paragraphs.append({"startIndex": index, "endIndex": end, "paragraph": {
            "elements": [{"textRun": {"content": line}}]}})
        index = end
    return {"title": "Test playbook", "revisionId": "rev1", "tabs": [{"tabProperties": {"tabId": tab_id, "title": "Instructions"},
        "documentTab": {"body": {"content": paragraphs}}}]}


class FakeGoogle:
    def __init__(self):
        self.doc = document()
        self.writes = []
        self.editable = True

    async def read(self, doc_id):
        return deepcopy(self.doc)

    async def can_edit(self, doc_id):
        return self.editable

    async def write(self, doc_id, snapshot, requests):
        if snapshot["revision"] != self.doc["revisionId"]:
            raise SourceError("stale revision", status=409, code="conflict")
        self.writes.append(requests)
        text = "".join(p["paragraph"]["elements"][0]["textRun"]["content"] for p in self.doc["tabs"][0]["documentTab"]["body"]["content"])
        raw = text.encode("utf-16-le")
        for req in requests:
            key, value = next(iter(req.items()))
            if key == "deleteContentRange":
                r = value["range"]
                assert r["endIndex"] <= len(raw)//2  # terminal newline remains
                raw = raw[:(r["startIndex"]-1)*2] + raw[(r["endIndex"]-1)*2:]
            elif key == "insertText":
                i = (value["location"]["index"]-1)*2
                raw = raw[:i] + value["text"].encode("utf-16-le") + raw[i:]
        self.doc = document(raw.decode("utf-16-le"))
        self.doc["revisionId"] = "rev" + str(len(self.writes)+1)


@pytest.fixture
async def env(monkeypatch):
    db = AsyncMongoMockClient().test
    fake = FakeGoogle()
    monkeypatch.setenv("LOMA_GOOGLE_DOCS_SKILLS_ENABLED", "true")
    monkeypatch.setattr(sync, "adapter", AsyncMock(return_value=fake))
    monkeypatch.setattr(sync, "refresh_runtime", AsyncMock())
    await db.users.insert_many([{"email": email, "system_role": "maintainer"} for email in ("owner@example.com", "other@example.com")])
    actor = skills.skill_actor.set("owner@example.com")
    try:
        yield db, fake
    finally:
        skills.skill_actor.reset(actor)


async def imported(db, scope="personal"):
    preview = await sync.preview(db, "owner@example.com", "https://docs.google.com/document/d/test/edit")
    return await sync.import_doc(db, "owner@example.com", url="https://docs.google.com/document/d/test/edit", tab_id="t1",
        slug="test", name="Test", description="Testing instructions", scope=scope, confirm_workspace=scope == "workspace", preview_hash=preview["hash"])


def test_url_validation():
    assert parse_url("https://docs.google.com/document/d/a-1/edit?tab=t1") == "a-1"
    for url in ("http://docs.google.com/document/d/x", "https://evil.test/document/d/x", "https://docs.google.com.evil.test/document/d/x"):
        with pytest.raises(SourceError):
            parse_url(url)


def test_tabs_unicode_whitespace():
    doc = document("Hello 😀\n  code\n\n")
    doc["tabs"].append(document("Other\n", "t2")["tabs"][0])
    with pytest.raises(SourceError):
        read_tab(doc)
    snap = read_tab(doc, "t1")
    assert snap["content"] == "Hello 😀\n  code\n\n"
    requests = build_requests(snap, "Hello 😀\n  updated\n\n")
    for req in requests:
        body = next(iter(req.values()))
        assert (body.get("range") or body.get("location"))["tabId"] == "t1"


@pytest.mark.parametrize("key,value", [("table", {}), ("tableOfContents", {})])
def test_unsupported_structures(key, value):
    doc = document()
    doc["tabs"][0]["documentTab"]["body"]["content"].append({key: value})
    with pytest.raises(SourceError):
        read_tab(doc)


def test_suggestions_fail_closed():
    doc = document()
    doc["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0]["suggestedInsertionIds"] = ["s1"]
    with pytest.raises(SourceError, match="suggestions"):
        read_tab(doc)


def test_formatting_roundtrip():
    doc = document()
    para = doc["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    para["paragraphStyle"] = {"namedStyleType": "HEADING_2"}
    para["elements"] = [{"textRun": {"content": "Hello", "textStyle": {"bold": True}}}, {"textRun": {"content": " world\n", "textStyle": {"italic": True}}}]
    snap = read_tab(doc)
    assert [block_key(b) for b in parse_markdown(snap["content"])] == [block_key(b) for b in snap["blocks"]]
    assert not build_requests(snap, snap["content"])


@pytest.mark.asyncio
async def test_import_does_not_write_and_regular_unchanged(env):
    db, fake = env
    linked = await imported(db)
    assert fake.writes == []
    assert "published_content" not in linked["source"]
    assert "Hello" in linked["content"]
    normal = await skills.upsert_skill(db, slug="regular", files=[skills.validate_text_file("SKILL.md", "---\ndescription: Regular\n---\nOld")], actor="owner@example.com")
    normal = await skills.update_skill_file(db, slug="regular", file_doc=skills.validate_text_file("SKILL.md", normal["content"]+" new"), actor="owner@example.com")
    assert normal["content"].endswith("new")
    assert fake.writes == []


@pytest.mark.asyncio
async def test_poll_deduplicates_versions_and_publishes_human_edit(env):
    db, fake = env
    await imported(db)
    await sync.sync(db, "test", "owner@example.com")
    assert await db.skill_versions.count_documents({}) == 1
    fake.doc = document("Human update\n")
    await sync.sync(db, "test", "owner@example.com")
    assert "Human update" in (await skills.get_skill(db, "test"))["content"]
    assert await db.skill_versions.count_documents({}) == 2


@pytest.mark.asyncio
async def test_conflict_does_not_write_and_valid_edit_does(env):
    db, fake = env
    linked = await imported(db)
    content = linked["content"].replace("Hello", "Agent update 😀")
    with pytest.raises(SourceError, match="changed"):
        await skills.update_skill_file(db, slug="test", file_doc=skills.validate_text_file("SKILL.md", content), actor="owner@example.com", base_hash="old")
    assert not fake.writes
    result = await skills.update_skill_file(db, slug="test", file_doc=skills.validate_text_file("SKILL.md", content), actor="owner@example.com", base_hash=linked["source"]["hash"])
    assert "Agent update 😀" in result["content"]
    assert len(fake.writes) == 1


@pytest.mark.asyncio
async def test_access_all_read_paths_and_package_bypass(env):
    db, fake = env
    linked = await imported(db)
    skills.skill_actor.set("other@example.com")
    assert not await skills.list_skills(db)
    for call in (skills.get_skill(db, "test"), skills.get_skill_file(db, "test", "SKILL.md"), skills.history(db, "test"), skills.version(db, "test", linked["latest_version_id"])):
        with pytest.raises(skills.SkillError):
            await call
    skills.skill_actor.set("owner@example.com")
    with pytest.raises(skills.SkillError, match="Package"):
        await skills.upsert_skill(db, slug="test", files=[skills.validate_text_file("SKILL.md", linked["content"])], actor="owner@example.com")


@pytest.mark.asyncio
async def test_workspace_writes_use_acting_users_connection(env):
    db, fake = env
    linked = await imported(db, "workspace")
    skills.skill_actor.set("other@example.com")
    await skills.update_skill_file(db, slug="test", actor="other@example.com", file_doc=skills.validate_text_file("SKILL.md", linked["content"].replace("Hello", "Changed")), base_hash=linked["source"]["hash"])
    sync.adapter.assert_awaited_with("other@example.com")
    with pytest.raises(skills.SkillError):
        await sync.configure(db, "test", "other@example.com", "disconnect")


@pytest.mark.asyncio
async def test_lease_excludes_overlap(env):
    db, _ = env
    await imported(db)
    async with sync.lease(db, "test"):
        with pytest.raises(SourceError, match="Another sync"):
            async with sync.lease(db, "test"):
                pass


@pytest.mark.asyncio
async def test_invalid_source_retains_published_and_revocation_suspends(env):
    db, fake = env
    linked = await imported(db)
    fake.doc["tabs"][0]["documentTab"]["body"]["content"].append({"table": {}})
    with pytest.raises(SourceError):
        await sync.sync(db, "test", "owner@example.com")
    assert (await skills.get_skill(db, "test"))["content"] == linked["content"]
    fake.read = AsyncMock(side_effect=SourceError("revoked", code="access_revoked"))
    with pytest.raises(SourceError):
        await sync.sync(db, "test", "owner@example.com")
    with pytest.raises(skills.SkillError, match="suspended"):
        await skills.get_skill(db, "test")


@pytest.mark.asyncio
async def test_disconnect_keeps_last_valid_and_private_access(env):
    db, fake = env
    linked = await imported(db)
    result = await sync.configure(db, "test", "owner@example.com", "disconnect")
    assert result["content"] == linked["content"]
    assert "source" not in result
    assert not fake.writes
    skills.skill_actor.set("other@example.com")
    with pytest.raises(skills.SkillError):
        await skills.get_skill(db, "test")


@pytest.mark.asyncio
async def test_write_timeout_reconciles_without_replay(env):
    db, fake = env
    linked = await imported(db)
    original = fake.write
    async def timed_out(*args):
        await original(*args)
        raise TimeoutError()
    fake.write = timed_out
    with pytest.raises(SourceError, match="could not be confirmed"):
        await sync.write_instructions(db, "test", linked["content"].replace("Hello", "Saved remotely"), "owner@example.com", linked["source"]["hash"])
    await sync.sync(db, "test", "owner@example.com")
    assert "Saved remotely" in (await skills.get_skill(db, "test"))["content"]
    assert len(fake.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("old,new", [("One\nTwo\n", "One\n"), ("One\n", "One\nTwo\n"), ("😀\n", "Other 😀\n"), ("One\n\n", "One\n\n"), ("One\n", "Zero\nOne\n")])
async def test_plain_text_patches(old, new):
    fake = FakeGoogle(); fake.doc = document(old)
    snapshot = read_tab(fake.doc)
    await fake.write("test", snapshot, build_requests(snapshot, new))
    assert read_tab(fake.doc)["content"] == new

@pytest.mark.asyncio
async def test_search_and_global_index_privacy(env):
    db, _ = env
    await imported(db)
    assert len(await skills.search_skills(db, "Hello")) == 1
    skills.skill_actor.set(None)
    assert not await skills.list_skills(db)
    assert "test:" not in await skills.skill_index_text(db)


@pytest.mark.asyncio
async def test_supporting_files_and_delete_do_not_touch_google(env):
    db, fake = env
    await imported(db)
    await skills.update_skill_file(db, slug="test", actor="owner@example.com", file_doc=skills.validate_text_file("notes.md", "Extra notes"))
    assert (await skills.get_skill(db, "test"))["scope"] == "personal"
    await skills.delete_skill_file(db, slug="test", path="notes.md", actor="owner@example.com")
    await skills.delete_skill(db, slug="test", actor="owner@example.com")
    assert not fake.writes
    with pytest.raises(skills.SkillError):
        await skills.get_skill_file(db, "test", "SKILL.md")


@pytest.mark.asyncio
async def test_feature_flag_and_preview_conflict(env, monkeypatch):
    db, _ = env
    with pytest.raises(SourceError, match="changed since preview"):
        await sync.import_doc(db, "owner@example.com", url="https://docs.google.com/document/d/test/edit", tab_id="t1", slug="x", name="Test", description="Test", preview_hash="stale")
    assert await db.skills.count_documents({}) == 0
    monkeypatch.setenv("LOMA_GOOGLE_DOCS_SKILLS_ENABLED", "false")
    with pytest.raises(SourceError, match="not enabled"):
        await sync.preview(db, "owner@example.com", "https://docs.google.com/document/d/test/edit")


@pytest.mark.asyncio
async def test_pending_save_blocks_replay_and_paused_sync_still_manual(env):
    db, _ = env
    linked = await imported(db)
    await sync.configure(db, "test", "owner@example.com", "pause")
    await sync.sync(db, "test", "owner@example.com")
    assert not (await skills.get_skill(db, "test"))["source"]["auto_sync_enabled"]
    await db.skills.update_one({"slug": "test"}, {"$set": {"source.pending": {"id": "pending"}}})
    with pytest.raises(SourceError, match="pending save"):
        await sync.write_instructions(db, "test", linked["content"], "owner@example.com", linked["source"]["hash"])


@pytest.mark.asyncio
async def test_fenced_publication_cannot_publish_after_lease_stolen(env):
    db, fake = env
    await imported(db)
    async with sync.lease(db, "test") as (record, guard):
        await db.skills.update_one({"slug": "test"}, {"$set": {"source.lease_token": "new-owner"}})
        with pytest.raises(SourceError, match="lease expired"):
            await sync.publish(db, record, guard, read_tab(document("Stale\n")), "owner@example.com")
    assert "Hello" in (await skills.get_skill(db, "test"))["content"]


@pytest.mark.asyncio
async def test_suspended_owner_can_disconnect(env):
    db, _ = env
    await imported(db)
    await db.skills.update_one({"slug": "test"}, {"$set": {"source.status": "suspended"}})
    result = await sync.configure(db, "test", "owner@example.com", "disconnect")
    assert "source" not in result


async def test_folder_list_preserves_unique_names(env):
    db, _ = env
    await db.skills.insert_many([
        {"slug": "regular-one", "folder": "Support", "enabled": True},
        {"slug": "regular-two", "folder": "Support", "enabled": True},
        {"slug": "private-other", "folder": "Private", "enabled": True,
         "access_controlled": True, "scope": "personal", "created_by": "other@example.com"},
    ])
    assert await skills.list_folders(db) == ["Support"]
