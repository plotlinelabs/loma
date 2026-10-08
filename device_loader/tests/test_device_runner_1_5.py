"""Runner 1.5.0: media out of band, suite resume after a reconnect, iOS recording, DNS health, shimmer-safe
UI reads, raised limits, iOS log merge, installed apps, device templates and relative spec paths."""
import asyncio
import base64
import json
import sys
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from mongomock_motor import AsyncMongoMockClient

from device_loader.api.device_routes import _clean_templates, setup_device_routes
from device_loader.backend import media as media_store
from device_loader.backend import store
from device_loader.backend.builds import BlobStore
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import (DeviceService, _case_row, _check_runner_version, _decode, _validate,
                                           suite_plan)
from device_loader.cli import device as cli
from device_loader.runner import loma_device_runner as ldr
from device_loader.tests.test_device_scenario import FAKE_ADB, ScenarioDriver, scenario_runner

OWNER = 'owner@example.com'


# ── 1. Media out of band ──────────────────────────────────────────────────


def test_media_store_is_bound_to_the_runner_taken_once_and_checked():
    store_ = media_store.MediaStore()
    media_id = store_.put('r1', b'VIDEO', sha256=__import__('hashlib').sha256(b'VIDEO').hexdigest())
    assert store_.take('r2', media_id) is None             # another runner cannot read it
    assert store_.take('r1', media_id) == b'VIDEO'
    assert store_.take('r1', media_id) is None             # taken once
    with pytest.raises(media_store.MediaError, match='checksum'):
        store_.put('r1', b'x', sha256='0' * 64)
    with pytest.raises(media_store.MediaError):
        store_.put('r1', b'')
    old = store_.put('r1', b'OLD')
    store_._prune(now=10 ** 12)                              # expired
    assert store_.take('r1', old) is None


def test_resolve_swaps_media_ids_for_bytes_and_reports_missing_ones():
    store_ = media_store.MediaStore()
    video = store_.put('r', b'MP4')
    shot = store_.put('r', b'PNG')
    data = {'mp4_media': video, 'screenshots': [{'name': 'a', 'png_media': shot}, {'name': 'b', 'png_media': 'gone'}]}
    out = media_store.resolve(data, 'r', store_)
    assert out['mp4_base64'] == b'MP4' and out['screenshots'][0]['png_base64'] == b'PNG'
    assert 'png_media' not in out['screenshots'][1] and 'expired' in out['media_error']
    assert _decode(b'raw', 'x') == b'raw' and _decode(base64.b64encode(b'z').decode(), 'x') == b'z'


@pytest.mark.asyncio
async def test_runner_moves_large_media_out_of_the_frame_and_keeps_small_media_inline(monkeypatch):
    r = ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'policy': {}}, drivers=[])
    uploaded = []

    async def upload(raw, kind):
        uploaded.append((kind, raw))
        return f'media{len(uploaded):04d}'
    monkeypatch.setattr(r, 'upload_media', upload)
    big = base64.b64encode(b'V' * (ldr.MEDIA_INLINE_MAX)).decode()
    small = base64.b64encode(b'png').decode()
    data = await r.offload_media({'mp4_base64': big, 'screenshots': [{'name': 's', 'png_base64': small}],
                                  'frames': [{'png_base64': big}]})
    assert data['mp4_media'] == 'media0001' and 'mp4_base64' not in data
    assert data['screenshots'][0]['png_base64'] == small                   # small: inline
    assert data['frames'][0]['png_media'] == 'media0002'
    assert uploaded[0] == ('mp4', b'V' * ldr.MEDIA_INLINE_MAX)

    async def failing(raw, kind):
        raise ldr.OpError('media upload failed (HTTP 502)')
    monkeypatch.setattr(r, 'upload_media', failing)
    kept = await r.offload_media({'mp4_base64': big})
    assert kept['mp4_base64'] == big and 'media_error' not in kept           # under 4 MB: kept inline
    huge = base64.b64encode(b'V' * (5 * 1024 * 1024)).decode()
    dropped = await r.offload_media({'mp4_base64': huge, 'verdict': 'pass'})
    assert 'mp4_base64' not in dropped and 'HTTP 502' in dropped['media_error'] and dropped['verdict'] == 'pass'


@pytest.mark.asyncio
async def test_scenario_video_travels_over_http_end_to_end(tmp_path, monkeypatch):
    """backend service -> WebSocket -> runner -> fake adb, with the video uploaded to /device-runner/media."""
    script = tmp_path / 'adb'
    script.write_text(FAKE_ADB.replace('{python}', sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv('FAKE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(ldr, 'MEDIA_INLINE_MAX', 1)  # every media item goes out of band
    test_media = media_store.MediaStore()
    monkeypatch.setattr(media_store, 'media', test_media)
    db = AsyncMongoMockClient()['loma_devices_media']
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    creds = await store.redeem_enrollment(db, token, {'name': 'Mac', 'hostname': 'mac'})
    test_hub = RunnerHub()
    monkeypatch.setattr('device_loader.api.device_routes.hub', test_hub)
    app = web.Application()
    setup_device_routes(app)
    with patch('device_loader.api.device_routes.get_db', return_value=db):
        server = TestServer(app)
        await server.start_server()
        base = str(server.make_url('')).rstrip('/')
        try:
            async with aiohttp.ClientSession() as http:
                runner = ldr.Runner({'server': base, 'runner_id': creds['runner_id'], 'secret': creds['secret'],
                                     'policy': {'keep_awake': False}}, drivers=[ldr.Android(str(script))], session=http)
                task = asyncio.create_task(runner.connect_once())
                for _ in range(100):
                    if test_hub.get(creds['runner_id']):
                        break
                    await asyncio.sleep(0.05)
                service = DeviceService(db, hub=test_hub, blobs=BlobStore(tmp_path / 'blobs'))
                leased = await service.lease(OWNER, 'conv-1', platform='android')
                assert leased['health']['network']['dns_ok'] is True and leased['installed']['apps'] == []
                result = await service.call(OWNER, 'conv-1', leased['device_id'], 'scenario', {
                    'app_id': 'com.example.demo', 'duration_s': 30, 'end_after_steps': True, 'record': True,
                    'steps': [{'action': 'screenshot', 'name': 'shot'}], 'expect': {'app_running': True}})
                assert result['mp4'] == b'MP4BYTES' and result['verdict'] == 'pass'
                assert result['screenshots'][0]['png'][:4] == b'\x89PNG'
                assert test_media.items == {}                          # every upload was collected
                # A wrong runner secret cannot upload.
                async with http.post(base + '/device-runner/media', data=b'x',
                                     headers={'X-Loma-Runner-Id': creds['runner_id'],
                                              'Authorization': 'Bearer nope'}) as response:
                    assert response.status == 401
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            await server.close()


# ── 2. Suite resumes after the runner reconnects ──────────────────────────


class ReconnectingSuite(DeviceService):
    def __init__(self, failures):
        super().__init__(db=None, hub=type('Hub', (), {'get': lambda self, rid: object()})())
        self.failures, self.calls = list(failures), []

    async def _platform(self, user_email, device_id):
        return 'android'

    async def _resolve(self, user_email, device_id):
        return {'runner_id': 'r'}, 'emulator-5554'

    async def call(self, user_email, scope, device_id, op, args):
        self.calls.append(op)
        if op == 'scenario' and self.failures:
            raise DeviceError(self.failures.pop(0))
        return {'verdict': 'pass', 'ran_ms': 10, 'logs': {'lines': [[5, 'I/T: ok']]}} if op == 'scenario' else {'ok': True}


@pytest.mark.asyncio
async def test_suite_waits_for_a_reconnected_runner_and_reruns_the_case(monkeypatch):
    monkeypatch.setattr(asyncio, 'sleep', _no_sleep)
    service = ReconnectingSuite(['Runner reconnected'])
    result = await service.suite(OWNER, 'conv-1', 'r/emulator-5554', {
        'health_check': False, 'keep_log_lines': 5,
        'cases': [{'name': 'a', 'duration_s': 5, 'expect': {}}, {'name': 'b', 'duration_s': 5, 'expect': {}}]})
    assert result['verdict'] == 'pass' and service.calls == ['scenario', 'scenario', 'scenario']
    assert result['cases'][0]['runner_reconnected'] is True and result['reconnects'][0]['runner_back'] is True
    assert result['cases'][1]['log_lines'] == [[5, 'I/T: ok']]   # evidence kept for a passing case
    # A runner that keeps dropping is not retried forever: the second drop of the same case is an error.
    flaky = ReconnectingSuite(['Runner disconnected', 'Runner disconnected'])
    result = await flaky.suite(OWNER, 'conv-1', 'r/emulator-5554', {
        'health_check': False, 'cases': [{'name': 'a', 'duration_s': 5, 'expect': {}}]})
    assert result['cases'][0]['verdict'] == 'error' and flaky.calls == ['scenario', 'scenario']


_real_sleep = asyncio.sleep


async def _no_sleep(_seconds=0, *args, **kwargs):
    await _real_sleep(0)


def test_keep_log_lines_validation_and_row():
    with pytest.raises(DeviceError, match='keep_log_lines'):
        suite_plan({'cases': [{'name': 'a', 'duration_s': 5, 'expect': {}}], 'keep_log_lines': 41})
    row, _ = _case_row('a', {'verdict': 'pass', 'logs': {'lines': [[1, 'x'], [2, 'y']]}}, 'failed', 10, keep_lines=1)
    assert row['log_lines'] == [[2, 'y']]
    row, _ = _case_row('a', {'verdict': 'pass', 'logs': {'lines': [[1, 'x']]}}, 'failed', 10)
    assert 'log_lines' not in row


# ── 3. iOS recording that exits at once ───────────────────────────────────


class FakeProc:
    def __init__(self, exits):
        self.returncode = 1 if exits else None
        self.exits, self.stderr = exits, _Reader(b'Error: device busy' if exits else b'')

    async def wait(self):
        if not self.exits:
            await asyncio.sleep(3600)
        return self.returncode

    def send_signal(self, sig):
        raise ProcessLookupError()

    def kill(self):
        raise ProcessLookupError()


class _Reader:
    def __init__(self, data):
        self.data = data

    async def read(self):
        return self.data


@pytest.mark.asyncio
async def test_ios_recording_that_exits_at_once_is_retried_then_reported(monkeypatch):
    procs = [FakeProc(True), FakeProc(True)]

    async def spawn(*argv, **kwargs):
        return procs.pop(0)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(ldr.OpError, match='exited at once \\(twice\\): Error: device busy'):
        await ldr.IOS()._start_recording('UDID', '/tmp/x.mp4')
    procs[:] = [FakeProc(True), FakeProc(False)]
    proc = await ldr.IOS()._start_recording('UDID', '/tmp/x.mp4')
    assert proc.returncode is None                              # the retry is recording


@pytest.mark.asyncio
async def test_a_recorder_that_fails_to_start_keeps_the_case_result():
    driver = ScenarioDriver()

    async def broken(serial, seconds, started=None, bitrate=None, stop=None):
        raise ldr.OpError('simctl recordVideo exited at once (twice): busy')
    driver.record = broken
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    result = await r.call('scenario', 'emulator-5554', {
        'duration_s': 5, 'record': True, 'end_after_steps': True, 'steps': [{'action': 'key', 'key': 'back'}],
        'expect': {}})
    assert result['verdict'] == 'pass' and 'busy' in result['video_error'] and 'mp4_base64' not in result


# ── 4. DNS in health, DNS-fixing recover ──────────────────────────────────


class NetDriver(ScenarioDriver):
    def __init__(self, dns_ok):
        super().__init__()
        self.dns_ok, self.recovered = dns_ok, None

    async def net_probe(self, serial, host):
        return {'host': host, 'dns_ok': self.dns_ok, 'reachable': self.dns_ok, 'ms': 5}

    async def recover(self, serial, known, restart=False, allow_global=True, extra_args=()):
        self.recovered = (restart, list(extra_args))
        self.dns_ok = True
        return {'method': 'cold_boot', 'steps': ['kill', 'cold_boot']}

    async def list(self):
        return [{'serial': 'emulator-5554', 'platform': 'android', 'virtual': True, 'name': 'Pixel'}]


@pytest.mark.asyncio
async def test_health_fails_on_broken_dns_and_recover_cold_boots_with_a_dns_server():
    driver = NetDriver(dns_ok=False)
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554', 'virtual': True, 'platform': 'android'})}
    r.known = {'emulator-5554': {'platform': 'android', 'virtual': True, 'avd': 'Pixel', 'port': 5554}}
    health = await r.call('health', 'emulator-5554', {})
    assert health['ok'] is False and 'cannot resolve DNS' in health['reasons'][0]
    result = await r.call('recover', 'emulator-5554', {})
    restart, extra = driver.recovered
    assert restart is True and extra[extra.index('-dns-server') + 1] == ldr.DNS_FALLBACK and result['dns_fix'] is True
    assert result['health']['ok'] is True
    off = scenario_runner(NetDriver(dns_ok=False))
    off.policy['net_probe_host'] = 'off'
    off.inventory = {'emulator-5554': (off.drivers[0], {'serial': 'emulator-5554'})}
    assert (await off.call('health', 'emulator-5554', {}))['ok'] is True and 'network' not in \
        await off.call('health', 'emulator-5554', {})


@pytest.mark.asyncio
async def test_android_net_probe_only_fails_on_unknown_host(monkeypatch):
    outputs = iter([(2, b'', 'ping: unknown host api.example.com'), (1, b'1 packets transmitted, 0 received', '')])

    async def fake_run(argv, **kwargs):
        return next(outputs)
    monkeypatch.setattr(ldr, 'run', fake_run)
    driver = ldr.Android('adb')
    assert (await driver.net_probe('emulator-5554', 'api.example.com'))['dns_ok'] is False
    probe = await driver.net_probe('emulator-5554', 'api.example.com')  # ICMP blocked, DNS fine
    assert probe['dns_ok'] is True and probe['reachable'] is False


# ── 7. UI that never goes idle ────────────────────────────────────────────


class Shimmer(ScenarioDriver):
    def __init__(self):
        super().__init__()
        self.reads = []

    async def ui_tree(self, serial, frozen=False):
        self.reads.append(frozen)
        if not frozen:
            raise ldr.OpError('uiautomator could not capture the screen (UI not idle?)')
        return {'screen': [1080, 2400], 'elements': [{'text': 'Offers', 'center': [5, 5]}]}


@pytest.mark.asyncio
async def test_scroll_and_wait_switch_to_the_frozen_dump_after_one_not_idle_read():
    driver = Shimmer()
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    found = await r.call('scroll_until_visible', 'emulator-5554', {'match': 'Offers'})
    assert found['found'] and found['ui_not_idle'] and driver.reads == [False, True]
    driver.reads.clear()
    waited = await r.call('wait_for', 'emulator-5554', {'match': 'Offers', 'timeout_s': 5})
    assert waited['found'] and driver.reads == [False, True]


def test_frozen_dump_restores_the_animator_scale_in_the_same_round_trip():
    script = ldr.ANDROID_UI_DUMP_FROZEN
    assert script.index('animator_duration_scale 0') < script.index('uiautomator dump')
    assert script.index('uiautomator dump') < script.index('animator_duration_scale "$a"') < script.index('cat ')


# ── 8. Limits, version gates ──────────────────────────────────────────────


def test_raised_limits_need_runner_1_5():
    spec = {'duration_s': 90, 'steps': [{'action': 'wait_for', 'match': 'x', 'timeout_s': 90}],
            'log_tags': ['T'], 'expect': {'logs': [{'match': str(i)} for i in range(20)]}}
    _validate('scenario', spec)
    old = type('C', (), {'version': '1.4.0'})()
    with pytest.raises(DeviceError, match=r'>= 1\.5\.0') as caught:
        _check_runner_version(old, 'scenario', spec)
    assert 'duration_s over 60' in str(caught.value) and 'more than 12 log rules' in str(caught.value)
    with pytest.raises(DeviceError, match=r'>= 1\.5\.0'):
        _check_runner_version(old, 'wait_for', {'match': 'x', 'timeout_s': 90})
    _check_runner_version(old, 'scenario', {'duration_s': 30})   # within the old limits: still fine
    _check_runner_version(type('C', (), {'version': '1.5.0'})(), 'scenario', spec)
    assert ldr.need_steps({'steps': [{'action': 'wait_for', 'match': 'x', 'timeout_s': 120}]}, 120000)
    with pytest.raises(DeviceError):
        _validate('scenario', {'duration_s': 121})


def test_relative_spec_paths_resolve_in_the_conversation_dir(tmp_path, monkeypatch):
    workspace = tmp_path / 'ws'
    (workspace / 'conversations' / 'c1' / 'e2e').mkdir(parents=True)
    (workspace / 'conversations' / 'c1' / 'e2e' / 'suite.json').write_text(json.dumps({'cases': []}))
    monkeypatch.setenv('LOMA_WORKSPACE_DIR', str(workspace))
    monkeypatch.delenv('LOMA_CONVERSATION_DIR', raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.resolve_spec_path('e2e/suite.json', 'c1') == str(workspace / 'conversations' / 'c1' / 'e2e' / 'suite.json')
    with pytest.raises(SystemExit, match='looked in'):
        cli.resolve_spec_path('missing.yaml', 'c1')
    monkeypatch.setenv('LOMA_CONVERSATION_DIR', str(workspace / 'conversations' / 'c1'))
    assert cli.resolve_spec_path('e2e/suite.json').endswith('e2e/suite.json')


# ── 10. iOS log merge / de-duplication ────────────────────────────────────


def test_ios_duplicate_copies_are_merged_but_repeated_events_are_kept():
    line = '2026-10-08 10:00:00.100 Df Runner[1:2] flutter: PL_E2E widget_loaded'
    copy = '2026-10-08 10:00:00.100 Df Runner[1:2] (Flutter) flutter: PL_E2E widget_loaded'
    entries = [(100, line, 'system'), (100, copy, 'system'), (180, 'flutter: PL_E2E widget_loaded', 'console'),
               (2000, line, 'system')]
    kept = ldr.dedupe_log_entries(entries)
    assert [at for at, _ in kept] == [100, 2000]      # 3 copies of one event -> 1; the later event stays
    assert ldr.log_message(line) == ldr.log_message(copy) == 'PL_E2E widget_loaded'


@pytest.mark.asyncio
async def test_scenario_auto_logs_on_ios_merge_console_and_system():
    driver = ScenarioDriver()
    driver.platform = 'ios'
    r = scenario_runner(driver)

    async def system(serial, since, source='auto'):
        return [(since + 1.2, '2026-10-08 10:00:00.100 Df Runner[1:2] flutter: T done'),
                (since + 1.2, '2026-10-08 10:00:00.100 Df Runner[1:2] flutter: T done'),
                (since + 1.5, '2026-10-08 10:00:00.400 Df Runner[1:2] flutter: T only_in_system')]
    driver.timed_logs = system
    logs, kept = await r._scenario_logs(driver, 'U', 'auto', ['T'], 0.0, [(200, 'flutter: T done')], 50)
    assert logs['counts'] == {'T': 2} and logs['merged'] == {'console': 1, 'system': 3, 'kept': 2}


# ── 11. installed ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_installed_reports_user_apps_and_current_builds():
    driver = ScenarioDriver()

    async def user_apps(serial):
        return ['com.example.demo', 'com.other']

    async def stamp(serial, package):
        return 'stamp-2' if package == 'com.other' else 'stamp-1'
    driver.user_apps, driver.install_stamp = user_apps, stamp
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    r.installed = {('emulator-5554', 'com.example.demo'): ('a' * 64, 'stamp-1'),
                   ('emulator-5554', 'com.other'): ('b' * 64, 'stamp-1'),
                   ('emulator-9999', 'com.example.demo'): ('c' * 64, 'x')}
    result = await r.call('installed', 'emulator-5554', {})
    assert result['apps'] == ['com.example.demo', 'com.other']
    assert result['builds'] == {'com.example.demo': {'sha256': 'a' * 64, 'current': True},
                                'com.other': {'sha256': 'b' * 64, 'current': False}}   # replaced since


# ── 6. Device templates ───────────────────────────────────────────────────


def test_templates_from_the_runner_config_are_validated():
    templates = ldr.load_templates({'templates': [
        {'name': 'pixel-clean', 'platform': 'android', 'avd': 'Pixel_7_API_34', 'snapshot': 'clean'},
        {'name': 'iphone', 'platform': 'ios', 'simulator': 'iPhone 15 Pro'},
        {'name': 'bad name!', 'platform': 'android', 'avd': 'x'},
        {'name': 'flags', 'platform': 'android', 'avd': '-wipe-data'},
        {'name': 'web', 'platform': 'web', 'avd': 'x'}]})
    assert set(templates) == {'pixel-clean', 'iphone'}
    assert templates['iphone']['base'] == 'iPhone 15 Pro' and templates['pixel-clean']['idle_shutdown_s'] == 1800
    assert _clean_templates([{'name': 'pixel-clean', 'platform': 'android', 'clean': True, 'avd': 'leak'},
                             {'name': '../x', 'platform': 'android'}]) == [
        {'name': 'pixel-clean', 'platform': 'android', 'clean': True}]


class BootDriver(ScenarioDriver):
    def __init__(self):
        super().__init__()
        self.booted, self.stopped = [], []

    async def boot(self, template, clean, extra_args=()):
        self.booted.append((template['name'], clean))
        return 'emulator-5556', {}

    async def shutdown(self, serial, info):
        self.stopped.append(serial)
        return {'shutdown': serial}

    async def list(self):
        return [{'serial': s, 'platform': 'android', 'virtual': True, 'name': 'Pixel'} for s in ('emulator-5556',)
                if s not in self.stopped]


@pytest.mark.asyncio
async def test_runner_boots_templates_shuts_down_only_its_own_and_reaps_idle_ones(monkeypatch):
    driver = BootDriver()
    r = ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'policy': {'keep_awake': False},
                    'templates': [{'name': 'pixel', 'platform': 'android', 'avd': 'Pixel_7', 'snapshot': 'clean'}]},
                   drivers=[driver])
    assert r.template_list() == [{'name': 'pixel', 'platform': 'android', 'clean': True}]
    with pytest.raises(ldr.OpError, match='runner-level'):
        await r.call('boot', 'emulator-5554', {'template': 'pixel'})
    with pytest.raises(ldr.OpError, match='No device template'):
        await r.call('boot', '-', {'template': 'nope'})
    booted = await r.call('boot', '-', {'template': 'pixel', 'clean': True})
    assert booted == {'serial': 'emulator-5556', 'booted': True, 'template': 'pixel', 'clean': True}
    assert driver.booted == [('pixel', True)] and 'emulator-5556' in r.booted
    r.booted['emulator-5556']['last_used'] = 0          # idle for ages
    await r.reap_idle()
    assert driver.stopped == ['emulator-5556'] and r.booted == {} and 'emulator-5556' not in r.known
    r.inventory['emulator-5554'] = (driver, {'serial': 'emulator-5554'})
    with pytest.raises(ldr.OpError, match='Only devices this runner booted'):
        await r.call('shutdown', 'emulator-5554', {})


class TemplateService(DeviceService):
    """The real lease / release over a fake hub with one template-capable runner."""

    def __init__(self, devices=()):
        class Conn:
            version, templates = '1.5.0', [{'name': 'pixel', 'platform': 'android', 'clean': True}]

        class Hub:
            calls = []

            def get(self, runner_id):
                return Conn()

            async def call(self, runner_id, op, serial, args):
                self.calls.append((op, serial, args))
                if op == 'boot':
                    return {'serial': 'emulator-5556', 'booted': True}
                if op == 'health':
                    return {'ok': True}
                if op == 'installed':
                    return {'apps': [], 'builds': {}}
                return {'shutdown': serial}
        super().__init__(db=AsyncMongoMockClient()['loma_templates'], hub=Hub())
        self.devices = list(devices)

    async def runners_for(self, user_email):
        return [{'runner_id': 'r_0123456789abcdef', 'name': 'Mac', 'owner_email': OWNER}]

    async def list_devices(self, user_email):
        return self.devices

    async def _resolve(self, user_email, device_id):
        runner_id, serial = device_id.split('/', 1)
        return {'runner_id': runner_id, 'owner_email': OWNER}, serial


@pytest.mark.asyncio
async def test_lease_boots_a_template_when_nothing_runs_and_release_shuts_it_down():
    service = TemplateService()
    result = await service.lease(OWNER, 'conv-1', platform='android')
    assert result['booted'] is True and result['template'] == 'pixel' and result['clean'] is True
    assert result['device_id'] == 'r_0123456789abcdef/emulator-5556' and result['health'] == {'ok': True}
    assert service.hub.calls[0] == ('boot', '-', {'template': 'pixel', 'clean': True})
    released = await service.release(OWNER, 'conv-1', result['device_id'])
    assert released == {'released': True, 'shutdown': True}
    assert service.hub.calls[-1] == ('shutdown', 'emulator-5556', {})
    explicit = await TemplateService().lease(OWNER, 'conv-2', template='pixel', clean=False)
    assert explicit['clean'] is False
    with pytest.raises(DeviceError, match='No device template matches'):
        await TemplateService().lease(OWNER, 'conv-3', template='iphone')
    with pytest.raises(DeviceError, match='cannot be combined'):
        await TemplateService().lease(OWNER, 'conv-3', device_id='r_0123456789abcdef/e', template='pixel')
    old = TemplateService()
    old.hub.get = lambda runner_id: type('C', (), {'version': '1.4.0', 'templates': []})()
    with pytest.raises(DeviceError, match='No devices are registered'):
        await old.lease(OWNER, 'conv-4', platform='android')       # no 1.5 runner: no auto-boot


def test_cli_lease_with_template():
    body = cli.build_body(cli.parser().parse_args(
        ['--user-email', OWNER, '--auth-token', 't', '--scope', 'c1', 'lease', '--template', 'pixel', '--clean']))
    assert body == {'scope': 'conv:c1', 'action': 'lease', 'device_id': None, 'platform': None,
                    'template': 'pixel', 'clean': True}
