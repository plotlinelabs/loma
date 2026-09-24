"""Offline Sheets contracts, source lifecycle, privacy and regression coverage."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from api import skill_service as skills, skill_sync_service as sync
from integrations import google_sheets_skill_source as sheets
from integrations.google_docs_skill_source import SourceError

URL = "https://docs.google.com/spreadsheets/d/example/edit#gid=0"
OWNER = "owner@example.com"


def document(values=None, gid=0):
    values = values if values is not None else [["Scenario", "Action"], ["Login", "Check invite"]]
    return {"properties": {"title": "Support playbook"}, "sheets": [{"properties": {
        "sheetId": gid, "title": "Escalations", "gridProperties": {"rowCount": 1000, "columnCount": 26}},
        "data": [{"rowData": [{"values": [{"formattedValue": v} if v else {} for v in row]} for row in values]}]}]}


class FakeSheets:
    def __init__(self):
        self.doc = document()
        self.metadata = AsyncMock(side_effect=lambda _: deepcopy(self.doc))
        self.read = AsyncMock(side_effect=lambda *_: deepcopy(self.doc))


@pytest_asyncio.fixture
async def env(monkeypatch):
    db = AsyncMongoMockClient().test
    fake = FakeSheets()
    monkeypatch.setenv("LOMA_GOOGLE_SHEETS_SKILLS_ENABLED", "true")
    monkeypatch.setattr(sync, "sheets_adapter", AsyncMock(return_value=fake))
    monkeypatch.setattr(sync, "refresh_runtime", AsyncMock())
    await db.users.insert_many([{"email": email, "system_role": "maintainer"} for email in (OWNER, "other@example.com")])
    actor = skills.skill_actor.set(OWNER)
    try:
        yield db, fake
    finally:
        skills.skill_actor.reset(actor)


async def imported(db, **kwargs):
    preview = await sync.preview_sheet(db, OWNER, URL, header_row=True)
    return await sync.import_doc(db, OWNER, url=URL, tab_id="0", slug="sheet", name="Support", description="Escalation reference",
        source_type="google_sheet", header_row=True, preview_hash=preview["hash"], **kwargs)


@pytest.mark.parametrize("url,gid", [(URL, 0), (URL.replace("#gid=0", "?gid=23"), 23), (URL.split("#")[0], None)])
def test_parse_url(url, gid):
    assert sheets.parse_url(url) == ("example", gid)


@pytest.mark.parametrize("url", ["http://docs.google.com/spreadsheets/d/a", "https://evil.test/spreadsheets/d/a", "https://docs.google.com.evil.test/spreadsheets/d/a", "https://docs.google.com/document/d/a", URL.replace("gid=0", "gid=no"), URL.replace("gid=0", "gid=-1")])
def test_reject_url(url):
    with pytest.raises(SourceError):
        sheets.parse_url(url)


def test_deterministic_displayed_values_blanks_multiline_unicode_and_injection():
    doc = document([["Scenario", "", "Scenario"], ["😀\n```\n# command", "", "0"], [], ["FALSE", "24/09/2026", "=literal"]])
    result = sheets.read_tab(doc, "example", 0, header_row=True)
    assert result == sheets.read_tab(deepcopy(doc), "example", 0, header_row=True)
    assert '"row": 3' in result["content"] and '"B": ""' in result["content"]
    assert "😀\\n```\\n# command" in result["content"] and "````json" in result["content"]
    assert "not authorization" in result["content"]
    assert result["hash"] != sheets.read_tab(doc, "example", 0, header_row=False)["hash"]
    assert result["hash"] != sheets.read_tab(doc, "another", 0, header_row=True)["hash"]


def test_tab_rename_preserves_hash_and_new_id_does_not_rebind():
    doc = document()
    initial = sheets.read_tab(doc, "example", 0)
    doc["sheets"][0]["properties"]["title"] = "Renamed"
    assert initial["hash"] == sheets.read_tab(doc, "example", 0)["hash"]
    doc["sheets"][0]["properties"]["sheetId"] = 9
    with pytest.raises(SourceError) as err:
        sheets.read_tab(doc, "example", 0)
    assert err.value.code == "access_revoked"


@pytest.mark.parametrize("key,value", [("merges", [{}]), ("charts", [{}]), ("slicers", [{}])])
def test_reject_structures(key, value):
    doc = document(); doc["sheets"][0][key] = value
    with pytest.raises(SourceError):
        sheets.read_tab(doc, "example", 0)


@pytest.mark.parametrize("axis", ["rowMetadata", "columnMetadata"])
def test_hidden_vs_filtered(axis):
    doc = document(); data = doc["sheets"][0]["data"][0]
    data[axis] = [{"hiddenByUser": True}]
    with pytest.raises(SourceError, match="Hidden"):
        sheets.read_tab(doc, "example", 0)
    data[axis] = [{"hiddenByFilter": True}]
    assert "Check invite" in sheets.read_tab(doc, "example", 0)["content"]


@pytest.mark.parametrize("cell", [{"effectiveValue": {"errorValue": {"type": "REF"}}}, {"chipRuns": [{}]}, {"userEnteredValue": {"formulaValue": '=IF(TRUE,IMAGE("url"),"")'}}, {"pivotTable": {"source": {"sheetId": 1}}}])
def test_formula_errors_and_embedded_content(cell):
    doc = document(); doc["sheets"][0]["data"][0]["rowData"][0]["values"][0].update(cell)
    with pytest.raises(SourceError, match="A1"):
        sheets.read_tab(doc, "example", 0)


def test_limits_and_empty_sources(monkeypatch):
    for doc in (document([]), document([["x" * 200001]])):
        with pytest.raises(SourceError):
            sheets.read_tab(doc, "example", 0)
    monkeypatch.setenv("LOMA_SHEETS_SKILL_MAX_CELLS", "100")
    with pytest.raises(SourceError, match="cell limit"):
        sheets.read_tab(document(), "example", 0)


@pytest.mark.asyncio
async def test_preview_gid_zero_among_multiple_tabs_and_limit_before_read(env, monkeypatch):
    db, fake = env
    fake.doc["sheets"].append(document(gid=42)["sheets"][0])
    result = await sync.preview_sheet(db, OWNER, URL)
    assert result["tab_id"] == "0" and len(result["tabs"]) == 2
    no_selection = await sync.preview_sheet(db, OWNER, URL.split("#")[0])
    assert "content" not in no_selection
    fake.read.reset_mock()
    monkeypatch.setenv("LOMA_SHEETS_SKILL_MAX_CELLS", "10")
    with pytest.raises(SourceError):
        await sync.preview_sheet(db, OWNER, URL)
    fake.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_readonly_lifecycle_versions_and_formula_result_refresh(env):
    db, fake = env
    linked = await imported(db)
    assert linked["source"]["type"] == "google_sheet" and "published_content" not in linked["source"]
    with pytest.raises(SourceError, match="read-only"):
        await skills.update_skill_file(db, slug="sheet", file_doc=skills.validate_text_file("SKILL.md", linked["content"]), actor=OWNER)
    await sync.sync(db, "sheet", OWNER)
    assert await db.skill_versions.count_documents({}) == 1
    fake.doc["sheets"][0]["data"][0]["rowData"][1]["values"][1] = {"formattedValue": "New result", "userEnteredValue": {"formulaValue": "=NOW()"}}
    await sync.sync(db, "sheet", OWNER)
    assert "New result" in (await skills.get_skill(db, "sheet"))["content"]
    assert await db.skill_versions.count_documents({}) == 2
    sync.sheets_adapter.assert_awaited_with(db, OWNER)
    await skills.update_skill_file(db, slug="sheet", file_doc=skills.validate_text_file("notes.md", "Local"), actor=OWNER)
    assert (await skills.get_skill_file(db, "sheet", "notes.md"))["content"] == "Local"
    await sync.configure(db, "sheet", OWNER, "pause")
    await sync.sync(db, "sheet", OWNER)
    assert not (await skills.get_skill(db, "sheet"))["source"]["auto_sync_enabled"]
    await sync.configure(db, "sheet", OWNER, "resume")
    await sync.configure(db, "sheet", OWNER, "disconnect")
    assert "source" not in await skills.get_skill(db, "sheet")
    skills.skill_actor.set("other@example.com")
    with pytest.raises(skills.SkillError):
        await skills.get_skill(db, "sheet")


@pytest.mark.asyncio
async def test_private_access_shared_cache_package_restore_protection(env):
    db, _ = env
    linked = await imported(db)
    for actor in (None, "other@example.com"):
        skills.skill_actor.set(actor)
        assert not await skills.list_skills(db)
        assert not await skills.search_skills(db, "Support")
        for fn in (skills.get_skill(db, "sheet"), skills.get_skill_file(db, "sheet", "SKILL.md"), skills.history(db, "sheet"), skills.version(db, "sheet", linked["latest_version_id"]), sync.history(db, "sheet", actor)):
            with pytest.raises(skills.SkillError):
                await fn
    skills.skill_actor.set(OWNER)
    with pytest.raises(skills.SkillError, match="Package"):
        await skills.upsert_skill(db, slug="sheet", files=[skills.validate_text_file("SKILL.md", linked["content"])], actor=OWNER)
    snapshot = await skills.version(db, "sheet", linked["latest_version_id"])
    with pytest.raises(skills.SkillError, match="Package"):
        await skills.upsert_skill(db, slug="sheet", files=snapshot["files_snapshot"], actor=OWNER, source="restore")


@pytest.mark.asyncio
async def test_invalid_retains_snapshot_revocation_suspends_recovery(env):
    db, fake = env
    linked = await imported(db)
    fake.doc["sheets"][0]["merges"] = [{}]
    with pytest.raises(SourceError):
        await sync.sync(db, "sheet", OWNER)
    assert (await skills.get_skill(db, "sheet"))["content"] == linked["content"]
    fake.doc = document(gid=1)
    with pytest.raises(SourceError):
        await sync.sync(db, "sheet", OWNER)
    with pytest.raises(skills.SkillError, match="suspended"):
        await skills.get_skill(db, "sheet")
    fake.doc = document()
    await sync.sync(db, "sheet", OWNER)
    assert (await skills.get_skill(db, "sheet"))["source"]["status"] == "up_to_date"


@pytest.mark.asyncio
async def test_preview_bound_to_settings_and_identity_and_workspace_confirmation(env):
    db, _ = env
    preview = await sync.preview_sheet(db, OWNER, URL, header_row=True)
    args = dict(url=URL, tab_id="0", slug="sheet", name="Test", description="Test", source_type="google_sheet", preview_hash=preview["hash"])
    with pytest.raises(SourceError, match="changed since preview"):
        await sync.import_doc(db, OWNER, **args, header_row=False)
    with pytest.raises(SourceError, match="Confirm"):
        await sync.import_doc(db, OWNER, **args, header_row=True, scope="workspace")
    assert await db.skills.count_documents({}) == 0


@pytest.mark.asyncio
async def test_lease_and_workspace_source_ownership(env):
    db, _ = env
    await imported(db, scope="workspace", confirm_workspace=True)
    async with sync.lease(db, "sheet"):
        with pytest.raises(SourceError, match="Another sync"):
            async with sync.lease(db, "sheet"):
                pass
    skills.skill_actor.set("other@example.com")
    with pytest.raises(skills.SkillError, match="Only the skill owner"):
        await sync.configure(db, "sheet", "other@example.com", "disconnect")
    await sync.sync(db, "sheet", "other@example.com")
    sync.sheets_adapter.assert_awaited_with(db, OWNER)


@pytest.mark.asyncio
async def test_dispatch_independent_flags_pause_and_no_change_versions(env, monkeypatch):
    from scheduler import skill_sync as worker
    db, fake = env
    await imported(db)
    monkeypatch.setenv("LOMA_GOOGLE_DOCS_SKILLS_ENABLED", "false")
    monkeypatch.setattr(worker, "get_db", lambda: db)
    await db.skills.update_one({"slug": "sheet"}, {"$set": {"source.next_check": skills.now_utc()}})
    fake.doc = document([["Title", "Value"], ["New", "Updated"]])
    await worker.sync_due_skills()
    assert "Updated" in (await skills.get_skill(db, "sheet"))["content"]
    await sync.configure(db, "sheet", OWNER, "pause")
    fake.read.reset_mock()
    await worker.sync_due_skills()
    fake.read.assert_not_awaited()
    monkeypatch.setenv("LOMA_GOOGLE_SHEETS_SKILLS_ENABLED", "false")
    with pytest.raises(SourceError, match="not enabled"):
        await sync.sync(db, "sheet", OWNER)
    # Disconnect remains possible when a provider is switched off.
    await sync.configure(db, "sheet", OWNER, "disconnect")


@pytest.mark.asyncio
async def test_rate_limit_per_actor_and_project():
    db = AsyncMongoMockClient().test
    for _ in range(25):
        await sync.sheets_rate_limit(db, OWNER)
    with pytest.raises(SourceError) as error:
        await sync.sheets_rate_limit(db, OWNER)
    assert error.value.code == "temporary_error"
    await sync.sheets_rate_limit(db, "someone@example.com")


@pytest.mark.asyncio
async def test_adapter_uses_stable_id_and_explicit_display_value_fields():
    source = sheets.GoogleSheetsSource("fake-token")
    source._request = AsyncMock(return_value={})
    await source.read("example", 0)
    call = source._request.call_args
    assert call.kwargs["json"]["dataFilters"] == [{"gridRange": {"sheetId": 0}}]
    assert "formattedValue" in call.kwargs["params"]["fields"]
    assert "chipRuns" in call.kwargs["params"]["fields"]
    assert not hasattr(source, "write")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error,code", [
    (429, {}, "temporary_error"),
    (403, {"errors": [{"reason": "rateLimitExceeded"}]}, "temporary_error"),
    (403, {"details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}, "connection_required"),
    (401, {}, "connection_required"),
    (403, {}, "access_revoked"),
    (404, {}, "access_revoked"),
    (500, {}, "temporary_error"),
])
async def test_http_error_classification(monkeypatch, status, error, code):
    import json
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @property
        def content(self): return self
        async def iter_chunked(self, size):
            yield json.dumps({"error": error}).encode()
    response = Response(); response.status = status
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def request(self, *args, **kwargs): return response
    monkeypatch.setattr(sheets.aiohttp, "ClientSession", Session)
    with pytest.raises(SourceError) as exc:
        await sheets.GoogleSheetsSource("test").read("example", 0)
    assert exc.value.code == code


@pytest.mark.asyncio
async def test_project_budget_separate_from_actor_budget():
    db = AsyncMongoMockClient().test
    for actor in range(140):
        await sync.sheets_rate_limit(db, f"{actor}@example.com")
    with pytest.raises(SourceError, match="budget"):
        await sync.sheets_rate_limit(db, "new@example.com")


@pytest.mark.asyncio
async def test_source_routes_preview_import_validation_and_role_guard(env, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from api import skill_source_routes as routes
    db, _ = env
    monkeypatch.setattr(routes, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_user_email", lambda request: OWNER)
    monkeypatch.setattr(routes, "require_maintainer_or_above", lambda request: None)
    monkeypatch.setattr(routes, "require_analyst_or_above", lambda request: None)
    app = web.Application(middlewares=[routes.skill_context]); routes.setup_skill_source_routes(app)
    async with TestClient(TestServer(app)) as client:
        r = await client.get('/api/skill-sources/google-sheets')
        assert (await r.json())["enabled"]
        r = await client.post('/api/skill-sources/google-sheets/preview', json={"url": URL, "header_row": True})
        assert r.status == 200
        preview = await r.json()
        fields = {"url": URL, "tab_id": "0", "slug": "sheet", "name": "Test", "description": "Test", "preview_hash": preview["hash"], "header_row": True}
        r = await client.post('/api/skill-sources/google-sheets/import', json=fields)
        assert r.status == 200 and (await r.json())["source"]["sheet_id"] == 0
        r = await client.post('/api/skill-sources/google-sheets/preview', json={"url": URL, "header_row": "false"})
        assert r.status == 400
        def forbidden(request): raise web.HTTPForbidden()
        monkeypatch.setattr(routes, "require_maintainer_or_above", forbidden)
        r = await client.post('/api/skill-sources/google-sheets/preview', json={"url": URL})
        assert r.status == 403
