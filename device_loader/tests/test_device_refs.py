"""Element refs (tap by e3), the Android animations switch, and the build-repo allowlist setting."""

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from device_loader.runner import loma_device_runner as ldr
from device_loader.backend import builds, service as service_mod, store
from device_loader.backend.builds import BlobStore
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import DeviceService, _validate, compact_tree
from device_loader.cli import device as device_cli

OWNER = 'owner@example.com'
TREE = {'units': 'pixels', 'screen': [1080, 2400], 'elements': [
    {'type': 'TextView', 'text': 'Welcome'},
    {'type': 'EditText', 'id': 'app:id/endpoint', 'clickable': True, 'center': [540, 600]},
    {'type': 'Button', 'text': 'Save', 'clickable': True, 'center': [540, 1800]},
]}


class TreeHub(RunnerHub):
    def __init__(self, online):
        super().__init__()
        self.online, self.calls = set(online), []

    version = None  # set by tests: the runner version reported in the hello

    def get(self, runner_id):
        return type('C', (), {'devices': [{'serial': 'emulator-5554', 'platform': 'android'}],
                              'version': self.version})()

    async def call(self, runner_id, op, serial, args):
        self.calls.append((op, dict(args)))
        return {k: v for k, v in TREE.items()} if op == 'ui_tree' else {'ok': True}


@pytest_asyncio.fixture
async def setup():
    service_mod._REFS.clear()
    db = AsyncMongoMockClient()['loma_refs_test']
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    runner = await store.redeem_enrollment(db, token, {})
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {
        'devices': [{'serial': 'emulator-5554', 'platform': 'android'}]}})
    hub = TreeHub({runner['runner_id']})
    return DeviceService(db, hub=hub, blobs=BlobStore()), hub, store.device_id(runner['runner_id'], 'emulator-5554')


def test_compact_tree_numbers_elements_after_filtering():
    out = compact_tree(TREE, compact=True, clickable_only=True)
    assert out['tree'].splitlines() == ["e1 EditText #endpoint @540,600 *", "e2 Button 'Save' @540,1800 *"]
    assert out['_refs'] == {'e1': [540, 600], 'e2': [540, 1800]}
    assert compact_tree(TREE)['elements'][2]['ref'] == 'e3'


@pytest.mark.parametrize('op,args', [
    ('tap', {}), ('tap', {'ref': 'e1', 'x': 1, 'y': 2}), ('tap', {'ref': 'x1'}), ('tap', {'ref': 'e0'}),
    ('set_text', {'text': 'a', 'ref': 'e1', 'match': 'Save'}), ('animations', {}), ('animations', {'enabled': 1}),
])
def test_ref_and_animations_validation(op, args):
    with pytest.raises(DeviceError):
        _validate(op, args)


@pytest.mark.asyncio
async def test_tap_by_ref_resolves_on_the_backend(setup):
    service, hub, device = setup
    with pytest.raises(DeviceError, match='unknown or expired'):
        await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e2'})
    reply = await service.call(OWNER, 'conv-1', device, 'ui_tree', {'compact': True})
    assert '_refs' not in reply and reply['tree'].startswith('e1 TextView')
    await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e3'})
    assert hub.calls[-1] == ('tap', {'x': 540, 'y': 1800})  # the runner only ever sees coordinates
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {'compact': True})  # the tap reset the refs
    with pytest.raises(DeviceError, match='No element e9'):
        await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e9'})
    # set_text by ref: focus the field, then edit what is focused (no match sent to the runner).
    await service.call(OWNER, 'conv-1', device, 'set_text', {'ref': 'e2', 'text': 'https://x.test'})
    assert hub.calls[-2:] == [('tap', {'x': 540, 'y': 600}), ('set_text', {'text': 'https://x.test'})]


@pytest.mark.asyncio
async def test_refs_expire_and_reset_after_screen_changes(setup, monkeypatch):
    service, hub, device = setup
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
    await service.call(OWNER, 'conv-1', device, 'swipe', {'x1': 1, 'y1': 900, 'x2': 1, 'y2': 100})
    with pytest.raises(DeviceError, match='unknown or expired'):
        await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e1'})
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
    clock = service_mod.time.monotonic() + service_mod.REF_TTL + 1
    monkeypatch.setattr(service_mod.time, 'monotonic', lambda: clock)
    with pytest.raises(DeviceError, match='unknown or expired'):
        await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e1'})


@pytest.mark.asyncio
@pytest.mark.parametrize('op,args', [
    ('tap', {'x': 1, 'y': 1}), ('tap_text', {'match': 'Save'}), ('key', {'key': 'back'}),
    ('set_text', {'text': 'a'}), ('clear_text', {}), ('type', {'text': 'a'}),
    ('scroll_until_visible', {'match': 'Save'}),
])
async def test_input_ops_reset_refs(setup, op, args):
    service, hub, device = setup
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
    await service.call(OWNER, 'conv-1', device, op, args)
    with pytest.raises(DeviceError, match='unknown or expired'):
        await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e1'})


@pytest.mark.asyncio
async def test_failed_install_or_launch_still_resets_refs(setup, monkeypatch):
    service, hub, device = setup

    async def failing(runner_id, op, serial, args):
        if op == 'ui_tree':
            return dict(TREE)
        raise DeviceError('launch failed')
    for op, args in (('launch', {'app_id': 'com.example'}), ('uninstall', {'app_id': 'com.example'})):
        monkeypatch.setattr(hub, 'call', failing)
        await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
        with pytest.raises(DeviceError, match='launch failed'):
            await service.call(OWNER, 'conv-1', device, op, args)
        with pytest.raises(DeviceError, match='unknown or expired'):
            await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e1'})


@pytest.mark.asyncio
async def test_refs_are_per_conversation(setup):
    service, hub, device = setup
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
    await service.release(OWNER, 'conv-1', device)
    with pytest.raises(DeviceError, match='unknown or expired'):
        await service.call(OWNER, 'conv-2', device, 'tap', {'ref': 'e1'})


def test_cli_ref_and_animations_bodies():
    base = ['--user-email', 'u', '--auth-token', 't', '--scope', 'c1']
    body = device_cli.build_body(device_cli.parser().parse_args(base + ['tap', '--device-id', 'd', '--ref', 'e3']))
    assert body['op'] == 'tap' and body['args'] == {'ref': 'e3'}
    body = device_cli.build_body(device_cli.parser().parse_args(base + ['animations', '--device-id', 'd', '--off']))
    assert body['op'] == 'animations' and body['args'] == {'enabled': False}
    body = device_cli.build_body(device_cli.parser().parse_args(
        base + ['set-text', '--device-id', 'd', '--ref', 'e2', '--text', 'hi']))
    assert body['args'] == {'text': 'hi', 'ref': 'e2'}
    with pytest.raises(SystemExit):
        device_cli.build_body(device_cli.parser().parse_args(base + ['tap', '--device-id', 'd', '--x', '1']))


@pytest.mark.asyncio
async def test_runner_animations_switch(monkeypatch):
    seen = []

    async def fake_run(argv, timeout=None, check=True):
        seen.append(argv)
        return 0, b'', ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    assert await ldr.Android().animations('emulator-5554', False) == {'animations': False}
    script = seen[-1][-1]
    assert script.count('settings put global') == 3 and script.endswith('animator_duration_scale 0')
    with pytest.raises(ldr.OpError, match='Android-only'):
        await ldr.IOS().animations('SIM', False)


@pytest.mark.asyncio
async def test_dispatch_workflow_must_be_allowlisted(monkeypatch):
    monkeypatch.setenv('LOMA_DEVICE_BUILD_REPOS', 'org/app')
    monkeypatch.setenv('GITHUB_API_KEY', 'token')
    monkeypatch.delenv('LOMA_DEVICE_BUILD_WORKFLOWS', raising=False)
    monkeypatch.setattr(builds, 'read_saved_settings', lambda: {})
    monkeypatch.setattr(builds, '_settings', {})
    fetched = []

    async def fake_fetch(self, *args):
        fetched.append(args)
        return 'b_x', {}
    monkeypatch.setattr(BlobStore, '_from_github', fake_fetch)
    # Empty allowlist: dispatching is denied, and the message says how to allow it.
    with pytest.raises(DeviceError, match='LOMA_DEVICE_BUILD_WORKFLOWS'):
        await BlobStore().from_github(OWNER, 'org/app', 'apk', pr=1, dispatch_workflow='deploy.yml')
    monkeypatch.setenv('LOMA_DEVICE_BUILD_WORKFLOWS', 'build.yml')
    monkeypatch.setattr(builds, '_settings', {})
    with pytest.raises(DeviceError, match=r'not allowed.*allowed: build.yml'):
        await BlobStore().from_github(OWNER, 'org/app', 'apk', pr=1, dispatch_workflow='deploy.yml')
    assert not fetched
    await BlobStore().from_github(OWNER, 'org/app', 'apk', pr=1, dispatch_workflow='build.yml')
    # No dispatch requested: the workflow allowlist does not apply.
    await BlobStore().from_github(OWNER, 'org/app', 'apk', pr=1)
    assert len(fetched) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('op,args', [
    ('launch', {'app_id': 'com.example', 'extras': {'endpoint': 'x'}}),
    ('launch', {'app_id': 'com.example', 'bool_extras': {'test_mode': True}}),
    ('launch', {'app_id': 'com.example', 'activity': '.Main'}),
    ('install', {'upload_id': 'u1', 'app_id': 'com.example', 'grant_appops': ['SCHEDULE_EXACT_ALARM']}),
    ('install', {'upload_id': 'u1', 'grant_privacy': ['photos']}),
    ('tap_text', {'match': 'Save'}), ('record', {'duration_s': 3}), ('animations', {'enabled': False}),
    ('logs', {'source': 'console'}),
])
async def test_old_runner_is_told_to_update(setup, op, args):
    service, hub, device = setup
    hub.version = '1.0.0'
    with pytest.raises(DeviceError, match=r'Runner too old .*runner 1\.0\.0.*>= 1\.1\.0'):
        await service.call(OWNER, 'conv-1', device, op, args)
    assert hub.calls == []  # refused before anything reached the runner


@pytest.mark.asyncio
async def test_old_runner_still_does_the_basics_and_new_runner_does_everything(setup):
    service, hub, device = setup
    hub.version = '1.0.0'
    await service.call(OWNER, 'conv-1', device, 'launch', {'app_id': 'com.example'})
    await service.call(OWNER, 'conv-1', device, 'tap', {'x': 1, 'y': 2})
    hub.version = ldr.VERSION
    await service.call(OWNER, 'conv-1', device, 'launch', {'app_id': 'com.example', 'activity': '.Main'})
    await service.call(OWNER, 'conv-1', device, 'tap_text', {'match': 'Save'})
    assert [op for op, _ in hub.calls] == ['launch', 'tap', 'launch', 'tap_text']


@pytest.mark.asyncio
async def test_hub_records_the_runner_version():
    class WS:
        closed = False
    conn = await RunnerHub().attach('r1', WS(), [], '1.1.0')
    assert conn.version == '1.1.0'
