"""Element refs (tap by e3), the Android animations switch, and the build-repo allowlist setting."""
import sys

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from device_runner import loma_device_runner as ldr
from devices import builds, service as service_mod, store
from devices.builds import BlobStore
from devices.hub import DeviceError, RunnerHub
from devices.service import DeviceService, _validate, compact_tree
from tools import device as device_cli

sys.path.insert(0, 'tests')
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

    def get(self, runner_id):
        return type('C', (), {'devices': [{'serial': 'emulator-5554', 'platform': 'android'}]})()

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


def test_allowlist_merges_env_and_integration_setting(monkeypatch):
    monkeypatch.setenv('LOMA_DEVICE_BUILD_REPOS', 'org/a')
    monkeypatch.setattr(builds, 'get_integration_extra', lambda *a, **k: 'Org/B, org/c\norg/d')
    monkeypatch.setitem(builds._repo_setting, 'at', None)
    assert builds.allowed_repos() == {'org/a', 'org/b', 'org/c', 'org/d'}
    # Cached for REPO_SETTING_TTL: a second read does not hit Mongo again.
    monkeypatch.setattr(builds, 'get_integration_extra', lambda *a, **k: pytest.fail('re-read too soon'))
    assert 'org/b' in builds.allowed_repos()
