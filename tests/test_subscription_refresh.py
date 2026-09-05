"""Synthetic refresh/rotation and filesystem lifecycle, no live vendor calls."""
import asyncio
import base64
import json
import os
import time
from unittest.mock import AsyncMock

import pytest

from broker import subscription_refresh as refresh
from broker.service import Denied


def jwt(exp):
    return 'synthetic.' + base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=') + '.synthetic'


def account(tmp_path, provider, exp):
    path = tmp_path / ('claude.json' if provider == 'claude' else 'codex.json')
    if provider == 'claude':
        data = {'claudeAiOauth': {'accessToken': 'synthetic-old', 'refreshToken': 'synthetic-refresh', 'expiresAt': exp * 1000, 'scopes': ['user:inference']}, 'retained': True}
    else:
        data = {'tokens': {'access_token': jwt(exp), 'refresh_token': 'synthetic-refresh', 'account_id': 'synthetic-account'}, 'retained': True}
    path.write_text(json.dumps(data))
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['claude', 'codex'])
async def test_rotation_is_backend_only_and_atomic(tmp_path, monkeypatch, provider):
    path = account(tmp_path, provider, time.time() - 1)
    exchange = AsyncMock(return_value={'access_token': jwt(time.time() + 3600) if provider == 'codex' else 'synthetic-new', 'refresh_token': 'synthetic-next', 'expires_in': 3600})
    monkeypatch.setattr(refresh, '_exchange', exchange)
    assert await refresh.ensure_fresh(provider, path) is True
    data = json.loads(path.read_text())
    assert data['retained']
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('.refresh-*'))
    auth = data['claudeAiOauth' if provider == 'claude' else 'tokens']
    assert auth['refreshToken' if provider == 'claude' else 'refresh_token'] == 'synthetic-next'
    if provider == 'codex':
        assert auth['account_id'] == 'synthetic-account'
    await refresh.ensure_fresh(provider, path)
    exchange.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_refreshes_exchange_once(tmp_path, monkeypatch):
    path = account(tmp_path, 'claude', time.time() - 1)
    async def exchange(*args):
        await asyncio.sleep(0.1)
        return {'access_token': 'synthetic-new', 'refresh_token': 'synthetic-next', 'expires_in': 3600}
    mocked = AsyncMock(side_effect=exchange)
    monkeypatch.setattr(refresh, '_exchange', mocked)
    await asyncio.gather(*(refresh.ensure_fresh('claude', path) for _ in range(5)))
    mocked.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [{}, {'access_token': 'new', 'expires_in': -1}, {'access_token': 'new\nheader', 'expires_in': 30}, {'access_token': 'new', 'expires_in': float('nan')}])
async def test_invalid_response_never_replaces_account(tmp_path, monkeypatch, response):
    path = account(tmp_path, 'claude', time.time() - 1)
    original = path.read_bytes()
    monkeypatch.setattr(refresh, '_exchange', AsyncMock(return_value=response))
    with pytest.raises(Denied):
        await refresh.ensure_fresh('claude', path)
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_relogin_during_refresh_not_overwritten(tmp_path, monkeypatch):
    path = account(tmp_path, 'claude', time.time() - 1)
    async def exchange(*args):
        path.write_text('{"different_account": true}')
        return {'access_token': 'synthetic-new', 'expires_in': 3600}
    monkeypatch.setattr(refresh, '_exchange', exchange)
    with pytest.raises(Denied):
        await refresh.ensure_fresh('claude', path)
    assert json.loads(path.read_text()) == {'different_account': True}


@pytest.mark.asyncio
async def test_symlink_credential_and_lock_paths_rejected(tmp_path, monkeypatch):
    path = account(tmp_path, 'claude', time.time() - 1)
    exchange = AsyncMock()
    monkeypatch.setattr(refresh, '_exchange', exchange)
    alias = tmp_path / 'alias'
    alias.symlink_to(path)
    with pytest.raises(Denied):
        await refresh.ensure_fresh('claude', alias)
    (tmp_path / 'claude.json.refresh.lock').symlink_to(path)
    with pytest.raises(Denied):
        await refresh.ensure_fresh('claude', path)
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_releases_refresh_lock(tmp_path, monkeypatch):
    path = account(tmp_path, 'claude', time.time() - 1)
    started = asyncio.Event()
    async def exchange(*args):
        started.set()
        await asyncio.sleep(100)
    monkeypatch.setattr(refresh, '_exchange', exchange)
    task = asyncio.create_task(refresh.ensure_fresh('claude', path))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(refresh, '_exchange', AsyncMock(return_value={'access_token': 'synthetic-new', 'expires_in': 3600}))
    assert await asyncio.wait_for(refresh.ensure_fresh('claude', path), timeout=1)


@pytest.mark.asyncio
async def test_nonrefreshable_tokens_work_only_until_expiry(tmp_path, monkeypatch):
    path = account(tmp_path, 'claude', time.time() + 30)
    data = json.loads(path.read_text())
    del data['claudeAiOauth']['refreshToken']
    path.write_text(json.dumps(data))
    exchange = AsyncMock()
    monkeypatch.setattr(refresh, '_exchange', exchange)
    await refresh.ensure_fresh('claude', path)
    data['claudeAiOauth']['expiresAt'] = 0
    path.write_text(json.dumps(data))
    with pytest.raises(Denied):
        await refresh.ensure_fresh('claude', path)
    exchange.assert_not_awaited()
