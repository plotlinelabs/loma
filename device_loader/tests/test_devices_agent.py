"""Agent surfaces for Loma Devices: isolated gateway device.* tools and the legacy CLI."""
import argparse
import base64
from unittest.mock import patch

import asyncio

import pytest
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from mongomock_motor import AsyncMongoMockClient

from device_loader.backend.gateway import DeviceTools, TOOLS
from device_loader.backend.hub import DeviceError
from device_loader.backend.service import _validate
from isolation.catalog import CATALOG
from isolation.gateway import ToolGateway, GatewayDenied
from isolation.protocol import RunAuthority

OWNER = 'owner@example.com'
AUTH = RunAuthority('run1', OWNER, frozenset(TOOLS))


class FakeService:
    def __init__(self):
        self.calls = []

    async def list_devices(self, owner):
        return [{'device_id': 'r_0123456789abcdef/emulator-5554', 'owner': owner}]

    async def lease(self, owner, scope, device_id=None, platform=None, template=None, clean=False):
        self.calls.append(('lease', owner, scope, device_id, platform))
        return {'device_id': 'r_0123456789abcdef/emulator-5554'}

    async def templates_for(self, owner):
        return []

    async def release(self, owner, scope, device_id):
        self.calls.append(('release', owner, scope, device_id))
        return {'released': True}

    async def call(self, owner, scope, device_id, op, args):
        _validate(op, args)  # the real service is the single argument validator
        self.calls.append(('call', owner, scope, device_id, op, args))
        if op == 'screenshot':
            return {'png': b'PNGDATA', 'width': 10, 'height': 20}
        if op == 'tap' and args.get('x') == 9999:
            raise DeviceError('Runner is offline')
        return {'ok': op}


class FakeArtifacts:
    def __init__(self, authority, fail=False):
        self.authority, self.ingested, self.fail = authority, [], fail

    def ingest(self, name, data):
        if self.fail:
            raise ValueError('limit')
        self.ingested.append((name, data))
        return {'artifact_id': 'a1', 'name': name, 'size': len(data)}


def test_catalog_has_device_tools_within_limit():
    names = {t['name'] for t in CATALOG}
    assert TOOLS <= names and len(CATALOG) <= 64


@pytest.mark.asyncio
async def test_device_tools_map_to_service_with_backend_scope():
    service = FakeService()
    tools = DeviceTools(None, AUTH, 'conv-42', artifacts=FakeArtifacts(AUTH), service=service)
    device = 'r_0123456789abcdef/emulator-5554'
    assert (await tools(AUTH, 'device.list', {}))['devices'][0]['owner'] == OWNER
    await tools(AUTH, 'device.lease', {'platform': 'android'})
    await tools(AUTH, 'device.input', {'device_id': device, 'action': 'tap', 'x': 1, 'y': 2})
    await tools(AUTH, 'device.input', {'device_id': device, 'action': 'open_url', 'url': 'demo://x'})
    await tools(AUTH, 'device.app', {'device_id': device, 'action': 'launch', 'app_id': 'com.example.demo'})
    await tools(AUTH, 'device.install', {'device_id': device, 'repo': 'example-org/mobile-sdk',
                                         'artifact_name': 'app-native-android', 'pr': 366, 'app_id': 'com.example.demo'})
    await tools(AUTH, 'device.observe', {'device_id': device, 'what': 'logs', 'filter': 'ExampleSDK'})
    assert all(call[2] == 'conv:conv-42' for call in service.calls)
    ops = [(c[4], c[5]) for c in service.calls if c[0] == 'call']
    assert ('tap', {'x': 1, 'y': 2}) in ops and ('open_url', {'url': 'demo://x'}) in ops
    assert ('launch', {'app_id': 'com.example.demo'}) in ops
    assert ('install', {'build': {'repo': 'example-org/mobile-sdk', 'artifact_name': 'app-native-android', 'pr': 366},
                        'app_id': 'com.example.demo'}) in ops
    assert ('logs', {'filter': 'ExampleSDK'}) in ops


@pytest.mark.asyncio
async def test_screenshot_is_registered_for_the_user_not_returned_as_base64():
    artifacts, registered = FakeArtifacts(AUTH), []

    async def on_artifact(receipt):
        registered.append(receipt)
        return {'name': receipt['name'], 'url': '/api/files/worker-a1'}

    tools = DeviceTools(None, AUTH, 'c', artifacts=artifacts, service=FakeService(), on_artifact=on_artifact)
    result = await tools(AUTH, 'device.observe', {'device_id': 'r_0123456789abcdef/e', 'what': 'screenshot'})
    assert result['delivered'] is True and result['file']['url'] == '/api/files/worker-a1'
    assert artifacts.ingested == [('device-screenshot-1.png', b'PNGDATA')] and registered[0]['artifact_id'] == 'a1'
    assert 'PNGDATA' not in str(result) and 'png_base64' not in str(result)
    for _ in range(7):
        await tools(AUTH, 'device.observe', {'device_id': 'r_0123456789abcdef/e', 'what': 'screenshot'})
    limited = await tools(AUTH, 'device.observe', {'device_id': 'r_0123456789abcdef/e', 'what': 'screenshot'})
    assert 'limit' in limited['device_error']


@pytest.mark.asyncio
async def test_large_results_are_capped_and_unexpected_errors_contained():
    from device_loader.backend.gateway import cap_result, MAX_RESULT
    import json as _json
    big = {'lines': ['x' * 1999] * 2000, 'cleared': False}
    capped = cap_result(big)
    assert capped['truncated'] and len(_json.dumps(capped).encode()) <= MAX_RESULT
    assert capped['lines'][-1] == big['lines'][-1]  # newest lines kept

    class Broken(FakeService):
        async def call(self, *a, **k):
            raise RuntimeError('mongo down')

    tools = DeviceTools(None, AUTH, 'c', service=Broken())
    result = await tools(AUTH, 'device.input', {'device_id': 'r_0123456789abcdef/e', 'action': 'tap', 'x': 1, 'y': 1})
    assert 'unexpectedly' in result['device_error']


@pytest.mark.asyncio
async def test_device_failures_do_not_poison_the_worker_broker():
    """A recoverable failure must pass isolation/worker_entry.Broker.rpc and leave it usable."""
    import json as _json
    from isolation.worker_entry import Broker
    tools = DeviceTools(None, AUTH, 'c', service=FakeService())
    failed = await tools(AUTH, 'device.input', {'device_id': 'r_0123456789abcdef/e', 'action': 'shell'})
    reader = asyncio.StreamReader()
    for request_id, result in (('1', failed), ('2', {'devices': []})):
        reader.feed_data((_json.dumps({'type': 'tool_response', 'id': request_id, 'result': result}) + '\n').encode())
    broker = Broker(reader, lambda data: None)
    assert (await broker.rpc('device.input', {}))['ok'] is False
    assert await broker.rpc('device.list', {}) == {'devices': []} and not broker.failed


@pytest.mark.asyncio
async def test_long_operations_report_pending_then_deliver(monkeypatch):
    import device_loader.backend.gateway as gateway
    monkeypatch.setattr(gateway, 'WAIT_SECONDS', 0.05)
    release = asyncio.Event()

    class Slow(FakeService):
        async def call(self, *a, **k):
            await release.wait()
            return {'installed': 'app.apk'}

    tools = DeviceTools(None, AUTH, 'c', service=Slow())
    device = 'r_0123456789abcdef/e'
    install = {'device_id': device, 'repo': 'example-org/mobile-sdk', 'artifact_name': 'app-native-android', 'pr': 1}
    first = await tools(AUTH, 'device.install', install)
    assert first['pending'] is True and 'error' not in first
    busy = await tools(AUTH, 'device.input', {'device_id': device, 'action': 'tap', 'x': 1, 'y': 1})
    assert busy['pending'] is True and 'device.install' in busy['device_error']
    release.set()
    assert await tools(AUTH, 'device.install', install) == {'installed': 'app.apk'}
    assert not tools.pending


@pytest.mark.asyncio
async def test_bad_arguments_and_device_errors_are_returned_not_raised():
    tools = DeviceTools(None, AUTH, 'c', service=FakeService())
    device = 'r_0123456789abcdef/e'
    for tool, args in [('device.input', {'device_id': device, 'action': 'shell'}),
                       ('device.input', {'device_id': device, 'action': 'tap', 'x': 1}),
                       ('device.input', {'device_id': device, 'action': 'tap', 'x': 1, 'y': 1, 'url': 'x'}),
                       ('device.app', {'device_id': device, 'action': 'format', 'app_id': 'a'}),
                       ('device.observe', {'device_id': device, 'what': 'files'}),
                       ('device.release', {}),
                       ('device.input', {'device_id': device, 'action': 'tap', 'x': 9999, 'y': 1})]:
        result = await tools(AUTH, tool, args)
        assert result['ok'] is False and 'device_error' in result and 'error' not in result, (tool, args)
    with pytest.raises(DeviceError):
        await tools(RunAuthority('other', OWNER, frozenset(TOOLS)), 'device.list', {})


@pytest.mark.asyncio
async def test_tool_gateway_routes_device_tools_and_audits():
    events = []

    async def authorize(_):
        return True

    async def audit(_, event):
        events.append(event)

    artifacts = FakeArtifacts(AUTH)
    tools = DeviceTools(None, AUTH, 'c', artifacts=artifacts, service=FakeService())
    gateway = ToolGateway(AUTH, authorize=authorize, audit=audit, artifacts=artifacts, devices=tools)
    assert (await gateway(AUTH, 'device.list', {}))['devices']
    assert [e['stage'] for e in events] == ['requested', 'completed']
    no_devices = ToolGateway(AUTH, authorize=authorize, audit=audit, artifacts=artifacts)
    with pytest.raises(GatewayDenied, match='unavailable'):
        await no_devices(AUTH, 'device.list', {})
    denied = ToolGateway(RunAuthority('run1', OWNER, frozenset()), authorize=authorize, audit=audit,
                         artifacts=FakeArtifacts(RunAuthority('run1', OWNER, frozenset())))
    with pytest.raises(GatewayDenied, match='not allowed'):
        await denied(RunAuthority('run1', OWNER, frozenset()), 'device.list', {})


# ── Legacy CLI ────────────────────────────────────────────────────────────


def test_cli_build_body(tmp_path):
    from device_loader.cli import device
    p = device.parser()
    body = device.build_body(p.parse_args(['--user-email', OWNER, '--auth-token', 't', '--scope', 'conv-1',
                                           'install', '--device-id', 'r_0123456789abcdef/e', '--repo', 'example-org/mobile-sdk',
                                           '--artifact-name', 'app-native-android', '--pr', '366', '--app-id', 'com.example.demo']))
    assert body == {'scope': 'conv:conv-1', 'action': 'call', 'device_id': 'r_0123456789abcdef/e', 'op': 'install',
                    'args': {'app_id': 'com.example.demo',
                             'build': {'repo': 'example-org/mobile-sdk', 'artifact_name': 'app-native-android', 'pr': 366}}}
    flow = tmp_path / 'f.yaml'
    flow.write_text('- launchApp')
    with pytest.raises(SystemExit):
        p.parse_args(['--user-email', OWNER, '--auth-token', 't', 'list'])  # --scope is required
    body = device.build_body(p.parse_args(['--user-email', OWNER, '--auth-token', 't', '--scope', 'cli', 'run-flow',
                                           '--device-id', 'r_0123456789abcdef/e', '--flow-file', str(flow)]))
    assert body['op'] == 'run_flow' and body['args'] == {'flow': '- launchApp'} and body['scope'] == 'conv:cli'
    body = device.build_body(p.parse_args(['--user-email', OWNER, '--auth-token', 't', '--scope', 'c', 'logs',
                                           '--device-id', 'r_0123456789abcdef/e', '--filter', 'ExampleSDK', '--clear']))
    assert body['args'] == {'lines': 300, 'clear': True, 'filter': 'ExampleSDK'}


@pytest.mark.asyncio
async def test_internal_endpoint_accepts_valid_hmac_token(monkeypatch):
    monkeypatch.setenv('OAUTH_ENCRYPTION_KEY', 'test-key')
    from tools._auth_token import create_user_auth_token
    from device_loader.api.device_routes import setup_device_routes
    db = AsyncMongoMockClient()['loma_devices_agent']
    app = web.Application()
    setup_device_routes(app)
    with patch('device_loader.api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession() as http:
                headers = {'X-Loma-User': OWNER, 'X-Loma-Auth-Token': create_user_auth_token(OWNER)}
                async with http.post(server.make_url('/internal/devices/call'), json={'action': 'list'}, headers=headers) as r:
                    assert r.status == 200 and (await r.json()) == {'devices': [], 'templates': []}
                async with http.post(server.make_url('/internal/devices/call'), json={'action': 'lease', 'scope': 'conv:c'}, headers=headers) as r:
                    assert r.status == 409 and 'No devices are registered' in (await r.json())['error']
                # A token for another user does not work for this user.
                bad = {'X-Loma-User': OWNER, 'X-Loma-Auth-Token': create_user_auth_token('x@y.z')}
                async with http.post(server.make_url('/internal/devices/call'), json={'action': 'list'}, headers=bad) as r:
                    assert r.status == 401
        finally:
            await server.close()
