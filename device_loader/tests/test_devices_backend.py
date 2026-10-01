"""Backend tests for Loma Devices: store, service policy, and a real runner over a real WebSocket."""
import asyncio
import sys
from datetime import timedelta
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
import aiohttp
from mongomock_motor import AsyncMongoMockClient

from device_loader.api.device_routes import setup_device_routes
from device_loader.runner import loma_device_runner as ldr
from device_loader.backend import store
from device_loader.backend.builds import BlobStore
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import DeviceService, _validate

from device_loader.tests.test_device_runner import FAKE_ADB, PNG, UI_XML  # noqa: E402

OWNER = 'owner@example.com'


@pytest.fixture
def db():
    return AsyncMongoMockClient()['loma_devices_test']


async def enroll(db, name='Mac'):
    token, _ = await store.create_enrollment(db, OWNER, name)
    return await store.redeem_enrollment(db, token, {'name': name, 'hostname': 'mac'})


# ── Store ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrollment_is_single_use_and_secret_hashed(db):
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    first = await store.redeem_enrollment(db, token, {})
    assert first['runner_id'].startswith('r_') and first['secret'].startswith('ldr_')
    assert await store.redeem_enrollment(db, token, {}) is None
    doc = await db.device_runners.find_one({'runner_id': first['runner_id']})
    assert first['secret'] not in str(doc) and doc['secret_hash'] == store.digest(first['secret'])
    assert (await store.authenticate_runner(db, first['runner_id'], first['secret']))['owner_email'] == OWNER
    assert await store.authenticate_runner(db, first['runner_id'], 'ldr_wrong') is None


@pytest.mark.asyncio
async def test_expired_enrollment_rejected(db):
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    await db.device_enrollments.update_many({}, {'$set': {'expires_at': store.now() - timedelta(minutes=1)}})
    assert await store.redeem_enrollment(db, token, {}) is None


@pytest.mark.asyncio
async def test_revoked_runner_cannot_authenticate(db):
    runner = await enroll(db)
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {'revoked': True}})
    assert await store.authenticate_runner(db, runner['runner_id'], runner['secret']) is None


def test_device_id_parsing():
    assert store.split_device_id('r_0123456789abcdef/emulator-5554') == ('r_0123456789abcdef', 'emulator-5554')
    for bad in ['emulator-5554', 'r_x/emulator', 'r_0123456789abcdef/a b', None, 5]:
        assert store.split_device_id(bad) is None


# ── Validation ────────────────────────────────────────────────────────────


@pytest.mark.parametrize('op,args', [
    ('shell', {}), ('tap', {'x': 1}), ('tap', {'x': 1, 'y': 2, 'z': 3}), ('tap', {'x': 1.5, 'y': 2}),
    ('open_url', {'url': 'no scheme'}), ('launch', {'app_id': '-rf'}), ('key', {'key': 'reboot'}),
    ('install', {}), ('install', {'upload_id': 'b_x', 'build': {'repo': 'a/b', 'artifact_name': 'x'}}),
    ('install', {'build': {'repo': 'a', 'artifact_name': 'x'}}),
    ('install', {'build': {'repo': 'a/b', 'artifact_name': 'x', 'pr': 1, 'run_id': 2}}),
    ('logs', {'clear': 1}),
])
def test_server_side_validation(op, args):
    with pytest.raises(DeviceError):
        _validate(op, args)


# ── Service: ACL + leases (fake hub) ──────────────────────────────────────


class FakeHub(RunnerHub):
    def __init__(self, online):
        super().__init__()
        self.online, self.calls = set(online), []

    def get(self, runner_id):
        return type('C', (), {'devices': [{'serial': 'emulator-5554', 'platform': 'android', 'name': 'Pixel'}]})() \
            if runner_id in self.online else None

    async def call(self, runner_id, op, serial, args):
        if runner_id not in self.online:
            raise DeviceError('Runner is offline')
        self.calls.append((runner_id, op, serial, args))
        return {'ok': True}


@pytest.mark.asyncio
async def test_acl_leases_and_audit(db):
    runner = await enroll(db)
    service = DeviceService(db, hub=FakeHub({runner['runner_id']}), blobs=BlobStore())
    device = store.device_id(runner['runner_id'], 'emulator-5554')
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {
        'devices': [{'serial': 'emulator-5554', 'platform': 'android'}]}})

    with pytest.raises(DeviceError, match='not shared'):
        await service.call('stranger@x.com', 'conv-1', device, 'tap', {'x': 1, 'y': 1})

    lease = await service.lease(OWNER, 'conv-1', platform='android')
    assert lease['device_id'] == device
    await service.call(OWNER, 'conv-1', device, 'tap', {'x': 1, 'y': 1})
    # A second session (another chat) cannot use a leased device...
    with pytest.raises(DeviceError, match='leased by another session'):
        await service.call(OWNER, 'conv-2', device, 'tap', {'x': 1, 'y': 1})
    with pytest.raises(DeviceError, match='leased by another session'):
        await service.lease(OWNER, 'conv-2', platform='android')
    # ...until it is released.
    assert (await service.release(OWNER, 'conv-1', device))['released'] is True
    await service.call(OWNER, 'conv-2', device, 'tap', {'x': 2, 'y': 2})

    # Sharing grants access; expired leases are reclaimable.
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {'shared_with': ['pm@x.com']}})
    await db.device_leases.update_many({}, {'$set': {'expires_at': store.now() - timedelta(seconds=1)}})
    await service.call('pm@x.com', 'conv-3', device, 'tap', {'x': 3, 'y': 3})

    ops = [row['op'] async for row in db.device_audit.find({}, {'op': 1})]
    assert ops.count('tap') == 3 and 'lease' in ops and 'release' in ops


@pytest.mark.asyncio
async def test_offline_and_empty_messages(db):
    service = DeviceService(db, hub=FakeHub(set()), blobs=BlobStore())
    with pytest.raises(DeviceError, match='No devices are registered'):
        await service.lease(OWNER, 'conv-1')
    runner = await enroll(db)
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {
        'devices': [{'serial': 'emulator-5554', 'platform': 'android'}]}})
    with pytest.raises(DeviceError, match='No online'):
        await service.lease(OWNER, 'conv-1', platform='android')


@pytest.mark.asyncio
async def test_github_builds_need_allowlisted_repo(db, monkeypatch):
    monkeypatch.delenv('LOMA_DEVICE_BUILD_REPOS', raising=False)
    with pytest.raises(DeviceError, match='LOMA_DEVICE_BUILD_REPOS'):
        await BlobStore().from_github(OWNER, 'example-org/mobile-sdk', 'app-native-android', pr=366)


# ── End to end: HTTP enroll → WebSocket → runner → fake adb ───────────────


@pytest.fixture
def fake_adb(tmp_path, monkeypatch):
    script = tmp_path / 'adb'
    script.write_text(FAKE_ADB.replace('{python}', sys.executable))
    script.chmod(0o755)
    (tmp_path / 'shot.png').write_bytes(PNG)
    (tmp_path / 'ui.xml').write_bytes(UI_XML)
    monkeypatch.setenv('FAKE_ADB_LOG', str(tmp_path / 'adb.log'))
    monkeypatch.setenv('FAKE_PNG', str(tmp_path / 'shot.png'))
    monkeypatch.setenv('FAKE_UI', str(tmp_path / 'ui.xml'))
    return str(script), tmp_path / 'adb.log'


@web.middleware
async def fake_identity(request, handler):
    request['user_email'] = request.headers.get('X-Test-User', '')
    return await handler(request)


@pytest.mark.asyncio
async def test_end_to_end_runner_over_websocket(db, fake_adb, tmp_path, monkeypatch):
    adb_path, adb_log = fake_adb
    test_hub, test_blobs = RunnerHub(), BlobStore(tmp_path / 'blobs')
    monkeypatch.setattr('device_loader.api.device_routes.hub', test_hub)
    monkeypatch.setattr('device_loader.api.device_routes.blobs', test_blobs)
    app = web.Application(middlewares=[fake_identity])
    setup_device_routes(app)
    with patch('device_loader.api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        base = str(server.make_url('')).rstrip('/')
        try:
            async with aiohttp.ClientSession() as http:
                # Dashboard mints an enrollment token; the runner redeems it.
                async with http.post(base + '/api/devices/enrollments', json={'name': 'Work Mac'},
                                     headers={'X-Test-User': OWNER}) as resp:
                    enrollment = await resp.json()
                assert any(' setup --server ' in c for c in enrollment['commands'])
                async with http.post(base + '/device-runner/enroll', json={'token': enrollment['token']}) as resp:
                    creds = await resp.json()
                async with http.post(base + '/device-runner/enroll', json={'token': enrollment['token']}) as resp:
                    assert resp.status == 401  # single use
                # Bad secret is refused before the upgrade.
                with pytest.raises(aiohttp.WSServerHandshakeError):
                    await http.ws_connect(base + '/device-runner/ws', headers={
                        'Authorization': 'Bearer nope', 'X-Loma-Runner-Id': creds['runner_id']})

                runner = ldr.Runner({'server': base, 'runner_id': creds['runner_id'], 'secret': creds['secret'],
                                     'policy': {}, 'cache_dir': str(tmp_path / 'cache')},
                                    drivers=[ldr.Android(adb_path)], session=http)
                task = asyncio.create_task(runner.connect_once())
                for _ in range(50):
                    if test_hub.get(creds['runner_id']):
                        break
                    await asyncio.sleep(0.05)
                assert test_hub.get(creds['runner_id']) is not None
                assert test_hub.get(creds['runner_id']).version == ldr.VERSION  # recorded from the hello

                service = DeviceService(db, hub=test_hub, blobs=test_blobs)
                devices = await service.list_devices(OWNER)
                assert [d['device_id'] for d in devices] == [f"{creds['runner_id']}/emulator-5554"]
                assert devices[0]['online'] is True
                device = devices[0]['device_id']

                await service.lease(OWNER, 'conv-1', platform='android')
                shot = await service.call(OWNER, 'conv-1', device, 'screenshot', {})
                assert shot['png'] == PNG and shot['width'] == 1080
                tree = await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
                assert tree['elements'][0]['text'] == 'Show modal'
                await service.call(OWNER, 'conv-1', device, 'tap', {'x': 300, 'y': 250})
                with pytest.raises(DeviceError, match='ASCII') as caught:
                    await service.call(OWNER, 'conv-1', device, 'type', {'text': 'héllo'})
                assert caught.value.code == 'unsupported' and caught.value.dispatched == 'no'  # across the wire

                # Ambiguity and settle, end to end: candidates and the post-action diff cross the WebSocket.
                two_buys = (b'<?xml version="1.0"?><hierarchy rotation="0"><node class="android.widget.FrameLayout" '
                            b'bounds="[0,0][1080,2400]"><node class="android.widget.Button" text="Buy" clickable="true" '
                            b'bounds="[0,100][500,200]"/><node class="android.widget.Button" text="Buy" clickable="true" '
                            b'bounds="[0,300][500,400]"/></node></hierarchy>')
                (tmp_path / 'ui-seq-00.xml').write_bytes(two_buys)
                with pytest.raises(DeviceError) as caught:
                    await service.call(OWNER, 'conv-1', device, 'tap_text', {'match': 'Buy'})
                assert caught.value.code == 'ambiguous' and len(caught.value.details['candidates']) == 2
                await service.call(OWNER, 'conv-1', device, 'ui_tree', {'compact': True})
                result = await service.call(OWNER, 'conv-1', device, 'tap_text',
                                            {'match': 'Buy', 'nth': 1, 'settle': True, 'settle_ms': 2000})
                assert result['tapped'] == [250, 150] and result['screen_after']['settled'] is True
                assert result['screen_after']['note'] == 'Nothing on screen changed'
                (tmp_path / 'ui-seq-00.xml').unlink()

                # Install: backend blob → runner downloads with its secret → checksum → adb install.
                apk = tmp_path / 'upload.apk'
                apk.write_bytes(b'fake apk bytes')
                import hashlib
                upload_id = test_blobs.add_file(apk, 'app-debug.apk', OWNER, hashlib.sha256(b'fake apk bytes').hexdigest(), 14)
                result = await service.call(OWNER, 'conv-1', device, 'install',
                                            {'upload_id': upload_id, 'app_id': 'com.example.demo'})
                assert result['installed'] == 'app-debug.apk' and result['build']['size'] == 14
                assert "'install'" in adb_log.read_text()
                # Another user cannot use my upload; another runner cannot fetch my blob.
                with pytest.raises(DeviceError):
                    await service.call('x@y.z', 'c', device, 'install', {'upload_id': upload_id})
                async with http.get(base + f'/device-runner/blobs/{upload_id}',
                                    headers={'Authorization': 'Bearer ' + creds['secret'],
                                             'X-Loma-Runner-Id': 'r_' + '0' * 16}) as resp:
                    assert resp.status == 401

                # Dashboard list, then revoke: the runner is told and exits the connection.
                async with http.get(base + '/api/devices', headers={'X-Test-User': OWNER}) as resp:
                    listing = await resp.json()
                assert listing['runners'][0]['online'] is True and listing['runners'][0]['is_owner'] is True
                async with http.delete(base + f"/api/devices/runners/{creds['runner_id']}",
                                       headers={'X-Test-User': 'someone@else.com'}) as resp:
                    assert resp.status == 404
                async with http.delete(base + f"/api/devices/runners/{creds['runner_id']}",
                                       headers={'X-Test-User': OWNER}) as resp:
                    assert resp.status == 200
                with pytest.raises(ldr.RevokedError):
                    await asyncio.wait_for(task, 5)
                with pytest.raises(DeviceError, match='offline|not shared'):
                    await service.call(OWNER, 'conv-1', device, 'tap', {'x': 1, 'y': 1})
        finally:
            await server.close()


@pytest.mark.asyncio
async def test_runner_download_and_internal_loopback(db):
    app = web.Application()
    setup_device_routes(app)
    with patch('device_loader.api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        base = str(server.make_url('')).rstrip('/')
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get(base + '/device-runner/download') as resp:
                    assert resp.status == 200 and 'Loma Device Runner' in await resp.text()
                async with http.post(base + '/internal/devices/call', json={'action': 'list'},
                                     headers={'X-Loma-User': OWNER, 'X-Loma-Auth-Token': 'bad'}) as resp:
                    assert resp.status == 401
        finally:
            await server.close()


# ── Phase 2 review regressions ────────────────────────────────────────────


def test_blob_reuse_and_multiple_runners(tmp_path):
    import hashlib
    blobs = BlobStore(tmp_path / 'b')
    path = blobs.new_path()
    path.write_bytes(b'x')
    blob_id = blobs.add_file(path, 'a.apk', OWNER, hashlib.sha256(b'x').hexdigest(), 1)
    for _ in range(8):  # the same build installed many times keeps working
        blobs.bind(blob_id, 'r_a')
        assert blobs.open_for_runner(blob_id, 'r_a') is not None
    blobs.bind(blob_id, 'r_b')  # a second runner does not steal access from the first
    assert blobs.open_for_runner(blob_id, 'r_a') and blobs.open_for_runner(blob_id, 'r_b')
    assert blobs.open_for_runner(blob_id, 'r_c') is None
    assert blobs.get(blob_id, owner='someone@else') is None


def test_blob_budget_and_orphan_cleanup(tmp_path, monkeypatch):
    root = tmp_path / 'b'
    root.mkdir()
    (root / 'orphan').write_bytes(b'left over from a previous process')
    blobs = BlobStore(root)
    blobs.new_path()
    assert not (root / 'orphan').exists()
    monkeypatch.setattr('device_loader.backend.builds.MAX_OWNER_BYTES', 10)
    with pytest.raises(DeviceError, match='storage is full'):
        blobs.reserve(OWNER, 11)
    blobs.reserve('other@x.com', 5)


def test_inflight_builds_count_against_the_budget(tmp_path, monkeypatch):
    blobs = BlobStore(tmp_path / 'b')
    monkeypatch.setattr('device_loader.backend.builds.MAX_OWNER_BYTES', 10)
    with blobs.reservation(OWNER, 8):
        with pytest.raises(DeviceError, match='storage is full'):
            blobs.reserve(OWNER, 5)
    blobs.reserve(OWNER, 5)  # released once the transfer finished


@pytest.mark.asyncio
async def test_long_ops_hold_the_lease_for_their_worst_case(db):
    runner = await enroll(db)
    service = DeviceService(db, hub=FakeHub({runner['runner_id']}), blobs=BlobStore())
    device = store.device_id(runner['runner_id'], 'emulator-5554')
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {
        'devices': [{'serial': 'emulator-5554', 'platform': 'android'}]}})
    await service.call(OWNER, 'conv-1', device, 'run_flow', {'flow': '- launchApp'})
    lease = await db.device_leases.find_one({'_id': device})
    assert store.aware(lease['expires_at']) - store.now() > timedelta(minutes=25)


@pytest.mark.asyncio
async def test_github_token_falls_back_to_the_integration(monkeypatch):
    monkeypatch.setenv('LOMA_DEVICE_BUILD_REPOS', 'example-org/mobile-sdk')
    monkeypatch.delenv('GITHUB_API_KEY', raising=False)
    monkeypatch.setattr('device_loader.backend.builds.get_integration_key', lambda provider: '')
    with pytest.raises(DeviceError, match='No GitHub token'):
        await BlobStore().from_github(OWNER, 'example-org/mobile-sdk', 'app', pr=1)
    seen = {}

    async def fake_fetch(self, owner, repo, artifact_name, pr, run_id, headers, *rest):
        seen.update(headers)
        return 'b_x', {}

    monkeypatch.setattr('device_loader.backend.builds.get_integration_key', lambda provider: 'ghp_integration')
    monkeypatch.setattr(BlobStore, '_from_github', fake_fetch)
    await BlobStore().from_github(OWNER, 'example-org/mobile-sdk', 'app', pr=1)
    assert seen['Authorization'] == 'Bearer ghp_integration'


def test_malformed_runner_frames_are_ignored():
    from device_loader.api.device_routes import _clean_devices
    assert _clean_devices({'serial': 'x'}) == [] and _clean_devices(None) == []


def test_sharing_is_case_insensitive():
    runner = {'owner_email': 'Owner@Example.com', 'shared_with': ['pm@x.com']}
    assert store.can_use(runner, 'owner@example.com') and store.can_use(runner, 'PM@x.com')
    assert not store.can_use(runner, 'other@x.com') and not store.can_use(runner, '')


@pytest.mark.asyncio
async def test_concurrent_first_lease_same_holder_does_not_report_busy(db):
    runner = await enroll(db)
    service = DeviceService(db, hub=FakeHub({runner['runner_id']}), blobs=BlobStore())
    device = store.device_id(runner['runner_id'], 'emulator-5554')
    results = await asyncio.gather(*(service._acquire(device, OWNER, 'conv-1') for _ in range(5)))
    assert all(r is not None for r in results)


@pytest.mark.asyncio
async def test_patch_rejects_non_object_and_enroll_command_is_quoted(db):
    app = web.Application(middlewares=[fake_identity])
    setup_device_routes(app)
    with patch('device_loader.api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession() as http:
                headers = {'X-Test-User': OWNER}
                async with http.post(server.make_url('/api/devices/enrollments'),
                                     json={'name': 'x"; $(touch /tmp/pwn) #'}, headers=headers) as r:
                    command = next(c for c in (await r.json())['commands'] if ' setup ' in c)
                import shlex
                assert shlex.split(command)[-1] == 'x"; $(touch /tmp/pwn) #'
                runner = await enroll(db)
                async with http.patch(server.make_url(f"/api/devices/runners/{runner['runner_id']}"),
                                      json='not-an-object', headers=headers) as r:
                    assert r.status == 400
        finally:
            await server.close()
