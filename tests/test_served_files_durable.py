"""Agent-produced files (PDF previews, downloads) keep working after a restart.

The in-memory registry is lost on every restart and deploy; the durable
`served_files` record must bring the link back for its owner only.
"""
import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from api import routes
from api.file_routes import setup_file_routes

OWNER = "alice@example.test"
OTHER = "bob@example.test"
PDF = b"%PDF-1.4 synthetic document"


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    from mongomock_motor import AsyncMongoMockClient

    db = AsyncMongoMockClient()[f"served_{uuid.uuid4().hex}"]
    served = tmp_path / "served"
    served.mkdir()
    monkeypatch.setattr(routes, "SERVED_FILES_DIR", served)
    monkeypatch.setattr(routes, "_served_files", {})

    @web.middleware
    async def identity(request, handler):
        request["user_email"] = request.headers.get("Test-User", "")
        request["system_role"] = "chatter"
        return await handler(request)

    app = web.Application(middlewares=[identity])
    setup_file_routes(app)
    with patch("api.routes.get_db", return_value=db):
        async with TestClient(TestServer(app)) as http:
            yield http, db, served, tmp_path


async def _register(tmp_path, owner=OWNER):
    source = tmp_path / "report.pdf"
    source.write_bytes(PDF)
    info = routes.register_served_file(str(source), owner_email=owner)
    await asyncio.gather(*routes._pending_persists)
    return info


def _restart():
    routes._served_files.clear()


@pytest.mark.asyncio
async def test_pdf_still_previews_after_a_restart(env):
    http, _, _, tmp_path = env
    info = await _register(tmp_path)
    _restart()
    response = await http.get(info["url"], headers={"Test-User": OWNER})
    assert response.status == 200
    assert response.headers["Content-Type"] == "application/pdf"
    assert response.headers["Content-Disposition"].startswith("inline;")
    assert await response.read() == PDF


@pytest.mark.asyncio
async def test_after_a_restart_other_users_still_get_404(env):
    http, _, _, tmp_path = env
    info = await _register(tmp_path)
    _restart()
    assert (await http.get(info["url"], headers={"Test-User": OTHER})).status == 404


@pytest.mark.asyncio
async def test_expired_registration_is_not_served(env):
    http, db, _, tmp_path = env
    info = await _register(tmp_path)
    await db.served_files.update_one({"_id": info["file_id"]},
                                     {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    _restart()
    assert (await http.get(info["url"], headers={"Test-User": OWNER})).status == 404


@pytest.mark.asyncio
async def test_a_stored_name_outside_the_served_dir_is_rejected(env):
    http, db, _, tmp_path = env
    info = await _register(tmp_path)
    await db.served_files.update_one({"_id": info["file_id"]}, {"$set": {"file_name": "../secret.pdf"}})
    _restart()
    assert (await http.get(info["url"], headers={"Test-User": OWNER})).status == 404


@pytest.mark.asyncio
async def test_registration_without_a_running_loop_still_works_in_process(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "SERVED_FILES_DIR", tmp_path)
    monkeypatch.setattr(routes, "_served_files", {})
    source = tmp_path / "a.pdf"
    source.write_bytes(PDF)
    info = await asyncio.to_thread(routes.register_served_file, str(source), owner_email=OWNER)
    assert info["file_id"] in routes._served_files


@pytest.mark.asyncio
async def test_startup_removes_only_expired_served_files(env):
    _, db, served, _ = env
    old, fresh, other = served / f"{'a' * 32}.pdf", served / f"{'b' * 32}.pdf", served / "notes.txt"
    for path in (old, fresh, other):
        path.write_bytes(PDF)
    month_ago = time.time() - 31 * 86400
    os.utime(old, (month_ago, month_ago))
    os.utime(other, (month_ago, month_ago))
    await routes.ensure_served_files_store()
    assert not old.exists() and fresh.exists() and other.exists()
    assert "expires_at_1" in await db.served_files.index_information()
