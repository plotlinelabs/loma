"""Availability resolver (Asset Library ticket 04)."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient

from api import routes
from api.asset_routes import setup_asset_routes
from observability import assets as asset_store
from observability.assets import resolve_asset_availability


OWNER = "alice@example.test"


def _register(tmp_path, monkeypatch, name="report.pdf"):
    served = tmp_path / "served"
    served.mkdir(exist_ok=True)
    monkeypatch.setattr(routes, "SERVED_FILES_DIR", served)
    monkeypatch.setattr(routes, "_served_files", {})
    source = tmp_path / name
    source.write_bytes(b"%PDF-1.4 synthetic")
    return routes.register_served_file(str(source), owner_email=OWNER)


@pytest.mark.asyncio
async def test_intact_asset_is_available(tmp_path, monkeypatch):
    artifact = _register(tmp_path, monkeypatch)
    status, reason = await resolve_asset_availability(artifact["file_id"])
    assert status == "available"
    assert reason is None


@pytest.mark.asyncio
async def test_removed_bytes_are_unavailable_with_path_reason(tmp_path, monkeypatch):
    artifact = _register(tmp_path, monkeypatch)
    path = Path(routes._served_files[artifact["file_id"]]["path"])
    path.unlink()
    status, reason = await resolve_asset_availability(artifact["file_id"])
    assert status == "unavailable"
    assert reason is not None
    assert str(path) in reason


@pytest.mark.asyncio
async def test_cleared_registry_reason_differs_from_missing_bytes(tmp_path, monkeypatch):
    artifact = _register(tmp_path, monkeypatch)
    path = Path(routes._served_files[artifact["file_id"]]["path"])
    path.unlink()
    _, missing_reason = await resolve_asset_availability(artifact["file_id"])

    routes._served_files.clear()
    status, cleared_reason = await resolve_asset_availability(artifact["file_id"])
    assert status == "unavailable"
    assert cleared_reason is not None
    assert artifact["file_id"] in cleared_reason
    assert cleared_reason != missing_reason


@pytest.mark.asyncio
async def test_expired_worker_record_reason_is_distinct(tmp_path, monkeypatch):
    artifact = _register(tmp_path, monkeypatch)
    path = Path(routes._served_files[artifact["file_id"]]["path"])
    path.unlink()
    _, missing_reason = await resolve_asset_availability(artifact["file_id"])
    routes._served_files.clear()
    _, cleared_reason = await resolve_asset_availability(artifact["file_id"])

    artifact_id = "a" * 32
    expires = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = AsyncMongoMockClient()["loma_test"]
    await db.isolated_artifact_downloads.insert_one({
        "_id": artifact_id,
        "expires_at": expires,
    })
    monkeypatch.setattr(asset_store, "get_db", lambda: db)

    status, worker_reason = await resolve_asset_availability(f"worker-{artifact_id}")
    assert status == "unavailable"
    assert worker_reason is not None
    assert expires.isoformat() in worker_reason
    assert worker_reason != missing_reason
    assert worker_reason != cleared_reason


@pytest_asyncio.fixture
async def list_client(tmp_path, monkeypatch):
    db = AsyncMongoMockClient()["loma_test"]
    monkeypatch.setattr(asset_store, "get_db", lambda: db)
    served = tmp_path / "served"
    served.mkdir()
    monkeypatch.setattr(routes, "SERVED_FILES_DIR", served)
    monkeypatch.setattr(routes, "_served_files", {})

    @web.middleware
    async def synthetic_identity(request, handler):
        request["user_email"] = request.headers.get("Test-User", "")
        request["system_role"] = request.headers.get("Test-Role", "chatter")
        return await handler(request)

    app = web.Application(middlewares=[synthetic_identity])
    setup_asset_routes(app)
    async with TestClient(TestServer(app)) as http:
        yield http


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
async def test_list_surfaces_status_and_reason(list_client, tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4 synthetic")
    intact = routes.register_served_file(str(source), owner_email=OWNER)
    gone = routes.register_served_file(str(source), owner_email=OWNER, original_name="gone.pdf")
    Path(routes._served_files[gone["file_id"]]["path"]).unlink()
    await _await_persist_tasks()

    response = await list_client.get("/api/assets", headers={"Test-User": OWNER})
    assert response.status == 200
    by_id = {row["file_id"]: row for row in (await response.json())["assets"]}
    assert by_id[intact["file_id"]]["status"] == "available"
    assert by_id[intact["file_id"]]["reason"] is None
    assert by_id[gone["file_id"]]["status"] == "unavailable"
    assert routes._served_files[gone["file_id"]]["path"] in by_id[gone["file_id"]]["reason"]


@pytest.mark.asyncio
async def test_list_omits_assets_cleared_from_process_registry(list_client, tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4 synthetic")
    artifact = routes.register_served_file(str(source), owner_email=OWNER)
    await _await_persist_tasks()

    routes._served_files.clear()

    response = await list_client.get("/api/assets", headers={"Test-User": OWNER})
    assert response.status == 200
    ids = [row["file_id"] for row in (await response.json())["assets"]]
    assert artifact["file_id"] not in ids
