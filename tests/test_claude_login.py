"""Synthetic login credentials only; never contact Anthropic or real accounts."""
import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from isolation import claude_login as mod
from isolation.accounts import _read
from isolation.login_supervisor import LoginSupervisor
from isolation.supervisor import Settings, docker_command
from api import claude_login_routes as routes

OWNER, OTHER = 'alice@example.test', 'bob@example.test'
URL = 'https://claude.ai/oauth/authorize?code_challenge=synthetic&state=synthetic'


def bundle():
    return {'.claude.json': {'oauthAccount': {'accountUuid': 'test-id', 'emailAddress': OWNER},
                             'mcpServers': {'evil': {}}, 'hooks': {'bad': True}},
            '.credentials.json': {'claudeAiOauth': {'accessToken': 'synthetic-access',
              'refreshToken': 'synthetic-refresh', 'expiresAt': (time.time() + 3600) * 1000,
              'scopes': ['user:inference', 'user:profile']}}}


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_USERS_DIR', str(tmp_path))
    monkeypatch.setenv('LOMA_CLAUDE_SHARED_LOGIN', 'on')


@pytest.mark.parametrize('email', ['../escape', '/etc/passwd', 'x/../a@b', 'x\\y@b', '', None, 'a@b\n'])
def test_path_rejected(email):
    with pytest.raises(ValueError):
        mod.directory(email)


@pytest.mark.asyncio
async def test_publish_disconnect_generation_and_discovery():
    await mod.begin(OWNER, 'first')
    await mod.begin(OWNER, 'second')
    with pytest.raises(ValueError, match='cancelled'):
        await mod.publish(OWNER, 'first', bundle())
    await mod.publish(OWNER, 'second', bundle())
    accounts = mod.shared_accounts()
    assert [a.email for a in accounts] == [OWNER]
    assert set(_read(accounts[0].directory, '.claude.json')) == {'oauthAccount'}
    assert os.stat(accounts[0].directory).st_mode & 0o777 == 0o700
    assert os.stat(accounts[0].directory / '.credentials.json').st_mode & 0o777 == 0o600
    await mod.disconnect(OWNER)
    assert mod.shared_accounts() == ()
    with pytest.raises(ValueError, match='cancelled'):
        await mod.publish(OWNER, 'second', bundle())


@pytest.mark.asyncio
async def test_symlinks_fail_closed(tmp_path):
    destination = tmp_path / 'target'; destination.mkdir()
    (tmp_path / OWNER).symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError):
        await mod.begin(OWNER, 'g')
    assert mod.shared_accounts() == ()


@pytest.mark.parametrize('mutate', [
    lambda b: b.update(extra={}),
    lambda b: b['.credentials.json']['claudeAiOauth'].update(expiresAt=1),
    lambda b: b['.credentials.json']['claudeAiOauth'].update(refreshToken=''),
    lambda b: b['.credentials.json']['claudeAiOauth'].update(scopes=[]),
    lambda b: b['.claude.json']['oauthAccount'].update(emailAddress='a\nb'),
])
def test_bad_credentials(mutate):
    b = bundle(); mutate(b)
    with pytest.raises((ValueError, KeyError)):
        mod.validate_bundle(b)


@pytest.mark.parametrize('url', [URL.replace('claude.ai', 'evil.test'),
    URL.replace('https:', 'http:'), URL.replace('/oauth/authorize', '/redirect'),
    URL.replace('claude.ai', 'user:password@claude.ai'), URL.replace('claude.ai', 'claude.ai:444')])
def test_reject_auth_link(url):
    assert mod.authorization_url(url) is None


def test_allowed_link_and_no_terminal_output():
    assert mod.authorization_url('\x1b[32m' + URL + '\n') == URL
    s = mod.Login(OWNER)
    assert 'queue' not in s.public() and 'owner' not in s.public()


def test_login_container_is_not_task_container():
    settings = Settings('loma/login@sha256:' + 'a' * 64, 'x' * 32, max_seconds=600)
    supervisor = LoginSupervisor(settings, 'loma-login-egress')
    command = supervisor.container_command('loma-worker-' + 'b' * 32)
    assert command[command.index('--network') + 1] == 'loma-login-egress'
    assert docker_command(settings, 'loma-worker-' + 'b' * 32)[command.index('--network') + 1] == 'none'
    for flag in ['--read-only', '--cap-drop', '--security-opt', '--log-driver']:
        assert flag in command
    assert '--volume' not in command and '--mount' not in command
    with pytest.raises(ValueError):
        LoginSupervisor(settings, 'host')


class FakeSocket:
    def __init__(self, frames):
        self.frames = frames
        self.sent = []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def __aiter__(self): return self
    async def __anext__(self):
        await asyncio.sleep(0)
        if not self.frames: raise StopAsyncIteration
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(self.frames.pop(0)))
    async def send_json(self, frame): self.sent.append(frame)
    async def send_str(self, frame): self.sent.append(json.loads(frame))


class FakeSession:
    def __init__(self, socket): self.socket = socket
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def ws_connect(self, *args, **kwargs): return self.socket


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', [True, False])
async def test_broker_commits_only_clean_done(monkeypatch, finish):
    session = mod.Login(OWNER)
    await mod.begin(OWNER, session.id)
    socket = FakeSocket([
        {'type': 'text', 'text': URL[:40]},
        {'type': 'text', 'text': URL[40:] + '\n'},
        {'type': 'tool_request', 'id': 'save', 'tool': 'save_credentials', 'arguments': bundle()},
    ] + ([{'type': 'done'}] if finish else []))
    monkeypatch.setattr(mod.aiohttp, 'ClientSession', lambda **kw: FakeSession(socket))
    await mod.run_login(session, ('https://test.invalid', 'x' * 32, object()), AsyncMock(return_value=True))
    assert session.state == ('connected' if finish else 'failed')
    assert bool(mod.shared_accounts()) == finish
    assert 'synthetic-access' not in json.dumps(session.public())


@pytest.mark.asyncio
async def test_revoke_and_cancel_never_publish(monkeypatch):
    session = mod.Login(OWNER)
    await mod.begin(OWNER, session.id)
    monkeypatch.setattr(mod.aiohttp, 'ClientSession', lambda **kw: FakeSession(FakeSocket([])))
    await mod.run_login(session, ('https://test.invalid', 'x' * 32, object()), AsyncMock(return_value=False))
    assert session.state == 'failed' and mod.shared_accounts() == ()


@pytest.mark.asyncio
async def test_rest_owner_binding_validation_cancel(monkeypatch):
    monkeypatch.setattr(routes, 'allowed', AsyncMock(return_value=True))
    monkeypatch.setattr(routes, 'remote_workers_enabled', lambda: True)
    monkeypatch.setattr(mod, 'transport', lambda: ('https://test.invalid', 'x'*32, object()))
    async def wait(session, *_):
        session.state, session.url = 'waiting', URL
        await asyncio.Event().wait()
    monkeypatch.setattr(mod, 'run_login', wait)
    @web.middleware
    async def identity(req, handler):
        req['user_email'] = req.headers.get('Test-User', '')
        return await handler(req)
    app = web.Application(middlewares=[identity]); routes.setup_claude_login_routes(app)
    async with TestClient(TestServer(app)) as client:
        assert (await client.post('/api/claude-auth/login')).status == 401
        response = await client.post('/api/claude-auth/login', headers={'Test-User': OWNER})
        assert response.status == 201
        sid = (await response.json())['id']; path = '/api/claude-auth/login/' + sid
        assert (await client.get(path, headers={'Test-User': OTHER})).status == 404
        assert (await client.post('/api/claude-auth/login', headers={'Test-User': OWNER})).status == 409
        for code in ['x\nrm -rf /', '\x1b', 'x'*2049]:
            assert (await client.post(path+'/code', headers={'Test-User': OWNER}, json={'code': code})).status == 400
        assert (await client.post(path+'/code', headers={'Test-User': OWNER}, json={'code': 'code#state'})).status == 200
        assert (await client.post(path+'/code', headers={'Test-User': OWNER}, json={'code': 'again'})).status == 409
        assert (await client.delete(path, headers={'Test-User': OTHER})).status == 404
        assert (await client.delete(path, headers={'Test-User': OWNER})).status == 200
        with pytest.raises(ValueError): await mod.publish(OWNER, sid, bundle())


@pytest.mark.asyncio
async def test_remote_discovery_shared_round_robin(db, monkeypatch):
    from tests.test_bounded_work import OWNER as A, OTHER as B
    from isolation.accounts import SubscriptionAccounts
    for email in (A, B):
        await mod.begin(email, 'g')
        await mod.publish(email, 'g', bundle())
    selector = SubscriptionAccounts(db, mod.shared_accounts(), check_access=AsyncMock(return_value=True),
                                    refresh=AsyncMock(return_value={'Authorization': 'Bearer fake'}))
    authority = SimpleNamespace(user_email=A, run_id='test')
    selected = []
    for _ in range(4):
        lease = await selector.select(authority, 'claude')
        selected.append(lease.account.email)
        await lease.release()
    assert selected == [B, A, B, A]  # sorted candidate order
    await selector.cooldown(selector.accounts[0].account_id, 60)
    lease = await selector.select(authority, 'claude')
    assert lease.account.email == A
    await lease.release()
    await mod.disconnect(A)
    with pytest.raises(ValueError): await selector.select(authority, 'claude')


from tests.test_bounded_work import db


@pytest.mark.asyncio
async def test_fixed_cli_wrapper_real_pty_and_clean_protocol(tmp_path):
    """Real PTY/protocol, synthetic executable substituted for Anthropic only."""
    import sys
    from pathlib import Path
    fake = tmp_path / 'fake_cli.py'
    fake.write_text('''import json,os,sys,time
from pathlib import Path
print("https://claude.ai/oauth/authorize?code_challenge=test&state=test",flush=True)
assert input() == "synthetic-code#state"
p = Path(os.environ["CLAUDE_CONFIG_DIR"])
(p/".claude.json").write_text(json.dumps({"oauthAccount":{"accountUuid":"test","emailAddress":"qa@example.test"}}))
(p/".credentials.json").write_text(json.dumps({"claudeAiOauth":{"accessToken":"synthetic","refreshToken":"synthetic","expiresAt":(time.time()+3600)*1000,"scopes":["user:inference"]}}))
''')
    runner = tmp_path / 'wrapper.py'
    runner.write_text(f'''import sys
from pathlib import Path
from isolation import claude_login_worker as w
original=w.subprocess.Popen
def fake(command, **kwargs):
    assert command == ['claude','auth','login']
    assert 'ANTHROPIC_API_KEY' not in kwargs['env']
    return original([sys.executable, {str(fake)!r}], **kwargs)
w.subprocess.Popen=fake
w.main(Path({str(tmp_path)!r}))
''')
    proc = await asyncio.create_subprocess_exec(sys.executable, str(runner),
        env={'PATH': os.environ['PATH'], 'PYTHONPATH': str(Path(__file__).parents[1])},
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def send(frame):
        proc.stdin.write((json.dumps(frame)+'\n').encode()); await proc.stdin.drain()
    output = ''; seen = []
    try:
        async with asyncio.timeout(15):
            await send({'type': 'start', 'input': {'runtime': 'claude-login'}})
            while raw := await proc.stdout.readline():
                frame = json.loads(raw); seen.append(frame['type'])
                if frame['type'] == 'text': output += frame['text']
                if frame['type'] == 'tool_request':
                    result = {'code': 'synthetic-code#state'} if frame['tool'] == 'login_code' else {'ok': True}
                    await send({'type': 'tool_response', 'id': frame['id'], 'result': result})
            await proc.wait()
        assert proc.returncode == 0, (await proc.stderr.read()).decode()
        assert seen[-1] == 'done' and 'oauth/authorize' in output
        assert 'synthetic-code' not in output
    finally:
        if proc.returncode is None: proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_expired_session_and_revoked_user(monkeypatch):
    monkeypatch.setattr(routes, 'allowed', AsyncMock(return_value=True))
    session = mod.Login(OWNER, expires=time.time()-1)
    req = SimpleNamespace(app={routes.SESSIONS: {OWNER: session}}, match_info={'session_id': session.id})
    monkeypatch.setattr(routes, 'owner', lambda _: OWNER)
    with pytest.raises(web.HTTPNotFound): await routes.current(req)
    monkeypatch.setattr(routes, 'allowed', AsyncMock(return_value=False))
    with pytest.raises(web.HTTPForbidden): await routes.current(req)


@pytest.mark.asyncio
async def test_deployment_merges_live_connections_and_pool_status(monkeypatch, tmp_path):
    from dataclasses import replace
    from tests.test_remote_entrypoint import synthetic_deployment
    from isolation import deployment as d
    from api import routes as api
    await mod.begin(OWNER, 'g'); await mod.publish(OWNER, 'g', bundle())
    config = synthetic_deployment(tmp_path)
    config = replace(config, claude_accounts=(), codex_accounts=())
    assert [a.email for a in config.accounts_for('claude')] == [OWNER]
    monkeypatch.setattr(d, 'load_deployment', lambda: config)
    status = api._remote_pool_status()
    assert status['accounts'] == [OWNER] and status['remote_workers']['claude_accounts'] == 1
    assert 'synthetic-access' not in json.dumps(status)
    await mod.disconnect(OWNER)
    assert config.accounts_for('claude') == ()
