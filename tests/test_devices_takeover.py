"""Loma Devices live view and take-over: a person pauses the agent, drives the device, hands it back."""
import base64
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from mongomock_motor import AsyncMongoMockClient

from api.device_routes import setup_device_routes
from devices import service as service_module
from devices import store
from devices.hub import DeviceError
from devices.service import DeviceService

OWNER = 'owner@example.com'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + (1080).to_bytes(4, 'big') + (2400).to_bytes(4, 'big')


class Hub:
    def __init__(self, platform='android'):
        self.conns, self.calls, self.platform = {}, [], platform

    def get(self, runner_id):
        return self.conns.get(runner_id)

    async def call(self, runner_id, op, serial, args):
        self.calls.append((op, args))
        if op == 'screenshot':
            width = 1179 if self.platform == 'ios' else 1080
            return {'png_base64': base64.b64encode(PNG).decode(), 'width': width, 'height': 2556 if width == 1179 else 2400}
        if op == 'ui_tree':
            return {'units': 'points', 'screen': [393, 852], 'elements': []}
        return {'ok': op}


@pytest_asyncio.fixture
async def env(monkeypatch):
    monkeypatch.setattr(service_module, 'SCREEN_MIN_INTERVAL', 0)
    service_module._SCREENS.clear()
    db = AsyncMongoMockClient()['loma_devices_takeover']
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    runner_id = (await store.redeem_enrollment(db, token, {}))['runner_id']
    await db.device_runners.update_one({'runner_id': runner_id}, {'$set': {'shared_with': ['teammate@example.com']}})

    def make(platform='android'):
        hub = Hub(platform)
        hub.conns[runner_id] = SimpleNamespace(version='1.2.0', templates=[], devices=[
            {'serial': 'emulator-5554', 'platform': platform}])
        return DeviceService(db, hub=hub), hub
    return db, make, f'{runner_id}/emulator-5554'


@pytest.mark.asyncio
async def test_takeover_pauses_the_agent_and_hand_back_resumes_it(env):
    db, make, device = env
    service, hub = make()
    await service.lease(OWNER, 'conv:agent', device_id=device)
    held = await service.start_takeover(OWNER, device)
    assert held['held'] and held['paused_session'] == 'conv:agent'
    with pytest.raises(DeviceError, match='took over this device'):
        await service.call(OWNER, 'conv:agent', device, 'tap', {'x': 1, 'y': 1})
    await service.screen(OWNER, device)  # frame gives the pixel size
    await service.takeover_input(OWNER, device, 'tap', {'fx': 0.5, 'fy': 0.25})
    assert hub.calls[-1] == ('tap', {'x': 540, 'y': 600})
    await service.takeover_input(OWNER, device, 'swipe', {'fx1': 0.5, 'fy1': 0.8, 'fx2': 0.5, 'fy2': 0.2})
    assert hub.calls[-1] == ('swipe', {'x1': 540, 'y1': 1920, 'x2': 540, 'y2': 480, 'duration_ms': 300})
    ended = await service.end_takeover(OWNER, device)
    assert ended == {'held': False, 'resumed_session': True}
    await service.call(OWNER, 'conv:agent', device, 'tap', {'x': 1, 'y': 1})  # the agent continues
    scopes = {row['scope'] for row in await db.device_audit.find({'op': {'$in': ['takeover', 'tap']}}).to_list(20)}
    assert scopes == {'takeover:' + OWNER, 'conv:agent'}


@pytest.mark.asyncio
async def test_ios_taps_are_in_points(env):
    _, make, device = env
    service, hub = make('ios')
    await service.start_takeover(OWNER, device)
    await service.takeover_input(OWNER, device, 'tap', {'fx': 0.5, 'fy': 0.5})
    assert hub.calls[-1] == ('tap', {'x': 196, 'y': 426})


@pytest.mark.asyncio
async def test_takeover_rules(env):
    db, make, device = env
    service, _ = make()
    with pytest.raises(DeviceError, match='Take over the device first'):
        await service.takeover_input(OWNER, device, 'tap', {'fx': 0.1, 'fy': 0.1})
    # A teammate cannot take over a session that is neither theirs nor on their runner.
    await service.lease(OWNER, 'conv:agent', device_id=device)
    with pytest.raises(DeviceError, match='Only the runner owner'):
        await service.start_takeover('teammate@example.com', device)
    await service.start_takeover(OWNER, device)
    for op, args in (('install', {}), ('tap', {'fx': 2, 'fy': 0}), ('tap', {'x': 1, 'y': 1}), ('type', {'text': ''})):
        with pytest.raises(DeviceError):
            await service.takeover_input(OWNER, device, op, args)
    # If the agent releases while a person holds the device, the person keeps it and nothing is shut down.
    result = await service.release(OWNER, 'conv:agent', device)
    lease = await db.device_leases.find_one({'_id': device})
    assert 'stays with them' in result['note'] and lease['scope'] == 'takeover:' + OWNER


@pytest.mark.asyncio
async def test_screen_is_rate_limited_per_viewer(env, monkeypatch):
    _, make, device = env
    service, _ = make()
    monkeypatch.setattr(service_module, 'SCREEN_MIN_INTERVAL', 60)
    service_module._LAST_FRAME.clear()
    assert (await service.screen(OWNER, device))[:4] == b'\x89PNG'
    with pytest.raises(DeviceError, match='slow down'):
        await service.screen(OWNER, device)
    with pytest.raises(DeviceError, match='not shared'):
        await service.screen('stranger@example.com', device)


@web.middleware
async def fake_identity(request, handler):
    request['user_email'] = request.headers.get('X-Test-User', '')
    return await handler(request)


@pytest.mark.asyncio
async def test_routes(env, monkeypatch):
    db, make, device = env
    service, hub = make()
    monkeypatch.setattr('api.device_routes.DeviceService', lambda db: service)
    app = web.Application(middlewares=[fake_identity])
    setup_device_routes(app)
    with patch('api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession(headers={'X-Test-User': OWNER}) as http:
                async with http.get(server.make_url('/api/devices/screen'), params={'device_id': device}) as resp:
                    assert resp.status == 200 and resp.content_type == 'image/png'
                async with http.post(server.make_url('/api/devices/takeover'),
                                     json={'device_id': device, 'action': 'start'}) as resp:
                    assert (await resp.json())['held'] is True
                async with http.post(server.make_url('/api/devices/takeover'), json={
                        'device_id': device, 'action': 'input', 'op': 'key', 'args': {'key': 'back'}}) as resp:
                    assert resp.status == 200 and hub.calls[-1] == ('key', {'key': 'back'})
                async with http.post(server.make_url('/api/devices/takeover'),
                                     json={'device_id': device, 'action': 'shell'}) as resp:
                    assert resp.status == 400
        finally:
            await server.close()
