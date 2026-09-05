"""Regression coverage for API authorization and durable artifact delivery."""
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from api import auth_middleware as auth, flow_routes, routes, task_routes, terminal_routes
from scheduler import engine


@pytest.fixture(autouse=True)
def proxy_secret(monkeypatch):
    monkeypatch.setenv('BACKEND_PROXY_SECRET', 'synthetic-proxy-test-key-32-characters')


class Request(dict):
    def __init__(self, body=None, *, user='caller@example.com', role='operator', file_id='invalid'):
        super().__init__(user_email=user, system_role=role)
        self.body = body or {}
        self.path = '/api/chat'
        self.method = 'POST'
        from api.proxy_identity import sign_identity
        timestamp = str(int(time.time()))
        self.headers = {'X-User-Email': user, 'X-Auth-Timestamp': timestamp,
                        'X-Auth-Signature': sign_identity(user, timestamp)}
        self.match_info = {'file_id': file_id, 'conversation_id': 'conv'}
        self.query = {}
        self.app = {}

    async def json(self):
        return self.body


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['pending', 'rejected', 'unknown', None])
async def test_nonactive_accounts_denied(status):
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value={'status': status} if status else None)
    handler = AsyncMock()
    with patch.object(auth, 'get_db', return_value=db):
        response = await auth.auth_middleware(Request(), handler)
    assert response.status == 403
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('user', [{'status': 'active'}, {'email': 'caller@example.com'}])
async def test_active_and_legacy_accounts_allowed(user):
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value=user)
    handler = AsyncMock(return_value=web.Response())
    with patch.object(auth, 'get_db', return_value=db):
        assert (await auth.auth_middleware(Request(), handler)).status == 200


@pytest.mark.asyncio
async def test_unprovisioned_user_can_get_profile_only():
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value=None)
    req = Request()
    req.path = '/api/governance/me'
    req.method = 'GET'
    handler = AsyncMock(return_value=web.Response())
    with patch.object(auth, 'get_db', return_value=db):
        assert (await auth.auth_middleware(req, handler)).status == 200
        req.method = 'POST'
        assert (await auth.auth_middleware(req, handler)).status == 403


@pytest.mark.asyncio
async def test_db_unavailable_fails_closed():
    handler = AsyncMock()
    with patch.object(auth, 'get_db', return_value=None):
        assert (await auth.auth_middleware(Request(), handler)).status == 503
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['chatter', 'analyst', 'operator'])
async def test_terminal_requires_privileged_role(role):
    req = Request(role=role)
    with pytest.raises(web.HTTPForbidden):
        await terminal_routes.handle_terminal_token(req)
    with pytest.raises(web.HTTPForbidden):
        await terminal_routes.handle_terminal_ws(req)


@pytest.mark.asyncio
async def test_terminal_tokens_are_bound_and_single_use():
    owner = Request(role='maintainer')
    response = await terminal_routes.handle_terminal_token(owner)
    token = json.loads(response.body)['token']
    other = Request(user='other@example.com', role='maintainer')
    other.query = {'token': token}
    assert (await terminal_routes.handle_terminal_ws(other)).status == 403
    owner.query = {'token': token}
    assert (await terminal_routes.handle_terminal_ws(owner)).status == 403


@pytest.mark.asyncio
async def test_terminal_owner_reaches_websocket_without_spawning_shell():
    owner = Request(role='maintainer')
    response = await terminal_routes.handle_terminal_token(owner)
    owner.query = {'token': json.loads(response.body)['token']}
    class StopBeforeShell(Exception):
        pass
    with patch.object(web.WebSocketResponse, 'prepare', AsyncMock(side_effect=StopBeforeShell)):
        with pytest.raises(StopBeforeShell):
            await terminal_routes.handle_terminal_ws(owner)
    assert (await terminal_routes.handle_terminal_ws(owner)).status == 403


@pytest.fixture
def artifact_dir(tmp_path, monkeypatch):
    served = tmp_path / 'served'
    served.mkdir()
    monkeypatch.setattr(routes, 'SERVED_FILES_DIR', served)
    monkeypatch.setattr(routes, '_served_files', {})
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize('extension', ['txt', 'json', 'pdf', 'html'])
async def test_file_survives_registry_restart_and_is_private(artifact_dir, extension):
    source = artifact_dir / f'report.{extension}'
    source.write_text('synthetic content')
    info = routes.register_served_file(str(source), owner_email='caller@example.com')
    routes._served_files.clear()
    req = Request(file_id=info['file_id'])
    response = await routes.handle_serve_file(req)
    assert response.status == 200
    assert response._path.read_text() == 'synthetic content'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert 'sandbox' in response.headers['Content-Security-Policy']
    req['user_email'] = 'other@example.com'
    assert (await routes.handle_serve_file(req)).status == 404


@pytest.mark.asyncio
async def test_unregistered_and_ownerless_files_not_served(artifact_dir):
    source = artifact_dir / 'report.txt'
    source.write_text('synthetic')
    info = routes.register_served_file(str(source))
    assert (await routes.handle_serve_file(Request(file_id=info['file_id']))).status == 404
    assert (await routes.handle_serve_file(Request(file_id='../report.txt'))).status == 404


@pytest.mark.asyncio
async def test_file_sharing_tracks_conversation_permissions(artifact_dir):
    source = artifact_dir / 'report.txt'
    source.write_text('synthetic')
    info = routes.register_served_file(str(source), conversation_id='conv')
    db = MagicMock()
    conversation = {'metadata': {'user_name': 'other@example.com', 'visibility': 'shared'}}
    db.conversations.find_one = AsyncMock(return_value=conversation)
    with patch.object(routes, 'get_db', return_value=db):
        req = Request(file_id=info['file_id'])
        assert (await routes.handle_serve_file(req)).status == 200
        conversation['metadata']['visibility'] = 'private'
        assert (await routes.handle_serve_file(req)).status == 404
        db.conversations.find_one.return_value = None
        assert (await routes.handle_serve_file(req)).status == 404


@pytest.mark.asyncio
async def test_symlink_replacement_denied(artifact_dir):
    source = artifact_dir / 'report.txt'
    source.write_text('synthetic')
    info = routes.register_served_file(str(source), owner_email='caller@example.com')
    dest = routes.SERVED_FILES_DIR / (info['file_id'] + '.txt')
    dest.unlink()
    dest.symlink_to(source)
    assert (await routes.handle_serve_file(Request(file_id=info['file_id']))).status == 404


@pytest.mark.asyncio
async def test_registered_handler_is_selected():
    app = web.Application()
    routes.setup_api_routes(app)
    resolved = await app.router.resolve(make_mocked_request('GET', '/api/files/example', app=app))
    assert resolved.handler is routes.handle_serve_file


@pytest.mark.asyncio
async def test_flow_creator_is_authenticated_and_default_run_as_validated():
    db = MagicMock()
    db.flows.insert_one = AsyncMock()
    db.users.find_one = AsyncMock(return_value={'status': 'active'})
    body = {'name': 'test', 'prompt': 'test', 'schedule_type': 'once', 'status': 'paused',
            'created_by': {'source': 'other@example.com'}}
    with patch.object(flow_routes, 'get_db', return_value=db):
        assert (await flow_routes.handle_create_flow(Request(body))).status == 201
    doc = db.flows.insert_one.await_args.args[0]
    assert doc['run_as'] == 'caller@example.com'
    assert doc['created_by']['source'] == 'caller@example.com'
    db.users.find_one.assert_awaited_once_with({'email': 'caller@example.com'})


@pytest.mark.asyncio
async def test_flow_cannot_run_as_other_user():
    body = {'name': 'test', 'prompt': 'test', 'schedule_type': 'once', 'run_as': 'other@example.com'}
    with patch.object(flow_routes, 'get_db', return_value=MagicMock()):
        assert (await flow_routes.handle_create_flow(Request(body))).status == 403


@pytest.mark.asyncio
async def test_injection_uses_configured_db_and_does_not_retry_persistence_failure():
    stream = SimpleNamespace(client=SimpleNamespace(query=AsyncMock()))
    observer = SimpleNamespace(record_injected_message=AsyncMock(side_effect=RuntimeError('test')))
    with patch('agent.active_streams.get_for_user', AsyncMock(return_value=stream)), \
         patch.object(routes, 'get_db', return_value=MagicMock()), \
         patch.object(routes, 'ConversationObserver', return_value=observer):
        assert (await routes.handle_inject_message(Request({'message': 'test'}))).status == 200
    stream.client.query.assert_awaited_once_with('test')


@pytest.mark.asyncio
async def test_injection_checks_db_before_delivery():
    stream = SimpleNamespace(client=SimpleNamespace(query=AsyncMock()))
    with patch('agent.active_streams.get_for_user', AsyncMock(return_value=stream)), \
         patch.object(routes, 'get_db', return_value=None):
        assert (await routes.handle_inject_message(Request({'message': 'test'}))).status == 503
    stream.client.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_reader_cannot_resume_original():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value={
        'metadata': {'user_name': 'other@example.com', 'visibility': 'shared'}, 'source': 'dashboard',
    })
    with patch.object(routes, 'get_db', return_value=db), patch.object(routes, 'is_draining', return_value=False):
        response = await routes.handle_chat(Request({'message': 'test', 'conversation_id': 'conv'}))
    assert response.status == 404
    db.conversations.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_shared_task_fork_is_private():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value={
        'metadata': {'user_name': 'other@example.com', 'visibility': 'shared'}, 'messages': [],
    })
    db.conversations.insert_one = AsyncMock()
    db.users.find_one = AsyncMock(return_value={})
    with patch.object(task_routes, 'get_db', return_value=db):
        assert (await task_routes.handle_fork_task(Request())).status == 201
    doc = db.conversations.insert_one.await_args.args[0]
    assert doc['metadata']['visibility'] == 'private'
    assert not routes._check_conversation_access(doc, 'unrelated@example.com', 'chatter')


def test_recurring_schedule_honors_start_and_end():
    scheduler = MagicMock()
    start = datetime(2030, 1, 1, tzinfo=timezone.utc)
    end = datetime(2030, 1, 5, tzinfo=timezone.utc)
    with patch.object(engine, '_scheduler', scheduler):
        engine._add_job_for_flow({'flow_id': 'test', 'schedule_type': 'recurring',
                                 'cron': '0 9 * * *', 'timezone': 'UTC', 'start_time': start, 'end_time': end})
    trigger = scheduler.add_job.call_args.kwargs['trigger']
    assert trigger.get_next_fire_time(None, datetime(2026, 1, 1, tzinfo=timezone.utc)) >= start
    assert trigger.get_next_fire_time(None, datetime(2031, 1, 1, tzinfo=timezone.utc)) is None


def test_recurring_schedule_normalizes_naive_boundaries():
    scheduler = MagicMock()
    with patch.object(engine, '_scheduler', scheduler):
        engine._add_job_for_flow({'flow_id': 'test', 'schedule_type': 'recurring',
                                 'cron': '0 9 * * *', 'timezone': 'UTC',
                                 'start_time': datetime(2030, 1, 1)})
    trigger = scheduler.add_job.call_args.kwargs['trigger']
    assert trigger.get_next_fire_time(None, datetime(2026, 1, 1, tzinfo=timezone.utc)).year == 2030
