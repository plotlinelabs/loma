"""Same-server transport and isolation contracts; only synthetic credentials."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import pytest

from isolation import claude_login as login
from isolation import bundled_login as broker
from isolation import login_proxy as proxy
from tests.test_claude_login import bundle, OWNER, URL


@pytest.mark.asyncio
async def test_unix_socket_login_and_publish(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_USERS_DIR', str(tmp_path / 'accounts'))
    monkeypatch.delenv('LOMA_CLAUDE_SHARED_LOGIN', raising=False)
    monkeypatch.delenv('LOMA_REMOTE_WORKERS', raising=False)
    monkeypatch.delenv('LOMA_LOGIN_MODE', raising=False)
    sock = tmp_path / 'login.sock'
    monkeypatch.setenv('LOMA_LOGIN_SOCKET', str(sock))
    async def run(request):
        assert not request.headers.get('Authorization')
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        assert await ws.receive_json() == {'type': 'start', 'input': {'runtime': 'claude-login'}}
        await ws.send_json({'type': 'text', 'text': URL + '\n'})
        await ws.send_json({'type': 'tool_request', 'id': 'code-0', 'tool': 'login_code', 'arguments': {}})
        assert (await ws.receive_json())['result'] == {'code': 'synthetic#code'}
        await ws.send_json({'type': 'tool_request', 'id': 'save', 'tool': 'save_credentials', 'arguments': bundle()})
        assert (await ws.receive_json())['result'] == {'ok': True}
        await ws.send_json({'type': 'done'})
        await ws.close()
        return ws
    app = web.Application(); app.router.add_get('/v1/run', run)
    runner = web.AppRunner(app); await runner.setup()
    await web.UnixSite(runner, str(sock)).start()
    try:
        assert login.enabled()
        session = login.Login(OWNER)
        session.queue.put_nowait('synthetic#code')
        await login.begin(OWNER, session.id)
        await login.run_login(session, login.transport(), AsyncMock(return_value=True))
        assert session.state == 'connected'
        assert [a.email for a in login.shared_accounts()] == [OWNER]
        assert 'synthetic-access' not in json.dumps(session.public())
        monkeypatch.setenv('LOMA_CLAUDE_SHARED_LOGIN', 'off')
        with pytest.raises(ValueError): login.transport()
    finally:
        await runner.cleanup()


def test_bundled_unavailable_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('LOMA_CLAUDE_SHARED_LOGIN', 'on')
    monkeypatch.setenv('LOMA_LOGIN_MODE', 'bundled')
    monkeypatch.setenv('LOMA_LOGIN_URL', 'https://unused.example')
    monkeypatch.setenv('LOMA_LOGIN_SOCKET', str(tmp_path / 'missing'))
    with pytest.raises(ValueError, match='full Docker Compose'): login.transport()


@pytest.mark.parametrize('target', ['localhost:443', '169.254.169.254:443', 'claude.ai:80',
    'claude.ai.evil.test:443', 'user@claude.ai:443', '[::1]:443', 'CLAUDE.AI:443'])
def test_proxy_rejects_destinations(target):
    with pytest.raises(ValueError): proxy.destination(f'CONNECT {target} HTTP/1.1\r\n\r\n'.encode())


@pytest.mark.parametrize('method', ['GET', 'POST', 'HEAD'])
def test_proxy_only_tunnels(method):
    with pytest.raises(ValueError): proxy.destination(f'{method} claude.ai:443 HTTP/1.1\r\n\r\n'.encode())


@pytest.mark.asyncio
@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fe80::1'])
async def test_dns_rebinding_denied(address, monkeypatch):
    resolver = AsyncMock(return_value=[(2, 1, 6, '', (address, 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolver)
    connector = AsyncMock(); monkeypatch.setattr(asyncio, 'open_connection', connector)
    with pytest.raises(ValueError): await proxy.connect('claude.ai')
    connector.assert_not_called()


@pytest.mark.asyncio
async def test_dns_address_pinned(monkeypatch):
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=[(2, 1, 6, '', ('1.1.1.1', 443))]))
    connector = AsyncMock(return_value=('reader', 'writer')); monkeypatch.setattr(asyncio, 'open_connection', connector)
    await proxy.connect('claude.ai')
    connector.assert_awaited_once_with('1.1.1.1', 443, family=2)


def test_firewall_is_deny_default_and_proxy_only():
    commands = broker.firewall_commands('172.25.0.2')
    assert all(Path(command[0]).is_absolute() for command in commands)
    assert ['/usr/sbin/iptables', '-P', 'OUTPUT', 'DROP'] in commands
    assert ['/usr/sbin/ip6tables', '-P', 'OUTPUT', 'DROP'] in commands
    permits = [c for c in commands if 'ACCEPT' in c]
    assert permits == [['/usr/sbin/iptables', '-A', 'OUTPUT', '-p', 'tcp', '-d', '172.25.0.2', '--dport', '3128', '-j', 'ACCEPT']]
    assert '--dport' in permits[0] and '3128' in permits[0]
    assert 'ESTABLISHED' not in str(commands)  # not a broad inherited-session bypass


def test_compose_no_secrets_or_docker_socket():
    import yaml
    data = yaml.safe_load((Path(__file__).parents[1] / 'docker-compose.yml').read_text())
    service = data['services']['loma-login']
    assert not service.get('ports') and not service.get('env_file')
    assert service['read_only'] and service['cap_drop'] == ['ALL']
    assert service['volumes'] == ['loma-login-socket:/run/loma-login']
    assert data['networks']['login-private']['internal'] is True
    assert service['networks'] == ['login-private']
    assert 'docker.sock' not in json.dumps(data)
    assert 'no-new-privileges:true' in service['security_opt']


@pytest.mark.asyncio
async def test_ready_pool_round_robin_disconnect_and_cooldown(tmp_path, monkeypatch):
    from agent.pool import ClientPool
    pool = ClientPool(3)
    accounts = []
    for email in ('a@example.test', 'b@example.test'):
        path = tmp_path / email; path.mkdir()
        for filename in ('.claude.json', '.credentials.json'): (path / filename).write_text('{}')
        accounts.append({'email': email, 'config_dir': str(path)})
    pool._accounts = accounts
    clients = [SimpleNamespace(_pool_account=a) for a in accounts]
    cleanup = AsyncMock(); monkeypatch.setattr(pool, '_disconnect_then_warm', cleanup)
    # Insert reverse completion order, but requests rotate sorted accounts.
    for c in reversed(clients): pool._available.put_nowait(c)
    assert pool._take_ready_client() is clients[0]
    pool._available.put_nowait(clients[0])
    assert pool._take_ready_client() is clients[1]
    pool._available.put_nowait(clients[1])
    (Path(accounts[0]['config_dir']) / '.credentials.json').unlink()
    assert pool._take_ready_client() is clients[1]
    await asyncio.sleep(0)
    cleanup.assert_awaited_once_with(clients[0])
    pool._available.put_nowait(clients[1])
    pool.mark_account_exhausted(accounts[1]['email'])
    assert pool._take_ready_client() is None
    await asyncio.sleep(0)
    assert cleanup.await_count == 2


def test_namespace_mount_order_and_flags(monkeypatch):
    from isolation import bundled_login_child as child
    from unittest.mock import Mock
    libc = Mock()
    libc.unshare.return_value = libc.mount.return_value = 0
    monkeypatch.setattr(child.ctypes, 'CDLL', lambda *a, **k: libc)
    monkeypatch.setattr(child.os, 'pipe2', lambda flags: (91, 92))
    monkeypatch.setattr(child.os, 'fork', lambda: 0)
    close = Mock(); monkeypatch.setattr(child.os, 'close', close)
    guard = Mock(); monkeypatch.setattr(child, 'guard_parent', guard)
    assert child.isolate_processes() == 91
    libc.unshare.assert_called_once_with(child.CLONE_NEWNS | child.CLONE_NEWPID)
    assert libc.mount.call_args_list[0].args == (None, b'/', None, child.MS_REC | child.MS_PRIVATE, None)
    assert libc.mount.call_args_list[1].args == (b'proc', b'/sandbox/proc', b'proc', 15, None)
    close.assert_called_once_with(92)
    guard.assert_called_once_with(91)


@pytest.mark.parametrize('failure', ['unshare', 'private_mount', 'proc_mount'])
def test_namespace_setup_fail_closed(monkeypatch, failure):
    from isolation import bundled_login_child as child
    from unittest.mock import Mock
    libc = Mock()
    libc.unshare.return_value = -1 if failure == 'unshare' else 0
    libc.mount.side_effect = [-1] if failure == 'private_mount' else [0, -1]
    monkeypatch.setattr(child.ctypes, 'CDLL', lambda *a, **k: libc)
    monkeypatch.setattr(child.os, 'pipe2', lambda flags: (91, 92))
    monkeypatch.setattr(child.os, 'close', Mock())
    monkeypatch.setattr(child.os, 'fork', lambda: 0)
    monkeypatch.setattr(child, 'guard_parent', Mock())
    with pytest.raises(OSError): child.isolate_processes()


def test_dead_parent_fails_closed(monkeypatch):
    from isolation import bundled_login_child as child
    from unittest.mock import Mock
    libc = Mock(); libc.prctl.return_value = 0
    monkeypatch.setattr(child.ctypes, 'CDLL', lambda *a, **k: libc)
    monkeypatch.setattr(child.os, 'getppid', lambda: 0)
    monkeypatch.setattr(child.select, 'select', lambda *args: ([91], [], []))
    with pytest.raises(RuntimeError, match='parent exited'): child.guard_parent(91)


def test_namespace_compose_capabilities_are_broker_only():
    import yaml
    data = yaml.safe_load((Path(__file__).parents[1] / 'docker-compose.yml').read_text())
    for name, service in data['services'].items():
        assert not service.get('privileged')
        assert service.get('pid') != 'host'
        if name == 'loma-login':
            assert 'SYS_ADMIN' in service['cap_add']
            assert 'apparmor:unconfined' in service['security_opt']
            assert not any('seccomp' in opt for opt in service['security_opt'])
        else:
            assert 'SYS_ADMIN' not in service.get('cap_add', [])
            assert 'apparmor:unconfined' not in service.get('security_opt', [])
