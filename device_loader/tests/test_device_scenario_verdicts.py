"""Runner 1.3.0: number checks on logs, step drift, launch_app options, preflight, video re-encode, suite."""
import asyncio
import base64
import json
import sys
import time
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from mongomock_motor import AsyncMongoMockClient

from device_loader.api.device_routes import setup_device_routes
from device_loader.backend import service as service_module
from device_loader.backend import store
from device_loader.backend.builds import BlobStore
from device_loader.backend.gateway import DeviceTools
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import DeviceService, _check_runner_version, _validate, suite_plan
from device_loader.cli import device as cli
from device_loader.runner import loma_device_runner as ldr
from device_loader.tests.test_device_scenario import FAKE_ADB, OWNER, ScenarioDriver, run_case, scenario_runner
from device_loader.tests.test_devices_agent import AUTH, FakeArtifacts, FakeService

SLOT = {'match': 'slot', 'number_after': 'height=', 'after_reaching': 100, 'value_min': 60}


def expect_for(rule):
    return ldr.need_expect({'expect': {'logs': [rule]}}, True, False, False)


def lines(*values):
    return [(index * 100, f'I/ViewTag: slot height={value}') for index, value in enumerate(values)]


# ── 2. Number checks on log lines ─────────────────────────────────────────


def test_log_values_read_the_first_number_after_the_marker():
    rule = {'match': 'slot', 'number_after': 'height'}
    entries = [(0, 'slot height=58.5 width=300'), (10, 'SLOT Height: 120'), (20, 'slot height -4'),
               (30, 'slot height=tall'), (40, 'other height=999'), (50, 'slot width=7')]
    assert ldr.log_values(rule, entries) == [(0, 58.5), (10, 120.0), (20, -4.0)]


def test_value_never_drops_after_reaching_a_level():
    expect = expect_for(SLOT)
    assert ldr.judge(expect, {}, lines(0, 40, 100, 100, 80)) == []            # low values before reaching 100 are fine
    [reason] = ldr.judge(expect, {}, lines(0, 100, 40, 30, 100))
    assert reason == "log 'slot': value 40 at 200 ms is below 60 (2 such values)"  # the collapse, with when
    [reason] = ldr.judge(expect, {}, lines(0, 40, 80))
    assert reason == "log 'slot': never reached 100 (highest value 80)"
    assert ldr.value_stats(expect, lines(0, 100, 40)) == {
        'slot': {'count': 3, 'min': 0.0, 'max': 100.0, 'first': 0.0, 'last': 40.0}}


def test_value_range_and_last_value_rules():
    entries = lines(10, 250, 90)
    assert ldr.judge(expect_for({'match': 'slot', 'number_after': 'height=', 'value_max': 300, 'last_min': 90,
                                 'last_max': 90}), {}, entries) == []
    failed = ldr.judge(expect_for({'match': 'slot', 'number_after': 'height=', 'value_max': 200, 'last_min': 100}),
                       {}, entries)
    assert failed == ["log 'slot': value 250 at 100 ms is above 200", "log 'slot': last value 90 is below 100"]
    [reason] = ldr.judge(expect_for({'match': 'slot', 'number_after': 'depth=', 'value_min': 1}), {}, entries)
    assert reason == "log 'slot': no number found after 'depth='"
    # No matching line at all is the existing "expected at least 1" failure, reported once.
    assert ldr.judge(expect_for(SLOT), {}, []) == ["log 'slot': 0 lines (expected at least 1)"]


def test_number_rules_are_validated_on_the_runner_and_the_backend():
    for bad in ({'match': 'x', 'value_min': 1},                              # needs number_after
                {'match': 'x', 'number_after': '', 'value_min': 1},
                {'match': 'x', 'number_after': 'h=', 'value_min': '60'},
                {'match': 'x', 'number_after': 'h=', 'value_max': True},
                {'match': 'x', 'number_after': 'h=', 'regex': '.*'}):
        with pytest.raises(ldr.OpError):
            expect_for(bad)
        with pytest.raises(DeviceError):
            _validate('scenario', {'duration_s': 2, 'log_tags': ['T'], 'expect': {'logs': [bad]}})
    _validate('scenario', {'duration_s': 2, 'log_tags': ['T'], 'expect': {'logs': [SLOT, {
        'match': 'x', 'number_after': 'ms ', 'value_max': 16.7, 'last_min': -1, 'last_max': 5}]}})


class SlotDriver(ScenarioDriver):
    """Each tap logs the next slot height, like an SDK that reports its layout."""

    def __init__(self, heights=(100, 100, 20)):
        super().__init__()
        self.heights = list(heights)

    async def tap(self, serial, x, y):
        self._mark(f'tap {x},{y}')
        self.log.append((time.time(), f'I/ViewTag: slot height={self.heights.pop(0)}'))


@pytest.mark.asyncio
async def test_scenario_fails_on_a_collapsing_value_and_reports_the_numbers():
    spec = {'duration_s': 5, 'end_after_steps': True, 'log_tags': ['ViewTag'],
            'steps': [{'action': 'tap', 'x': 1, 'y': 1, 'after_ms': 30}] * 3, 'expect': {'logs': [SLOT]}}
    result = await run_case(SlotDriver(), spec)
    assert result['verdict'] == 'fail' and len(result['failed']) == 1
    assert result['failed'][0].startswith("log 'slot': value 20 at ") and result['failed'][0].endswith('is below 60')
    assert result['logs']['values'] == {'slot': {'count': 3, 'min': 20.0, 'max': 100.0, 'first': 100.0, 'last': 20.0}}
    assert (await run_case(SlotDriver((100, 100, 100)), spec))['verdict'] == 'pass'


# ── 5. Step drift ─────────────────────────────────────────────────────────


class SlowSwipeDriver(ScenarioDriver):
    async def swipe(self, serial, x1, y1, x2, y2, duration_ms):
        self._mark('swipe')
        await asyncio.sleep(0.5)  # like `idb ui swipe`, far slower than the gesture itself


@pytest.mark.asyncio
async def test_late_timed_steps_report_drift_and_fail_max_drift_ms():
    steps = [{'at_ms': 0, 'action': 'swipe', 'x1': 1, 'y1': 9, 'x2': 1, 'y2': 1},
             {'at_ms': 100, 'action': 'key', 'key': 'back'}]
    result = await run_case(SlowSwipeDriver(), {'duration_s': 1, 'steps': steps, 'expect': {'max_drift_ms': 200}})
    swipe, key = result['steps']
    assert swipe['drift_ms'] < 100 and 380 <= key['drift_ms'] <= 700 and key['ok'] is True
    assert result['max_drift_ms'] == key['drift_ms'] and result['verdict'] == 'fail'
    [reason] = result['failed']
    assert reason.startswith('step 2 (key) ran ') and 'expected at most 200 ms' in reason
    on_time = await run_case(ScenarioDriver(), {'duration_s': 1, 'steps': steps, 'expect': {'max_drift_ms': 200}})
    assert on_time['verdict'] == 'pass' and on_time['max_drift_ms'] < 200
    # after_ms steps have no planned time, so no drift is reported and the rule cannot be used.
    plain = await run_case(ScenarioDriver(), {'duration_s': 1, 'end_after_steps': True,
                                              'steps': [{'action': 'key', 'key': 'back'}]})
    assert 'max_drift_ms' not in plain and 'drift_ms' not in plain['steps'][0]
    untimed = {'duration_s': 1, 'steps': [{'action': 'key', 'key': 'back'}], 'expect': {'max_drift_ms': 5}}
    with pytest.raises(ldr.OpError, match='at_ms step'):
        await run_case(ScenarioDriver(), untimed)
    with pytest.raises(DeviceError, match='at_ms step'):
        _validate('scenario', untimed)


# ── 3. launch_app with activity / extras ──────────────────────────────────


class LaunchDriver(ScenarioDriver):
    def __init__(self):
        super().__init__()
        self.launches = []

    async def launch(self, serial, app_id, **options):
        self.launches.append((app_id, options))


@pytest.mark.asyncio
async def test_launch_app_step_takes_activity_extras_and_restart():
    driver = LaunchDriver()
    result = await run_case(driver, {'duration_s': 2, 'end_after_steps': True, 'app_id': 'com.example.demo', 'steps': [
        {'action': 'key', 'key': 'home'},
        {'action': 'launch_app', 'activity': '.NativeActivity', 'extras': {'screen': 'home'},
         'bool_extras': {'loader': True}},
        {'action': 'launch_app', 'app_id': 'com.example.other', 'restart': True}]})
    assert [step['ok'] for step in result['steps']] == [True, True, True]
    first, back, other = driver.launches
    assert 'restart' not in first[1]                                         # the launch at t0 is unchanged
    assert back == ('com.example.demo', {'extras': {'screen': 'home'}, 'bool_extras': {'loader': True},
                                         'activity': '.NativeActivity', 'console': False, 'restart': False})
    assert other == ('com.example.other', {'extras': {}, 'bool_extras': {}, 'activity': None, 'console': False,
                                           'restart': True})
    for bad in ({'action': 'launch_app', 'activity': 'bad activity'}, {'action': 'launch_app', 'restart': 'yes'},
                {'action': 'launch_app', 'console': True}, {'action': 'stop_app', 'activity': '.Main'},
                {'action': 'launch_app', 'extras': {'bad key': 'v'}}):
        with pytest.raises(ldr.OpError):
            await run_case(LaunchDriver(), {'duration_s': 2, 'app_id': 'com.example.demo', 'steps': [bad]})
        with pytest.raises(DeviceError):
            _validate('scenario', {'duration_s': 2, 'app_id': 'com.example.demo', 'steps': [bad]})
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        await run_case(LaunchDriver(), {'duration_s': 1, 'app_id': 'com.allowed', 'steps': [
            {'action': 'launch_app', 'app_id': 'com.other', 'activity': '.Main'}]}, ('com.allowed',))


@pytest.mark.asyncio
async def test_android_and_ios_launch_only_restart_when_asked(monkeypatch):
    calls = []

    async def fake_run(args, **kwargs):
        calls.append(list(args))
        return 0, b'com.example.demo/.MainActivity\n', ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    android = ldr.Android('adb')
    await android.launch('emulator-5554', 'com.example.demo', activity='.NativeActivity', restart=False)
    assert calls[-1][-5:] == ['am', 'start', '-W', '-n', 'com.example.demo/.NativeActivity']  # no -S: comes to the front
    await android.launch('emulator-5554', 'com.example.demo', extras={'screen': 'home'}, restart=False)
    front = calls[-1]
    assert '-S' not in front and front[front.index('-a') + 1] == 'android.intent.action.MAIN'
    assert front[front.index('-c') + 1] == 'android.intent.category.LAUNCHER'
    await android.launch('emulator-5554', 'com.example.demo', activity='.NativeActivity')
    assert '-S' in calls[-1] and '-a' not in calls[-1]                       # default: restart, as before 1.3.0
    calls.clear()
    await android.launch('emulator-5554', 'com.example.demo', restart=True)
    assert 'force-stop' in calls[0] and 'monkey' in calls[1]
    ios = ldr.IOS()
    await ios.launch('UDID-1', 'com.example.demo', extras={'screen': 'home'}, restart=False)
    assert '--terminate-running-process' not in calls[-1]
    await ios.launch('UDID-1', 'com.example.demo', extras={'screen': 'home'})
    assert '--terminate-running-process' in calls[-1]
    await ios.launch('UDID-1', 'com.example.demo', restart=True)
    assert '--terminate-running-process' in calls[-1]


# ── 6. Preflight ──────────────────────────────────────────────────────────


async def config_server(enabled=True):
    seen = []

    async def init(request):
        seen.append((request.method, request.headers.get('X-Api-Key'), await request.text()))
        return web.json_response({'loaderEnabled': enabled, 'secret': 'never-returned'})

    async def moved(request):
        raise web.HTTPFound('/sdk/init')
    app = web.Application()
    app.router.add_route('*', '/sdk/init', init)
    app.router.add_get('/moved', moved)
    server = TestServer(app)
    await server.start_server()
    return server, str(server.make_url('')).rstrip('/'), seen


def preflight_runner(driver, mode='any'):
    r = scenario_runner(driver)
    r.policy['preflight'] = mode
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    return r


@pytest.mark.asyncio
async def test_preflight_passes_then_the_scenario_runs():
    server, base, seen = await config_server()
    try:
        driver = ScenarioDriver()
        result = await preflight_runner(driver).call('scenario', 'emulator-5554', {
            'duration_s': 1, 'end_after_steps': True, 'steps': [{'action': 'key', 'key': 'back'}], 'expect': {},
            'preflight': [{'name': 'sdk init', 'url': base + '/sdk/init', 'method': 'POST',
                           'headers': {'X-Api-Key': 'k1'}, 'body': {'userId': 'u1'},
                           'contains': ['"loaderEnabled": true']}]})
    finally:
        await server.close()
    assert result['preflight'] == [{'name': 'sdk init', 'ok': True, 'status': 200}] and result['verdict'] == 'pass'
    assert seen == [('POST', 'k1', '{"userId": "u1"}')] and 'key back' in [c[0] for c in driver.calls]
    assert 'never-returned' not in json.dumps(result)                        # the response body never comes back


@pytest.mark.asyncio
async def test_failed_preflight_blocks_the_run_without_touching_the_device():
    server, base, _ = await config_server(enabled=False)
    try:
        driver = ScenarioDriver()
        result = await preflight_runner(driver).call('scenario', 'emulator-5554', {
            'app_id': 'com.example.demo', 'duration_s': 2, 'record': True,
            'steps': [{'action': 'key', 'key': 'back'}],
            'preflight': [{'url': base + '/sdk/init', 'contains': ['"loaderEnabled": true']},
                          {'url': base + '/missing'}, {'url': base + '/moved'},
                          {'url': 'http://127.0.0.1:9/down', 'timeout_s': 2}]})
    finally:
        await server.close()
    assert driver.calls == [] and result['verdict'] == 'blocked' and 'steps' not in result
    wiped, missing, moved, down = result['preflight']
    assert wiped == {'name': 'GET 127.0.0.1/sdk/init', 'ok': False, 'status': 200,
                     'error': 'response does not contain \'"loaderEnabled": true\''}
    assert missing['error'] == 'status 404 (expected 200)'
    assert moved['error'] == 'status 302 (expected 200)'                     # redirects are not followed
    assert down['ok'] is False and down['error'].startswith('request failed (')
    assert result['failed'][0] == 'preflight GET 127.0.0.1/sdk/init: ' + wiped['error'] and len(result['failed']) == 4


@pytest.mark.asyncio
async def test_preflight_policy_public_blocks_private_hosts_and_off_refuses():
    driver = ScenarioDriver()
    spec = {'duration_s': 1, 'preflight': [{'url': 'http://127.0.0.1:9/x'}, {'url': 'http://192.168.1.10/x'},
                                           {'url': 'http://localhost:9/x'}, {'url': 'http://[::1]:9/x'}]}
    result = await preflight_runner(driver, 'public').call('scenario', 'emulator-5554', spec)
    assert result['verdict'] == 'blocked' and driver.calls == []
    assert all('private or local network' in check['error'] and 'status' not in check for check in result['preflight'])
    assert ldr.default_policy()['preflight'] == 'public'
    with pytest.raises(ldr.OpError, match='turned off'):
        await preflight_runner(driver, 'off').call('scenario', 'emulator-5554', spec)
    assert await ldr.private_host('8.8.8.8', None) is False and await ldr.private_host('169.254.169.254', 80) is True
    # CGNAT / Tailscale (incl. MagicDNS 100.100.100.100) and IPv4-mapped loopback are not public either.
    for host in ('100.64.0.1', '100.100.100.100', '::ffff:127.0.0.1', '0.0.0.0', '224.0.0.1'):
        assert await ldr.private_host(host, None) is True, host


@pytest.mark.asyncio
async def test_private_host_dns_lookup_has_a_timeout(monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(5)
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', slow)
    with pytest.raises(asyncio.TimeoutError):
        await ldr.private_host('slow.example.com', 443, timeout=0.05)
    monkeypatch.setattr(ldr, 'PREFLIGHT_DNS_S', 0.05)
    result = await preflight_runner(ScenarioDriver(), 'public').call('scenario', 'emulator-5554', {
        'duration_s': 1, 'preflight': [{'url': 'https://slow.example.com/x'}]})
    assert result['verdict'] == 'blocked'
    assert result['preflight'][0]['error'] == 'request failed (TimeoutError): the DNS lookup timed out'


@pytest.mark.asyncio
async def test_preflight_checks_share_one_time_budget(monkeypatch):
    monkeypatch.setattr(ldr, 'PREFLIGHT_BUDGET_S', 0)
    driver = ScenarioDriver()
    result = await preflight_runner(driver).call('scenario', 'emulator-5554', {
        'duration_s': 1, 'preflight': [{'url': 'http://a.example.com/x'}, {'url': 'http://b.example.com/x'}]})
    assert result['verdict'] == 'blocked' and driver.calls == []
    assert all(check['error'].startswith('not checked: ') for check in result['preflight'])


@pytest.mark.asyncio
async def test_preflight_never_uses_the_runner_loma_session():
    server, base, seen = await config_server()
    try:
        runner = preflight_runner(ScenarioDriver())
        runner.session = object()                     # any use of the runner's own session would raise
        result = await runner.call('scenario', 'emulator-5554', {
            'duration_s': 1, 'end_after_steps': True, 'steps': [{'action': 'key', 'key': 'back'}], 'expect': {},
            'preflight': [{'url': base + '/sdk/init', 'contains': ['"loaderEnabled": true']}]})
    finally:
        await server.close()
    assert result['preflight'][0]['ok'] is True and len(seen) == 1


def test_suite_verdict_is_the_worst_outcome_and_counts_are_nested():
    def rows(*verdicts):
        return [{'name': f'c{i}', 'verdict': v} for i, v in enumerate(verdicts)]
    summary = service_module.suite_summary
    assert summary(rows('pass', 'pass'), [])['verdict'] == 'pass'
    assert summary(rows('pass'), ['later'])['verdict'] == 'fail'
    assert summary(rows('blocked', 'blocked'), [])['verdict'] == 'blocked'
    assert summary(rows('blocked', 'error'), [])['verdict'] == 'error'
    assert summary(rows('error', 'fail', 'blocked'), [])['verdict'] == 'fail'
    assert summary([], [])['verdict'] == 'fail'
    only_error = summary(rows('error'), [])
    assert only_error['counts'] == {'error': 1} and 'error' not in only_error


def test_preflight_is_validated_on_the_runner_and_the_backend():
    for bad in ([], [{'url': 'https://a.co'}] * 5, [{'url': 'file:///etc/passwd'}], [{'url': 'ftp://a.co/x'}],
                [{'url': 'https://a.co', 'method': 'DELETE'}], [{'url': 'https://a.co', 'body': 'x'}],  # body needs POST
                [{'url': 'https://a.co', 'headers': {'X-A': 'a\r\nX-B: b'}}], [{'url': 'https://a.co', 'status': 99}],
                [{'url': 'https://a.co', 'method': 'POST', 'body': 'x' * 5000}], [{'url': 'https://a.co', 'shell': 'id'}],
                [{'url': 'https://a.co', 'contains': 'ok'}], [{'url': 'https://a.co', 'timeout_s': 60}]):
        with pytest.raises(ldr.OpError):
            ldr.need_preflight({'preflight': bad})
        with pytest.raises(DeviceError):
            _validate('scenario', {'duration_s': 1, 'preflight': bad})
    good = [{'url': 'https://api.example.com/sdk/init?v=2', 'method': 'POST', 'headers': {'X-Api-Key': 'k'},
             'body': '{"a":1}', 'status': 201, 'contains': ['ok'], 'timeout_s': 5, 'name': 'init'}]
    _validate('scenario', {'duration_s': 1, 'preflight': good})
    [check] = ldr.need_preflight({'preflight': good})
    assert (check['host'], check['status'], check['name']) == ('api.example.com', 201, 'init')
    assert ldr.need_preflight({}) == []


# ── 4. A recording over the media limit is re-encoded, not dropped ────────


@pytest.mark.asyncio
async def test_fit_media_reencodes_a_large_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(ldr, 'MAX_MEDIA_BYTES', 100)
    source = tmp_path / 'rec.mp4'
    source.write_bytes(b'x' * 50)
    assert await ldr.fit_media(source, 'Recording', 12) == b'x' * 50         # small enough: untouched, no encoder run
    source.write_bytes(b'x' * 500)
    ran, sizes = [], {'ffmpeg': 300, 'Preset1280x720': 200, 'Preset960x540': 80}

    async def fake_run(args, **kwargs):
        ran.append(args[0] if args[0] == 'ffmpeg' else args[2])
        (tmp_path / 'reencoded.mp4').write_bytes(b's' * sizes[ran[-1]])
        return 0, b'', ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: '/usr/bin/' + tool if tool in ('ffmpeg', 'avconvert') else None)
    assert await ldr.fit_media(source, 'Recording', 12) == b's' * 80
    assert ran == ['ffmpeg', 'Preset1280x720', 'Preset960x540']              # best quality that fits
    ffmpeg = ldr.reencode_commands(source, tmp_path / 'out.mp4', 12)[0]
    assert ffmpeg[ffmpeg.index('-b:v') + 1] == '300k' and str(source) in ffmpeg  # bitrate floor for a tiny limit
    sizes.update({'Preset960x540': 400, 'Preset640x480': 400})
    with pytest.raises(ldr.OpError, match='could not be re-encoded smaller'):
        await ldr.fit_media(source, 'Recording', 12)
    assert not (tmp_path / 'reencoded.mp4').exists()
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: None)
    with pytest.raises(ldr.OpError, match='install ffmpeg on the runner machine'):
        await ldr.fit_media(source, 'Recording', 12)


# ── Backend: version gate ─────────────────────────────────────────────────


def test_a_1_2_runner_is_told_to_update_only_for_the_new_parts():
    old = type('C', (), {'version': '1.2.0'})()
    _check_runner_version(old, 'scenario', {'duration_s': 1, 'expect': {'app_running': True},
                                            'steps': [{'action': 'launch_app'}]})
    for args, what in (({'preflight': [{'url': 'https://a.co'}]}, 'preflight'),
                       ({'expect': {'max_drift_ms': 100}}, 'expect.max_drift_ms'),
                       ({'expect': {'logs': [SLOT]}}, 'number_after'),
                       ({'steps': [{'action': 'launch_app', 'activity': '.Main'}]}, 'launch_app options')):
        with pytest.raises(DeviceError, match=r'>= 1\.3\.0') as caught:
            _check_runner_version(old, 'scenario', {'duration_s': 1, **args})
        assert what in str(caught.value)
    current = type('C', (), {'version': ldr.VERSION})()
    _check_runner_version(current, 'scenario', {'duration_s': 1, 'preflight': [{'url': 'https://a.co'}],
                                                'expect': {'max_drift_ms': 100, 'logs': [SLOT]}})
    assert ldr.VERSION == '1.4.0'


# ── 7. Suite ──────────────────────────────────────────────────────────────


CASE = {'duration_s': 5, 'steps': [{'action': 'key', 'key': 'back'}], 'expect': {}}


def test_suite_plan_merges_defaults_and_requires_a_verdict_per_case():
    plan, reset, stop, keep = suite_plan({
        'defaults': {'app_id': 'com.example.demo', 'log_tags': ['T'], 'expect': {'app_running': True}},
        'cases': [{'name': 'login', **CASE, 'expect': {'logs': [{'match': 'ok'}]}},
                  {'name': 'deep_link', 'duration_s': 7, 'steps': []}]})
    assert (reset, stop, keep) == ('none', False, 'failed') and [name for name, _ in plan] == ['login', 'deep_link']
    assert plan[0][1]['expect'] == {'app_running': True, 'logs': [{'match': 'ok'}]}  # merged key by key
    assert plan[1][1] == {'app_id': 'com.example.demo', 'log_tags': ['T'], 'expect': {'app_running': True},
                          'duration_s': 7, 'steps': []}
    for bad, why in (({'cases': []}, 'list of 1-20'), ({'cases': [{'name': 'a', **CASE}] * 21}, 'list of 1-20'),
                     ({'cases': [{**CASE}]}, 'needs a name'), ({'cases': [{'name': 'a b', **CASE}]}, 'needs a name'),
                     ({'cases': [{'name': 'a', **CASE}, {'name': 'a', **CASE}]}, 'used twice'),
                     ({'cases': [{'name': 'a', 'duration_s': 5}]}, r'cases\[1\] \(a\): needs expect'),
                     ({'cases': [{'name': 'a', **CASE, 'duration_s': 99}]}, r'cases\[1\] \(a\): duration_s'),
                     ({'cases': [{'name': 'a', **CASE}], 'reset': 'reset_app'}, 'needs app_id'),
                     ({'cases': [{'name': 'a', **CASE}], 'reset': 'wipe'}, 'reset must be'),
                     ({'cases': [{'name': 'a', **CASE}], 'keep_video': 'none'}, 'keep_video'),
                     ({'cases': [{'name': 'a', **CASE}], 'defaults': {'steps': []}}, 'defaults'),
                     ({'cases': [{'name': 'a', **CASE}], 'parallel': 4}, 'suite needs cases'),
                     ({'cases': [{'name': f'c{i}', **CASE, 'duration_s': 60} for i in range(16)]}, 'at most 900 s'),
                     ({'cases': [{'name': f'c{i}', **CASE, 'duration_s': 60} for i in range(8)], 'retries': 1},
                      'retries included'),
                     ({'cases': [{'name': 'a', **CASE}], 'retries': 3}, 'retries must be'),
                     ({'cases': [{'name': 'a', **CASE}], 'setup': [{'action': 'install'}]}, r'setup\[1\]: action'),
                     ({'cases': [{'name': 'a', **CASE}], 'setup': [{'action': 'key', 'key': 'nope'}]},
                      r'setup\[1\] \(key\)'),
                     ({'cases': [{'name': 'a', **CASE, 'only': ['web']}]}, 'only must be'),
                     ({'cases': [{'name': 'a', **CASE}], 'platform_defaults': {'web': {}}}, 'platform_defaults')):
        with pytest.raises(DeviceError, match=why):
            suite_plan(bad)


class SuiteService(DeviceService):
    """The real suite loop over canned per-case scenario results."""

    def __init__(self, results):
        super().__init__(None)
        self.results, self.calls = dict(results), []

    async def call(self, user_email, scope, device_id, op, args):
        self.calls.append((op, args.get('app_id')))
        if op == 'reset_app':
            return {'cleared': args['app_id']}
        if op == 'health':
            return {'ok': True, 'screenshot_ms': 300}
        if op != 'scenario':
            return {}
        result = self.results[args['log_tags'][0]]
        if isinstance(result, Exception):
            raise result
        return dict(result)


def suite_args(*names, **extra):
    return {'defaults': {'app_id': 'com.example.demo', 'duration_s': 5, 'expect': {'app_running': True}},
            'cases': [{'name': name, 'log_tags': [name]} for name in names], **extra}


@pytest.mark.asyncio
async def test_suite_runs_each_case_and_returns_one_summary():
    service = SuiteService({
        'login': {'verdict': 'pass', 'ran_ms': 1200, 'mp4': b'V1', 'app_running': True, 'steps': [{'i': 1, 'ok': True}]},
        'loader': {'verdict': 'fail', 'ran_ms': 3000, 'mp4': b'V2', 'max_drift_ms': 900,
                   'failed': ["log 'slot': value 40 at 2100 ms is below 60", 'step 2 (key) ran 900 ms late'],
                   'steps': [{'i': 1, 'ok': True}, {'i': 2, 'ok': False, 'error': 'boom'}],
                   'logs': {'counts': {'slot': 3}, 'values': {'slot': {'count': 3}}, 'lines': [[1, 'x']] * 50},
                   'screenshots': [{'name': 'end', 'at_ms': 5, 'png': b'PNG'}]},
        'config': {'verdict': 'blocked', 'failed': ['preflight init: status 500 (expected 200)'],
                   'preflight': [{'name': 'init', 'ok': False, 'status': 500}]},
        'offline': DeviceError('Runner is offline')})
    result = await service.suite(OWNER, 'conv-1', 'r/e', suite_args('login', 'loader', 'config', 'offline',
                                                                   reset='reset_app'))
    assert (result['verdict'], result['total'], result['passed']) == ('fail', 4, 1)
    assert result['counts'] == {'blocked': 1, 'error': 1, 'fail': 1, 'pass': 1} and 'not_run' not in result
    assert 'error' not in result          # a top-level 'error' key reads as a broker denial in the isolated worker
    assert service.calls == [('health', None)] + [('reset_app', 'com.example.demo'), ('scenario', 'com.example.demo')] * 4
    login, loader, config, offline = result['cases']
    assert login == {'name': 'login', 'verdict': 'pass', 'ran_ms': 1200, 'app_running': True}  # video of a pass is dropped
    assert loader['mp4'] == b'V2' and loader['steps_not_ok'] == [{'i': 2, 'ok': False, 'error': 'boom'}]
    assert loader['logs'] == {'counts': {'slot': 3}, 'values': {'slot': {'count': 3}}}        # no raw lines
    assert config['preflight'][0]['status'] == 500 and offline == {
        'name': 'offline', 'verdict': 'error', 'failed': ['Runner is offline']}
    assert result['table'].splitlines() == [
        '| Case | Verdict | Ran (ms) | First reason |', '|---|---|---|---|',
        '| login | pass | 1200 |  |',
        "| loader | fail | 3000 | log 'slot': value 40 at 2100 ms is below 60 |",
        '| config | blocked |  | preflight init: status 500 (expected 200) |',
        '| offline | error |  | Runner is offline |']


@pytest.mark.asyncio
async def test_suite_stop_on_fail_keep_video_and_media_budget(monkeypatch):
    results = {'a': {'verdict': 'pass', 'mp4': b'A' * 10}, 'b': {'verdict': 'fail', 'failed': ['x'], 'mp4': b'B' * 10},
               'c': {'verdict': 'pass', 'mp4': b'C' * 10}}
    stopped = await SuiteService(results).suite(OWNER, 'conv-1', 'r/e', suite_args('a', 'b', 'c', stop_on_fail=True))
    assert stopped['not_run'] == ['c'] and stopped['verdict'] == 'fail' and len(stopped['cases']) == 2
    assert stopped['table'].splitlines()[-1] == '| c | not run | | stop_on_fail: an earlier case did not pass |'
    assert stopped['not_run_reason'].startswith('stop_on_fail') and '<skipped' in stopped['junit']
    monkeypatch.setattr(service_module, 'MAX_SUITE_MEDIA', 25)
    kept = await SuiteService({**results, 'b': results['a']}).suite(OWNER, 'conv-1', 'r/e',
                                                                   suite_args('a', 'b', 'c', keep_video='all'))
    assert kept['verdict'] == 'pass' and kept['passed'] == 3
    assert [('mp4' in case, 'media_dropped' in case) for case in kept['cases']] == [(True, False), (True, False),
                                                                                  (False, True)]


class SuiteTools(FakeService):
    async def suite(self, owner, scope, device_id, args):
        suite_plan(args)
        self.calls.append(('suite', device_id, args))
        return {'verdict': 'fail', 'total': 2, 'passed': 1, 'fail': 1, 'table': '| Case |', 'cases': [
            {'name': 'a', 'verdict': 'pass'},
            {'name': 'b', 'verdict': 'fail', 'failed': ['x'], 'mp4': b'MP4',
             'screenshots': [{'name': 'end', 'at_ms': 1, 'png': b'PNG'}]}]}


@pytest.mark.asyncio
async def test_isolated_suite_tool_delivers_case_media():
    artifacts = FakeArtifacts(AUTH)

    async def registry(receipt):
        return {'name': receipt['name'], 'url': '/files/' + receipt['name']}
    service = SuiteTools()
    tools = DeviceTools(None, AUTH, 'conv-1', artifacts=artifacts, service=service, on_artifact=registry)
    result = await tools(AUTH, 'device.suite', {'device_id': 'r_0123456789abcdef/e', 'cases': [{'name': 'a', **CASE}]})
    assert result['verdict'] == 'fail' and result['cases'][0] == {'name': 'a', 'verdict': 'pass'}
    failed = result['cases'][1]
    assert failed['video']['name'] == 'device-scenario-1.mp4' and failed['screenshots'][0]['step'] == 'end'
    assert 'mp4' not in failed and [data for _, data in artifacts.ingested] == [b'MP4', b'PNG']
    assert service.calls[0][1] == 'r_0123456789abcdef/e' and 'device_id' not in service.calls[0][2]
    bad = await tools(AUTH, 'device.suite', {'device_id': 'r_0123456789abcdef/e', 'cases': [{'name': 'a'}]})
    assert bad['ok'] is False and 'needs expect' in bad['device_error']


def test_catalog_suite_reuses_the_scenario_fields():
    from isolation.catalog import CATALOG, SCENARIO_FIELDS
    tools = {tool['name']: tool['input_schema'] for tool in CATALOG}
    scenario, suite = tools['device.scenario'], tools['device.suite']
    assert set(scenario['properties']) == {'device_id', *SCENARIO_FIELDS}
    assert scenario['required'] == ['device_id', 'duration_s']
    case = suite['properties']['cases']['items']['properties']
    assert set(case) == {'name', 'only', *SCENARIO_FIELDS} and 'steps' not in suite['properties']['defaults']['properties']
    step, expect = SCENARIO_FIELDS['steps']['items']['properties'], SCENARIO_FIELDS['expect']['properties']
    assert {'activity', 'extras', 'bool_extras', 'restart'} <= set(step) and 'max_drift_ms' in expect
    assert {'number_after', 'value_min', 'after_reaching', 'last_max'} <= set(expect['logs']['items']['properties'])
    assert SCENARIO_FIELDS['preflight']['maxItems'] == 4
    json.dumps(CATALOG)


def test_cli_suite_body_media_and_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('LOMA_CONVERSATION_DIR', str(tmp_path))
    spec = tmp_path / 'suite.yaml'
    spec.write_text('defaults: {app_id: com.example.demo, expect: {app_running: true}}\nreset: reset_app\n'
                    'cases:\n  - {name: login, duration_s: 5, steps: [{action: key, key: back}]}\n')
    argv = ['--user-email', OWNER, '--auth-token', 't', '--scope', 'c1', 'suite', '--device-id', 'r/e', '--spec', str(spec)]
    body = cli.build_body(cli.parser().parse_args(argv))
    assert (body['action'], body['device_id'], body['scope']) == ('suite', 'r/e', 'conv:c1') and 'op' not in body
    assert [name for name, _ in suite_plan(body['args'])[0]] == ['login']
    reply = {'verdict': 'fail', 'table': '| Case |', 'cases': [
        {'name': 'login', 'verdict': 'fail', 'mp4_base64': base64.b64encode(b'MP4').decode(),
         'screenshots': [{'name': 'end', 'png_base64': base64.b64encode(b'PNG').decode()}]}]}
    monkeypatch.setattr(cli, '_request', lambda *a, **k: json.loads(json.dumps(reply)))
    assert cli.main(argv) == 1                                               # a failed suite fails the command
    [case] = json.loads(capsys.readouterr().out)['cases']
    assert 'suite-login' in case['saved_to'] and open(case['saved_to'], 'rb').read() == b'MP4'
    assert 'suite-login-end' in case['screenshots'][0]['saved_to'] and 'mp4_base64' not in case
    reply.update(verdict='pass', cases=[])
    assert cli.main(argv) == 0
    monkeypatch.setattr(cli, '_request', lambda *a, **k: {'verdict': 'blocked', 'failed': ['preflight x']})
    (tmp_path / 'case.json').write_text('{"duration_s": 1}')
    assert cli.main(argv[:6] + ['scenario', '--device-id', 'r/e', '--spec', str(tmp_path / 'case.json')]) == 1


# ── End to end: backend suite -> WebSocket -> runner -> fake adb ──────────


SLOT_ADB = FAKE_ADB.replace("    log('ViewTag: content_shown')\n",
                            "    log('ViewTag: slot height=100')\n    log('ViewTag: slot height=%s' % "
                            "open(state + '.height').read())\n")


@pytest.mark.asyncio
async def test_suite_end_to_end_over_the_runner_websocket(tmp_path, monkeypatch):
    assert SLOT_ADB != FAKE_ADB
    script = tmp_path / 'adb'
    script.write_text(SLOT_ADB.replace('{python}', sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv('FAKE_STATE', str(tmp_path / 'state'))
    (tmp_path / 'state.height').write_text('40')                             # the slot collapses after reaching 100
    db = AsyncMongoMockClient()['loma_devices_suite']
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    creds = await store.redeem_enrollment(db, token, {'name': 'Mac', 'hostname': 'mac'})
    test_hub = RunnerHub()
    monkeypatch.setattr('device_loader.api.device_routes.hub', test_hub)
    flag = {'body': {'loaderEnabled': True}}

    async def sdk_init(request):
        return web.json_response(flag['body'])
    app = web.Application()
    setup_device_routes(app)
    app.router.add_get('/sdk/init', sdk_init)
    with patch('device_loader.api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        base = str(server.make_url('')).rstrip('/')
        try:
            async with aiohttp.ClientSession() as http:
                runner = ldr.Runner({'server': base, 'runner_id': creds['runner_id'], 'secret': creds['secret'],
                                     'policy': {'keep_awake': False, 'preflight': 'any'}},
                                    drivers=[ldr.Android(str(script))], session=http)
                task = asyncio.create_task(runner.connect_once())
                for _ in range(100):
                    if test_hub.get(creds['runner_id']):
                        break
                    await asyncio.sleep(0.05)
                assert test_hub.get(creds['runner_id']).version == ldr.VERSION
                service = DeviceService(db, hub=test_hub, blobs=BlobStore(tmp_path / 'blobs'))
                device = (await service.lease(OWNER, 'conv-1', platform='android'))['device_id']
                tap = {'action': 'tap', 'x': 10, 'y': 90}
                suite = {
                    'defaults': {'app_id': 'com.example.demo', 'duration_s': 20, 'end_after_steps': True,
                                 'log_tags': ['ViewTag'], 'record': True,
                                 'preflight': [{'name': 'sdk init', 'url': base + '/sdk/init',
                                                'contains': ['"loaderEnabled": true']}],
                                 'expect': {'app_running': True}},
                    'cases': [
                        {'name': 'slot_shown', 'steps': [tap], 'expect': {'logs': [{'match': 'slot', 'min': 2}]}},
                        {'name': 'slot_keeps_height', 'steps': [tap], 'expect': {'logs': [SLOT]}},
                        {'name': 'background_foreground', 'steps': [
                            tap, {'action': 'key', 'key': 'home'},
                            {'action': 'launch_app', 'activity': '.MainActivity', 'extras': {'screen': 'home'}}],
                         'expect': {'logs': [{'match': 'slot', 'number_after': 'height=', 'value_max': 100}]}}]}
                result = await service.suite(OWNER, 'conv-1', device, suite)
                print(result['table'])
                assert [case['verdict'] for case in result['cases']] == ['pass', 'fail', 'pass'], result
                assert result['health']['ok'] is True and result['platform'] == 'android'
                assert (result['verdict'], result['passed'], result['counts']) == ('fail', 2, {'fail': 1, 'pass': 2})
                shown, collapsed, relaunched = result['cases']
                assert 'mp4' not in shown and collapsed['mp4'] == b'MP4BYTES'  # video only for the failing case
                [reason] = collapsed['failed']
                assert reason.startswith("log 'slot': value 40 at ") and reason.endswith(' ms is below 60')
                assert collapsed['logs']['values']['slot'] == {'count': 2, 'min': 40.0, 'max': 100.0,
                                                               'first': 100.0, 'last': 40.0}
                assert f"| slot_keeps_height | fail | {collapsed['ran_ms']} | {reason} |" in result['table']
                calls = open(tmp_path / 'state.calls').read()
                # launch_app with an activity: no -S, so the backgrounded app comes back instead of restarting.
                assert "'am', 'start', '-W', '-n', 'com.example.demo/.MainActivity', '--es', 'screen', 'home'" in calls
                # The environment breaks (the flag is wiped): the case is blocked and adb is not called again.
                flag['body'] = {'loaderEnabled': False}
                before = len(open(tmp_path / 'state.calls').read().splitlines())
                async with http.post(base + '/internal/devices/call', headers={'X-Loma-User': OWNER},
                                     json={'action': 'suite'}) as denied:
                    assert denied.status == 401                               # the CLI route keeps its auth
                blocked = await service.suite(OWNER, 'conv-1', device, {**suite, 'stop_on_fail': True,
                                                                        'health_check': False})
                print(blocked['table'])
                assert blocked['verdict'] == 'blocked' and blocked['counts'] == {'blocked': 1}
                assert blocked['not_run'] == ['slot_keeps_height', 'background_foreground']
                assert blocked['cases'][0]['failed'] == [
                    'preflight sdk init: response does not contain \'"loaderEnabled": true\'']
                assert len(open(tmp_path / 'state.calls').read().splitlines()) == before
                audit = [a['op'] async for a in db.device_audit.find({'ok': True})]
                assert audit.count('scenario') == 4
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            await server.close()
