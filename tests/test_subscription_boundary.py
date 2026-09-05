"""Synthetic protocol/credential boundary checks, without live provider calls."""
import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from broker import controller
from broker.gateway import (SubscriptionProxyRegistry, McpProxyRegistry, create_gateway_app,
                            _claude_subscription_auth, _codex_subscription_auth)
from broker.operations import ToolInvoke
from broker.service import Denied
from broker.tool_policy import prepare_argv
from utils.secret_redaction import redact_secrets


@pytest.fixture
def account(tmp_path):
    path = tmp_path / 'account'
    path.mkdir()
    (path / '.credentials.json').write_text(json.dumps({'claudeAiOauth': {
        'accessToken': 'synthetic-provider-value', 'expiresAt': (time.time() + 60) * 1000}}))
    (path / 'private-history.json').write_text('private history')
    (path / 'auth.json').write_text(json.dumps({'tokens': {
        'access_token': 'synthetic-codex-value', 'account_id': 'synthetic-account'}}))
    return {'config_dir': str(path)}


def test_subscription_registration_requires_run(account):
    registry = SubscriptionProxyRegistry()
    with pytest.raises(Denied):
        registry.register('claude', account['config_dir'], capability='')
    assert not hasattr(registry, 'register_anonymous')


def test_subscription_registry_revocation_and_expiry(account):
    registry = SubscriptionProxyRegistry()
    token = registry.register('claude', account['config_dir'], capability='sample')
    assert registry.lookup(token)['capability'] == 'sample'
    registry.revoke(token)
    with pytest.raises(Denied):
        registry.lookup(token)
    token = registry.register('claude', account['config_dir'], capability='sample', ttl_seconds=0)
    with pytest.raises(Denied):
        registry.lookup(token)


@pytest.mark.parametrize('provider', ['claude', 'codex'])
@pytest.mark.asyncio
async def test_subscription_proxy_protocol_and_revocation(account, monkeypatch, provider):
    from broker import gateway
    seen = []
    async def upstream(request):
        seen.append((request.path, dict(request.headers), await request.read()))
        return web.json_response({'ok': True})
    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', upstream)
    registry = SubscriptionProxyRegistry()
    filename = '.credentials.json' if provider == 'claude' else 'auth.json'
    token = registry.register(provider, Path(account['config_dir']) / filename, capability='sample')
    broker = SimpleNamespace(execute=AsyncMock(return_value={}))
    async with TestServer(app) as server:
        monkeypatch.setitem(gateway.SUBSCRIPTION_UPSTREAMS, provider, str(server.make_url('')).rstrip('/'))
        async with TestClient(TestServer(create_gateway_app(broker, McpProxyRegistry(), registry))) as client:
            route = 'v1/messages' if provider == 'claude' else 'responses'
            response = await client.post(f'/sub/{token}/{route}', json={'model': 'synthetic'},
                                         headers={'Authorization': 'Bearer forged',
                                                  'X-API-Key': 'forged', 'ChatGPT-Account-Id': 'forged'})
            assert response.status == 200
            assert (await response.json()) == {'ok': True}
            broker.execute.assert_awaited_with('sample', 'subscription.request', provider)
            assert 'forged' not in json.dumps(seen, default=str)
            assert seen[0][1]['Authorization'].startswith('Bearer synthetic-')
            assert 'Set-Cookie' not in response.headers
            for method, tail in [('post', 'admin'), ('get', route), ('post', route + '?redirect=1')]:
                response = await getattr(client, method)(f'/sub/{token}/{tail}')
                assert response.status == 403
            assert len(seen) == 1
            registry.revoke(token)
            response = await client.post(f'/sub/{token}/{route}')
            assert response.status == 403
            assert len(seen) == 1


@pytest.mark.parametrize('error,status', [(Denied(), 403), (RuntimeError('synthetic'), 503)])
@pytest.mark.asyncio
async def test_subscription_authz_error_precedes_credentials(account, monkeypatch, error, status):
    from broker import gateway
    read = AsyncMock(side_effect=AssertionError('must not read'))
    monkeypatch.setattr(gateway, '_claude_subscription_auth', read)
    registry = SubscriptionProxyRegistry()
    token = registry.register('claude', account['config_dir'], capability='sample')
    broker = SimpleNamespace(execute=AsyncMock(side_effect=error))
    async with TestClient(TestServer(create_gateway_app(broker, McpProxyRegistry(), registry))) as client:
        response = await client.post(f'/sub/{token}/v1/messages')
        assert response.status == status
        read.assert_not_called()


@pytest.mark.parametrize('data', [[], {}, {'claudeAiOauth': []}, {'claudeAiOauth': {'accessToken': 123}},
    {'claudeAiOauth': {'accessToken': 'synthetic', 'expiresAt': 1}}])
def test_malformed_or_expired_claude_auth_is_rejected(tmp_path, data):
    path = tmp_path / 'synthetic.json'
    path.write_text(json.dumps(data))
    with pytest.raises(Denied):
        _claude_subscription_auth(str(path))


def test_codex_api_key_cannot_silently_replace_subscription(tmp_path):
    path = tmp_path / 'synthetic.json'
    path.write_text(json.dumps({'OPENAI_API_KEY': 'synthetic'}))
    with pytest.raises(Denied):
        _codex_subscription_auth(str(path))


def test_claude_config_never_copies_account_files(account, tmp_path, monkeypatch):
    from agent.pool import _prepare_worker_subscription_env
    from broker.controller import RunContext
    workspace = tmp_path / 'worker'
    workspace.mkdir()
    registry = SubscriptionProxyRegistry()
    monkeypatch.setattr(controller, '_sub_registry', registry)
    ctx = RunContext(run_id='sample', capability='sample', workspace=workspace, user_email='owner@example.test')
    handle = controller.current_run.set(ctx)
    try:
        env, tokens = _prepare_worker_subscription_env(account, workspace)
        assert list(Path(env['CLAUDE_CONFIG_DIR']).iterdir()) == []
        assert 'synthetic-provider-value' not in json.dumps(env)
        assert tokens == ctx.sub_proxy_tokens
        assert registry.lookup(tokens[0])['capability'] == ctx.capability
    finally:
        controller.current_run.reset(handle)


def test_claude_anonymous_or_unavailable_run_cannot_fallback(account, tmp_path, monkeypatch):
    from agent.pool import _prepare_worker_subscription_env
    handle = controller.current_run.set(None)
    monkeypatch.setenv('LOMA_SUBSCRIPTION_PROXY', '0')
    try:
        with pytest.raises(controller.ExecutionUnavailable):
            _prepare_worker_subscription_env(account, tmp_path)
    finally:
        controller.current_run.reset(handle)


def test_subscription_proxy_reference_redacted():
    assert 'loma_subproxy_' not in redact_secrets('url /sub/loma_subproxy_synthetic_value/messages')


def test_binary_input_is_preserved():
    raw = bytes(range(256))
    argv, files = ToolInvoke._validate_params({'argv': [], 'files': {'image.png': {
        'encoding': 'base64', 'data': base64.b64encode(raw).decode()}}})
    assert files['image.png'] == raw


@pytest.mark.parametrize('value', [{'encoding': 'base64', 'data': '???'},
    {'encoding': 'base64', 'data': []}, {'encoding': 'file', 'data': '/etc/passwd'},
    {'encoding': 'base64', 'data': '', 'path': '/etc/passwd'}])
def test_binary_input_rejects_malformed_envelope(value):
    with pytest.raises(Denied):
        ToolInvoke._validate_params({'argv': [], 'files': {'x': value}})


def test_posthog_accepts_real_commands_and_repeated_filters():
    args = ['events', 'test', '--filter', 'x=1', '--filter', 'y=2']
    assert prepare_argv('posthog', args, {}) == args
    with pytest.raises(Denied):
        prepare_argv('posthog', ['query', '--sql', 'select 1'], {})


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['cmd_download', 'cmd_upload'])
async def test_agreement_authentication_precedes_personal_token_lookup(command, monkeypatch):
    from tools import agreement_review
    monkeypatch.setattr('tools._auth_token.verify_user_auth_token', lambda *a: False)
    result = await getattr(agreement_review, command)(SimpleNamespace(
        user_email='other@example.test', auth_token='synthetic-invalid'))
    assert result == {'error': 'Authentication required'}

@pytest.mark.asyncio
async def test_codex_worker_gets_no_account_auth(account, tmp_path, monkeypatch):
    from agent.codex_runtime import CodexWorker
    from broker import worker
    monkeypatch.setenv('LOMA_WORKER_ROOT', str(tmp_path / 'workers'))
    monkeypatch.delenv('LOMA_WORKER_UID_RANGE', raising=False)
    monkeypatch.delenv('LOMA_WORKER_UID', raising=False)
    monkeypatch.setattr(controller, '_sub_registry', SubscriptionProxyRegistry())
    monkeypatch.setattr(controller, 'proxy_mcp_servers_for_worker', lambda x: ({}, [], []))
    ctx = controller.RunContext(run_id='test', capability='sample', workspace=tmp_path,
                                user_email='owner@example.test')
    class StopBeforeVendorCall(Exception):
        pass
    async def spawn(*args, **kwargs):
        home = Path(kwargs['env']['CODEX_HOME'])
        assert home != Path(account['config_dir'])
        assert not (home / 'auth.json').exists()
        import tomllib
        config = tomllib.loads((home / 'config.toml').read_text())
        assert config['model_provider'] == 'loma_subscription'
        assert config['model_providers']['loma_subscription']['requires_openai_auth'] is False
        assert kwargs['env']['LOMA_RUN_CAPABILITY'] == 'sample'
        assert 'synthetic-codex-value' not in json.dumps(kwargs['env'])
        raise StopBeforeVendorCall()
    monkeypatch.setattr(worker, 'spawn_worker', spawn)
    handle = controller.current_run.set(ctx)
    try:
        instance = CodexWorker(account=account, model='synthetic')
        with pytest.raises(StopBeforeVendorCall):
            await instance.connect()
    finally:
        controller.current_run.reset(handle)
        for path in (tmp_path / 'workers').glob('codex-*'):
            worker.cleanup_workspace(path)


def test_opencode_no_longer_copies_provider_credentials(tmp_path, monkeypatch):
    from agent.opencode_runtime import _prepare_opencode_data_home
    source = tmp_path / 'backend-data' / 'opencode'
    source.mkdir(parents=True)
    (source / 'auth.json').write_text('synthetic-backend-credential')
    monkeypatch.setenv('XDG_DATA_HOME', str(source.parent))
    home = _prepare_opencode_data_home(tmp_path / 'worker-config')
    assert not (home / 'opencode' / 'auth.json').exists()
    assert (source / 'auth.json').read_text() == 'synthetic-backend-credential'

@pytest.mark.parametrize('tool,args', [
    ('ashby', ['list-applications', '--job-id', 'synthetic']),
    ('sentry', ['daily', '1', '--days', '7']),
    ('pylon', ['create-thread', 'synthetic', 'Example']),
    ('grafana', ['query', 'lag', 'synthetic', '--range', '30']),
    ('grafana', ['oncall', 'current']),
    ('telegram', ['status']),
    ('monetize_now', ['get-account', 'synthetic']),
])
def test_reviewed_adapter_command_groups(tool, args):
    assert prepare_argv(tool, args, {}) == args


def test_undeclared_command_group_cannot_consume_arbitrary_token():
    with pytest.raises(Denied):
        prepare_argv('grafana', ['query', 'unknown'], {})


def test_opencode_managed_server_credentials_are_distinct(monkeypatch):
    from agent import opencode_runtime as oc
    monkeypatch.setattr(oc, '_managed_server_passwords', {
        'http://127.0.0.1:19001': 'synthetic-one',
        'http://127.0.0.1:19002': 'synthetic-two',
    })
    assert oc._auth('http://127.0.0.1:19001').password == 'synthetic-one'
    assert oc._auth('http://127.0.0.1:19002').password == 'synthetic-two'
    assert oc._auth('http://127.0.0.1:19003') is None


@pytest.mark.asyncio
async def test_opencode_teardown_revokes_managed_password(monkeypatch, tmp_path):
    from agent import opencode_runtime as oc
    monkeypatch.setattr(oc, '_managed_server_passwords', {'http://127.0.0.1:19001': 'synthetic'})
    server = oc._OpenCodeServer(config_hash='synthetic', config_home=tmp_path,
        host='127.0.0.1', port=19001, process=None)
    await server.terminate()
    assert oc._managed_server_passwords == {}

@pytest.mark.asyncio
@pytest.mark.parametrize('method,path', [('get', 'v1/files'), ('post', 'v1/fine_tuning/jobs'),
    ('delete', 'v1/models/synthetic'), ('post', 'v1/responses?redirect=1')])
async def test_model_grant_is_not_general_provider_account_access(monkeypatch, method, path):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic')
    broker = SimpleNamespace(execute=AsyncMock(return_value={}))
    app = create_gateway_app(broker, McpProxyRegistry())
    async with TestClient(TestServer(app)) as client:
        response = await getattr(client, method)('/model/openai/' + path,
            headers={'Authorization': 'Bearer sample'})
        assert response.status == 403
        assert (await response.json()) == {'error': 'Unsupported model operation'}


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 429, 500])
async def test_gateway_recovery_only_retries_auth_rejections(account, monkeypatch, status):
    from broker import gateway, subscription_refresh
    path = Path(account['config_dir']) / 'auth.json'
    data = json.loads(path.read_text())
    data['tokens']['refresh_token'] = 'synthetic-refresh'
    path.write_text(json.dumps(data))
    registry = SubscriptionProxyRegistry()
    token = registry.register('codex', path, capability='sample')
    broker = SimpleNamespace(execute=AsyncMock(return_value={}))
    calls = []
    async def upstream(request):
        calls.append((request.headers['Authorization'], await request.read()))
        return web.json_response({'ok': True}, status=status if len(calls) == 1 else 200)
    exchange = AsyncMock(return_value={'access_token': 'synthetic-renewed'})
    monkeypatch.setattr(subscription_refresh, '_exchange', exchange)
    app = web.Application()
    app.router.add_post('/responses', upstream)
    async with TestServer(app) as server:
        monkeypatch.setitem(gateway.SUBSCRIPTION_UPSTREAMS, 'codex', str(server.make_url('')).rstrip('/'))
        async with TestClient(TestServer(create_gateway_app(broker, McpProxyRegistry(), registry))) as client:
            response = await client.post(f'/sub/{token}/responses', data=b'synthetic-request')
            assert response.status == (200 if status == 401 else status)
            await response.read()
    assert len(calls) == (2 if status == 401 else 1)
    if status == 401:
        exchange.assert_awaited_once()
        assert calls[1] == ('Bearer synthetic-renewed', b'synthetic-request')
    else:
        exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_never_replays_after_revocation_during_recovery(account, monkeypatch):
    from broker import gateway, subscription_refresh
    path = Path(account['config_dir']) / 'auth.json'
    data = json.loads(path.read_text())
    data['tokens']['refresh_token'] = 'synthetic-refresh'
    path.write_text(json.dumps(data))
    registry = SubscriptionProxyRegistry()
    token = registry.register('codex', path, capability='sample')
    broker = SimpleNamespace(execute=AsyncMock(return_value={}))
    calls = []
    async def upstream(request):
        calls.append(True)
        return web.Response(status=401)
    async def exchange(*args):
        registry.revoke(token)
        return {'access_token': 'synthetic-renewed'}
    monkeypatch.setattr(subscription_refresh, '_exchange', exchange)
    app = web.Application()
    app.router.add_post('/responses', upstream)
    async with TestServer(app) as server:
        monkeypatch.setitem(gateway.SUBSCRIPTION_UPSTREAMS, 'codex', str(server.make_url('')).rstrip('/'))
        async with TestClient(TestServer(create_gateway_app(broker, McpProxyRegistry(), registry))) as client:
            response = await client.post(f'/sub/{token}/responses')
            assert response.status == 502
            assert 'synthetic-renewed' not in await response.text()
    assert calls == [True]
