"""Real Mongo registration, real HTTP downloads, no privileged path on wire."""
from datetime import datetime, timedelta, timezone
import json
import os
from unittest.mock import AsyncMock

from aiohttp import web
import pytest
from isolation.artifacts import ArtifactScope
from isolation.downloads import DownloadRegistry, open_download
from isolation.protocol import RunAuthority
from tests.test_bounded_work import db, OWNER, OTHER


async def setup(db, tmp_path, monkeypatch):
    monkeypatch.setenv('LOMA_WORKER_ARTIFACT_DIR', str(tmp_path / 'artifacts'))
    await db.conversations.insert_one({'conversation_id': 'current', 'metadata': {'user_name': OWNER}})
    scope = ArtifactScope(tmp_path / 'artifacts', RunAuthority('run', OWNER, frozenset()), 'current')
    emit = AsyncMock()
    registry = DownloadRegistry(db, scope, emit=emit)
    meta = scope.ingest('report.txt', b'hello world')
    result = await registry(meta)
    scope.close()
    return scope, registry, meta, result, emit


@pytest.mark.asyncio
async def test_durable_registration_and_current_owner_checks(db, tmp_path, monkeypatch):
    scope, registry, meta, result, emit = await setup(db, tmp_path, monkeypatch)
    assert str(tmp_path) not in json.dumps(emit.call_args.args[0])
    assert emit.call_args.args[0]['file_url'] == result['url']
    fd, _ = await open_download(db, OWNER, result['file_id'])
    with os.fdopen(fd, 'rb') as handle:
        assert handle.read() == b'hello world'
    for owner, identifier in [(OTHER, result['file_id']), (OWNER, '../secret'), (OWNER, 'worker-' + 'a' * 32)]:
        with pytest.raises(ValueError):
            await open_download(db, owner, identifier)
    await db.users.update_one({'email': OWNER}, {'$set': {'status': 'inactive'}})
    with pytest.raises(ValueError):
        await open_download(db, OWNER, result['file_id'])


@pytest.mark.asyncio
async def test_expiration_and_conversation_revocation(db, tmp_path, monkeypatch):
    scope, registry, meta, result, emit = await setup(db, tmp_path, monkeypatch)
    await db.conversations.update_one({'conversation_id': 'current'}, {'$set': {'metadata.user_name': OTHER}})
    with pytest.raises(ValueError):
        await open_download(db, OWNER, result['file_id'])
    await db.conversations.update_one({'conversation_id': 'current'}, {'$set': {'metadata.user_name': OWNER}})
    await db.isolated_artifact_downloads.update_one({'_id': meta['artifact_id']}, {'$set': {'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    with pytest.raises(ValueError):
        await open_download(db, OWNER, result['file_id'])


@pytest.mark.asyncio
async def test_http_download_range_and_security_headers(db, tmp_path, monkeypatch, aiohttp_client):
    from api import routes
    from observability import db as db_module
    scope, registry, meta, result, emit = await setup(db, tmp_path, monkeypatch)
    monkeypatch.setattr(db_module, 'get_db', lambda: db)
    monkeypatch.setattr(routes, 'get_user_email', lambda request: OWNER)
    app = web.Application()
    app.router.add_get('/api/files/{file_id}', routes.handle_serve_file)
    client = await aiohttp_client(app)
    response = await client.get(result['url'])
    assert response.status == 200 and await response.read() == b'hello world'
    assert response.headers['Content-Security-Policy'].startswith('sandbox;')
    assert response.headers['Cache-Control'] == 'private, no-store'
    response = await client.get(result['url'], headers={'Range': 'bytes=1-4'})
    assert response.status == 206 and await response.read() == b'ello'
    assert response.headers['Content-Range'] == 'bytes 1-4/11'
    response = await client.get(result['url'], headers={'Range': 'bytes=999-'})
    assert response.status == 416
    monkeypatch.setattr(routes, 'get_user_email', lambda request: OTHER)
    response = await client.get(result['url'])
    assert response.status == 404
