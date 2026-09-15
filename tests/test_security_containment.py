"""Containment regression tests; real HTTP/WS, synthetic files, no external services."""
import base64
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from api import routes
from api.file_routes import setup_file_routes
from api.terminal_routes import setup_terminal_routes

OWNER = 'alice@example.test'
OTHER = 'bob@example.test'


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    served = tmp_path / 'served'
    served.mkdir()
    monkeypatch.setattr(routes, 'SERVED_FILES_DIR', served)
    monkeypatch.setattr(routes, '_served_files', {})

    @web.middleware
    async def synthetic_identity(request, handler):
        # Test-only identity injection. Production auth is tested separately.
        request['user_email'] = request.headers.get('Test-User', '')
        request['system_role'] = request.headers.get('Test-Role', 'chatter')
        return await handler(request)

    app = web.Application(middlewares=[synthetic_identity])
    setup_file_routes(app)
    setup_file_routes(app)
    setup_terminal_routes(app)
    async with TestClient(TestServer(app)) as http:
        yield http


def register(tmp_path, content=b'%PDF-1.4 synthetic document', owner=OWNER):
    source = tmp_path / 'document.pdf'
    source.write_bytes(content)
    return routes.register_served_file(str(source), owner_email=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['chatter', 'analyst', 'operator', 'maintainer', 'admin'])
async def test_host_terminal_denied_for_every_role(client, role):
    with patch('pty.fork', side_effect=AssertionError('shell must never start')):
        response = await client.post('/api/terminal/token', headers={'Test-User': OWNER, 'Test-Role': role})
        assert response.status == 403
        assert 'token' not in await response.json()
        for token in ['', 'previously-issued-token']:
            with pytest.raises(WSServerHandshakeError) as error:
                await client.ws_connect('/api/terminal/ws?token=' + token, headers={'Test-User': OWNER, 'Test-Role': role})
            assert error.value.status == 403


@pytest.mark.asyncio
async def test_anonymous_terminal_denied(client):
    assert (await client.post('/api/terminal/token')).status == 403
    assert (await client.get('/api/terminal/ws?token=old-token')).status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['GET', 'HEAD'])
@pytest.mark.parametrize('user,role,status', [('', 'chatter', 401), (OTHER, 'chatter', 404), (OTHER, 'admin', 404), (OWNER, 'chatter', 200)])
async def test_file_owner_matrix(client, tmp_path, method, user, role, status):
    artifact = register(tmp_path)
    response = await client.request(method, artifact['url'], headers={'Test-User': user, 'Test-Role': role})
    assert response.status == status
    if status == 200:
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Access-Control-Allow-Origin' not in response.headers
        assert response.headers['Content-Type'] == 'application/pdf'
        assert response.headers['Content-Disposition'].startswith('inline;')
        assert await response.read() == (b'' if method == 'HEAD' else b'%PDF-1.4 synthetic document')


@pytest.mark.asyncio
@pytest.mark.parametrize('headers,expected', [({'Range': 'bytes=2-5'}, b'2345'), ({'Range': 'bytes=-3'}, b'789'), ({'Range': 'bytes=7-'}, b'789'), ({'Range': 'bytes=0-99'}, b'0123456789')])
async def test_pdf_ranges(client, tmp_path, headers, expected):
    artifact = register(tmp_path, b'0123456789')
    response = await client.get(artifact['url'], headers={'Test-User': OWNER, **headers})
    assert response.status == 206
    assert await response.read() == expected
    assert response.headers['Content-Range'].endswith('/10')


@pytest.mark.asyncio
@pytest.mark.parametrize('range_value', ['bytes=99-', 'bytes=5-2', 'bytes=0-1,4-5', 'garbage'])
async def test_invalid_ranges(client, tmp_path, range_value):
    artifact = register(tmp_path, b'0123456789')
    response = await client.get(artifact['url'], headers={'Test-User': OWNER, 'Range': range_value})
    assert response.status == 416


@pytest.mark.asyncio
async def test_another_user_cannot_range_or_conditional_fetch(client, tmp_path):
    artifact = register(tmp_path)
    for headers in [{'Range': 'bytes=0-4'}, {'If-None-Match': '*'}, {'If-Modified-Since': 'Wed, 21 Oct 2099 07:28:00 GMT'}]:
        response = await client.get(artifact['url'], headers={'Test-User': OTHER, **headers})
        assert response.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['base64', 'padded-base64', 'traversal', 'unknown', 'unowned'])
async def test_legacy_unknown_and_unowned_files_denied(client, tmp_path, kind):
    artifact = register(tmp_path)
    file_id = artifact['file_id']
    if kind in ('base64', 'padded-base64', 'traversal'):
        path = str(tmp_path / 'document.pdf')
        if kind == 'traversal':
            path = str(tmp_path / 'served' / '..' / 'document.pdf')
        file_id = base64.urlsafe_b64encode(path.encode()).decode()
        if kind != 'padded-base64':
            file_id = file_id.rstrip('=')
    elif kind == 'unknown':
        file_id = '0' * 32
    else:
        del routes._served_files[file_id]['owner_email']
    with patch.object(routes, 'register_served_file', side_effect=AssertionError('no fallback')):
        response = await client.get('/api/files/' + file_id, headers={'Test-User': OWNER})
    assert response.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['missing', 'symlink', 'outside', 'fifo', 'directory', 'root-symlink'])
async def test_storage_escapes_and_nonfiles_denied(client, tmp_path, kind):
    artifact = register(tmp_path)
    entry = routes._served_files[artifact['file_id']]
    path = Path(entry['path'])
    path.unlink()
    if kind == 'symlink':
        path.symlink_to(tmp_path / 'document.pdf')
    elif kind == 'outside':
        entry['path'] = str(tmp_path / 'document.pdf')
    elif kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'directory':
        path.mkdir()
    elif kind == 'root-symlink':
        root = routes.SERVED_FILES_DIR
        root.rename(tmp_path / 'moved')
        root.symlink_to(tmp_path / 'moved', target_is_directory=True)
    response = await client.get(artifact['url'], headers={'Test-User': OWNER})
    assert response.status in (404, 410)


@pytest.mark.asyncio
async def test_registry_restart_fails_closed(client, tmp_path):
    artifact = register(tmp_path)
    routes._served_files.clear()
    assert (await client.get(artifact['url'], headers={'Test-User': OWNER})).status == 404


@pytest.mark.parametrize('owner', ['', None, '   '])
def test_registration_requires_owner(tmp_path, owner):
    with pytest.raises(ValueError, match='owner'):
        register(tmp_path, owner=owner)


@pytest.mark.asyncio
async def test_opencode_delivery_binds_authenticated_owner(client, tmp_path):
    from agent.opencode_runtime import _emit_text
    source = tmp_path / 'result.pdf'
    source.write_bytes(b'synthetic')
    events = [event async for event in _emit_text(
        f'Saved to {source}', turn_count=1, observer=None, include_steps=True,
        source='dashboard', emitted_artifact_ids=set(), emitted_file_paths=set(), user_email=OWNER,
    )]
    artifact = next(e for e in events if isinstance(e, dict) and e.get('type') == 'file')
    assert (await client.get(artifact['url'], headers={'Test-User': OWNER})).status == 200
    assert (await client.get(artifact['url'], headers={'Test-User': OTHER})).status == 404


@pytest.mark.asyncio
async def test_claude_file_artifact_and_registration_failure(client, tmp_path):
    from agent.client import _detect_file_artifact
    source = tmp_path / 'result.pdf'
    source.write_bytes(b'synthetic')
    artifact = _detect_file_artifact(str(source), owner_email=OWNER)
    assert (await client.get(artifact['file_url'], headers={'Test-User': OWNER})).status == 200
    assert (await client.get(artifact['file_url'], headers={'Test-User': OTHER})).status == 404
    assert _detect_file_artifact(str(source)) is None
    with patch.object(routes, 'register_served_file', side_effect=OSError('synthetic')):
        assert _detect_file_artifact(str(source), owner_email=OWNER) is None


@pytest.mark.asyncio
async def test_active_content_sandboxed(client, tmp_path):
    source = tmp_path / 'report.html'
    source.write_text('<script>throw new Error("must not execute")</script>')
    artifact = routes.register_served_file(str(source), owner_email=OWNER)
    response = await client.get(artifact['url'], headers={'Test-User': OWNER})
    assert response.status == 200
    assert response.headers['Content-Security-Policy'].startswith('sandbox;')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
