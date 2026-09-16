"""Lifecycle and trusted-issuer boundary regression tests (no real credentials)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from api import recall_session
from agent import recall_runtime


@pytest.mark.asyncio
async def test_launch_requires_real_session_cookie(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(recall_session, 'issuer_call', call)
    assert await recall_session.launch_recall(SimpleNamespace(headers={'X-User-Email': 'owner@example.com'}), 'c', 'owner@example.com') is None
    call.assert_not_called()


@pytest.mark.asyncio
async def test_launch_mismatched_cookie_identity_is_revoked(monkeypatch):
    call = AsyncMock(side_effect=[{'email': 'other@example.com', 'grant': 'synthetic'}, {}])
    monkeypatch.setattr(recall_session, 'issuer_call', call)
    request = SimpleNamespace(headers={'Cookie': 'session=synthetic', 'X-User-Email': 'owner@example.com'})
    assert await recall_session.launch_recall(request, 'c', 'owner@example.com') is None
    assert call.call_args_list[1].args[0] == {'action': 'revoke', 'grant': 'synthetic'}


@pytest.mark.parametrize('url', ['http://evil.example', 'file:///tmp/key', 'https://user:pass@example.com', 'https://example.com?redirect=x', 'https://example.com/path'])
def test_issuer_url_is_not_a_redirect_or_credential_sink(monkeypatch, url):
    monkeypatch.setenv('LOMA_RECALL_ISSUER_URL', url)
    with pytest.raises(ValueError): recall_session.issuer_url()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, RuntimeError('synthetic'), asyncio.CancelledError()])
async def test_turn_always_revokes_and_refreshes(monkeypatch, failure):
    from agent import client
    refresh, revoke = AsyncMock(), AsyncMock()
    monkeypatch.setattr(recall_runtime, 'refresh_history', refresh)
    monkeypatch.setattr(recall_runtime, 'revoke', revoke)
    async def run(*args):
        if failure: raise failure
        yield 'normal reply'
    monkeypatch.setattr(client, '_stream_agent', run)
    session = {'user_id': 'synthetic', 'grant': 'test'}
    if failure:
        with pytest.raises(type(failure)):
            _ = [x async for x in client.stream_agent('hello', recall_session=session)]
    else:
        assert [x async for x in client.stream_agent('hello', recall_session=session)] == ['normal reply']
    revoke.assert_awaited_once_with(session)
    assert refresh.await_count == 2


@pytest.mark.asyncio
async def test_opencode_cache_includes_credential_values(monkeypatch, tmp_path):
    from agent import opencode_runtime as runtime
    monkeypatch.setattr(runtime, '_opencode_config_cache', {})
    monkeypatch.setattr(runtime, '_load_current_agent_config', AsyncMock(return_value={'mcp_servers': {}}))
    monkeypatch.setattr(runtime.tempfile, 'gettempdir', lambda: str(tmp_path))
    def config(token): return {'loma-recall': {'type': 'stdio', 'command': 'python', 'args': [], 'env': {'GRANT': token}}}
    first = await runtime._write_managed_opencode_config(config('synthetic-one'))
    second = await runtime._write_managed_opencode_config(config('synthetic-two'))
    assert first != second
    assert first == await runtime._write_managed_opencode_config(config('synthetic-one'))
    assert first[0].stat().st_mode & 0o777 == 0o700


def test_runtime_config_does_not_forward_browser_cookie_or_private_key(monkeypatch):
    monkeypatch.setenv('LOMA_RECALL_ISSUER_URL', 'http://localhost:13001')
    config = recall_runtime.runtime_config({'grant': 'test', 'capability': 'test', 'expires_at': 1,
        'cookie': 'must-not-forward', 'private_key': 'must-not-forward'})
    assert 'must-not-forward' not in str(config)
    assert set(config['env']) == {'LOMA_RECALL_BACKEND_URL', 'LOMA_RECALL_ISSUER_URL', 'LOMA_RECALL_GRANT', 'LOMA_RECALL_CAPABILITY', 'LOMA_RECALL_EXPIRES_AT'}


@pytest.mark.asyncio
async def test_adapter_renews_before_expiry_and_does_not_redirect(aiohttp_server):
    from aiohttp import web
    from tests.test_history_recall_mcp import module
    seen = []
    async def issuer(request):
        seen.append(await request.json())
        return web.json_response({'capability': 'renewed-synthetic', 'expires_at': 9999999999})
    async def fetch(request):
        assert request.headers['Authorization'] == 'Bearer renewed-synthetic'
        return web.json_response({'messages': []})
    app = web.Application()
    app.router.add_post('/api/recall-session', issuer)
    app.router.add_post('/api/recall/fetch', fetch)
    server = await aiohttp_server(app)
    url = str(server.make_url('/')).rstrip('/')
    client = module.RecallClient(url, 'expired-synthetic', url, 'scoped-synthetic', 0)
    assert await client.call('fetch', {'conversation_id': 'c'}) == {'messages': []}
    assert seen == [{'action': 'renew', 'grant': 'scoped-synthetic'}]
    assert client.calls == 1


@pytest.mark.asyncio
async def test_codex_override_uses_private_home_and_cleans_up(monkeypatch, tmp_path):
    from agent import codex_runtime as runtime, codex_pool
    account_home = tmp_path / 'account'
    account_home.mkdir()
    (account_home / 'auth.json').write_text('{"synthetic":true}')
    borrowed = SimpleNamespace(account={'email': 'test@example.com', 'config_dir': str(account_home)})
    pool = SimpleNamespace(acquire=AsyncMock(return_value=borrowed), safe_disconnect=AsyncMock(),
        release=AsyncMock(), _mcp_servers=lambda: {}, status=lambda: {'available':0,'pool_size':1})
    monkeypatch.setattr(codex_pool, 'get_codex_pool', lambda: pool)
    homes = []
    class Worker:
        def __init__(self, account, model): self.account=account
        async def connect(self, **kwargs):
            from pathlib import Path
            home = Path(self.account['config_dir']); homes.append(home)
            assert home != account_home and home.stat().st_mode & 0o777 == 0o700
            assert (home/'auth.json').exists()
            assert 'loma-recall' in kwargs['mcp_servers']
        async def run_turn(self, prompt):
            yield {'type':'agent_message','message':'normal reply'}
    monkeypatch.setattr(runtime, 'CodexWorker', Worker)
    result = [x async for x in runtime.run_codex_agent(full_prompt='test',selected_model='codex/gpt-test',
        user_mcp_overrides={'loma-recall':{'command':'synthetic'}})]
    assert result and not homes[0].exists()
    assert (account_home/'auth.json').read_text() == '{"synthetic":true}'
    pool.release.assert_awaited_once_with(borrowed)


@pytest.mark.asyncio
async def test_opencode_setup_failure_cleans_scoped_config(monkeypatch, tmp_path):
    from agent import opencode_runtime as runtime
    import hashlib, json
    overrides={'loma-recall':{'command':'synthetic'}}
    key=hashlib.sha256(json.dumps(overrides,sort_keys=True).encode()).hexdigest()
    home=tmp_path/'execution';home.mkdir()
    server=SimpleNamespace(terminate=AsyncMock())
    monkeypatch.setattr(runtime,'_opencode_config_cache',{key:(home,'hash',0)})
    monkeypatch.setattr(runtime,'_opencode_servers',{'hash':server})
    async def fail(**kwargs):
        raise RuntimeError('setup failed')
        yield
    monkeypatch.setattr(runtime,'_run_opencode_agent',fail)
    with pytest.raises(RuntimeError):
        _=[x async for x in runtime.run_opencode_agent(user_mcp_overrides=overrides)]
    server.terminate.assert_awaited_once()
    assert not home.exists() and not runtime._opencode_config_cache


@pytest.mark.asyncio
async def test_concurrent_opencode_start_does_not_kill_booting_server(monkeypatch,tmp_path):
    from agent import opencode_runtime as runtime
    monkeypatch.delenv('OPENCODE_SERVER_URL',raising=False)
    monkeypatch.setattr(runtime,'_opencode_servers',{})
    monkeypatch.setattr(runtime,'_opencode_server_lock',asyncio.Lock())
    monkeypatch.setattr(runtime,'_write_managed_opencode_config',AsyncMock(return_value=(tmp_path,'same')))
    monkeypatch.setattr(runtime,'_retire_stale_servers',AsyncMock())
    monkeypatch.setattr(runtime.shutil,'which',lambda _: '/synthetic/opencode')
    monkeypatch.setattr(runtime,'_pick_server_port',lambda _:14097)
    monkeypatch.setattr(runtime,'_open_server_log',lambda _: (None,None))
    class Process:
        returncode=None
        def terminate(self): self.returncode=-15
        async def wait(self): return self.returncode
    spawn=AsyncMock(side_effect=lambda *a,**k:Process())
    monkeypatch.setattr(runtime.asyncio,'create_subprocess_exec',spawn)
    count=0
    async def health(url):
        nonlocal count
        count+=1
        await asyncio.sleep(.01)
        return count>=3
    monkeypatch.setattr(runtime,'_health_check',health)
    first,second=await asyncio.gather(runtime._ensure_server_instance(),runtime._ensure_server_instance())
    assert first is second and first.is_alive
    assert spawn.await_count==1
