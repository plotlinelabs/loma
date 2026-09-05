"""Credential setup uses only fixed vendor login commands, not a shell."""
import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from api import terminal_routes as terminal


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(terminal, '_login_tokens', {})
    monkeypatch.setattr(terminal.shutil, 'which', lambda name: '/synthetic/' + name)


@pytest.mark.parametrize('provider,command', [
    ('claude', ['/synthetic/claude', 'auth', 'login']),
    ('codex', ['/synthetic/codex', 'login', '--device-auth']),
])
def test_login_grant_is_fixed_argv_and_contains_no_backend_env(tmp_path, monkeypatch, provider, command):
    monkeypatch.setenv('SYNTHETIC_BACKEND_SECRET', 'must-stay-backend')
    email = 'owner@example.test'
    config = tmp_path / email
    token = terminal.register_login_terminal(email, provider, config)
    grant = terminal._login_tokens[token]
    assert grant['argv'] == command
    assert grant['email'] == email
    assert grant['expiry'] > time.time()
    assert 'must-stay-backend' not in json.dumps(grant)
    assert 'SYNTHETIC_BACKEND_SECRET' not in grant['env']
    assert config.stat().st_mode & 0o777 == 0o700
    assert not any(arg in ('bash', 'sh', '-c', '-p') for arg in command)


@pytest.mark.parametrize('email', ['../other', 'a/b@example.test', '.', '..', ''])
def test_login_directory_identity_is_validated(tmp_path, email):
    with pytest.raises(web.HTTPBadRequest):
        terminal.register_login_terminal(email, 'claude', tmp_path / 'unused')
    assert terminal._login_tokens == {}


def test_unknown_provider_is_not_an_arbitrary_backend_command(tmp_path):
    with pytest.raises(web.HTTPBadRequest):
        terminal.register_login_terminal('owner@example.test', 'bash', tmp_path / 'unused')


@pytest.mark.asyncio
async def test_login_grant_is_owner_bound_and_consumed_before_fork(tmp_path, monkeypatch):
    token = terminal.register_login_terminal('owner@example.test', 'claude', tmp_path / 'owner')
    fork = Mock(side_effect=AssertionError('must not fork'))
    monkeypatch.setattr(terminal.pty, 'fork', fork)
    req = make_mocked_request('GET', '/api/terminal/ws?token=' + token)
    req['user_email'] = 'another@example.test'
    req['system_role'] = 'chatter'
    response = await terminal.handle_terminal_ws(req)
    assert response.status == 403
    assert token not in terminal._login_tokens
    fork.assert_not_called()
