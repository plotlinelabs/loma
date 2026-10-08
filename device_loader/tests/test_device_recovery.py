"""Emulator / simulator auto-recovery (runner 1.4.0).

Emulators crash, get closed or hang mid-run, and the run then fails with "device not connected".
The runner remembers how to start each device again (AVD name + port, or the simulator UDID), restarts
a recently used one that disappeared, and offers a `recover` op; the backend calls it when a lease or
suite health check fails, and when a device dies under a suite case (the case is then re-run once).
"""
import asyncio

import pytest

from device_loader.api.device_routes import _clean_device
from device_loader.backend.gateway import DeviceTools
from device_loader.backend.hub import DeviceError
from device_loader.backend.service import _check_runner_version, _validate
from device_loader.cli import device as cli
from device_loader.runner import loma_device_runner as ldr
from device_loader.tests.test_device_e2e_hardening import PASS, FakeRun, Suite, suite
from device_loader.tests.test_device_scenario import OWNER, ScenarioDriver
from device_loader.tests.test_devices_agent import AUTH, FakeArtifacts, FakeService

SERIAL = 'emulator-5554'
UDID = 'A1B2C3D4-0000-4000-8000-000000000001'
AVD = {'avd': 'Pixel_7_API_34', 'port': 5554}


class RecoverDriver(ScenarioDriver):
    """A fake emulator that can be 'crashed' (gone from list()) and brought back by recover()."""

    def __init__(self, fail=False):
        super().__init__()
        self.running, self.fail, self.recovered = True, fail, []

    async def list(self):
        return [{'serial': SERIAL, 'platform': self.platform, 'virtual': True, 'name': 'Pixel 7',
                 'os_version': '14'}] if self.running else []

    async def identity(self, serial):
        return dict(AVD)

    async def recover(self, serial, known, restart=False, allow_global=True, extra_args=()):
        self.recovered.append({'serial': serial, 'avd': known.get('avd'), 'restart': restart})
        await asyncio.sleep(0)
        if self.fail:
            raise ldr.OpError('Emulator Pixel_7_API_34 did not boot')
        self.running = True
        return {'method': 'cold_boot', 'steps': ['kill', 'cold_boot']}


def make_runner(driver, **policy):
    return ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r',
                       'policy': {'keep_awake': False, **policy}}, drivers=[driver])


async def settle(runner):
    for task in list(runner.recoveries.values()):
        try:
            await task
        except ldr.OpError:
            pass


async def crash(runner, driver):
    driver.running = False
    await runner.refresh()
    runner.missing_since[SERIAL] -= ldr.RECOVER_GRACE_S + 1


# ── Runner: remember, watch, restart ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_crashed_emulator_that_was_in_use_is_restarted_by_the_watchdog():
    driver = RecoverDriver()
    runner = make_runner(driver)
    listed = await runner.refresh()
    assert listed[0]['state'] == 'ok' and runner.known[SERIAL]['avd'] == 'Pixel_7_API_34'
    await runner.call('health', SERIAL, {})  # marks it as in use
    driver.running = False
    listed = await runner.refresh()
    assert listed == [{'serial': SERIAL, 'platform': 'android', 'virtual': True, 'name': 'Pixel 7',
                       'os_version': '14', 'state': 'down'}]
    runner.watch()
    assert runner.recoveries == {}  # within the grace period: may be a quick reboot
    runner.missing_since[SERIAL] -= ldr.RECOVER_GRACE_S + 1
    runner.watch()
    assert SERIAL in runner.recoveries and runner._listing()[0]['state'] == 'recovering'
    await settle(runner)
    assert driver.recovered == [{'serial': SERIAL, 'avd': 'Pixel_7_API_34', 'restart': False}]
    assert SERIAL in runner.inventory and SERIAL not in runner.recovery_state


@pytest.mark.asyncio
async def test_the_watchdog_leaves_idle_devices_and_stops_a_boot_loop():
    driver = RecoverDriver(fail=True)
    runner = make_runner(driver)
    await runner.refresh()
    await crash(runner, driver)
    runner.watch()
    assert runner.recoveries == {}  # never used by a test: closing it was probably on purpose
    assert await runner.refresh() == []  # and it is not listed as down either
    runner._mark_used(SERIAL)
    for _ in range(ldr.RECOVER_AUTO_MAX + 2):
        runner.watch()
        await settle(runner)
    assert len(driver.recovered) == ldr.RECOVER_AUTO_MAX  # then it stops and reports
    listed = await runner.refresh()
    assert listed[0]['state'] == 'down' and 'did not boot' in listed[0]['error']
    with pytest.raises(ldr.OpError, match='The last restart failed: Emulator Pixel_7_API_34 did not boot'):
        await runner.call('health', SERIAL, {})
    # auto_recover: false turns the watchdog off.
    off_driver = RecoverDriver()
    off = make_runner(off_driver, auto_recover=False)
    await off.refresh()
    off._mark_used(SERIAL)
    await crash(off, off_driver)
    off.watch()
    assert off.recoveries == {}


@pytest.mark.asyncio
async def test_a_call_to_a_dead_device_starts_the_restart_and_recover_waits_for_it():
    driver = RecoverDriver()
    runner = make_runner(driver)
    await runner.refresh()
    await runner.call('health', SERIAL, {})
    driver.running = False
    await runner.refresh()
    with pytest.raises(ldr.OpError, match='the runner is restarting it now'):
        await runner.call('tap', SERIAL, {'x': 1, 'y': 2})
    with pytest.raises(ldr.OpError, match='Device is restarting'):
        await runner.call('tap', SERIAL, {'x': 1, 'y': 2})
    result = await runner.call('recover', SERIAL, {})  # joins the running restart
    assert result['recovered'] is True and result['method'] == 'cold_boot' and result['health']['ok'] is True
    assert len(driver.recovered) == 1 and 'not running' in result['note']


@pytest.mark.asyncio
async def test_recover_leaves_a_healthy_device_alone_unless_cold_and_never_touches_physical_ones():
    driver = RecoverDriver()
    runner = make_runner(driver)
    await runner.refresh()
    result = await runner.call('recover', SERIAL, {})
    assert result['method'] == 'none' and driver.recovered == []
    await runner.call('recover', SERIAL, {'cold': True})
    assert driver.recovered == [{'serial': SERIAL, 'avd': 'Pixel_7_API_34', 'restart': True}]
    runner.inventory['R58M'] = (driver, {'serial': 'R58M', 'platform': 'android', 'virtual': False})
    with pytest.raises(ldr.OpError, match='Physical devices are never restarted'):
        await runner.call('recover', 'R58M', {})
    with pytest.raises(ldr.OpError, match='never saw it running'):
        await runner.call('recover', 'emulator-5600', {})
    assert all(d['serial'] != 'emulator-5600' for d in await runner.refresh())


# ── Android: the escalation ladder ────────────────────────────────────────


@pytest.mark.asyncio
async def test_android_reconnects_an_offline_emulator_before_restarting_it(monkeypatch):
    fake = FakeRun({('adb', 'devices'): (0, b'List of devices attached\nemulator-5554\toffline\n')})
    monkeypatch.setattr(ldr, 'run', fake)
    android = ldr.Android()

    async def booted(serial, seconds, proc=None):
        return True
    monkeypatch.setattr(android, 'wait_booted', booted)
    assert await android.recover(SERIAL, dict(AVD)) == {'method': 'reconnect', 'steps': ['reconnect']}
    assert ['adb', 'reconnect', 'offline'] in fake.calls


def stub_boot(monkeypatch, tmp_path, boots, started, killed):
    monkeypatch.setattr(ldr, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(ldr, 'emulator_binary', lambda: '/sdk/emulator/emulator')

    async def kill(name):
        killed.append(name)
    monkeypatch.setattr(ldr, 'kill_avd', kill)
    monkeypatch.setattr(ldr, 'start_detached', lambda argv, log: started.append(argv) or object())
    android = ldr.Android()

    async def booted(serial, seconds, proc=None):
        return next(boots)
    monkeypatch.setattr(android, 'wait_booted', booted)
    return android


@pytest.mark.asyncio
async def test_android_cold_boots_on_the_same_port_then_retries_with_the_software_gpu(monkeypatch, tmp_path):
    fake, killed, started = FakeRun(), [], []
    monkeypatch.setattr(ldr, 'run', fake)
    monkeypatch.setattr(ldr, 'clear_avd_locks', lambda name: ['multiinstance.lock'])
    android = stub_boot(monkeypatch, tmp_path, iter([False, True]), started, killed)
    result = await android.recover(SERIAL, dict(AVD), restart=True, extra_args=['-memory', '4096'])
    assert result['method'] == 'cold_boot_software_gpu'
    assert result['steps'] == ['kill', 'cleared_locks', 'cold_boot', 'kill', 'cleared_locks', 'cold_boot_software_gpu']
    assert started[0] == ['/sdk/emulator/emulator', '-avd', 'Pixel_7_API_34', '-port', '5554', '-no-snapshot-load',
                          '-no-boot-anim', '-no-audio', '-memory', '4096']
    assert started[1][-2:] == ['-gpu', 'swiftshader_indirect'] and killed == ['Pixel_7_API_34'] * 2
    assert ['adb', '-s', SERIAL, 'emu', 'kill'] in fake.calls
    assert ['adb', 'devices'] not in fake.calls  # restart=True skips the soft steps (hung, not offline)


@pytest.mark.asyncio
async def test_android_failure_reports_the_emulator_log_and_adb_restart_respects_busy_devices(monkeypatch, tmp_path):
    monkeypatch.setattr(ldr, 'run', FakeRun())
    monkeypatch.setattr(ldr, 'clear_avd_locks', lambda name: [])
    android = stub_boot(monkeypatch, tmp_path, iter([False, False]), [], [])
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs' / 'emulator-Pixel_7_API_34.log').write_text('FATAL | Not enough space to create userdata')
    with pytest.raises(ldr.OpError, match='Not enough space'):
        await android.recover(SERIAL, dict(AVD), restart=True)
    with pytest.raises(ldr.OpError, match='AVD name is unknown'):
        await android.recover(SERIAL, {}, restart=True)

    async def hung(argv, **kwargs):
        raise ldr.OpError('adb timed out after 10s')
    monkeypatch.setattr(ldr, 'run', hung)
    with pytest.raises(ldr.OpError, match='would cut off other devices'):
        await android.recover(SERIAL, dict(AVD), allow_global=False)


@pytest.mark.asyncio
async def test_android_identity_and_stale_avd_locks(monkeypatch, tmp_path):
    monkeypatch.setattr(ldr, 'run', FakeRun({('adb', '-s', SERIAL, 'emu', 'avd', 'name'): (0, b'Pixel_7_API_34\r\nOK\r\n')}))
    assert await ldr.Android().identity(SERIAL) == AVD
    assert await ldr.Android().identity('R58M') == {}  # a physical device has no AVD
    monkeypatch.setenv('ANDROID_AVD_HOME', str(tmp_path))
    folder = tmp_path / 'Pixel_7_API_34.avd'
    (folder / 'hardware-qemu.ini.lock').mkdir(parents=True)
    (folder / 'multiinstance.lock').write_text('')
    (folder / 'config.ini').write_text('keep me')
    assert sorted(ldr.clear_avd_locks('Pixel_7_API_34')) == ['hardware-qemu.ini.lock', 'multiinstance.lock']
    assert [p.name for p in folder.iterdir()] == ['config.ini']


def test_emulator_args_are_format_checked_and_headless_linux_gets_no_window(monkeypatch):
    monkeypatch.setattr(ldr.sys, 'platform', 'linux')
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    assert ldr.emulator_args({'emulator_args': ['-memory', '4096']}) == ['-memory', '4096', '-no-window']
    with pytest.raises(ldr.OpError):
        ldr.emulator_args({'emulator_args': ['-memory; rm -rf ~']})


# ── iOS ───────────────────────────────────────────────────────────────────


def sim_list(state):
    return ('{"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-0": [{"udid": "%s", "state": "%s"}]}}'
            % (UDID, state)).encode()


@pytest.mark.asyncio
async def test_ios_boots_a_shut_down_simulator_and_restarts_a_hung_one(monkeypatch):
    fake = FakeRun({('xcrun', 'simctl', 'list'): (0, sim_list('Shutdown'))})
    monkeypatch.setattr(ldr, 'run', fake)
    ios = ldr.IOS()
    assert await ios.recover(UDID, {}) == {'method': 'boot', 'steps': ['boot']}
    assert ['xcrun', 'simctl', 'boot', UDID] in fake.calls and ['xcrun', 'simctl', 'bootstatus', UDID, '-b'] in fake.calls
    states = iter([sim_list('Booted'), sim_list('Shutdown')])

    class Hung(FakeRun):
        async def __call__(self, argv, timeout=None, check=True, **kwargs):
            if argv[:3] == ['xcrun', 'simctl', 'list']:
                self.calls.append(list(argv))
                return 0, next(states), ''
            return await super().__call__(argv, timeout, check, **kwargs)
    hung = Hung()
    monkeypatch.setattr(ldr, 'run', hung)
    result = await ios.recover(UDID, {}, restart=True)
    assert result == {'method': 'restart', 'steps': ['shutdown', 'boot']}
    assert not any('killall' in call for call in hung.calls)


@pytest.mark.asyncio
async def test_ios_restarts_coresimulator_only_when_no_other_simulator_is_busy(monkeypatch):
    calls = []

    async def wedged(argv, **kwargs):
        calls.append(list(argv))
        if argv[:3] == ['xcrun', 'simctl', 'list']:
            raise ldr.OpError('xcrun timed out after 20s')
        return 0, b'', ''

    async def no_sleep(*_):
        return None
    monkeypatch.setattr(ldr, 'run', wedged)
    monkeypatch.setattr(ldr.asyncio, 'sleep', no_sleep)
    ios = ldr.IOS()
    with pytest.raises(ldr.OpError, match='would disturb other simulators'):
        await ios.recover(UDID, {}, allow_global=False)
    assert not any(call[0] == 'killall' for call in calls)
    result = await ios.recover(UDID, {})
    assert result['method'] == 'restart_coresimulator'
    assert ['killall', '-9', 'com.apple.CoreSimulator.CoreSimulatorService'] in calls


# ── Backend: lease, suite, listing, tools ─────────────────────────────────


class HealingSuite(Suite):
    """The real suite loop; recover brings the device back (or fails)."""

    def __init__(self, results, health=None, recover=None):
        super().__init__(results, health=health)
        self.recover_result = recover or {'recovered': True, 'method': 'cold_boot', 'health': {'ok': True}}

    async def call(self, user_email, scope, device_id, op, args):
        if op == 'recover':
            self.calls.append('recover')
            if isinstance(self.recover_result, Exception):
                raise self.recover_result
            return dict(self.recover_result)
        return await super().call(user_email, scope, device_id, op, args)


@pytest.mark.asyncio
async def test_suite_restarts_an_unhealthy_device_before_the_cases():
    service = HealingSuite({'a': PASS}, health={'ok': False, 'reasons': ['screenshot failed: device offline']})
    result = await service.suite(OWNER, 'conv-1', 'r/e', suite('a'))
    assert service.calls[:2] == ['health', 'recover'] and result['verdict'] == 'pass'
    assert result['health'] == {'ok': True} and result['recoveries'][0]['before'] == 'cases'
    failing = HealingSuite({'a': PASS}, health={'ok': False, 'reasons': ['screenshot failed']},
                           recover=DeviceError('Emulator Pixel did not boot'))
    blocked = await failing.suite(OWNER, 'conv-1', 'r/e', suite('a'))
    assert blocked['verdict'] == 'blocked' and 'restart failed: Emulator Pixel did not boot' in blocked['not_run_reason']


@pytest.mark.asyncio
async def test_suite_reruns_a_case_whose_device_died_under_it():
    gone = DeviceError('Device is not connected to this runner (it crashed or was closed); the runner is restarting it')
    service = HealingSuite({'a': PASS, 'b': [gone, PASS], 'c': PASS}, health={'ok': True})
    result = await service.suite(OWNER, 'conv-1', 'r/e', suite('a', 'b', 'c'))
    assert result['verdict'] == 'pass' and result['passed'] == 3  # a restart is not a retry, and not flaky
    assert service.calls.count('recover') == 1 and result['recoveries'][0]['before'] == 'b'
    # An ordinary runner error is not a dead device: no restart.
    plain = HealingSuite({'a': DeviceError('scenario failed on the runner')}, health={'ok': True})
    assert (await plain.suite(OWNER, 'conv-1', 'r/e', suite('a')))['verdict'] == 'error'
    assert 'recover' not in plain.calls


def test_recover_op_validation_gate_and_device_state_passthrough():
    _validate('recover', {'cold': True})
    with pytest.raises(DeviceError):
        _validate('recover', {'wipe': True})

    class Conn:
        version = '1.3.0'
    with pytest.raises(DeviceError, match='>= 1.4.0'):
        _check_runner_version(Conn(), 'recover', {})
    cleaned = _clean_device({'serial': SERIAL, 'platform': 'android', 'state': 'down', 'error': 'did not boot'})
    assert cleaned['state'] == 'down' and cleaned['error'] == 'did not boot'
    assert _clean_device({'serial': SERIAL, 'state': 'weird'})['state'] == 'ok'
    assert 'error' not in _clean_device({'serial': SERIAL, 'state': 'ok', 'error': 'x'})


@pytest.mark.asyncio
async def test_recover_tool_and_cli():
    service = FakeService()
    tools = DeviceTools(None, AUTH, 'conv-1', artifacts=FakeArtifacts(AUTH), service=service)
    async def lease(owner, scope, device_id=None, platform=None, recover=False, cold=False, template=None, clean=False):
        service.calls.append(('lease', device_id, platform, recover, cold))
        return {'device_id': device_id}
    service.lease = lease
    assert await tools(AUTH, 'device.lease', {'device_id': 'r/e', 'recover': True, 'cold': True}) == {'device_id': 'r/e'}
    assert service.calls[-1] == ('lease', 'r/e', None, True, True)
    assert (await tools(AUTH, 'device.lease', {'recover': True}))['ok'] is False  # needs a device_id
    await tools(AUTH, 'device.lease', {'platform': 'ios'})
    assert service.calls[-1] == ('lease', None, 'ios', False, False)
    body = cli.build_body(cli.parser().parse_args(
        ['--user-email', OWNER, '--auth-token', 't', '--scope', 'c1', 'recover', '--device-id', 'r/e', '--cold']))
    assert body == {'scope': 'conv:c1', 'action': 'lease', 'device_id': 'r/e', 'recover': True, 'cold': True}


class LeaseService(HealingSuite):
    """The real lease() over a fake runner/lease store."""

    def __init__(self, health, recover=None):
        super().__init__({}, health=health, recover=recover)

        class Hub:
            def get(self, runner_id):
                return object()
        self.hub = Hub()

    async def _resolve(self, user_email, device_id):
        return {'runner_id': 'r'}, 'emulator-5554'

    async def _acquire(self, device_id, user_email, scope, ttl=None):
        from datetime import datetime, timezone
        return {'expires_at': datetime.now(timezone.utc)}

    async def _audit(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_lease_restarts_an_unhealthy_device_and_recover_forces_it():
    service = LeaseService(health={'ok': False, 'reasons': ['screenshot failed: device offline']})
    result = await service.lease(OWNER, 'conv-1', 'r/emulator-5554')
    assert service.calls == ['health', 'recover', 'installed'] and result['recovery']['recovered'] is True
    assert result['health'] == {'ok': True}
    healthy = LeaseService(health={'ok': True})
    result = await healthy.lease(OWNER, 'conv-1', 'r/emulator-5554')
    assert healthy.calls == ['health', 'installed'] and 'recovery' not in result
    forced = LeaseService(health={'ok': True})
    result = await forced.lease(OWNER, 'conv-1', 'r/emulator-5554', recover=True)
    assert forced.calls == ['recover', 'installed'] and result['recovery']['method'] == 'cold_boot'
    broken = LeaseService(health={'ok': False, 'reasons': ['x']}, recover=DeviceError('did not boot'))
    result = await broken.lease(OWNER, 'conv-1', 'r/emulator-5554')
    assert result['recovery'] == {'recovered': False, 'error': 'did not boot'} and 'lease another device' in result['note']
    with pytest.raises(DeviceError, match='needs a device_id'):
        await forced.lease(OWNER, 'conv-1', None, recover=True)


@pytest.mark.asyncio
async def test_a_slow_device_on_an_overloaded_machine_is_not_restarted(monkeypatch):
    driver = RecoverDriver()
    runner = make_runner(driver)
    await runner.refresh()
    monkeypatch.setattr(ldr, 'HEALTH_SCREENSHOT_MS', -1)
    monkeypatch.setattr(ldr.os, 'getloadavg', lambda: (64.0, 60.0, 50.0))
    monkeypatch.setattr(ldr.os, 'cpu_count', lambda: 8)
    with pytest.raises(ldr.OpError, match='overloaded .* Not restarting'):
        await runner.call('recover', SERIAL, {})
    assert driver.recovered == []
    await runner.call('recover', SERIAL, {'cold': True})  # forced
    assert len(driver.recovered) == 1


@pytest.mark.asyncio
async def test_the_avd_is_read_again_when_a_port_comes_back():
    driver = RecoverDriver()
    runner = make_runner(driver)
    await runner.refresh()
    driver.identity = lambda serial: asyncio.sleep(0, {'avd': 'Tablet_API_35', 'port': 5554})
    await runner.refresh()
    assert runner.known[SERIAL]['avd'] == 'Pixel_7_API_34'  # still the same running emulator: not re-read
    driver.running = False
    await runner.refresh()
    driver.running = True
    await runner.refresh()
    assert runner.known[SERIAL]['avd'] == 'Tablet_API_35'  # a new emulator on the same port


@pytest.mark.asyncio
async def test_wait_booted_only_fails_on_an_emulator_that_errored(monkeypatch):
    class Proc:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code
    android = ldr.Android()
    answers = iter([False, True])

    async def responsive(serial, timeout=10):
        return next(answers)

    async def no_sleep(*_):
        return None
    monkeypatch.setattr(android, 'responsive', responsive)
    monkeypatch.setattr(ldr.asyncio, 'sleep', no_sleep)
    assert await android.wait_booted(SERIAL, 1, Proc(1)) is False
    assert await android.wait_booted(SERIAL, 5, Proc(0)) is True  # a launcher that handed off to qemu exits 0


def test_avd_processes_match_only_that_avds_emulator(monkeypatch):
    listing = (b'  101 /sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64 -netdelay none -avd Pixel_7_API_34 -port 5554\n'
               b'  102 /sdk/emulator/emulator @Pixel_7_API_34 -no-window\n'
               b'  103 /sdk/emulator/qemu/qemu-system-aarch64 -avd Pixel_7_API_340 -port 5556\n'
               b'  104 vim notes-about -avd Pixel_7_API_34\n'
               b'  105 /sdk/emulator/emulator -avd Tablet -port 5558\n')

    class Done:
        stdout = listing
    monkeypatch.setattr(ldr.subprocess, 'run', lambda *a, **k: Done())
    assert ldr.avd_processes('Pixel_7_API_34') == [101, 102]
