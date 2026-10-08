"""Runner 1.4.0 and suite hardening, from the SDK PR #369 device run (Oct 2026):

- iOS: a launch_app step with extras restarts the app (a running iOS app ignores launch arguments);
  console capture survives a relaunch inside a scenario.
- iOS reset_app: reinstall the cached build (or wipe the data container) and reset the keychain.
- health: a device too slow to test on is reported as blocked before any case runs.
- screenshot fingerprints + expect.screens (the right logo / locale, without looking at images).
- suite: setup / teardown, retries with a flaky verdict, platform_defaults / only, not-run reasons,
  JUnit, a deadline, and the same suite on several devices at once (matrix).
- release_all at the end of a run; CLI --var / E2E_* placeholders so secrets stay out of spec files.
"""
import asyncio
import hashlib
import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from mongomock_motor import AsyncMongoMockClient

from device_loader.backend import service as service_module
from device_loader.backend.gateway import DeviceTools
from device_loader.backend.hub import DeviceError
from device_loader.backend.service import DeviceService, _check_runner_version, _validate, suite_junit
from device_loader.cli import device as cli
from device_loader.runner import loma_device_runner as ldr
from device_loader.tests.test_device_scenario import OWNER, ScenarioDriver, raw_frame, run_case
from device_loader.tests.test_devices_agent import AUTH, FakeArtifacts, FakeService

UDID = 'A1B2C3D4-0000-4000-8000-000000000001'


class FakeRun:
    """Records ldr.run argv; answers from a {argv prefix: (code, stdout)} table."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    async def __call__(self, argv, timeout=None, check=True, **kwargs):
        self.calls.append(list(argv))
        for prefix, (code, out) in self.answers.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return code, out, ''
        return 0, b'', ''


async def no_sleep(*_):
    return None


# ── iOS launch arguments and console capture ──────────────────────────────


@pytest.mark.asyncio
async def test_ios_launch_with_extras_restarts_unless_told_not_to(monkeypatch):
    fake = FakeRun({('xcrun', 'simctl', 'spawn', UDID, 'launchctl'): (0, b'1 0 UIKitApplication:com.example.demo[a]')})
    monkeypatch.setattr(ldr, 'run', fake)
    ios = ldr.IOS()
    result = await ios.launch(UDID, 'com.example.demo', extras={'locale': 'hi'}, restart=None)
    assert fake.calls[-1] == ['xcrun', 'simctl', 'launch', '--terminate-running-process', UDID, 'com.example.demo',
                              '-locale', 'hi']
    assert 'ignored_extras' not in result
    # restart=false with extras on a running app: iOS keeps the old arguments, and the result says so.
    result = await ios.launch(UDID, 'com.example.demo', extras={'locale': 'hi'}, restart=False)
    assert '--terminate-running-process' not in fake.calls[-1] and result['ignored_extras'] is True
    # A plain resume (no extras) never restarts.
    await ios.launch(UDID, 'com.example.demo', restart=None)
    assert fake.calls[-1] == ['xcrun', 'simctl', 'launch', UDID, 'com.example.demo']


class LiveProc:
    returncode = None

    def terminate(self):
        self.returncode = -15

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_ios_console_capture_carries_on_across_a_relaunch(monkeypatch, tmp_path):
    fake = FakeRun()
    monkeypatch.setattr(ldr, 'run', fake)
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    started = []

    async def spawn(*argv, **kwargs):
        started.append(argv)
        return LiveProc()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(asyncio, 'sleep', no_sleep)
    ios = ldr.IOS()
    await ios.launch(UDID, 'com.example.demo', console=True)
    log = Path(ios.consoles[UDID][1])
    log.write_bytes(b'before relaunch\n')
    # Resume (background -> foreground): the running capture is kept, the app only comes to the front.
    result = await ios.launch(UDID, 'com.example.demo', console=True, append=True)
    assert result.get('resumed') is True and len(started) == 1
    assert fake.calls[-1] == ['xcrun', 'simctl', 'launch', UDID, 'com.example.demo']
    # A cold relaunch (the app was stopped, or extras changed) appends to the same file instead of truncating it.
    await ios.stop(UDID, 'com.example.demo')
    await ios.launch(UDID, 'com.example.demo', extras={'locale': 'hi'}, console=True, append=True)
    assert len(started) == 2 and '--terminate-running-process' in started[-1]
    assert log.read_bytes() == b'before relaunch\n'
    # Without append (a new scenario) the capture starts empty, as before.
    await ios.launch(UDID, 'com.example.demo', console=True)
    assert log.read_bytes() == b''


class LaunchRecorder(ScenarioDriver):
    def __init__(self, platform):
        super().__init__()
        self.platform, self.launches = platform, []

    async def launch(self, serial, app_id, **options):
        self.launches.append(options)

    def console_size(self, serial):
        return None


@pytest.mark.asyncio
async def test_launch_app_steps_restart_on_ios_resume_on_android_and_keep_the_console():
    steps = [{'action': 'key', 'key': 'home'}, {'action': 'launch_app', 'extras': {'locale': 'hi'}},
             {'action': 'launch_app'}, {'action': 'launch_app', 'app_id': 'com.example.other'}]
    android = LaunchRecorder('android')
    await run_case(android, {'duration_s': 2, 'end_after_steps': True, 'app_id': 'com.example.demo', 'steps': steps})
    assert [launch.get('restart') for launch in android.launches[1:]] == [False, False, False]
    ios = LaunchRecorder('ios')
    await run_case(ios, {'duration_s': 2, 'end_after_steps': True, 'app_id': 'com.example.demo', 'console': True,
                         'steps': steps})
    first, with_extras, resume, other = ios.launches
    assert first['console'] is True and 'append' not in first                       # t0: a fresh capture
    assert (with_extras['restart'], with_extras['console'], with_extras['append']) == (None, True, True)
    assert (resume['restart'], resume['append']) == (None, True)
    assert other == {'extras': {}, 'bool_extras': {}, 'activity': None, 'console': False, 'restart': None}


# ── iOS reset_app ─────────────────────────────────────────────────────────


class ResetDriver:
    platform = 'ios'

    def __init__(self):
        self.calls = []

    async def stop(self, serial, app_id):
        self.calls.append('stop')

    async def install(self, serial, path, app_id, allowed=()):
        self.calls.append(('install', Path(path).read_bytes()))
        return {'bundle_id': app_id}

    async def install_stamp(self, serial, package):
        return 'stamp-2'

    async def grant(self, serial, package, appops=(), privacy=()):
        self.calls.append(('grant', list(privacy)))

    async def reset_app(self, serial, app_id):
        self.calls.append('wipe')
        return {'cleared': app_id, 'method': 'wipe'}

    async def reset_keychain(self, serial):
        self.calls.append('keychain')
        return True


@pytest.mark.asyncio
async def test_ios_reset_reinstalls_the_cached_build_and_regrants(monkeypatch, tmp_path):
    fake = FakeRun()
    monkeypatch.setattr(ldr, 'run', fake)
    runner = ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'cache_dir': str(tmp_path),
                         'policy': {'keep_awake': False}}, drivers=[])
    build = b'PK-fake-ipa'
    sha = hashlib.sha256(build).hexdigest()
    (tmp_path / sha).write_bytes(build)
    runner.installed[(UDID, 'com.example.demo')] = (sha, 'stamp-1')
    runner.granted[(UDID, 'com.example.demo')] = ['photos']
    driver = ResetDriver()
    result = await runner.reset_ios(driver, UDID, 'com.example.demo')
    assert result == {'cleared': 'com.example.demo', 'method': 'reinstall', 'regranted': ['photos'],
                      'keychain_reset': True}
    assert driver.calls == ['stop', ('install', build), ('grant', ['photos']), 'keychain']
    assert ['xcrun', 'simctl', 'uninstall', UDID, 'com.example.demo'] in fake.calls
    assert runner.installed[(UDID, 'com.example.demo')] == (sha, 'stamp-2')
    # No cached build (e.g. the runner restarted): wipe the data container instead.
    driver = ResetDriver()
    result = await runner.reset_ios(driver, UDID, 'com.example.other')
    assert result == {'cleared': 'com.example.other', 'method': 'wipe', 'keychain_reset': True}
    assert driver.calls == ['wipe', 'keychain']


@pytest.mark.asyncio
async def test_ios_wipe_only_touches_this_simulators_app_container(monkeypatch, tmp_path):
    container = tmp_path / 'CoreSimulator' / 'Devices' / UDID / 'data' / 'Containers' / 'Data' / 'Application' / 'X'
    (container / 'Documents').mkdir(parents=True)
    (container / 'Documents' / 'user.json').write_text('{}')
    (container / 'Library' / 'Preferences').mkdir(parents=True)
    (container / 'Library' / 'Preferences' / 'com.example.demo.plist').write_text('x')
    (container / '.com.apple.mobile_container_manager.metadata.plist').write_text('m')
    fake = FakeRun({('xcrun', 'simctl', 'get_app_container'): (0, str(container).encode() + b'\n')})
    monkeypatch.setattr(ldr, 'run', fake)
    result = await ldr.IOS().reset_app(UDID, 'com.example.demo')
    assert result == {'cleared': 'com.example.demo', 'method': 'wipe'}
    assert sorted(p.name for p in container.iterdir()) == ['Documents', 'Library', 'tmp']
    assert not any(container.rglob('*.json')) and not any(container.rglob('*.plist'))
    assert ['xcrun', 'simctl', 'spawn', UDID, 'defaults', 'delete', 'com.example.demo'] in fake.calls
    elsewhere = tmp_path / 'somewhere'
    elsewhere.mkdir()
    monkeypatch.setattr(ldr, 'run', FakeRun({('xcrun', 'simctl', 'get_app_container'): (0, str(elsewhere).encode())}))
    with pytest.raises(ldr.OpError, match='Unexpected app data container'):
        await ldr.IOS().reset_app(UDID, 'com.example.demo')


# ── Device health ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_flags_a_slow_device(monkeypatch):
    driver = ScenarioDriver()
    runner = ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r',
                         'policy': {'keep_awake': False}}, drivers=[driver])
    runner.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    healthy = await runner.call('health', 'emulator-5554', {})
    assert healthy['ok'] is True and 'screenshot_ms' in healthy and 'ui_tree_ms' in healthy
    monkeypatch.setattr(ldr, 'HEALTH_SCREENSHOT_MS', -1)
    slow = await runner.call('health', 'emulator-5554', {})
    assert slow['ok'] is False and slow['reasons'][0].startswith('a screenshot took')


# ── Screen fingerprints ───────────────────────────────────────────────────


def test_fingerprints_tell_two_screens_apart():
    blank = ldr.fingerprint(ldr.android_raw_signature(raw_frame()))
    panel = ldr.fingerprint(ldr.android_raw_signature(raw_frame((0, 60, 30, 120))))
    assert ldr.FINGERPRINT.fullmatch(blank) and service_module.FINGERPRINT.fullmatch(blank)
    assert ldr.fingerprint_diff(blank, blank) == 0 and ldr.fingerprint_diff(blank, panel) > 0.1
    steps = [{'action': 'screenshot', 'name': 'logo', 'fingerprint': panel}]
    assert ldr.judge_screens([{'shot': 'logo', 'like': panel, 'max_diff': 0.1}], steps) == []
    [reason] = ldr.judge_screens([{'shot': 'logo', 'like': blank, 'max_diff': 0.1}], steps)
    assert 'differs from the known-good fingerprint' in reason
    [reason] = ldr.judge_screens([{'shot': 'logo', 'unlike': panel, 'max_diff': 0.1}], steps)
    assert 'matches the known-bad fingerprint' in reason


@pytest.mark.asyncio
async def test_scenario_fingerprints_a_screenshot_and_judges_it():
    driver = ScenarioDriver()
    first = await run_case(driver, {'duration_s': 2, 'end_after_steps': True, 'expect': {}, 'steps': [
        {'action': 'tap', 'x': 10, 'y': 90}, {'action': 'screenshot', 'name': 'panel', 'fingerprint': True}]})
    good = first['steps'][1]['fingerprint']
    spec = {'duration_s': 2, 'end_after_steps': True,
            'steps': [{'action': 'screenshot', 'name': 'panel', 'fingerprint': True}],
            'expect': {'screens': [{'shot': 'panel', 'like': good}]}}
    _validate('scenario', spec)
    assert (await run_case(driver, spec))['verdict'] == 'pass'          # the panel is still shown
    result = await run_case(ScenarioDriver(), spec)                   # a fresh screen without the panel
    assert result['verdict'] == 'fail' and 'known-good' in result['failed'][0]
    for bad in ({'screens': [{'shot': 'other', 'like': good}]}, {'screens': [{'shot': 'panel'}]},
                {'screens': [{'shot': 'panel', 'like': 'v1:nope'}]}):
        with pytest.raises(DeviceError):
            _validate('scenario', {**spec, 'expect': bad})
        with pytest.raises(ldr.OpError):
            await run_case(ScenarioDriver(), {**spec, 'expect': bad})


def test_1_4_features_need_a_1_4_runner():
    old = type('C', (), {'version': '1.3.0'})()
    for args, what in (({'steps': [{'action': 'screenshot', 'name': 'a', 'fingerprint': True}]}, 'fingerprint'),
                       ({'expect': {'screens': [{'shot': 'a'}]}}, 'expect.screens')):
        with pytest.raises(DeviceError, match=r'>= 1\.4\.0') as caught:
            _check_runner_version(old, 'scenario', {'duration_s': 1, **args})
        assert what in str(caught.value)
    with pytest.raises(DeviceError, match='health'):
        _check_runner_version(old, 'health', {})
    _check_runner_version(old, 'scenario', {'duration_s': 1, 'steps': [{'action': 'screenshot', 'name': 'a'}]})


# ── Suite: health, setup / teardown, retries, platforms, deadline, JUnit ──


class Suite(DeviceService):
    """The real suite loop over scripted per-case results (a list per case = one result per attempt)."""

    def __init__(self, results, health=None, fail_ops=(), platform='android'):
        super().__init__(None)
        self.results = {name: list(value) if isinstance(value, list) else [value] for name, value in results.items()}
        self.health_result, self.fail_ops, self.platform, self.calls = health, set(fail_ops), platform, []

    async def _platform(self, user_email, device_id):
        return self.platform

    async def call(self, user_email, scope, device_id, op, args):
        self.calls.append(op if op != 'scenario' else ('scenario', args['log_tags'][0], args.get('app_id')))
        if op in self.fail_ops:
            raise DeviceError(f'{op} failed on the runner')
        if op == 'health':
            if self.health_result is None:
                raise DeviceError('Runner too old for health')
            return self.health_result
        if op != 'scenario':
            return {}
        queue = self.results[args['log_tags'][0]]
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, Exception):
            raise result
        return dict(result)


def suite(*names, **extra):
    return {'defaults': {'app_id': 'com.example.demo', 'duration_s': 5, 'expect': {'app_running': True}},
            'cases': [{'name': name, 'log_tags': [name]} for name in names], **extra}


PASS, FAIL = {'verdict': 'pass', 'ran_ms': 10}, {'verdict': 'fail', 'failed': ['boom'], 'ran_ms': 10}


@pytest.mark.asyncio
async def test_a_slow_device_blocks_the_suite_before_any_case():
    service = Suite({'a': PASS}, health={'ok': False, 'reasons': ['a screenshot took 11000 ms (limit 5000 ms)']})
    result = await service.suite(OWNER, 'conv-1', 'r/e', suite('a', 'b', setup=[{'action': 'key', 'key': 'wakeup'}]))
    assert result['verdict'] == 'blocked' and result['not_run'] == ['a', 'b'] and result['cases'] == []
    assert result['not_run_reason'].startswith('device_slow: a screenshot took 11000 ms')
    assert service.calls == ['health', 'recover']             # one restart attempt, then no case touched it
    assert '| a | not run | | device_slow' in result['table'] and result['junit'].count('<skipped') == 2
    # An older runner has no health op: the suite simply runs.
    older = await Suite({'a': PASS, 'b': PASS}).suite(OWNER, 'conv-1', 'r/e', suite('a', 'b'))
    assert older['verdict'] == 'pass' and 'health' not in older


@pytest.mark.asyncio
async def test_setup_runs_once_teardown_always_and_a_failed_setup_blocks():
    service = Suite({'a': PASS, 'b': DeviceError('Runner is offline')}, health={'ok': True})
    setup = [{'action': 'key', 'key': 'wakeup'}, {'action': 'animations', 'enabled': False},
             {'action': 'logs', 'clear': True}]
    result = await service.suite(OWNER, 'conv-1', 'r/e', suite('a', 'b', setup=setup,
                                                               teardown=[{'action': 'animations', 'enabled': True}]))
    assert service.calls == ['health', 'key', 'animations', 'logs', ('scenario', 'a', 'com.example.demo'),
                             ('scenario', 'b', 'com.example.demo'), 'animations']
    assert result['verdict'] == 'error' and result['health'] == {'ok': True}
    broken = Suite({'a': PASS}, health={'ok': True}, fail_ops={'animations'})
    blocked = await broken.suite(OWNER, 'conv-1', 'r/e', suite('a', setup=setup))
    assert blocked['verdict'] == 'blocked' and blocked['not_run'] == ['a']
    assert blocked['not_run_reason'] == 'setup[2] (animations) failed: animations failed on the runner'


@pytest.mark.asyncio
async def test_retries_turn_a_pass_on_retry_into_flaky():
    service = Suite({'steady': PASS, 'flaky': [FAIL, PASS], 'broken': [FAIL, FAIL, FAIL],
                     'blocked': {'verdict': 'blocked', 'failed': ['preflight x']}}, health={'ok': True})
    result = await service.suite(OWNER, 'conv-1', 'r/e', suite('steady', 'flaky', 'broken', 'blocked', retries=2,
                                                               reset='reset_app'))
    verdicts = {case['name']: (case['verdict'], case.get('attempts')) for case in result['cases']}
    assert verdicts == {'steady': ('pass', None), 'flaky': ('flaky', 2), 'broken': ('fail', 3),
                        'blocked': ('blocked', None)}
    assert result['verdict'] == 'fail' and result['counts'] == {'blocked': 1, 'fail': 1, 'flaky': 1, 'pass': 1}
    assert service.calls.count('reset_app') == 1 + 2 + 3 + 1             # every attempt starts from a clean app
    assert '| flaky | flaky | 10 | passed on attempt 2; ' in result['table']
    only_flaky = await Suite({'a': [FAIL, PASS]}, health={'ok': True}).suite(OWNER, 'c', 'r/e', suite('a', retries=1))
    assert only_flaky['verdict'] == 'flaky' and only_flaky['passed'] == 0


@pytest.mark.asyncio
async def test_platform_defaults_and_only_pick_the_cases_for_the_device(monkeypatch):
    spec = suite('both', 'android_only', platform_defaults={'ios': {'app_id': 'com.example.ios'}})
    spec['cases'][1]['only'] = ['android']
    ios = Suite({'both': PASS, 'android_only': PASS}, health={'ok': True}, platform='ios')
    result = await ios.suite(OWNER, 'conv-1', 'r/e', spec)
    assert [case['name'] for case in result['cases']] == ['both'] and result['platform'] == 'ios'
    assert ('scenario', 'both', 'com.example.ios') in ios.calls
    android = Suite({'both': PASS, 'android_only': PASS}, health={'ok': True})
    assert (await android.suite(OWNER, 'conv-1', 'r/e', spec))['passed'] == 2
    monkeypatch.setattr(service_module, 'SUITE_DEADLINE_S', -1)
    late = await Suite({'both': PASS, 'android_only': PASS}, health={'ok': True}).suite(OWNER, 'c', 'r/e', spec)
    assert late['not_run'] == ['both', 'android_only'] and 'deadline' in late['not_run_reason']


def test_junit_marks_failures_errors_flaky_and_skipped():
    rows = [{'name': 'a', 'verdict': 'pass', 'ran_ms': 1500}, {'name': 'b', 'verdict': 'fail', 'failed': ['x <y>']},
            {'name': 'c', 'verdict': 'blocked', 'failed': ['preflight']}, {'name': 'd', 'verdict': 'flaky', 'attempts': 2}]
    root = ET.fromstring(suite_junit(rows, ['e'], 'stop_on_fail'))
    assert (root.get('tests'), root.get('failures'), root.get('errors'), root.get('skipped')) == ('5', '1', '1', '1')
    assert root.find("testcase[@name='a']").get('time') == '1.500'
    assert root.find("testcase[@name='b']/failure").text == 'x <y>'
    assert 'flaky' in root.find("testcase[@name='d']/system-out").text


@pytest.mark.asyncio
async def test_matrix_runs_the_suite_on_each_device_and_combines_the_tables():
    class Matrix(Suite):
        async def _platform(self, user_email, device_id):
            return 'ios' if device_id.endswith('sim') else 'android'

        async def call(self, user_email, scope, device_id, op, args):
            if op == 'scenario' and device_id.endswith('sim'):
                return dict(FAIL)
            return await super().call(user_email, scope, device_id, op, args)
    service = Matrix({'a': PASS}, health={'ok': True})
    result = await service.matrix(OWNER, 'conv-1', ['r/emu', 'r/sim'], suite('a'))
    assert result['verdict'] == 'fail' and [d['verdict'] for d in result['devices']] == ['pass', 'fail']
    assert result['table'].splitlines()[2:] == ['| r/emu | android | pass | 1/1 | pass 1 |',
                                                '| r/sim | ios | fail | 0/1 | fail 1 |']
    for bad in (['r/emu'], ['r/emu', 'r/emu'], ['a', 'b', 'c', 'd', 'e']):
        with pytest.raises(DeviceError, match='device_ids'):
            await service.matrix(OWNER, 'conv-1', bad, suite('a'))


# ── Leases are released when the run ends ─────────────────────────────────


@pytest.mark.asyncio
async def test_release_all_frees_only_this_conversations_devices():
    db = AsyncMongoMockClient()['loma_devices_release_all']
    await db.device_leases.insert_many([
        {'_id': 'r/a', 'owner_email': OWNER, 'scope': 'conv:1'}, {'_id': 'r/b', 'owner_email': OWNER, 'scope': 'conv:1'},
        {'_id': 'r/c', 'owner_email': OWNER, 'scope': 'conv:2'}, {'_id': 'r/d', 'owner_email': 'x@y.z', 'scope': 'conv:1'}])
    result = await DeviceService(db).release_all(OWNER, 'conv:1')
    assert sorted(result['released']) == ['r/a', 'r/b']
    assert sorted([lease['_id'] async for lease in db.device_leases.find()]) == ['r/c', 'r/d']


@pytest.mark.asyncio
async def test_isolated_tools_release_on_close_and_run_a_matrix():
    class Service(FakeService):
        async def release_all(self, owner, scope):
            self.calls.append(('release_all', owner, scope))
            return {'released': ['r/a']}

        async def matrix(self, owner, scope, device_ids, args):
            self.calls.append(('matrix', device_ids, sorted(args)))
            return {'verdict': 'pass', 'table': '|', 'devices': [
                {'device_id': d, 'verdict': 'pass', 'cases': [{'name': 'a', 'verdict': 'pass', 'mp4': b'V'}]}
                for d in device_ids]}

    async def registry(receipt):
        return {'name': receipt['name'], 'url': '/f/' + receipt['name']}
    service = Service()
    tools = DeviceTools(None, AUTH, 'conv-1', artifacts=FakeArtifacts(AUTH), service=service, on_artifact=registry)
    result = await tools(AUTH, 'device.suite', {'device_ids': ['r/a', 'r/b'], 'cases': [{'name': 'a'}]})
    assert result['verdict'] == 'pass' and result['devices'][0]['cases'][0]['video']['name'].endswith('.mp4')
    assert service.calls[-1] == ('matrix', ['r/a', 'r/b'], ['cases'])
    assert (await tools(AUTH, 'device.release', {'all': True})) == {'released': ['r/a']}
    assert await tools.close() == {'released': ['r/a']}
    assert service.calls[-1] == ('release_all', AUTH.user_email, 'conv:conv-1')


# ── CLI: placeholders instead of secrets in spec files ────────────────────


BASE = ['--user-email', OWNER, '--auth-token', 't', '--scope', 'c1']


def test_cli_fills_placeholders_from_vars_and_e2e_env_only(tmp_path, monkeypatch):
    spec = tmp_path / 'suite.yaml'
    spec.write_text('defaults: {app_id: com.example.demo, extras: {api_key: "${API_KEY}"}, '
                    'preflight: [{url: "https://api.example.com/init", headers: {X-Key: "${E2E_KEY}"}}], '
                    'expect: {app_running: true}}\ncases:\n  - {name: a, duration_s: 5}\n')
    monkeypatch.setenv('E2E_KEY', 'from-env')
    monkeypatch.setenv('GITHUB_API_KEY', 'never-substituted')
    body = cli.build_body(cli.parser().parse_args(BASE + ['suite', '--device-id', 'r/e', '--spec', str(spec),
                                                          '--var', 'API_KEY=k123']))
    defaults = body['args']['defaults']
    assert defaults['extras'] == {'api_key': 'k123'} and defaults['preflight'][0]['headers'] == {'X-Key': 'from-env'}
    assert body['device_id'] == 'r/e' and 'device_ids' not in body
    spec.write_text('{"duration_s": 1, "extras": {"t": "${GITHUB_API_KEY}"}}')
    with pytest.raises(SystemExit, match=r'\$\{GITHUB_API_KEY\}'):
        cli.build_body(cli.parser().parse_args(BASE + ['scenario', '--device-id', 'r/e', '--spec', str(spec)]))
    with pytest.raises(SystemExit, match='KEY=VALUE'):
        cli.parse_vars(['no-equals'])


def test_cli_matrix_and_release_all_bodies(tmp_path):
    spec = tmp_path / 'm.json'
    spec.write_text('{"cases": []}')
    matrix = cli.build_body(cli.parser().parse_args(
        BASE + ['suite', '--device-id', 'r/a', '--device-id', 'r/b', '--spec', str(spec)]))
    assert matrix['device_ids'] == ['r/a', 'r/b'] and 'device_id' not in matrix
    assert cli.build_body(cli.parser().parse_args(BASE + ['release', '--all'])) == {
        'scope': 'conv:c1', 'action': 'release', 'all': True}
    assert cli.build_body(cli.parser().parse_args(BASE + ['release', '--device-id', 'r/a']))['device_id'] == 'r/a'
    with pytest.raises(SystemExit):
        cli.build_body(cli.parser().parse_args(BASE + ['release']))


def test_cli_cleanup_keeps_evidence_and_saves_junit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('LOMA_CONVERSATION_DIR', str(tmp_path))
    folder = cli.output_dir('c1')
    for name in ('keep.png', 'probe1.png', 'probe2.mp4', 'notes.txt'):
        (folder / name).write_bytes(b'x')
    assert cli.main(['--scope', 'c1', 'cleanup', '--keep', str(folder / 'keep.png')]) == 0
    assert sorted(p.name for p in folder.iterdir()) == ['keep.png', 'notes.txt']
    assert json.loads(capsys.readouterr().out)['removed'] == 2
    args = cli.parser().parse_args(['--scope', 'c1', 'suite', '--device-id', 'r/e', '--spec', 'x', '--junit',
                                    str(tmp_path / 'report.xml')])
    saved = cli.save_media(args, {'verdict': 'pass', 'junit': '<testsuite/>', 'cases': []})
    assert saved['junit_saved_to'] == str(tmp_path / 'report.xml') and 'junit' not in saved
    assert (tmp_path / 'report.xml').read_text() == '<testsuite/>'
