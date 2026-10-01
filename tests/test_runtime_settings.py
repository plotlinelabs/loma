"""Runtime preferences and new setup paths cannot rewrite deployment env files."""
import asyncio
import builtins
import os
from pathlib import Path
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from mongomock_motor import AsyncMongoMockClient
from api import runtime_settings as settings
from api import bounded_work_routes, human_task_worker, human_tasks
from tools import ashby


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("ASHBY_ALLOWED_USERS", "LOMA_BOUNDED_WORK_ENABLED"):
        monkeypatch.delenv(key, raising=False)


def test_defaults_are_restricted():
    result = settings.effective()
    assert not result["bounded_work_enabled"]
    assert result["ashby_allowed_users"] == []


def test_explicit_legacy_overrides_including_empty_deny(monkeypatch):
    monkeypatch.setenv("LOMA_BOUNDED_WORK_ENABLED", "false")
    monkeypatch.setenv("ASHBY_ALLOWED_USERS", "")
    result = settings.effective({"bounded_work_enabled": True, "ashby_allowed_users": ["a@b.com"]})
    assert not result["bounded_work_enabled"]
    assert result["ashby_allowed_users"] == []
    assert len(result["overrides"]) == 2


@pytest.mark.asyncio
async def test_admin_activation_is_dynamic(monkeypatch):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(bounded_work_routes, "get_db", lambda: db)
    assert not await bounded_work_routes.enabled()
    await db.gateway_config.insert_one({"_id": "runtime-settings", "bounded_work_enabled": True})
    assert await bounded_work_routes.enabled()
    await db.gateway_config.update_one({"_id": "runtime-settings"}, {"$set": {"bounded_work_enabled": False}})
    assert not await bounded_work_routes.enabled()


@pytest.mark.asyncio
async def test_db_unavailable_cannot_enable_work():
    with pytest.raises(RuntimeError):
        await settings.read(None)


def test_ashby_legacy_does_not_need_database(monkeypatch):
    monkeypatch.setenv("ASHBY_ALLOWED_USERS", " First@example.test, SECOND@example.test ")
    assert ashby._allowed_users() == {"first@example.test", "second@example.test"}


def test_ashby_unconfigured_fails_closed(monkeypatch):
    monkeypatch.delenv("OBSERVABILITY_MONGODB_URI", raising=False)
    with pytest.raises(ashby.AshbyToolError):
        ashby._allowed_users()


def test_ashby_admin_config_and_revoke(monkeypatch):
    import mongomock
    client = mongomock.MongoClient()
    monkeypatch.setattr("pymongo.MongoClient", lambda *a, **kw: client)
    monkeypatch.setenv("OBSERVABILITY_MONGODB_URI", "mongodb://fixture")
    monkeypatch.setenv("OBSERVABILITY_DB_NAME", "fixture")
    db = client.fixture
    db.users.insert_one({"email": "first@example.test", "status": "active"})
    db.gateway_config.insert_one({"_id": "runtime-settings", "ashby_allowed_users": ["first@example.test"]})
    assert ashby._allowed_users() == {"first@example.test"}
    db.users.update_one({"email": "first@example.test"}, {"$set": {"deleted": True}})
    assert ashby._allowed_users() == set()
    db.users.update_one({"email": "first@example.test"}, {"$unset": {"deleted": ""}})
    db.gateway_config.update_one({"_id": "runtime-settings"}, {"$set": {"ashby_allowed_users": []}})
    assert ashby._allowed_users() == set()


@pytest.mark.asyncio
async def test_deleted_assignee_is_rejected():
    db = AsyncMongoMockClient().test
    await db.users.insert_one({"email": "gone@example.test", "status": "active", "deleted": True})
    from aiohttp import web
    with pytest.raises(web.HTTPForbidden):
        await human_tasks.active_user(db, "gone@example.test")


@pytest.mark.asyncio
async def test_storage_outage_does_not_prevent_worker_startup(monkeypatch):
    from observability import db as module
    db = SimpleNamespace(conversations=SimpleNamespace(update_many=AsyncMock(side_effect=RuntimeError("offline"))))
    monkeypatch.setattr(module, "get_db", lambda: db)
    monkeypatch.setenv("LOMA_ENABLE_SCHEDULER", "true")
    app = {}
    ctx = human_task_worker.lifecycle(app)
    await anext(ctx)
    await asyncio.sleep(0)
    assert app["human_task_worker_running"]
    await ctx.aclose()


@pytest.mark.asyncio
async def test_setup_workers_and_reads_do_not_write_files(monkeypatch):
    db = AsyncMongoMockClient().test
    from observability import db as module
    monkeypatch.setattr(module, "get_db", lambda: db)
    monkeypatch.setenv("LOMA_ENABLE_SCHEDULER", "false")
    original = builtins.open
    def guarded(path, mode="r", *args, **kw):
        assert not any(c in mode for c in "wax+"), f"Unexpected write: {path}"
        return original(path, mode, *args, **kw)
    monkeypatch.setattr(builtins, "open", guarded)
    await settings.read(db)
    ctx = human_task_worker.lifecycle({})
    await anext(ctx)
    await ctx.aclose()


def test_no_environment_writer_in_new_setup_paths():
    root = Path(__file__).parents[1]
    for name in ("api/runtime_settings.py", "api/session_gateway.py",
                 "dashboard/src/lib/gateway-config.ts", "dashboard/src/app/gateway-setup/route.ts"):
        source = (root / name).read_text()
        for token in ("writeFile", "write_text", "write_bytes", "set_key(", "env_routes", "writeFileSync"):
            assert token not in source, (name, token)
