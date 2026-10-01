"""Managed-key verification remains fail-closed and binds the human decision."""
import hashlib
import hmac
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient
from api import session_gateway as gateway
from api.human_task_routes import setup_status


def request(key):
    stamp = str(int(time.time()))
    raw = b'{}'
    payload = '\n'.join([stamp, 'GET', '/api/human-tasks/setup-status', 'admin@example.test', hashlib.sha256(raw).hexdigest()])
    return SimpleNamespace(headers={'X-Work-Time': stamp, 'X-Work-Signature': hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()},
                           content_length=2, method='GET', path='/api/human-tasks/setup-status',
                           read=AsyncMock(return_value=raw), get=lambda *a: 'admin@example.test', app={})


@pytest.mark.asyncio
async def test_managed_key_overrides_legacy_env(monkeypatch):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(gateway, 'get_db', lambda: db)
    monkeypatch.setenv('LOMA_WORK_GATEWAY_SECRET', 'old' * 16)
    await db.gateway_config.insert_one({'_id': 'human-session', 'secret': 'new' * 16})
    assert (await gateway.verify_dashboard_signature(request('new' * 16)))[0] == 'admin@example.test'
    with pytest.raises(web.HTTPUnauthorized):
        await gateway.verify_dashboard_signature(request('old' * 16))


@pytest.mark.asyncio
@pytest.mark.parametrize('secret', ['', 'short'])
async def test_invalid_managed_key_never_falls_back(monkeypatch, secret):
    db = AsyncMongoMockClient().test
    monkeypatch.setattr(gateway, 'get_db', lambda: db)
    monkeypatch.setenv('LOMA_WORK_GATEWAY_SECRET', 'old' * 16)
    await db.gateway_config.insert_one({'_id': 'human-session', 'secret': secret})
    with pytest.raises(web.HTTPUnauthorized):
        await gateway.verify_dashboard_signature(request('old' * 16))


@pytest.mark.asyncio
async def test_database_failure_does_not_fall_back(monkeypatch):
    db = SimpleNamespace(gateway_config=SimpleNamespace(find_one=AsyncMock(side_effect=RuntimeError('offline'))))
    monkeypatch.setattr(gateway, 'get_db', lambda: db)
    with pytest.raises(web.HTTPServiceUnavailable):
        await gateway.verify_dashboard_signature(request('old' * 16))


@pytest.mark.asyncio
@pytest.mark.parametrize('running', [True, False])
async def test_status_reports_actual_worker_not_env(monkeypatch, running):
    from api import human_task_routes
    db = AsyncMongoMockClient().test
    await db.users.insert_one({'email': 'admin@example.test', 'system_role': 'admin'})
    monkeypatch.setattr(gateway, 'get_db', lambda: db)
    monkeypatch.setattr(human_task_routes, 'get_db', lambda: db)
    monkeypatch.setenv('LOMA_WORK_GATEWAY_SECRET', 'key' * 16)
    req = request('key' * 16)
    req.app['human_task_worker_running'] = running
    result = await setup_status(req)
    import json
    assert json.loads(result.body)['scheduler_running'] is running
    assert result.headers['Cache-Control'] == 'no-store'
