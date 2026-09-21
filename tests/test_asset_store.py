"""Asset store + recording seam (Asset Library ticket 01)."""
import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient

from api import routes
from api.file_routes import setup_file_routes
from observability import assets as asset_store
from observability.assets import (
    AssetDescriptor,
    asset_recording,
    delete_assets_for_conversation,
    list_assets,
    record_asset,
    resolve_asset_availability,
)


OWNER = "alice@example.test"
OTHER = "bob@example.test"


@pytest.fixture
def asset_db(monkeypatch):
    db = AsyncMongoMockClient()["loma_test"]
    monkeypatch.setattr(asset_store, "get_db", lambda: db)
    return db


@pytest.fixture
def served_meta(monkeypatch):
    def lookup(file_id):
        names = {
            "old": "old.pdf",
            "new": "new.pdf",
            "other": "other.pdf",
            "file-aaa": "report.pdf",
        }
        return {
            "name": names.get(file_id, "report.pdf"),
            "mime_type": "application/pdf",
            "size_bytes": 16,
        }
    monkeypatch.setattr(asset_store, "_served_meta", lookup)


def _descriptor(**overrides):
    values = dict(
        file_id="file-aaa",
        owner_email=OWNER,
        conversation_id="conv-1",
        source="opencode",
    )
    values.update(overrides)
    return AssetDescriptor(**values)


async def _await_persist_tasks():
    current = asyncio.current_task()
    pending = [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done()
        and getattr(task.get_coro(), "cr_code", None)
        and task.get_coro().cr_code.co_name == "_persist_asset"
    ]
    if pending:
        await asyncio.gather(*pending)


@pytest.mark.asyncio
async def test_record_asset_writes_expected_document(asset_db, served_meta):
    record_asset(_descriptor())
    await _await_persist_tasks()

    rows = await list_assets(OWNER)
    assert len(rows) == 1
    row = rows[0]
    assert row["file_id"] == "file-aaa"
    assert row["owner_email"] == OWNER
    assert row["conversation_id"] == "conv-1"
    assert row["name"] == "report.pdf"
    assert row["mime_type"] == "application/pdf"
    assert row["size_bytes"] == 16
    assert row["source"] == "opencode"
    assert isinstance(row["created_at"], datetime)
    assert "_id" not in row


@pytest.mark.asyncio
async def test_list_assets_returns_only_owner_rows_newest_first(asset_db, served_meta, monkeypatch):
    times = [
        datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 20, 11, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc),
    ]
    monkeypatch.setattr(asset_store, "_utcnow", lambda: times.pop(0))

    record_asset(_descriptor(file_id="old"))
    await _await_persist_tasks()
    record_asset(_descriptor(file_id="new"))
    await _await_persist_tasks()
    record_asset(_descriptor(file_id="other", owner_email=OTHER))
    await _await_persist_tasks()

    rows = await list_assets(OWNER)
    assert [row["file_id"] for row in rows] == ["new", "old"]
    assert all(row["owner_email"] == OWNER for row in rows)

    other_rows = await list_assets(OTHER)
    assert [row["file_id"] for row in other_rows] == ["other"]


@pytest.mark.asyncio
async def test_assets_collection_indexes(asset_db):
    await asset_store.ensure_indexes(asset_db)
    info = await asset_db.assets.index_information()
    keys = {tuple(index["key"]) for index in info.values()}
    assert (("file_id", 1),) in keys
    assert (("owner_email", 1), ("created_at", -1)) in keys
    unique = [
        index for index in info.values()
        if index.get("unique") and index["key"] == [("file_id", 1)]
    ]
    assert unique


@pytest.mark.asyncio
async def test_delete_assets_for_conversation_removes_library_rows(asset_db, served_meta):
    record_asset(_descriptor(file_id="stay", conversation_id="conv-keep"))
    await _await_persist_tasks()
    record_asset(_descriptor(file_id="gone", conversation_id="conv-drop"))
    await _await_persist_tasks()

    removed = await delete_assets_for_conversation("conv-drop")
    assert removed == 1

    rows = await list_assets(OWNER)
    assert [row["file_id"] for row in rows] == ["stay"]


@pytest.mark.asyncio
async def test_duplicate_file_id_keeps_a_single_row(asset_db, served_meta):
    record_asset(_descriptor())
    await _await_persist_tasks()
    record_asset(_descriptor(conversation_id="conv-2"))
    await _await_persist_tasks()

    rows = await list_assets(OWNER)
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "conv-1"


@pytest.fixture
def served_dir(tmp_path, monkeypatch):
    served = tmp_path / "served"
    served.mkdir()
    monkeypatch.setattr(routes, "SERVED_FILES_DIR", served)
    monkeypatch.setattr(routes, "_served_files", {})
    return tmp_path


def _write_source(tmp_path, name="report.pdf", content=b"%PDF-1.4 synthetic"):
    source = tmp_path / name
    source.write_bytes(content)
    return source


@pytest.mark.asyncio
async def test_registration_records_one_asset_with_opencode_provenance(asset_db, served_dir):
    source = _write_source(served_dir)
    with asset_recording(conversation_id="conv-1"):
        result = routes.register_served_file(str(source), owner_email=OWNER)
    await _await_persist_tasks()

    rows = await list_assets(OWNER)
    assert len(rows) == 1
    row = rows[0]
    assert row["file_id"] == result["file_id"]
    assert row["owner_email"] == OWNER
    assert row["conversation_id"] == "conv-1"
    assert row["name"] == "report.pdf"
    assert row["mime_type"] == "application/pdf"
    assert row["size_bytes"] == len(b"%PDF-1.4 synthetic")
    assert row["source"] == "opencode"
    assert result["url"] == f"/api/files/{result['file_id']}"
    assert result["file_id"] in routes._served_files


@pytest.mark.asyncio
async def test_dual_file_turn_records_two_assets_sharing_conversation(asset_db, served_dir):
    pdf = _write_source(served_dir, "report.pdf", b"%PDF")
    csv = _write_source(served_dir, "data.csv", b"a,b\n1,2\n")
    with asset_recording(conversation_id="conv-shared"):
        first = routes.register_served_file(str(pdf), owner_email=OWNER)
        await _await_persist_tasks()
        second = routes.register_served_file(str(csv), owner_email=OWNER)
        await _await_persist_tasks()

    rows = await list_assets(OWNER)
    assert {row["file_id"] for row in rows} == {first["file_id"], second["file_id"]}
    assert {row["conversation_id"] for row in rows} == {"conv-shared"}
    assert {row["name"] for row in rows} == {"report.pdf", "data.csv"}


@pytest_asyncio.fixture
async def file_client(served_dir):
    @web.middleware
    async def synthetic_identity(request, handler):
        request["user_email"] = request.headers.get("Test-User", "")
        request["system_role"] = request.headers.get("Test-Role", "chatter")
        return await handler(request)

    app = web.Application(middlewares=[synthetic_identity])
    setup_file_routes(app)
    async with TestClient(TestServer(app)) as http:
        yield http


class _BoomAssets:
    async def create_index(self, *_args, **_kwargs):
        return None

    async def insert_one(self, *_args, **_kwargs):
        raise RuntimeError("durable write failed")


class _BoomDb:
    assets = _BoomAssets()


@pytest.mark.asyncio
async def test_durable_write_failure_does_not_break_serving(
    served_dir, file_client, monkeypatch, caplog,
):
    monkeypatch.setattr(asset_store, "get_db", lambda: _BoomDb())
    source = _write_source(served_dir)
    with asset_recording(conversation_id="conv-1"):
        result = routes.register_served_file(str(source), owner_email=OWNER)
    await _await_persist_tasks()

    assert result["file_id"]
    assert result["url"].startswith("/api/files/")
    response = await file_client.get(
        result["url"], headers={"Test-User": OWNER},
    )
    assert response.status == 200
    assert await response.read() == b"%PDF-1.4 synthetic"
    assert "Asset recording failed" in caplog.text


@pytest.mark.asyncio
async def test_availability_available_when_servable(served_dir):
    source = _write_source(served_dir)
    result = routes.register_served_file(str(source), owner_email=OWNER)
    status, reason = await resolve_asset_availability(result["file_id"])
    assert status == "available"
    assert reason is None


@pytest.mark.asyncio
async def test_availability_unavailable_when_not_servable():
    status, reason = await resolve_asset_availability("missing-file-id")
    assert status == "unavailable"
    assert reason is not None
    assert "missing-file-id" in reason
