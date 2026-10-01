"""Loma Devices verification: network capture, SDK analytics check, visual check."""
import json
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from mongomock_motor import AsyncMongoMockClient

from device_loader.runner import loma_device_runner as ldr
from device_loader.backend import store, verify
from device_loader.backend.gateway import DeviceTools, TOOLS
from device_loader.backend.hub import DeviceError
from device_loader.backend.service import DeviceService, _validate
from isolation.catalog import CATALOG
from isolation.protocol import RunAuthority

from device_loader.tests.test_devices_agent import FakeArtifacts  # noqa: E402

OWNER = 'owner@example.com'

FAKE_ADB = r'''#!{python}
import os, sys
with open(os.environ['FAKE_ADB_LOG'], 'a') as log:
    log.write(repr(sys.argv[1:]) + '\n')
'''

FAKE_MITMDUMP = r'''#!{python}
import json, os, socket, sys, time
port = int(sys.argv[sys.argv.index('--listen-port') + 1])
assert sys.argv[sys.argv.index('--listen-host') + 1] == '127.0.0.1'
server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('127.0.0.1', port)); server.listen(5)
with open(os.environ['LOMA_NETCAP_OUT'], 'a') as out:
    for entry in ({'method': 'POST', 'url': 'https://api.example.com/sdk/init', 'status': 200},
                  {'method': 'POST', 'url': 'https://api.example.com/sdk/campaign/trigger', 'status': 200},
                  {'method': 'GET', 'url': 'https://cdn.example.com/img.png', 'status': 200},
                  {'tls_failed': True, 'host': 'pinned.example.com'}):
        out.write(json.dumps(entry) + '\n')
time.sleep(30)
'''


def script(path, body):
    path.write_text(body.replace('{python}', sys.executable))
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def netcap_runner(tmp_path, monkeypatch):
    adb = script(tmp_path / 'adb', FAKE_ADB)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    script(bin_dir / 'mitmdump', FAKE_MITMDUMP)
    monkeypatch.setenv('PATH', f"{bin_dir}:{ldr.os.environ['PATH']}")
    monkeypatch.setenv('FAKE_ADB_LOG', str(tmp_path / 'adb.log'))
    driver = ldr.Android(adb)
    runner = ldr.Runner({'policy': {}, 'state_dir': str(tmp_path / 'state')}, drivers=[driver])
    runner.inventory['emulator-5554'] = (driver, {'serial': 'emulator-5554', 'platform': 'android'})

    def adb_calls():
        return [eval(line) for line in (tmp_path / 'adb.log').read_text().splitlines()]
    yield runner, adb_calls
    for capture in runner.captures.values():
        capture['proc'].kill()


# ── Network capture ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_proxies_the_emulator_reads_flows_and_always_clears_the_proxy(netcap_runner):
    runner, adb_calls = netcap_runner
    assert 'netcap' in runner.capabilities()
    started = await runner.call('netcap', 'emulator-5554', {'action': 'start'})
    port = started['port']
    assert ['-s', 'emulator-5554', 'shell', 'settings', 'put', 'global', 'http_proxy', f'10.0.2.2:{port}'] in adb_calls()
    for _ in range(40):
        read = await runner.call('netcap', 'emulator-5554', {'action': 'read', 'filter': '/sdk/'})
        if read['matched'] == 2:
            break
        await ldr.asyncio.sleep(0.05)
    assert [f['url'].rsplit('/', 1)[-1] for f in read['flows']] == ['init', 'trigger']
    assert read['tls_failures'] == ['pinned.example.com'] and read['capturing'] is True
    stopped = await runner.call('netcap', 'emulator-5554', {'action': 'stop'})
    assert stopped['capturing'] is False
    assert adb_calls()[-1] == ['-s', 'emulator-5554', 'shell', 'settings', 'put', 'global', 'http_proxy', ':0']
    assert runner.captures == {}


@pytest.mark.asyncio
async def test_a_restarted_runner_clears_proxies_it_left_behind(netcap_runner):
    runner, adb_calls = netcap_runner
    runner.netcap_dir.mkdir(parents=True)
    (runner.netcap_dir / 'active.json').write_text('["emulator-5554", "gone-device"]')
    await runner.clear_stale_proxies()
    assert ['-s', 'emulator-5554', 'shell', 'settings', 'put', 'global', 'http_proxy', ':0'] in adb_calls()
    assert json.loads((runner.netcap_dir / 'active.json').read_text()) == []


@pytest.mark.asyncio
async def test_capture_is_android_only_and_validated(netcap_runner):
    runner, _ = netcap_runner
    with pytest.raises(ldr.OpError, match='Invalid action'):
        await runner.call('netcap', 'emulator-5554', {'action': 'shell'})
    ios = ldr.IOS()
    with pytest.raises(ldr.OpError, match='Android-only'):
        await runner.netcap(ios, 'UDID', {'action': 'start'})
    with pytest.raises(DeviceError):
        _validate('netcap', {'action': 'delete'})


def test_network_summary_groups_sdk_calls():
    summary = verify.summarize_network({'flows': [
        {'method': 'POST', 'url': 'https://api.example.com/sdk/event/track?x=1', 'status': 200},
        {'method': 'POST', 'url': 'https://api.example.com/sdk/event/track', 'status': 500},
        {'method': 'GET', 'url': 'https://cdn.example.com/a.png', 'status': 200}], 'tls_failures': ['x.com']})
    assert summary['sdk_events'] == [{'endpoint': 'POST /sdk/event/track', 'count': 2,
                                    'statuses': {'200': 1, '500': 1}, 'errors': 0}]
    assert 'tls_note' in summary and len(summary['flows']) == 3


# ── SDK analytics ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def clickhouse(monkeypatch):
    seen = []

    async def handler(request):
        seen.append(dict(request.query))
        assert request.method == 'GET'  # read-only
        assert request.headers['X-ClickHouse-User'] == 'reader'
        sql = request.query['query']
        if 'FROM triggersTable' in sql:
            data = [{'campaign_id': 'flow_1', 'campaign_type': 'FLOW', 'trigger_event': 'app_open',
                     'elimination_reasons': [], 'at': 't'}]
        elif 'FROM flowsTable' in sql:
            data = [{'flow_id': 'flow_1', 'action_type': 'show', 'shown': 1, 'at': 't'}]
        else:
            data = [{'event': 'app_open', 'source': 'F', 'at': 't'}]
        return web.json_response({'data': data})
    app = web.Application()
    app.router.add_get('/', handler)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv('LOMA_DEVICE_CLICKHOUSE_URL', str(server.make_url('')).rstrip('/'))
    monkeypatch.setenv('LOMA_DEVICE_CLICKHOUSE_USER', 'reader')
    monkeypatch.setenv('LOMA_DEVICE_CLICKHOUSE_PASSWORD', 'pw')
    monkeypatch.setenv('LOMA_DEVICE_ANALYTICS_PRODUCTS', 'demo-product')
    yield seen
    await server.close()


@pytest.mark.asyncio
async def test_sdk_events_check_uses_typed_parameters_and_gives_a_verdict(clickhouse):
    result = await verify.sdk_events_activity({'product_id': 'demo-product', 'user_id': "u'1 OR 1=1",
                                             'flow_id': 'flow_1', 'since_s': 600})
    assert result['verdict'] == {'flow_id': 'flow_1', 'triggered': True, 'shown': True, 'clicked': False,
                                 'eliminated': []}
    assert result['events'][0]['event'] == 'app_open'
    for query in clickhouse:  # the user id never enters the SQL text
        assert query['param_u'] == "u'1 OR 1=1" and "OR 1=1" not in query['query']
        assert query['param_p'] == 'demo-product' and query['param_s'] == '600'


@pytest.mark.asyncio
@pytest.mark.parametrize('args,message', [
    ({'product_id': 'prod-customer', 'user_id': 'u'}, 'not allowed'),
    ({'product_id': 'demo-product'}, 'user_id'),
    ({'product_id': 'demo-product', 'user_id': 'u', 'since_s': 5}, 'since_s'),
    ({'product_id': 'demo-product', 'user_id': 'u', 'flow_id': "x' --"}, 'flow_id'),
])
async def test_sdk_events_check_is_limited_to_allowlisted_test_products(clickhouse, args, message):
    with pytest.raises(DeviceError, match=message):
        await verify.sdk_events_activity(args)


# ── Visual check ──────────────────────────────────────────────────────────


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(text, stop_reason='end_turn'):
    response = SimpleNamespace(stop_reason=stop_reason, model='claude-opus-5-5',
                               content=[SimpleNamespace(type='thinking'), SimpleNamespace(type='text', text=text)])
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(response)))


@pytest.mark.asyncio
async def test_visual_check_sends_the_image_and_returns_a_structured_verdict():
    client = fake_client(json.dumps({'passed': False, 'confidence': 'high', 'observed': 'Bottom sheet, CTA cut off',
                                     'issues': ['Claim button clipped by the nav bar']}))
    verdict = await verify.visual_check(b'PNGBYTES', 'bottom sheet with a Claim button', client=client)
    assert verdict['passed'] is False and verdict['issues'] == ['Claim button clipped by the nav bar']
    sent = client.beta.messages.kwargs
    assert sent['model'] == 'claude-opus-5-5' and sent['fallbacks'] == 'default'
    assert sent['output_config']['format']['type'] == 'json_schema'
    assert sent['messages'][0]['content'][0]['source']['media_type'] == 'image/png'


@pytest.mark.asyncio
async def test_visual_check_handles_refusals_and_bad_input():
    with pytest.raises(DeviceError, match='declined'):
        await verify.visual_check(b'P', 'x', client=fake_client('', stop_reason='refusal'))
    with pytest.raises(DeviceError, match='no verdict'):
        await verify.visual_check(b'P', 'x', client=fake_client('not json'))
    with pytest.raises(DeviceError, match='expect'):
        await verify.visual_check(b'P', ' ', client=fake_client('{}'))


# ── Service + agent wiring ────────────────────────────────────────────────


class Hub:
    def __init__(self):
        self.conns, self.calls = {}, []

    def get(self, runner_id):
        return self.conns.get(runner_id)

    async def call(self, runner_id, op, serial, args):
        self.calls.append((op, args))
        if op == 'screenshot':
            import base64
            return {'png_base64': base64.b64encode(b'PNG').decode(), 'width': 1, 'height': 1}
        if op == 'netcap' and args['action'] == 'read':
            return {'flows': [{'method': 'POST', 'url': 'https://api.example.com/sdk/init', 'status': 200}],
                    'matched': 1, 'tls_failures': [], 'capturing': True}
        return {'capturing': args.get('action') == 'start'}


@pytest_asyncio.fixture
async def wired():
    db = AsyncMongoMockClient()['loma_devices_verify']
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    runner_id = (await store.redeem_enrollment(db, token, {}))['runner_id']
    hub = Hub()
    hub.conns[runner_id] = SimpleNamespace(version='1.2.0', devices=[], templates=[])
    return db, DeviceService(db, hub=hub), hub, f'{runner_id}/emulator-5554'


@pytest.mark.asyncio
async def test_release_stops_a_capture_left_running(wired):
    db, service, hub, device = wired
    await service.lease(OWNER, 'conv:1', device_id=device)
    await service.call(OWNER, 'conv:1', device, 'netcap', {'action': 'start'})
    result = await service.release(OWNER, 'conv:1', device)
    assert result['capture_stopped'] is True and hub.calls[-1] == ('netcap', {'action': 'stop'})


@pytest.mark.asyncio
async def test_checks_need_the_device_session(wired, monkeypatch):
    db, service, hub, device = wired
    await service.lease(OWNER, 'conv:other', device_id=device)
    with pytest.raises(DeviceError, match='leased by another session'):
        await service.sdk_events_check(OWNER, 'conv:mine', device, {'product_id': 'p', 'user_id': 'u'})


@pytest.mark.asyncio
async def test_agent_verification_tools(wired, monkeypatch):
    db, service, hub, device = wired
    auth = RunAuthority('run1', OWNER, frozenset(TOOLS))

    async def on_artifact(receipt):
        return {'name': receipt['name'], 'url': '/f'}

    async def fake_judge(png, expect):
        return {'passed': True, 'confidence': 'high', 'observed': expect, 'issues': []}
    monkeypatch.setattr('device_loader.backend.service.judge_screenshot', fake_judge)
    tools = DeviceTools(db, auth, 'c1', artifacts=FakeArtifacts(auth), service=service, on_artifact=on_artifact)
    started = await tools(auth, 'device.configure', {'device_id': device, 'capture_network': True})
    assert started == {'network_capture': {'capturing': True}}
    network = await tools(auth, 'device.observe', {'device_id': device, 'what': 'network', 'filter': '/sdk/'})
    assert network['sdk_events'][0]['endpoint'] == 'POST /sdk/init'
    visual = await tools(auth, 'device.observe', {'device_id': device, 'what': 'visual', 'expect': 'a nudge'})
    assert visual['passed'] is True and visual['screenshot']['name'] == 'device-visual-1.png' and 'png' not in visual
    bad = await tools(auth, 'device.observe', {'device_id': device, 'what': 'sdk_events', 'user_id': 'u'})
    assert bad['ok'] is False and 'missing' in bad['device_error']
    assert len(CATALOG) <= 64
