"""OAuth exchanges are synthetic; no live credentials or provider calls."""
import asyncio
import base64
import json
import os
import stat
import time
from unittest.mock import AsyncMock

import pytest

from isolation import oauth as mod
from isolation.accounts import SubscriptionAccounts
from isolation.models import ModelDenied
from tests.test_worker_accounts import account
from tests.test_worker_run import AUTH
from tests.test_bounded_work import db


def jwt(**claims):
    return 'h.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.s'


def setup(tmp_path, runtime, fresh=False):
    a = account(tmp_path, runtime)
    name = '.credentials.json' if runtime == 'claude' else 'auth.json'
    path = a.directory / name
    doc = json.loads(path.read_text())
    expiry = time.time() + (3600 if fresh else -60)
    if runtime == 'claude':
        doc['claudeAiOauth'].update(refreshToken='test-refresh', expiresAt=expiry*1000,
            scopes=['user:profile', 'user:inference'], subscriptionType='max')
    else:
        doc['tokens'].update(refresh_token='test-refresh', access_token=jwt(exp=expiry))
    path.write_text(json.dumps(doc))
    return a, path, doc


def response(runtime):
    return {'access_token': 'synthetic-new' if runtime == 'claude' else jwt(exp=time.time()+3600),
            'refresh_token': 'synthetic-rotated', 'expires_in': 3600}


def adapter(monkeypatch, runtime, result=None):
    exchange = AsyncMock(return_value=result or response(runtime))
    monkeypatch.setattr(mod, '_exchange', exchange)
    return mod.OAuthRefresh(check_access=AsyncMock(return_value=True)), exchange


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime', ['claude', 'codex'])
async def test_refresh_persists_rotation_before_headers(tmp_path, monkeypatch, runtime):
    a, path, before = setup(tmp_path, runtime)
    refresh, exchange = adapter(monkeypatch, runtime)
    fingerprint = a.identity()
    headers = await refresh(AUTH, a)
    doc = json.loads(path.read_text())
    key = 'claudeAiOauth' if runtime == 'claude' else 'tokens'
    rk = 'refreshToken' if runtime == 'claude' else 'refresh_token'
    ak = 'accessToken' if runtime == 'claude' else 'access_token'
    assert headers['Authorization'] == 'Bearer ' + doc[key][ak]
    assert doc[key][rk] == 'synthetic-rotated'
    assert a.identity() == fingerprint
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (a.directory / '.loma-oauth.pending').exists()
    body = exchange.call_args.args[1]
    assert body['refresh_token'] == 'test-refresh'
    assert body['client_id'] == mod.PROVIDERS[runtime][1]
    assert body['grant_type'] == 'refresh_token'
    if runtime == 'claude':
        assert body['scope'] == 'user:profile user:inference'
        assert doc[key]['subscriptionType'] == 'max'
        assert headers['anthropic-beta'] == 'oauth-2025-04-20'
    else:
        assert headers['ChatGPT-Account-Id'] == 'provider-account'
    # New adapter/process reads persisted credentials, no second exchange.
    again = mod.OAuthRefresh(check_access=AsyncMock(return_value=True))
    assert await again(AUTH, a) == headers
    assert exchange.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime', ['claude', 'codex'])
async def test_fresh_credentials_do_not_refresh(tmp_path, monkeypatch, runtime):
    a, path, doc = setup(tmp_path, runtime, True)
    refresh, exchange = adapter(monkeypatch, runtime)
    assert 'Authorization' in await refresh(AUTH, a)
    assert json.loads(path.read_text()) == doc
    exchange.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime', ['claude', 'codex'])
async def test_concurrent_adapters_single_exchange(tmp_path, monkeypatch, runtime):
    a, path, _ = setup(tmp_path, runtime)
    refresh, exchange = adapter(monkeypatch, runtime)
    async def slow(*args):
        await asyncio.sleep(.08)
        return response(runtime)
    exchange.side_effect = slow
    other = mod.OAuthRefresh(check_access=AsyncMock(return_value=True))
    results = await asyncio.gather(*(r(AUTH, a) for r in [refresh, other]*5))
    assert all(r == results[0] for r in results)
    assert exchange.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime', ['claude', 'codex'])
@pytest.mark.parametrize('problem', ['missing-access', 'bad-access', 'bad-refresh', 'expired', 'identity', 'account', 'token-type', 'network'])
async def test_invalid_exchange_fails_closed_and_fences_retry(tmp_path, monkeypatch, runtime, problem):
    a, path, before = setup(tmp_path, runtime)
    result = response(runtime)
    if problem == 'missing-access': result.pop('access_token')
    if problem == 'bad-access': result['access_token'] = 'secret\r\nheader'
    if problem == 'bad-refresh': result['refresh_token'] = None
    if problem == 'expired':
        if runtime == 'claude': result['expires_in'] = 0
        else: result['access_token'] = jwt(exp=time.time()-10)
    if problem in ['identity', 'account']:
        if runtime == 'claude': result['account'] = {'uuid': 'other', 'email_address': a.email}
        else:
            result['id_token'] = jwt(sub='other' if problem == 'identity' else 'provider-one',
                **{'https://api.openai.com/auth': {'chatgpt_account_id': 'other'}})
    if problem == 'token-type': result['token_type'] = 'basic'
    refresh, exchange = adapter(monkeypatch, runtime, result)
    if problem == 'network': exchange.side_effect = RuntimeError('secret-refresh-provider-error')
    for _ in range(2):
        with pytest.raises(ModelDenied) as caught:
            await refresh(AUTH, a)
        assert 'secret' not in str(caught.value)
    assert exchange.await_count == 1
    assert json.loads(path.read_text()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disconnect', 'relogin', 'rotate', 'revoke'])
async def test_changes_during_exchange_not_overwritten(tmp_path, monkeypatch, change):
    a, path, doc = setup(tmp_path, 'codex')
    refresh, exchange = adapter(monkeypatch, 'codex')
    async def perform(*args):
        if change == 'disconnect': path.unlink()
        if change == 'relogin': account(tmp_path, identity='other')
        if change == 'rotate':
            doc['tokens']['refresh_token'] = 'external-rotation'; path.write_text(json.dumps(doc))
        if change == 'revoke': refresh.check_access.return_value = False
        return response('codex')
    exchange.side_effect = perform
    with pytest.raises(ModelDenied): await refresh(AUTH, a)
    if change == 'disconnect': assert not path.exists()
    else: assert 'synthetic-rotated' not in path.read_text()


@pytest.mark.asyncio
async def test_cancel_leaves_fence_and_reconnect_recovers(tmp_path, monkeypatch):
    a, path, doc = setup(tmp_path, 'codex')
    refresh, exchange = adapter(monkeypatch, 'codex')
    entered = asyncio.Event()
    async def pending(*args):
        entered.set(); await asyncio.Event().wait()
    exchange.side_effect = pending
    task = asyncio.create_task(refresh(AUTH, a)); await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    with pytest.raises(ModelDenied): await refresh(AUTH, a)
    assert exchange.await_count == 1
    doc['tokens']['refresh_token'] = 'reconnected'; path.write_text(json.dumps(doc))
    exchange.side_effect = None
    assert 'Authorization' in await refresh(AUTH, a)
    assert exchange.await_count == 2


@pytest.mark.asyncio
async def test_no_rotated_refresh_or_id_token_preserves_old(tmp_path, monkeypatch):
    a, path, before = setup(tmp_path, 'codex')
    result = response('codex'); result.pop('refresh_token')
    refresh, _ = adapter(monkeypatch, 'codex', result)
    await refresh(AUTH, a)
    after = json.loads(path.read_text())['tokens']
    for key in ['id_token', 'refresh_token', 'account_id']:
        assert after[key] == before['tokens'][key]


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['lock-symlink', 'file-symlink', 'missing-refresh', 'bad-expiry', 'scope-escalation', 'persist-failure'])
async def test_storage_and_validation_failures(tmp_path, monkeypatch, problem):
    a, path, doc = setup(tmp_path, 'claude')
    result = response('claude')
    if problem == 'lock-symlink': (a.directory / '.loma-oauth.lock').symlink_to(path)
    if problem == 'file-symlink':
        target = a.directory / 'other'; path.rename(target); path.symlink_to(target)
    if problem == 'missing-refresh':
        doc['claudeAiOauth'].pop('refreshToken'); path.write_text(json.dumps(doc))
    if problem == 'bad-expiry':
        doc['claudeAiOauth']['expiresAt'] = True; path.write_text(json.dumps(doc))
    if problem == 'scope-escalation': result['scope'] = 'user:admin'
    refresh, exchange = adapter(monkeypatch, 'claude', result)
    if problem == 'persist-failure':
        original = mod._atomic
        def fail(fd, name, value):
            if name == '.credentials.json': raise OSError('secret-disk-error')
            return original(fd, name, value)
        monkeypatch.setattr(mod, '_atomic', fail)
    with pytest.raises(ModelDenied): await refresh(AUTH, a)
    assert 'synthetic-rotated' not in path.read_text()


@pytest.mark.asyncio
async def test_default_selector_wires_real_adapter(db, tmp_path, monkeypatch):
    a, path, _ = setup(tmp_path, 'codex')
    exchange = AsyncMock(return_value=response('codex')); monkeypatch.setattr(mod, '_exchange', exchange)
    pool = SubscriptionAccounts(db, [a], check_access=AsyncMock(return_value=True))
    selected = await pool.select(AUTH, 'codex')
    headers = await selected.resolve_headers(AUTH, selected.account_id)
    assert headers['Authorization'].startswith('Bearer ')
    assert exchange.await_count == 1
    await db.users.update_one({'email': a.email}, {'$set': {'codex_pool_enabled': False}})
    with pytest.raises(ModelDenied): await selected.resolve_headers(AUTH, selected.account_id)
    assert exchange.await_count == 1


@pytest.mark.asyncio
async def test_exchange_transport_fixed_url_json_no_redirects(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    seen = []
    async def handler(request):
        seen.append(await request.json())
        return web.json_response({'access_token': 'synthetic'})
    app = web.Application(); app.router.add_post('/token', handler)
    async with TestServer(app) as server:
        monkeypatch.setitem(mod.PROVIDERS, 'codex', (str(server.make_url('/token')), 'test-client'))
        # Environment overrides must never change the trusted URL.
        monkeypatch.setenv('CODEX_REFRESH_TOKEN_URL_OVERRIDE', 'http://127.0.0.1:1')
        result = await mod._exchange('codex', {'refresh_token': 'synthetic'})
    assert result == {'access_token': 'synthetic'}
    assert seen == [{'refresh_token': 'synthetic'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['redirect', 'unauthorized', 'server-error', 'large', 'bad-json', 'list'])
async def test_transport_rejects_bad_responses(monkeypatch, kind):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    seen = []
    async def handler(request):
        seen.append(request.path)
        if request.path == '/leak': raise AssertionError('Redirect followed')
        if kind == 'redirect': return web.Response(status=307, headers={'Location': '/leak'})
        if kind == 'unauthorized': return web.Response(status=401, text='sensitive response')
        if kind == 'server-error': return web.Response(status=500, text='sensitive response')
        if kind == 'large': return web.Response(text=' ' * (mod.LIMIT + 1))
        if kind == 'bad-json': return web.Response(text='sensitive response')
        return web.json_response([])
    app = web.Application(); app.router.add_post('/{any}', handler)
    async with TestServer(app) as server:
        monkeypatch.setitem(mod.PROVIDERS, 'claude', (str(server.make_url('/token')), 'test-client'))
        with pytest.raises((ModelDenied, ValueError)) as caught:
            await mod._exchange('claude', {})
        assert 'sensitive' not in str(caught.value)
    assert seen == ['/token']


@pytest.mark.asyncio
async def test_cancellation_waiting_for_file_lock(tmp_path, monkeypatch):
    import fcntl
    a, path, _ = setup(tmp_path, 'codex')
    refresh, exchange = adapter(monkeypatch, 'codex')
    with (a.directory / '.loma-oauth.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        task = asyncio.create_task(refresh(AUTH, a))
        await asyncio.sleep(.1); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    exchange.assert_not_called()
    assert not (a.directory / '.loma-oauth.pending').exists()
    assert 'Authorization' in await refresh(AUTH, a)


@pytest.mark.asyncio
async def test_timeout_fences_and_never_uses_stale_token(tmp_path, monkeypatch):
    a, path, _ = setup(tmp_path, 'codex')
    refresh, exchange = adapter(monkeypatch, 'codex')
    exchange.side_effect = asyncio.TimeoutError()
    for _ in range(2):
        with pytest.raises(ModelDenied): await refresh(AUTH, a)
    assert exchange.await_count == 1


@pytest.mark.asyncio
async def test_access_token_cannot_switch_account(tmp_path, monkeypatch):
    a, path, doc = setup(tmp_path, 'codex')
    result = response('codex')
    result['access_token'] = jwt(exp=time.time()+3600,
        **{'https://api.openai.com/auth': {'chatgpt_account_id': 'wrong'}})
    refresh, _ = adapter(monkeypatch, 'codex', result)
    with pytest.raises(ModelDenied): await refresh(AUTH, a)
    assert json.loads(path.read_text()) == doc


def test_cross_process_refresh_serialized(tmp_path):
    import subprocess
    import sys
    a, path, _ = setup(tmp_path, 'codex')
    script = '''
import asyncio, json, sys, time, base64
from pathlib import Path
from unittest.mock import AsyncMock
from isolation.accounts import SubscriptionAccount
from isolation import oauth
async def exchange(*args):
    with (Path(sys.argv[1]) / 'exchanges').open('a') as log: log.write('exchange\\n')
    await asyncio.sleep(.2)
    body = base64.urlsafe_b64encode(json.dumps({'exp':time.time()+3600}).encode()).decode().rstrip('=')
    return {'access_token':'h.'+body+'.s', 'refresh_token':'rotated'}
oauth._exchange = exchange
account = SubscriptionAccount('codex', sys.argv[2], Path(sys.argv[1]))
asyncio.run(oauth.OAuthRefresh(check_access=AsyncMock(return_value=True))(object(), account))
'''
    processes = [subprocess.Popen([sys.executable, '-c', script, str(a.directory), a.email],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    for p in processes:
        out, err = p.communicate(timeout=15)
        assert p.returncode == 0, err.decode()
    assert (a.directory / 'exchanges').read_text() == 'exchange\n'


@pytest.mark.asyncio
async def test_codex_opaque_token_uses_refresh_timestamp(tmp_path, monkeypatch):
    a, path, doc = setup(tmp_path, 'codex')
    doc['tokens']['access_token'] = 'opaque-old'
    path.write_text(json.dumps(doc))
    result = {'access_token': 'opaque-new', 'refresh_token': 'rotated'}
    refresh, exchange = adapter(monkeypatch, 'codex', result)
    assert (await refresh(AUTH, a))['Authorization'] == 'Bearer opaque-new'
    assert (await refresh(AUTH, a))['Authorization'] == 'Bearer opaque-new'
    assert exchange.await_count == 1


@pytest.mark.asyncio
async def test_run_assembly_resolves_oauth_without_worker_credentials(db, tmp_path, monkeypatch):
    from isolation import run
    from tests.test_worker_run import args, seed
    await seed(db)
    a, path, _ = setup(tmp_path, 'codex')
    exchange = AsyncMock(return_value=response('codex')); monkeypatch.setattr(mod, '_exchange', exchange)
    pool = SubscriptionAccounts(db, [a], check_access=AsyncMock(return_value=True))
    async def worker(**kw):
        headers = await kw['execute_tool'].models.resolve_headers(kw['authority'])
        assert headers['ChatGPT-Account-Id'] == 'provider-account'
        assert headers['Authorization'] == 'Bearer ' + json.loads(path.read_text())['tokens']['access_token']
        wire = json.dumps(kw['input'])
        assert all(secret not in wire for secret in [a.account_id, str(a.directory), 'synthetic-rotated', headers['Authorization']])
        yield 'OAuth assembly verified'
    monkeypatch.setattr(run, 'stream_worker', worker)
    assert [e async for e in run.stream_run(**args(db, tmp_path/'artifacts', subscription_accounts=pool))] == ['OAuth assembly verified']
    row = await db.isolated_model_budgets.find_one({})
    assert row['spec']['account_id'] == a.account_id and row['active'] is False
