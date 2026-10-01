"""Loma Devices: boot devices from runner templates, clean state, shutdown on release and when idle."""
import json
import sys

import pytest
from mongomock_motor import AsyncMongoMockClient

from device_loader.runner import loma_device_runner as ldr
from device_loader.backend import store
from device_loader.backend.gateway import DeviceTools, TOOLS
from device_loader.backend.hub import DeviceError
from device_loader.backend.service import DeviceService
from isolation.protocol import RunAuthority

OWNER = 'owner@example.com'

FAKE_ADB = r'''#!{python}
import os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_ADB_LOG'], 'a') as log:
    log.write(repr(args) + '\n')
if args[:2] == ['devices', '-l']:
    print('List of devices attached')
elif args[2:] == ['shell', 'getprop', 'sys.boot_completed']:
    print('1')
'''

FAKE_EMULATOR = r'''#!{python}
import os, sys, time
with open(os.environ['FAKE_EMU_LOG'], 'a') as log:
    log.write(repr(sys.argv[1:]) + '\n')
if sys.argv[1:] == ['-list-avds']:
    print('Pixel_7_API_34')
    sys.exit(0)
time.sleep(3)
'''


@pytest.fixture
def android(tmp_path, monkeypatch):
    adb = tmp_path / 'adb'
    adb.write_text(FAKE_ADB.replace('{python}', sys.executable))
    adb.chmod(0o755)
    emulator = tmp_path / 'sdk' / 'emulator' / 'emulator'
    emulator.parent.mkdir(parents=True)
    emulator.write_text(FAKE_EMULATOR.replace('{python}', sys.executable))
    emulator.chmod(0o755)
    monkeypatch.setenv('ANDROID_HOME', str(tmp_path / 'sdk'))
    monkeypatch.setenv('FAKE_ADB_LOG', str(tmp_path / 'adb.log'))
    monkeypatch.setenv('FAKE_EMU_LOG', str(tmp_path / 'emu.log'))
    monkeypatch.setattr(ldr, 'CONFIG_DIR', tmp_path / 'home')
    monkeypatch.setattr(ldr, 'port_in_use', lambda port: False)

    def log(name):
        path = tmp_path / name
        return [eval(line) for line in path.read_text().splitlines()] if path.exists() else []
    return ldr.Android(str(adb)), log, tmp_path


TEMPLATES = [{'name': 'pixel-34', 'platform': 'android', 'avd': 'Pixel_7_API_34', 'snapshot': 'clean', 'headless': True},
             {'name': 'raw', 'platform': 'android', 'avd': 'Pixel_7_API_34'},
             {'name': 'iphone', 'platform': 'ios', 'simulator': 'Loma iPhone 15'}]


def test_templates_come_only_from_valid_config_entries(capsys):
    templates = ldr.load_templates({'templates': TEMPLATES + [
        {'name': 'bad name', 'platform': 'android', 'avd': 'x'}, {'name': 'x', 'platform': 'web', 'avd': 'x'},
        {'name': 'y', 'platform': 'android', 'avd': '-wipe-data'}, {'name': 'z', 'platform': 'ios', 'simulator': '-x'},
        'nope']})
    assert sorted(templates) == ['iphone', 'pixel-34', 'raw']
    assert templates['pixel-34']['headless'] and templates['raw']['snapshot'] is None
    assert 'Ignoring invalid device template' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_android_clean_boot_uses_the_snapshot_read_only_then_shuts_down(android, tmp_path):
    driver, log, _ = android
    runner = ldr.Runner({'policy': {}, 'templates': TEMPLATES, 'state_dir': str(tmp_path / 'state')},
                        drivers=[driver])
    assert 'lifecycle' in runner.capabilities()
    assert runner.template_list()[0] == {'name': 'pixel-34', 'platform': 'android', 'clean': True}
    result = await runner.call('boot', ldr.RUNNER_DEVICE, {'template': 'pixel-34', 'clean': True})
    assert result == {'serial': 'emulator-5554', 'booted': True, 'template': 'pixel-34', 'clean': True}
    argv = [a for a in log('emu.log') if a != ['-list-avds']][0]
    assert argv == ['-avd', 'Pixel_7_API_34', '-port', '5554', '-no-boot-anim', '-no-audio', '-no-window',
                    '-snapshot', 'clean', '-no-snapshot-save', '-read-only']
    assert json.loads((tmp_path / 'state' / 'booted.json').read_text())['emulator-5554']['template'] == 'pixel-34'
    # A restarted runner still knows it booted this device.
    again = ldr.Runner({'policy': {}, 'templates': TEMPLATES, 'state_dir': str(tmp_path / 'state')}, drivers=[driver])
    assert 'emulator-5554' in again.booted

    runner.inventory['emulator-5554'] = (driver, {'serial': 'emulator-5554'})
    await runner.shutdown(driver, 'emulator-5554')
    assert ['-s', 'emulator-5554', 'emu', 'kill'] in log('adb.log') and runner.booted == {}


@pytest.mark.asyncio
async def test_boot_refuses_unknown_templates_and_clean_without_snapshot(android, tmp_path):
    driver, _, _ = android
    runner = ldr.Runner({'policy': {}, 'templates': TEMPLATES, 'state_dir': str(tmp_path)}, drivers=[driver])
    with pytest.raises(ldr.OpError, match='No device template'):
        await runner.call('boot', ldr.RUNNER_DEVICE, {'template': 'Pixel_7_API_34'})
    with pytest.raises(ldr.OpError, match='no clean snapshot'):
        await runner.call('boot', ldr.RUNNER_DEVICE, {'template': 'raw', 'clean': True})
    with pytest.raises(ldr.OpError, match='runner-level'):
        await runner.call('boot', 'emulator-5554', {'template': 'raw'})
    with pytest.raises(ldr.OpError, match='cannot run ios'):
        await runner.call('boot', ldr.RUNNER_DEVICE, {'template': 'iphone'})


@pytest.mark.asyncio
async def test_only_devices_the_runner_booted_can_be_shut_down_and_idle_ones_are_reaped(android, tmp_path):
    driver, log, _ = android
    runner = ldr.Runner({'policy': {}, 'templates': TEMPLATES, 'state_dir': str(tmp_path)}, drivers=[driver])
    runner.inventory['emulator-5556'] = (driver, {'serial': 'emulator-5556'})
    with pytest.raises(ldr.OpError, match='Only devices this runner booted'):
        await runner.call('shutdown', 'emulator-5556', {})
    runner.booted = {'emulator-5558': {'template': 'pixel-34', 'platform': 'android', 'last_used': 0},
                     'emulator-5560': {'template': 'pixel-34', 'platform': 'android', 'last_used': ldr.time.time()}}
    await runner.reap_idle()
    assert list(runner.booted) == ['emulator-5560']
    assert ['-s', 'emulator-5558', 'emu', 'kill'] in log('adb.log')


@pytest.mark.asyncio
async def test_ios_clean_boot_clones_and_deletes_the_clone(monkeypatch, tmp_path):
    seen = []

    async def fake_run(argv, **kwargs):
        seen.append(argv)
        if argv[:4] == ['xcrun', 'simctl', 'list', 'devices']:
            return 0, json.dumps({'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-17-5': [
                {'udid': 'BASE-UDID', 'name': 'Loma iPhone 15', 'state': 'Shutdown'}]}}).encode(), ''
        if argv[:3] == ['xcrun', 'simctl', 'clone']:
            return 0, b'CLONE-UDID\n', ''
        return 0, b'', ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    template = ldr.load_templates({'templates': TEMPLATES})['iphone']
    ios = ldr.IOS()
    serial, info = await ios.boot(template, True, tmp_path)
    assert serial == 'CLONE-UDID' and info == {'clone': True}
    assert seen[1][:4] == ['xcrun', 'simctl', 'clone', 'BASE-UDID']
    assert ['xcrun', 'simctl', 'boot', 'CLONE-UDID'] in seen and ['open', '-a', 'Simulator'] in seen
    seen.clear()
    await ios.shutdown('CLONE-UDID', info)
    assert seen == [['xcrun', 'simctl', 'shutdown', 'CLONE-UDID'], ['xcrun', 'simctl', 'delete', 'CLONE-UDID']]


# ── Backend: lease boots, release shuts down ──────────────────────────────


class Conn:
    def __init__(self, version='1.2.0', devices=None, templates=None):
        self.version, self.devices = version, devices or []
        self.templates = templates if templates is not None else [
            {'name': 'pixel-34', 'platform': 'android', 'clean': True},
            {'name': 'raw', 'platform': 'android', 'clean': False}]


class Hub:
    def __init__(self):
        self.conns, self.calls = {}, []

    def get(self, runner_id):
        return self.conns.get(runner_id)

    async def call(self, runner_id, op, serial, args):
        self.calls.append((op, serial, args))
        if op == 'boot':
            serial = f'emulator-{5554 + 2 * sum(1 for c in self.calls if c[0] == "boot")}'
            self.conns[runner_id].devices.append({'serial': serial, 'platform': 'android', 'name': 'Pixel'})
            return {'serial': serial, 'booted': True, 'template': args['template'], 'clean': args['clean']}
        return {}


@pytest.fixture
def db():
    return AsyncMongoMockClient()['loma_devices_lifecycle']


async def setup(db, conn):
    token, _ = await store.create_enrollment(db, OWNER, 'Mac mini')
    runner_id = (await store.redeem_enrollment(db, token, {'name': 'Mac mini'}))['runner_id']
    hub = Hub()
    hub.conns[runner_id] = conn
    return DeviceService(db, hub=hub), hub, runner_id


@pytest.mark.asyncio
async def test_lease_with_template_boots_clean_and_release_shuts_down(db):
    service, hub, runner_id = await setup(db, Conn())
    lease = await service.lease(OWNER, 'conv:1', template='pixel-34', clean=True)
    assert lease['device_id'] == f'{runner_id}/emulator-5556' and lease['booted'] and lease['clean']
    assert hub.calls[0] == ('boot', '-', {'template': 'pixel-34', 'clean': True})
    await service.call(OWNER, 'conv:1', lease['device_id'], 'configure', {'dark_mode': True})
    result = await service.release(OWNER, 'conv:1', lease['device_id'])
    assert result == {'released': True, 'shutdown': True}  # clean device: no restore needed, it is discarded
    assert hub.calls[-1] == ('shutdown', 'emulator-5556', {})
    ops = [row['op'] for row in await db.device_audit.find({}).to_list(20)]
    assert ops[:2] == ['boot', 'lease'] and ops[-2:] == ['shutdown', 'release']


@pytest.mark.asyncio
async def test_lease_boots_automatically_when_nothing_is_running_or_everything_is_taken(db):
    service, hub, runner_id = await setup(db, Conn())
    first = await service.lease(OWNER, 'conv:a', platform='android')
    assert first['booted'] and first['clean']  # auto-boot uses the template's clean state when it has one
    second = await service.lease(OWNER, 'conv:b', platform='android')
    assert second['device_id'] != first['device_id'] and [c[0] for c in hub.calls] == ['boot', 'boot']


@pytest.mark.asyncio
async def test_template_errors_are_clear(db):
    service, hub, _ = await setup(db, Conn())
    with pytest.raises(DeviceError, match="available: \\['pixel-34', 'raw'\\]"):
        await service.lease(OWNER, 'conv:1', template='nope')
    with pytest.raises(DeviceError, match='No clean-capable device template'):
        await service.lease(OWNER, 'conv:1', template='raw', clean=True)
    with pytest.raises(DeviceError, match='cannot be combined'):
        await service.lease(OWNER, 'conv:1', device_id='r_0123456789abcdef/x', template='raw')
    with pytest.raises(DeviceError, match='Invalid template'):
        await service.lease(OWNER, 'conv:1', template='-avd x')
    old, _, _ = await setup(AsyncMongoMockClient()['old'], Conn(version='1.1.0'))
    with pytest.raises(DeviceError, match=r'boot .*>= 1\.2\.0'):
        await old.lease(OWNER, 'conv:1', template='raw')


@pytest.mark.asyncio
async def test_agent_lists_templates_and_leases_with_them(db):
    service, hub, _ = await setup(db, Conn())
    auth = RunAuthority('run1', OWNER, frozenset(TOOLS))
    tools = DeviceTools(db, auth, 'conv-7', service=service)
    listing = await tools(auth, 'device.list', {})
    assert [t['template'] for t in listing['templates']] == ['pixel-34', 'raw']
    lease = await tools(auth, 'device.lease', {'template': 'pixel-34', 'clean': True})
    assert lease['booted'] is True and lease['template'] == 'pixel-34'
