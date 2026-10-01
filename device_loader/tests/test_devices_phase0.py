"""Loma Devices phase 0: configure, record/burst for agents, offline notifications, runner self-update, timeline."""
import asyncio
import hashlib
import sys
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from mongomock_motor import AsyncMongoMockClient

from device_loader.api.device_routes import setup_device_routes
from device_loader.runner import loma_device_runner as ldr
from device_loader.backend import service as service_module
from device_loader.backend import store
from device_loader.backend.gateway import DeviceTools, MAX_RECORDINGS, TOOLS
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import DeviceOffline, DeviceService, _check_runner_version, _validate, audit_detail
from isolation.catalog import CATALOG
from isolation.protocol import RunAuthority

from device_loader.tests.test_device_runner import FAKE_ADB, PNG, UI_XML  # noqa: E402
from device_loader.tests.test_devices_agent import FakeArtifacts  # noqa: E402

OWNER = 'owner@example.com'
DEVICE = 'r_0123456789abcdef/emulator-5554'


@pytest.fixture
def db():
    return AsyncMongoMockClient()['loma_devices_phase0']


@pytest.fixture
def adb(tmp_path, monkeypatch):
    script = tmp_path / 'adb'
    script.write_text(FAKE_ADB.replace('{python}', sys.executable))
    script.chmod(0o755)
    (tmp_path / 'shot.png').write_bytes(PNG)
    (tmp_path / 'ui.xml').write_bytes(UI_XML)
    log = tmp_path / 'adb.log'
    monkeypatch.setenv('FAKE_ADB_LOG', str(log))
    monkeypatch.setenv('FAKE_PNG', str(tmp_path / 'shot.png'))
    monkeypatch.setenv('FAKE_UI', str(tmp_path / 'ui.xml'))

    def calls():
        return [eval(line) for line in log.read_text().splitlines()] if log.exists() else []
    return str(script), calls


# ── configure: validation ─────────────────────────────────────────────────


@pytest.mark.parametrize('args', [
    {}, {'reset': True, 'dark_mode': True}, {'locale': 'ar-SA'}, {'grant': ['CAMERA']},
    {'locale': 'arabic!', 'app_id': 'com.a'}, {'timezone': '../etc/passwd'}, {'clock_offset_s': 10 ** 9},
    {'location': {'lat': 91, 'lon': 0}}, {'location': {'lat': 1}}, {'font_scale': 5}, {'font_scale': True},
    {'grant': ['CAMERA; rm -rf /'], 'app_id': 'com.a'}, {'dark_mode': 'yes'}, {'shell': 'id'},
])
def test_configure_rejects_bad_arguments(args):
    with pytest.raises(DeviceError):
        _validate('configure', args)


def test_configure_accepts_a_full_setup():
    _validate('configure', {'app_id': 'com.example.demo', 'locale': 'ar-SA', 'timezone': 'Asia/Dubai',
                            'clock_offset_s': 86400, 'location': {'lat': 25.2, 'lon': 55.27}, 'dark_mode': True,
                            'font_scale': 1.3, 'grant': ['POST_NOTIFICATIONS'], 'revoke': ['android.permission.CAMERA']})
    _validate('configure', {'reset': True})


def test_configure_needs_runner_1_2():
    class Conn:
        version = '1.1.0'
    with pytest.raises(DeviceError, match=r'configure .*>= 1\.2\.0'):
        _check_runner_version(Conn(), 'configure', {'dark_mode': True})
    Conn.version = '1.2.0'
    _check_runner_version(Conn(), 'configure', {'dark_mode': True})
    Conn.version = '1.1.0'
    _check_runner_version(Conn(), 'tap_text', {})  # older ops keep their old minimum


def test_audit_detail_never_records_typed_text_or_url_queries():
    assert audit_detail('set_text', {'text': 'hunter2', 'match': 'Password'}) == {'match': 'Password', 'chars': 7}
    assert audit_detail('open_url', {'url': 'app://offer?token=secret'}) == {'url': 'app://offer'}
    assert audit_detail('configure', {'location': {'lat': 1, 'lon': 2}, 'dark_mode': True}) == {
        'dark_mode': True, 'location': True}
    assert audit_detail('screenshot', {}) is None


# ── configure: runner ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_android_configure_applies_and_reset_restores(adb):
    path, calls = adb
    runner = ldr.Runner({'policy': {}}, drivers=[ldr.Android(path)])
    result = await runner.call('configure', 'emulator-5554', {
        'app_id': 'com.example.demo', 'locale': 'ar_SA', 'timezone': 'Asia/Dubai', 'clock_offset_s': 86400,
        'location': {'lat': 25.2, 'lon': 55.27}, 'dark_mode': True, 'font_scale': 1.3, 'grant': ['CAMERA']})
    assert set(result['applied']) == {'dark_mode', 'font_scale', 'timezone', 'clock_offset_s', 'location',
                                      'locale', 'grant'}
    argv = [c[2:] for c in calls() if c[:2] == ['-s', 'emulator-5554']]
    assert ['shell', 'cmd', 'uimode', 'night', 'yes'] in argv
    assert ['shell', 'settings', 'put', 'system', 'font_scale', '1.30'] in argv
    assert ['shell', 'settings', 'put', 'global', 'auto_time', '0'] in argv
    assert ['shell', 'cmd', 'alarm', 'set-timezone', 'Asia/Dubai'] in argv
    assert ['emu', 'geo', 'fix', '55.270000', '25.200000'] in argv
    assert ['shell', 'cmd', 'locale', 'set-app-locales', 'com.example.demo', '--locales', 'ar-SA'] in argv
    assert ['shell', 'pm', 'grant', 'com.example.demo', 'android.permission.CAMERA'] in argv
    set_time = [a for a in argv if a[:4] == ['shell', 'cmd', 'alarm', 'set-time']][0]
    assert abs(int(set_time[4]) / 1000 - (ldr.time.time() + 86400)) < 60

    before = len(calls())
    reset = await runner.call('configure', 'emulator-5554', {'reset': True})
    assert {'dark_mode', 'font_scale', 'clock_offset_s', 'auto_time', 'timezone'} <= set(reset['restored'])
    restored = [c[2:] for c in calls()[before:]]
    assert ['shell', 'cmd', 'uimode', 'night', 'no'] in restored
    assert ['shell', 'settings', 'delete', 'system', 'font_scale'] in restored  # was unset before
    assert ['shell', 'cmd', 'locale', 'set-app-locales', 'com.example.demo', '--locales', "''"] in restored
    assert await runner.call('configure', 'emulator-5554', {'reset': True}) == {'reset': True, 'restored': []}


@pytest.mark.asyncio
async def test_configure_respects_the_app_allowlist(adb):
    path, _ = adb
    runner = ldr.Runner({'policy': {'allowed_app_ids': ['com.example.demo']}}, drivers=[ldr.Android(path)])
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        await runner.call('configure', 'emulator-5554', {'app_id': 'com.other', 'grant': ['CAMERA']})


@pytest.mark.asyncio
async def test_ios_configure_reports_host_clock_settings_as_unsupported(monkeypatch):
    seen = []

    async def fake_run(argv, **kwargs):
        seen.append(argv)
        return 0, b'light\n' if argv[-1] in ('appearance',) else b'large\n' if argv[-1] == 'content_size' else b'', ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    ios = ldr.IOS()
    saved = {}
    result = await ios.configure('UDID-1', {'timezone': 'Asia/Dubai', 'clock_offset_s': 5, 'dark_mode': True,
                                            'font_scale': 1.3, 'location': {'lat': 1.5, 'lon': 2.5},
                                            'locale': 'ar-SA', 'app_id': 'com.example.demo',
                                            'grant': ['photos', 'CAMERA']}, saved)
    assert set(result['unsupported']) == {'timezone', 'clock_offset_s', 'grant:CAMERA'}
    assert set(result['applied']) == {'dark_mode', 'font_scale', 'location', 'locale', 'grant:photos'}
    assert ['xcrun', 'simctl', 'ui', 'UDID-1', 'appearance', 'dark'] in seen
    assert ['xcrun', 'simctl', 'ui', 'UDID-1', 'content_size', 'extra-extra-large'] in seen
    assert ['xcrun', 'simctl', 'location', 'UDID-1', 'set', '1.500000,2.500000'] in seen
    assert ['xcrun', 'simctl', 'spawn', 'UDID-1', 'defaults', 'write', 'com.example.demo', 'AppleLanguages',
            '-array', 'ar-SA'] in seen
    seen.clear()
    restored = await ios.restore('UDID-1', saved)
    assert set(restored) == {'dark_mode', 'font_scale', 'location', 'locale:com.example.demo'}
    assert ['xcrun', 'simctl', 'ui', 'UDID-1', 'appearance', 'light'] in seen


# ── Leases: wait for a device, notify the owner, restore on release ───────


class FakeConn:
    def __init__(self, version='1.2.0'):
        self.version, self.devices, self.calls = version, [
            {'serial': 'emulator-5554', 'platform': 'android', 'name': 'Pixel', 'os_version': '14'}], []


class FakeHub:
    def __init__(self):
        self.conns, self.calls = {}, []

    def get(self, runner_id):
        return self.conns.get(runner_id)

    async def call(self, runner_id, op, serial, args):
        self.calls.append((op, serial, args))
        return {'applied': ['dark_mode']} if op == 'configure' and not args.get('reset') else {'restored': ['x']}


async def seed_runner(db):
    token, _ = await store.create_enrollment(db, OWNER, 'Work Mac')
    runner = await store.redeem_enrollment(db, token, {'name': 'Work Mac'})
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {'devices': [
        {'serial': 'emulator-5554', 'platform': 'android', 'name': 'Pixel', 'os_version': '14', 'virtual': True}]}})
    return runner['runner_id']


@pytest.mark.asyncio
async def test_offline_lease_notifies_the_owner_once_and_can_wait(db, monkeypatch):
    runner_id = await seed_runner(db)
    hub = FakeHub()
    service = DeviceService(db, hub=hub)
    with pytest.raises(DeviceOffline, match='No online android devices'):
        await service.lease(OWNER, 'conv:abc', platform='android')
    notes = await db.notifications.find({}).to_list(10)
    assert len(notes) == 1 and notes[0]['user_email'] == OWNER and 'Work Mac' in notes[0]['title']
    assert notes[0]['conversation_id'] == 'abc'
    with pytest.raises(DeviceOffline):
        await service.lease(OWNER, 'conv:abc', platform='android')
    assert await db.notifications.count_documents({}) == 1  # rate-limited per runner

    monkeypatch.setattr(service_module, 'WAIT_ONLINE_POLL', 0.01)

    async def come_online():
        await asyncio.sleep(0.05)
        hub.conns[runner_id] = FakeConn()
    asyncio.ensure_future(come_online())
    lease = await service.lease(OWNER, 'conv:abc', platform='android', wait_online_s=5)
    assert lease['device_id'] == f'{runner_id}/emulator-5554'
    with pytest.raises(DeviceError, match='wait_online_s'):
        await service.lease(OWNER, 'conv:abc', platform='android', wait_online_s=9999)


@pytest.mark.asyncio
async def test_release_restores_settings_changed_in_the_session(db):
    runner_id = await seed_runner(db)
    hub = FakeHub()
    hub.conns[runner_id] = FakeConn()
    service = DeviceService(db, hub=hub)
    device = f'{runner_id}/emulator-5554'
    await service.lease(OWNER, 'conv:1', device_id=device)
    await service.call(OWNER, 'conv:1', device, 'configure', {'dark_mode': True})
    assert (await db.device_leases.find_one({'_id': device}))['configured'] is True
    result = await service.release(OWNER, 'conv:1', device)
    assert result == {'released': True, 'settings_restored': True}
    assert hub.calls[-1] == ('configure', 'emulator-5554', {'reset': True})
    # A session that never configured anything does not touch the device on release.
    await service.lease(OWNER, 'conv:2', device_id=device)
    calls = len(hub.calls)
    assert await service.release(OWNER, 'conv:2', device) == {'released': True}
    assert len(hub.calls) == calls
    audit = await db.device_audit.find({'op': 'configure'}).to_list(5)
    assert audit[0]['detail'] == {'dark_mode': True}


# ── Agent tools: configure, record, burst ─────────────────────────────────


class MediaService:
    def __init__(self):
        self.calls = []

    async def call(self, owner, scope, device_id, op, args):
        _validate(op, args)
        self.calls.append((op, args))
        if op == 'record':
            return {'mp4': b'MP4DATA', 'bytes': 7, 'duration_s': args['duration_s']}
        if op == 'burst':
            return {'frames': [{'png': b'P%d' % i, 'at_ms': i * 100} for i in range(args['count'])],
                    'interval_ms': args.get('interval_ms', 500)}
        return {'applied': sorted(args)}

    async def lease(self, owner, scope, device_id=None, platform=None, wait_online_s=0, template=None, clean=False):
        self.calls.append(('lease', wait_online_s))
        return {'device_id': DEVICE}


def test_catalog_stays_within_the_worker_limit():
    names = [t['name'] for t in CATALOG]
    assert 'device.configure' in names and TOOLS <= set(names) and len(names) <= 64


@pytest.mark.asyncio
async def test_agent_gets_recordings_and_bursts_as_user_evidence():
    auth = RunAuthority('run1', OWNER, frozenset(TOOLS))
    artifacts, delivered = FakeArtifacts(auth), []

    async def on_artifact(receipt):
        delivered.append(receipt['name'])
        return {'name': receipt['name'], 'url': '/files/' + receipt['name']}
    service = MediaService()
    tools = DeviceTools(None, auth, 'conv-9', artifacts=artifacts, service=service, on_artifact=on_artifact)
    result = await tools(auth, 'device.observe', {'device_id': DEVICE, 'what': 'record', 'duration_s': 5,
                                                   'app_id': 'com.example.demo'})
    assert result['delivered'] is True and result['file']['name'] == 'device-recording-1.mp4'
    assert 'mp4' not in result and artifacts.ingested[0] == ('device-recording-1.mp4', b'MP4DATA')
    burst = await tools(auth, 'device.observe', {'device_id': DEVICE, 'what': 'burst', 'count': 10})
    assert burst['delivered'] == 8 and burst['frames_not_delivered'] == 2  # frames share the 8-screenshot budget
    for _ in range(MAX_RECORDINGS - 1):
        await tools(auth, 'device.observe', {'device_id': DEVICE, 'what': 'record', 'duration_s': 1})
    limited = await tools(auth, 'device.observe', {'device_id': DEVICE, 'what': 'record', 'duration_s': 1})
    assert limited['ok'] is False and 'Recording limit' in limited['device_error']
    configured = await tools(auth, 'device.configure', {'device_id': DEVICE, 'dark_mode': True})
    assert configured == {'applied': ['dark_mode']} and service.calls[-1] == ('configure', {'dark_mode': True})
    await tools(auth, 'device.lease', {'platform': 'android', 'wait_online_s': 120})
    assert service.calls[-1] == ('lease', 120)


# ── Runner self-update ────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status, data):
        self.status, self.content = status, self

        async def read(limit):
            return data[:limit]
        self.read = read

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, status, data):
        self.status, self.data, self.urls = status, data, []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakeResponse(self.status, self.data)


class FakeWs:
    closed = False

    async def close(self):
        self.closed = True


def new_script(version='9.9.9'):
    return f"VERSION = '{version}'\nprint('runner')\n".encode()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    target = tmp_path / 'loma_device_runner.py'
    target.write_bytes(b"VERSION = '1.2.0'\n")
    monkeypatch.setattr(ldr, 'INSTALLED_SCRIPT', target)
    monkeypatch.setattr(ldr.sys, 'argv', [str(target), 'run'])
    monkeypatch.setenv('LOMA_DEVICE_RUNNER_SERVICE', '1')
    return target


@pytest.mark.asyncio
async def test_self_update_replaces_the_script_and_asks_for_a_restart(installed):
    data = new_script()
    runner = ldr.Runner({'server': 'https://loma.test', 'runner_id': 'r_x', 'secret': 's', 'policy': {}},
                        drivers=[], session=FakeSession(200, data))
    assert 'self_update' in runner.capabilities()
    ws = FakeWs()
    await runner.self_update(ws, {'version': '9.9.9', 'sha256': hashlib.sha256(data).hexdigest()})
    assert installed.read_bytes() == data and runner.exit_code == ldr.UPDATE_EXIT_CODE and ws.closed
    assert runner.session.urls == ['https://loma.test/device-runner/download']


@pytest.mark.asyncio
@pytest.mark.parametrize('frame_version,served,digest_of', [
    ('9.9.9', new_script(), b'something else'),        # checksum mismatch
    ('9.9.9', b'def broken(:\n', None),                 # not Python
    ('9.9.9', new_script('9.9.8'), None),               # declares another version
    ('1.0.0', new_script('1.0.0'), None),               # not newer: ignored
])
async def test_self_update_refuses_bad_downloads(installed, frame_version, served, digest_of):
    runner = ldr.Runner({'server': 'https://loma.test', 'runner_id': 'r_x', 'secret': 's', 'policy': {}},
                        drivers=[], session=FakeSession(200, served))
    await runner.self_update(FakeWs(), {'version': frame_version,
                                        'sha256': hashlib.sha256(digest_of or served).hexdigest()})
    assert installed.read_bytes() == b"VERSION = '1.2.0'\n" and runner.exit_code is None


def test_self_update_needs_the_service_and_can_be_turned_off(installed, monkeypatch):
    assert 'self_update' not in ldr.Runner({'policy': {'auto_update': False}}, drivers=[]).capabilities()
    monkeypatch.delenv('LOMA_DEVICE_RUNNER_SERVICE')
    assert 'self_update' not in ldr.Runner({'policy': {}}, drivers=[]).capabilities()


@web.middleware
async def fake_identity(request, handler):
    request['user_email'] = request.headers.get('X-Test-User', '')
    return await handler(request)


@pytest.mark.asyncio
async def test_server_offers_updates_and_serves_the_timeline(db, monkeypatch):
    test_hub = RunnerHub()
    monkeypatch.setattr('device_loader.api.device_routes.hub', test_hub)
    runner_id = await seed_runner(db)
    token, _ = await store.create_enrollment(db, OWNER, 'Old Mac')
    creds = await store.redeem_enrollment(db, token, {})
    app = web.Application(middlewares=[fake_identity])
    setup_device_routes(app)
    with patch('device_loader.api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        base = str(server.make_url('')).rstrip('/')
        try:
            async with aiohttp.ClientSession() as http:
                headers = {'Authorization': 'Bearer ' + creds['secret'], 'X-Loma-Runner-Id': creds['runner_id']}
                from device_loader.api.device_routes import runner_release
                served_version, served_digest = runner_release()
                assert served_version == ldr.VERSION  # the served script is the one in this repo
                async with http.ws_connect(base + '/device-runner/ws', headers=headers) as ws:
                    await ws.send_json({'type': 'hello', 'version': served_version, 'capabilities': ['self_update'],
                                        'devices': []})
                    with pytest.raises(asyncio.TimeoutError):  # up to date: no offer
                        await asyncio.wait_for(ws.receive_json(), 0.5)
                async with http.ws_connect(base + '/device-runner/ws', headers=headers) as ws:
                    await ws.send_json({'type': 'hello', 'version': '1.1.9', 'capabilities': ['self_update'],
                                        'devices': []})
                    frame = await asyncio.wait_for(ws.receive_json(), 5)
                    assert frame == {'type': 'update', 'version': served_version, 'sha256': served_digest}
                async with http.get(base + '/api/devices', headers={'X-Test-User': OWNER}) as resp:
                    listing = await resp.json()
                old = next(r for r in listing['runners'] if r['runner_id'] == creds['runner_id'])
                assert old['update_available'] is True and old['self_update'] is True

                device = f'{runner_id}/emulator-5554'
                for op, ok in (('lease', True), ('tap', True), ('tap', False)):
                    await db.device_audit.insert_one({'at': store.now(), 'device_id': device, 'actor': OWNER,
                                                      'scope': 'conv:c1', 'op': op, 'ok': ok})
                async with http.get(base + '/api/devices/activity', params={'device_id': device},
                                    headers={'X-Test-User': OWNER}) as resp:
                    timeline = await resp.json()
                assert timeline['sessions'] == [{**timeline['sessions'][0], 'scope': 'conv:c1', 'ops': 3,
                                                 'failures': 1, 'conversation_id': 'c1'}]
                async with http.get(base + '/api/devices/activity', params={'device_id': device},
                                    headers={'X-Test-User': 'stranger@example.com'}) as resp:
                    assert resp.status == 404
        finally:
            await server.close()


def test_cli_configure_and_wait_online():
    from device_loader.cli import device
    p = device.parser()
    base = ['--user-email', OWNER, '--auth-token', 't', '--scope', 'c']
    body = device.build_body(p.parse_args(base + ['configure', '--device-id', DEVICE, '--locale', 'ar-SA',
                                                  '--app-id', 'com.a', '--location', '25.2,55.27', '--dark-mode', 'on',
                                                  '--clock-offset', '86400', '--grant', 'CAMERA']))
    assert body['op'] == 'configure' and body['args'] == {
        'app_id': 'com.a', 'locale': 'ar-SA', 'clock_offset_s': 86400, 'grant': ['CAMERA'], 'dark_mode': True,
        'location': {'lat': 25.2, 'lon': 55.27}}
    assert device.build_body(p.parse_args(base + ['configure', '--device-id', DEVICE, '--reset']))['args'] == {
        'reset': True}
    assert device.build_body(p.parse_args(base + ['lease', '--wait-online', '120']))['wait_online_s'] == 120
