"""Library list endpoint (Asset Library ticket 02)."""
import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from mongomock_motor import AsyncMongoMockClient

from api import routes
from api.asset_routes import setup_asset_routes
from observability import assets as asset_store
from observability.assets import AssetDescriptor, record_asset


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
            "new": "new.png",
            "other": "other.csv",
        }
        mimes = {
            "old": "application/pdf",
            "new": "image/png",
            "other": "text/csv",
        }
        return {
            "name": names.get(file_id, "report.pdf"),
            "mime_type": mimes.get(file_id, "application/pdf"),
            "size_bytes": 16,
        }
    monkeypatch.setattr(asset_store, "_served_meta", lookup)
    monkeypatch.setattr(routes, "_served_files", {
        "old": {"path": "/tmp/old.pdf", "original_name": "old.pdf"},
        "new": {"path": "/tmp/new.png", "original_name": "new.png"},
        "other": {"path": "/tmp/other.csv", "original_name": "other.csv"},
    })


@pytest_asyncio.fixture
async def client(asset_db):
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


def _descriptor(**overrides):
    values = dict(
        file_id="old",
        owner_email=OWNER,
        conversation_id="conv-1",
        source="opencode",
    )
    values.update(overrides)
    return AssetDescriptor(**values)


@pytest.mark.asyncio
async def test_unauthenticated_list_is_rejected(client):
    response = await client.get("/api/assets")
    assert response.status == 401


@pytest.mark.asyncio
async def test_empty_list_for_user_with_no_assets(client):
    response = await client.get("/api/assets", headers={"Test-User": OWNER})
    assert response.status == 200
    body = await response.json()
    assert body["assets"] == []
    assert body["query"] == {}


@pytest.mark.asyncio
async def test_list_returns_only_caller_assets_newest_first(
    client, served_meta, monkeypatch,
):
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

    response = await client.get("/api/assets", headers={"Test-User": OWNER})
    assert response.status == 200
    body = await response.json()
    assert [row["file_id"] for row in body["assets"]] == ["new", "old"]
    assert all(row["file_id"] != "other" for row in body["assets"])
    assert body["assets"][0]["name"] == "new.png"
    assert body["assets"][0]["mime_type"] == "image/png"
    assert body["assets"][0]["size_bytes"] == 16
    assert body["assets"][0]["created_at"] == "2026-09-20T11:00:00Z"
    assert body["query"] == {}

    other = await client.get("/api/assets", headers={"Test-User": OTHER})
    assert [row["file_id"] for row in (await other.json())["assets"]] == ["other"]


@pytest.mark.asyncio
async def test_list_accepts_query_object_without_filtering(client, served_meta):
    record_asset(_descriptor(file_id="old"))
    await _await_persist_tasks()
    record_asset(_descriptor(file_id="new"))
    await _await_persist_tasks()

    response = await client.get("/api/assets?q=old", headers={"Test-User": OWNER})
    assert response.status == 200
    body = await response.json()
    assert body["query"] == {"q": "old"}
    assert {row["file_id"] for row in body["assets"]} == {"old", "new"}
