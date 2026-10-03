"""scenario, logs tags/cursor, screen-change signatures and keep-awake (runner 1.2.0)."""
import asyncio
import base64
import json
import struct
import sys
import time
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from mongomock_motor import AsyncMongoMockClient

from device_loader.api.device_routes import setup_device_routes
from device_loader.backend import store
from device_loader.backend.builds import BlobStore
from device_loader.backend.gateway import DeviceTools
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import DeviceService, _check_runner_version, _validate
from device_loader.cli import device as cli
from device_loader.runner import loma_device_runner as ldr
from device_loader.tests.test_devices_agent import AUTH, FakeArtifacts, FakeService

OWNER = 'owner@example.com'
W, H = 60, 120


def raw_frame(white_box=None, header=16):
    """An Android `screencap` raw frame: black, with an optional white box (x1, y1, x2, y2)."""
    pixels = bytearray(W * H * 4)
    if white_box:
        x1, y1, x2, y2 = white_box
        for y in range(y1, y2):
            for x in range(x1, x2):
                i = (y * W + x) * 4
                pixels[i:i + 4] = b'\xff\xff\xff\xff'
    head = struct.pack('<III', W, H, 1) + (b'\x00\x00\x00\x00' if header == 16 else b'')
    return head + bytes(pixels)


def bmp_frame(white_box=None, bpp=24, top_down=False):
    step = bpp // 8
    stride = (W * bpp + 31) // 32 * 4
    rows = bytearray(stride * H)
    if white_box:
        x1, y1, x2, y2 = white_box
        for y in range(y1, y2):
            row = y if top_down else H - 1 - y
            for x in range(x1, x2):
                i = row * stride + x * step
                rows[i:i + 3] = b'\xff\xff\xff'
    offset = 54
    header = b'BM' + struct.pack('<IHHI', offset + len(rows), 0, 0, offset)
    info = struct.pack('<IiiHHIIiiII', 40, W, -H if top_down else H, 1, bpp, 0, len(rows), 0, 0, 0, 0)
    return header + info + bytes(rows)


# ── Signatures and change periods ─────────────────────────────────────────


def test_raw_and_bmp_signatures_agree_and_locate_the_change():
    box = (0, 60, 30, 120)  # bottom-left quarter
    for header in (12, 16):
        assert ldr.android_raw_signature(raw_frame(header=header))['cells'] == [0] * (ldr.GRID_COLS * ldr.GRID_ROWS)
    raw = ldr.android_raw_signature(raw_frame(box))
    for bpp in (24, 32):
        for top_down in (False, True):
            assert ldr.bmp_signature(bmp_frame(box, bpp, top_down))['cells'] == raw['cells']
    changed, found = ldr.frame_change(ldr.android_raw_signature(raw_frame()), raw)
    assert 0.2 < changed < 0.3  # a quarter of the screen below the status bar crop
    assert found[0] == 0 and found[2] <= 32 and 55 <= found[1] <= 62 and found[3] == 120


def test_status_bar_changes_are_ignored_and_sizes_must_match():
    blank = ldr.android_raw_signature(raw_frame())
    clock = ldr.android_raw_signature(raw_frame((0, 0, W, 5)))  # top 5 %: status bar
    assert ldr.frame_change(blank, clock) == (0.0, None)
    assert ldr.frame_change(None, blank) is None
    with pytest.raises(ldr.OpError):
        ldr.android_raw_signature(raw_frame()[:-10])
    with pytest.raises(ldr.OpError):
        ldr.bmp_signature(b'PNG...')


def test_merge_changes_groups_bursts_into_periods():
    samples = [{'at_ms': at, 'changed': c, 'box': [at, 0, at + 1, 10]} for at, c in
               [(250, 0.1), (500, 0.3), (750, 0.2), (3000, 0.05)]]
    periods = ldr.merge_changes(samples, 250)
    assert periods == [{'from_ms': 250, 'to_ms': 750, 'frames': 3, 'max_changed': 0.3, 'box': [250, 0, 751, 10]},
                       {'from_ms': 3000, 'to_ms': 3000, 'frames': 1, 'max_changed': 0.05, 'box': [3000, 0, 3001, 10]}]


def test_need_steps_validation():
    ok = ldr.need_steps({'steps': [{'at_ms': 0, 'action': 'tap', 'x': 1, 'y': 2},
                                   {'at_ms': 500, 'action': 'tap_text', 'match': 'Continue'}]}, 1000)
    assert ok[1]['selector'] == ('Continue', 'any', False) and ok[1]['timeout_s'] == 5
    for bad in ([{'at_ms': 1000, 'action': 'tap', 'x': 1, 'y': 1}],           # outside the window
                [{'at_ms': 5, 'action': 'tap', 'x': 1, 'y': 1}, {'at_ms': 1, 'action': 'key', 'key': 'back'}],
                [{'at_ms': 0, 'action': 'shell', 'cmd': 'id'}],
                [{'at_ms': 0, 'action': 'tap', 'x': 1, 'y': 1, 'ref': 'e3'}],
                [{'at_ms': 0, 'action': 'open_url', 'url': 'file:///etc/passwd'}],
                [{'at_ms': 0, 'action': 'wait_for', 'match': 'x', 'timeout_s': 31}]):
        with pytest.raises(ldr.OpError):
            ldr.need_steps({'steps': bad}, 1000)


# ── Scenario on the runner (fake driver) ──────────────────────────────────


class ScenarioDriver:
    platform = 'android'

    def __init__(self):
        self.calls, self.screen, self.log = [], raw_frame(), []

    def _mark(self, what):
        self.calls.append((what, time.monotonic()))

    async def stop(self, serial, app_id):
        self._mark('stop')

    async def launch(self, serial, app_id, **options):
        self._mark('launch')
        self.log.append((time.time(), 'I/AppTag: first_event'))

    async def logs(self, serial, lines, clear, source='auto'):
        self._mark('logs_clear' if clear else 'logs')
        return []

    async def tap(self, serial, x, y):
        self._mark(f'tap {x},{y}')
        self.screen = raw_frame((0, 60, 30, 120))  # the tap reveals a white panel
        self.log.append((time.time(), 'I/ViewTag: content_shown'))

    async def key(self, serial, key):
        self._mark('key ' + key)

    async def ui_tree(self, serial):
        return {'screen': [W, H], 'elements': [{'text': 'Continue', 'center': [10, 90], 'clickable': True}]}

    async def frame(self, serial, region=None):
        await asyncio.sleep(0.01)
        return ldr.android_raw_signature(self.screen, region)

    async def record(self, serial, seconds, started=None, bitrate=None, stop=None):
        self._mark('record')
        self.bitrate = bitrate
        await asyncio.sleep(0.05)
        await started()
        try:
            await asyncio.wait_for(stop.wait(), seconds - 0.5)
        except asyncio.TimeoutError:
            pass
        return self.video

    video, running = b'MP4DATA', True

    async def is_running(self, serial, app_id):
        return self.running

    async def screenshot(self, serial):
        self._mark('screenshot')
        return b'\x89PNG-fake'

    async def type_text(self, serial, text):
        self._mark('type ' + text)
        return {}

    async def clear_text(self, serial, length):
        self._mark('clear_text')
        return {}

    async def swipe(self, serial, x1, y1, x2, y2, duration_ms):
        self._mark('swipe')

    async def timed_logs(self, serial, since, source='auto'):
        return [(at, line) for at, line in self.log if at >= since] + [(since + 0.1, 'I/Other: noise')]


def scenario_runner(driver):
    return ldr.Runner({'server': 'https://loma.test', 'secret': 's', 'runner_id': 'r', 'policy': {'keep_awake': False}},
                      drivers=[driver])


@pytest.mark.asyncio
async def test_scenario_runs_timed_steps_while_recording_sampling_and_logging():
    driver = ScenarioDriver()
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    result = await r.call('scenario', 'emulator-5554', {
        'app_id': 'com.example.demo', 'duration_s': 2, 'record': True, 'sample_ms': 150,
        'log_tags': ['AppTag', 'ViewTag'],
        'steps': [{'at_ms': 600, 'action': 'tap_text', 'match': 'Continue'}, {'at_ms': 1200, 'action': 'key', 'key': 'back'}]})
    names = [c[0] for c in driver.calls]
    assert names[:3] == ['stop', 'logs_clear', 'record'] and 'launch' in names  # recording before the launch
    assert result['launch']['ok'] and result['video_offset_ms'] >= 40
    first, second = result['steps']
    assert first['ok'] and first['tapped'] == 'Continue' and 580 <= first['ran_ms'] <= 800
    assert second['ok'] and 1180 <= second['ran_ms'] <= 1400
    frames = result['frames']
    assert frames['samples'] >= 6 and frames['units'] == 'pixels'
    [period] = frames['changes']
    assert first['ran_ms'] - 50 <= period['from_ms'] <= first['ran_ms'] + 400 and period['max_changed'] > 0.2
    logs = result['logs']
    assert logs['counts'] == {'AppTag': 1, 'ViewTag': 1}
    assert 0 <= logs['first_ms']['AppTag'] < logs['first_ms']['ViewTag']
    assert all('Other' not in line for _, line in logs['lines'])
    assert base64.b64decode(result['mp4_base64']) == b'MP4DATA'


@pytest.mark.asyncio
async def test_scenario_reports_failed_and_skipped_steps_without_aborting(monkeypatch):
    monkeypatch.setattr(ldr, 'SCENARIO_GRACE', 0.2)
    driver = ScenarioDriver()
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    result = await r.call('scenario', 'emulator-5554', {'duration_s': 1, 'steps': [
        {'at_ms': 0, 'action': 'tap_text', 'match': 'Missing', 'timeout_s': 0},
        {'at_ms': 100, 'action': 'tap', 'x': 5, 'y': 5},
        {'at_ms': 200, 'action': 'wait_for', 'match': 'Continue', 'gone': True, 'timeout_s': 3},
        {'at_ms': 900, 'action': 'key', 'key': 'back'}]})
    missing, tap, slow, last = result['steps']
    assert missing['ok'] is False and 'Missing' in missing['error']
    assert tap['ok'] is True and 'stop' not in [c[0] for c in driver.calls]  # no app_id: nothing stopped
    assert slow['ok'] is False and 'cancelled' in slow['error']  # still waiting at window end + grace
    assert last == {'i': 4, 'action': 'key', 'at_ms': 900, 'skipped': True}
    assert 'frames' not in result and 'logs' not in result and 'mp4_base64' not in result


@pytest.mark.asyncio
async def test_scenario_rejects_bad_specs_before_touching_the_device():
    driver = ScenarioDriver()
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    for bad in ({'duration_s': 61}, {'duration_s': 2, 'sample_ms': 50}, {'duration_s': 2, 'extras': {'a': 'b'}},
                {'duration_s': 2, 'log_tags': []}, {'duration_s': 1, 'steps': [{'at_ms': 1000, 'action': 'key', 'key': 'back'}]},
                {'duration_s': 2, 'sample_region': [0, 0, 10, 10]},                      # needs sample_ms
                {'duration_s': 2, 'sample_ms': 200, 'sample_region': [5, 5, 5, 9]},
                {'duration_s': 2, 'sample_ms': 200, 'sample_min_change': 2},
                {'duration_s': 2, 'expect': {'settled_by_ms': 500}},                     # needs sample_ms
                {'duration_s': 2, 'expect': {'logs': [{'match': 'x'}]}},                 # needs log_tags
                {'duration_s': 2, 'expect': {'app_running': True}},                      # needs app_id
                {'duration_s': 2, 'expect': {'colour': 'red'}},
                {'duration_s': 2, 'steps': [{'action': 'stop_app'}]},                    # no app to stop
                {'duration_s': 2, 'steps': [{'at_ms': 0, 'after_ms': 0, 'action': 'key', 'key': 'back'}]},
                {'duration_s': 2, 'steps': [{'action': 'screenshot', 'name': '../x'}]},
                {'duration_s': 2, 'steps': [{'action': 'screenshot'}] * 7}):
        with pytest.raises(ldr.OpError):
            await r.call('scenario', 'emulator-5554', bad)
    assert driver.calls == []
    r.allowed_apps = ('com.allowed',)
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        await r.call('scenario', 'emulator-5554', {'duration_s': 1, 'app_id': 'com.other'})


async def run_case(driver, spec, allowed=()):
    r = scenario_runner(driver)
    r.allowed_apps = allowed
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    return await r.call('scenario', 'emulator-5554', spec)


@pytest.mark.asyncio
async def test_scenario_runs_a_plain_sequential_flow_and_returns_early():
    """A functional case: no timings, steps one after the other, screenshots as evidence, a verdict."""
    driver = ScenarioDriver()
    result = await run_case(driver, {
        'app_id': 'com.example.app', 'duration_s': 30, 'end_after_steps': True, 'record': True, 'log_tags': ['ViewTag'],
        'steps': [{'action': 'tap_text', 'match': 'Continue'},
                  {'action': 'set_text', 'text': 'hello', 'clear': False},
                  {'action': 'scroll_until_visible', 'match': 'Continue'},
                  {'action': 'screenshot', 'name': 'done'},
                  {'action': 'key', 'key': 'home', 'after_ms': 100},
                  {'action': 'launch_app'}],
        'expect': {'app_running': True, 'logs': [{'match': 'content_shown'}, {'match': 'ERROR', 'max': 0}]}})
    assert [s['ok'] for s in result['steps']] == [True] * 6 and all('at_ms' not in s for s in result['steps'])
    assert result['ran_ms'] < 5000  # returned when the steps were done, not after 30 s
    assert result['verdict'] == 'pass' and 'failed' not in result and result['app_running'] is True
    [shot] = result['screenshots']
    assert shot['name'] == 'done' and base64.b64decode(shot['png_base64']).startswith(b'\x89PNG')
    assert base64.b64decode(result['mp4_base64']) == b'MP4DATA'
    assert [c[0] for c in driver.calls].count('launch') == 2 and 'type hello' in [c[0] for c in driver.calls]
    assert driver.bitrate < 4000000  # a 30 s window records at a lower bitrate to fit the media limit


@pytest.mark.asyncio
async def test_scenario_verdict_explains_each_failed_expectation():
    driver = ScenarioDriver()
    driver.running = False  # the app died
    result = await run_case(driver, {
        'app_id': 'com.example.app', 'duration_s': 2, 'sample_ms': 150, 'log_tags': ['AppTag', 'ViewTag'],
        'stop_on_fail': True,
        'steps': [{'at_ms': 800, 'action': 'tap', 'x': 5, 'y': 5},
                  {'action': 'tap_text', 'match': 'Missing', 'timeout_s': 0},
                  {'action': 'key', 'key': 'back'}],
        'expect': {'app_running': True, 'settled_by_ms': 300, 'log_order': ['ViewTag', 'AppTag'],
                   'logs': [{'match': 'content_shown', 'by_ms': 100}, {'match': 'first_event', 'max': 0},
                            {'match': 'never_logged'}]}})
    assert result['verdict'] == 'fail'
    reasons = ' | '.join(result['failed'])
    for expected in ('step 2 (tap_text)', 'step 3 (key) did not run', 'app is not running', 'screen still changing',
                     "'content_shown': first at", "'first_event': 1 lines (expected at most 0)",
                     "'never_logged': 0 lines", "log order: 'AppTag'"):
        assert expected in reasons, expected
    assert result['steps'][2] == {'i': 3, 'action': 'key', 'skipped': True}  # stop_on_fail


@pytest.mark.asyncio
async def test_scenario_sample_region_and_min_change_filter_the_timeline():
    spec = {'duration_s': 1, 'sample_ms': 150, 'steps': [{'at_ms': 300, 'action': 'tap', 'x': 5, 'y': 5}]}
    # The tap paints the bottom-left quarter. Watching the top half sees nothing ...
    result = await run_case(ScenarioDriver(), {**spec, 'sample_region': [0, 0, W, H // 2 - 5]})
    assert result['frames']['changes'] == [] and result['frames']['region'] == [0, 0, W, H // 2 - 5]
    # ... watching the bottom-left quarter sees all of it change ...
    result = await run_case(ScenarioDriver(), {**spec, 'sample_region': [0, 60, 30, 120]})
    assert result['frames']['changes'][0]['max_changed'] > 0.9
    # ... and a floor above the change size drops it from the whole-screen timeline.
    result = await run_case(ScenarioDriver(), {**spec, 'sample_min_change': 0.5})
    assert result['frames']['changes'] == []
    with pytest.raises(ldr.OpError, match='too small or off screen'):
        ldr.android_raw_signature(raw_frame(), (0, 0, 10, 10))


@pytest.mark.asyncio
async def test_scenario_keeps_the_result_when_the_video_cannot_be_delivered(monkeypatch):
    monkeypatch.setattr(ldr, 'MAX_MEDIA_BYTES', 4)
    result = await run_case(ScenarioDriver(), {'duration_s': 1, 'record': True, 'steps': [{'action': 'key', 'key': 'back'}]})
    assert 'mp4_base64' not in result and 'over the media limit' in result['video_error']
    assert result['steps'][0]['ok'] is True


@pytest.mark.asyncio
async def test_scenario_app_steps_respect_the_runner_app_allowlist():
    with pytest.raises(ldr.OpError, match='allowed_app_ids'):
        await run_case(ScenarioDriver(), {'duration_s': 1, 'app_id': 'com.allowed',
                                          'steps': [{'action': 'launch_app', 'app_id': 'com.other'}]}, ('com.allowed',))


def test_judge_passes_when_every_rule_holds():
    expect = ldr.need_expect({'expect': {'settled_by_ms': 900, 'log_order': ['a', 'b'],
                                         'logs': [{'match': 'A', 'min': 2, 'after_ms': 5}]}}, True, True, False)
    result = {'steps': [{'i': 1, 'action': 'tap', 'ok': True}], 'frames': {'samples': 4, 'last_change_ms': 800}}
    assert ldr.judge(expect, result, [(10, 'x a'), (20, 'x A again'), (30, 'b')]) == []
    assert ldr.judge(expect, {'frames': {'samples': 0}}, [(10, 'a'), (11, 'a'), (30, 'b')]) == [
        'no screen samples were captured, so settled_by_ms cannot be checked']


# ── Logs: tags + cursor ───────────────────────────────────────────────────


class LogDriver(ScenarioDriver):
    async def logs(self, serial, lines, clear, source='auto'):
        return ['I/A: one', 'I/B: two', 'I/C: three', 'I/A: four']


@pytest.mark.asyncio
async def test_logs_tags_counts_and_since_cursor():
    driver = LogDriver()
    r = scenario_runner(driver)
    r.inventory = {'emulator-5554': (driver, {'serial': 'emulator-5554'})}
    first = await r.call('logs', 'emulator-5554', {'tags': ['I/A', 'I/B']})
    assert first['lines'] == ['I/A: one', 'I/B: two', 'I/A: four'] and first['counts'] == {'I/A': 2, 'I/B': 1}
    assert first['cursor'].startswith('t:')
    driver.log = [(time.time() - 60, 'I/A: old'), (time.time() + 1, 'I/A: new')]
    later = await r.call('logs', 'emulator-5554', {'since': first['cursor'], 'tags': ['I/A']})
    assert later['lines'] == ['I/A: new'] and later['cursor'] >= first['cursor']
    with pytest.raises(ldr.OpError, match='since or clear'):
        await r.call('logs', 'emulator-5554', {'since': first['cursor'], 'clear': True})
    with pytest.raises(ldr.OpError):
        await r.call('logs', 'emulator-5554', {'since': 'x:1'})


def test_ios_console_since_reads_only_new_complete_lines(tmp_path):
    ios = ldr.IOS()
    path = tmp_path / 'console.log'
    path.write_bytes(b'one\ntwo\npart')
    ios.consoles['SIM'] = (None, path)
    lines, end = ios.console_since('SIM', 0)
    assert lines == ['one', 'two'] and end == 8
    path.write_bytes(b'one\ntwo\npartial done\nthree\n')
    assert ios.console_since('SIM', end) == (['partial done', 'three'], 27)
    path.write_bytes(b'fresh\n')  # truncated by logs clear: start again
    assert ios.console_since('SIM', 27) == (['fresh'], 6)


@pytest.mark.asyncio
async def test_android_timed_logs_use_epoch_format_and_clock_skew(monkeypatch):
    now = time.time()
    calls = []

    async def fake_run(args, timeout=60, check=True, **kwargs):
        calls.append(args)
        if args[-2:] == ['date', '+%s.%N']:
            return 0, f'{now + 100:.6f}\n'.encode(), ''  # device clock 100 s ahead
        lines = [f'{now + 100 - 30:.3f}  1  2 I Tag: before', '--------- beginning of main',
                 f'{now + 100 + 1:.3f}  1  2 I Tag: after']
        return 0, '\n'.join(lines).encode(), ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    entries = await ldr.Android('adb').timed_logs('emulator-5554', now - 5)
    assert [line for _, line in entries] == [f'{now + 101:.3f}  1  2 I Tag: after']
    assert abs(entries[0][0] - (now + 1)) < 0.5
    assert ['adb', '-s', 'emulator-5554', 'logcat', '-d', '-v', 'epoch'] in calls


@pytest.mark.asyncio
async def test_android_frame_reads_raw_screencap_without_the_output_cap(monkeypatch):
    seen = {}

    async def fake_run(args, timeout=60, check=True, limit=None, **kwargs):
        seen.update(args=args, limit=limit)
        return 0, raw_frame(), ''
    monkeypatch.setattr(ldr, 'run', fake_run)
    signature = await ldr.Android('adb').frame('emulator-5554')
    assert seen['args'] == ['adb', '-s', 'emulator-5554', 'exec-out', 'screencap'] and seen['limit'] > ldr.MAX_OUTPUT
    assert signature['width'] == W


# ── Keep awake ────────────────────────────────────────────────────────────


def test_keep_awake_holds_one_caffeinate_and_renews(monkeypatch):
    spawned = []

    class Proc:
        def __init__(self, argv, **kwargs):
            self.argv, self.alive = argv, True
            spawned.append(self)

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            self.alive = False

        def wait(self, timeout=None):
            return 0
    clock = [1000.0]
    monkeypatch.setattr(ldr.sys, 'platform', 'darwin')
    monkeypatch.setattr(ldr.shutil, 'which', lambda tool: '/usr/bin/' + tool)
    monkeypatch.setattr(ldr.subprocess, 'Popen', Proc)
    awake = ldr.KeepAwake()
    monkeypatch.setattr(ldr.time, 'monotonic', lambda: clock[0])
    awake.touch()
    awake.touch()
    assert len(spawned) == 1 and spawned[0].argv == ['caffeinate', '-i', '-s', '-t', '900']
    clock[0] += ldr.KeepAwake.RENEW + 1
    awake.touch()
    assert len(spawned) == 2 and spawned[0].alive is False and spawned[1].alive
    monkeypatch.setattr(ldr.sys, 'platform', 'linux')
    ldr.KeepAwake().touch()
    assert len(spawned) == 2
    assert ldr.Runner({'policy': {'keep_awake': False}}, drivers=[]).keep_awake.enabled is False


# ── Backend validation, version gate, CLI, isolated tool ──────────────────


@pytest.mark.parametrize('args', [
    {'duration_s': 5},
    {'duration_s': 20, 'app_id': 'com.example.demo', 'record': True, 'sample_ms': 250, 'log_tags': ['A'],
     'log_source': 'console', 'console': True, 'extras': {'k': 'v'},
     'steps': [{'at_ms': 0, 'action': 'tap', 'x': 1, 'y': 2}, {'at_ms': 10, 'action': 'tap_text', 'match': 'Go'},
               {'at_ms': 19999, 'action': 'wait_for', 'match': 'Done', 'gone': True, 'timeout_s': 10}]},
])
def test_service_accepts_valid_scenarios(args):
    _validate('scenario', {'duration_s': 60, 'app_id': 'com.example.app', 'end_after_steps': True, 'stop_on_fail': True,
                           'sample_ms': 250, 'sample_region': [0, 100, 1080, 900], 'sample_min_change': 0.02,
                           'log_tags': ['A'],
                           'steps': [{'action': 'set_text', 'match': 'Email', 'text': 'a@b.co'},
                                     {'action': 'scroll_until_visible', 'match': 'Pay', 'direction': 'down'},
                                     {'action': 'screenshot', 'name': 'cart'}, {'action': 'stop_app'},
                                     {'action': 'launch_app', 'app_id': 'com.example.other', 'after_ms': 500},
                                     {'action': 'wait_for', 'match': 'Home', 'timeout_s': 30}],
                           'expect': {'steps_ok': True, 'app_running': True, 'settled_by_ms': 5000,
                                      'logs': [{'match': 'paid', 'min': 1, 'by_ms': 9000}], 'log_order': ['a', 'b']}})
    _validate('scenario', args)


@pytest.mark.parametrize('args', [
    {}, {'duration_s': 61}, {'duration_s': 5, 'sample_ms': 100}, {'duration_s': 5, 'console': True},
    {'duration_s': 5, 'log_source': 'console'}, {'duration_s': 5, 'log_tags': ['x'] * 9},
    {'duration_s': 5, 'steps': [{'at_ms': 5000, 'action': 'tap', 'x': 1, 'y': 1}]},
    {'duration_s': 5, 'steps': [{'at_ms': 0, 'action': 'tap', 'ref': 'e1'}]},
    {'duration_s': 5, 'steps': [{'at_ms': 0, 'action': 'install', 'app_id': 'x'}]},
    {'duration_s': 5, 'steps': [{'at_ms': 0, 'action': 'tap_text', 'match': 'x', 'timeout_s': 31}]},
    {'duration_s': 5, 'steps': [{'at_ms': 0, 'action': 'key', 'key': 'reboot'}]},
])
def test_service_rejects_bad_scenarios(args):
    with pytest.raises(DeviceError):
        _validate('scenario', args)


@pytest.mark.parametrize('extra', [
    {'sample_region': [0, 0, 100, 100]}, {'sample_ms': 200, 'sample_region': [9, 0, 3, 100]},
    {'sample_ms': 200, 'sample_min_change': 1.5}, {'expect': {'settled_by_ms': 100}},
    {'expect': {'logs': [{'match': 'x'}]}}, {'log_tags': ['A'], 'expect': {'logs': [{'match': 'x', 'min': '1'}]}},
    {'expect': {'app_running': True}}, {'expect': {'unknown': 1}}, {'steps': [{'action': 'stop_app'}]},
    {'steps': [{'action': 'screenshot', 'name': 'a b'}]}, {'steps': [{'action': 'screenshot'}] * 7},
    {'steps': [{'at_ms': 1, 'after_ms': 1, 'action': 'key', 'key': 'back'}]},
    {'steps': [{'action': 'launch_app', 'app_id': 'bad id'}]},
])
def test_service_rejects_bad_generic_scenario_fields(extra):
    with pytest.raises(DeviceError):
        _validate('scenario', {'duration_s': 5, **extra})


def test_record_keeps_its_own_20_second_limit():
    _validate('record', {'duration_s': 20})
    with pytest.raises(DeviceError):
        _validate('record', {'duration_s': 21})


def test_service_logs_tags_and_since():
    _validate('logs', {'tags': ['A', 'B'], 'since': 't:1700000000.123'})
    _validate('logs', {'since': 'c:1024'})
    for bad in ({'since': 'x:1'}, {'since': 't:1', 'clear': True}, {'tags': 'A'}, {'tags': []}):
        with pytest.raises(DeviceError):
            _validate('logs', bad)


def test_old_runners_are_told_to_update_for_new_ops():
    old = type('C', (), {'version': '1.1.0'})()
    with pytest.raises(DeviceError, match=r'>= 1\.2\.0'):
        _check_runner_version(old, 'scenario', {'duration_s': 1})
    with pytest.raises(DeviceError, match='logs with tags'):
        _check_runner_version(old, 'logs', {'tags': ['A']})
    _check_runner_version(old, 'logs', {'filter': 'A'})
    _check_runner_version(old, 'tap_text', {'match': 'x'})
    with pytest.raises(DeviceError, match=r'>= 1\.1\.0'):
        _check_runner_version(type('C', (), {'version': '1.0.0'})(), 'tap_text', {'match': 'x'})
    with pytest.raises(DeviceError, match=r'>= 1\.2\.0'):
        _check_runner_version(type('C', (), {'version': '1.0.0'})(), 'scenario', {'duration_s': 1})
    _check_runner_version(type('C', (), {'version': ldr.VERSION})(), 'scenario', {'duration_s': 1})


def test_cli_scenario_spec_logs_cursor_and_env_auth(tmp_path, monkeypatch):
    spec = tmp_path / 'case.yaml'
    spec.write_text('app_id: com.example.demo\nduration_s: 4\nsample_ms: 250\nsteps:\n'
                    '  - {at_ms: 1500, action: tap_text, match: Continue}\n')
    monkeypatch.setenv('LOMA_USER_EMAIL', OWNER)
    monkeypatch.setenv('LOMA_AUTH_TOKEN', 'tok')
    args = cli.parser().parse_args(['--scope', 'c1', 'scenario', '--device-id', 'r/e', '--spec', str(spec)])
    assert args.user_email == OWNER and args.auth_token == 'tok'
    body = cli.build_body(args)
    assert body['op'] == 'scenario' and body['args']['steps'][0] == {'at_ms': 1500, 'action': 'tap_text', 'match': 'Continue'}
    _validate('scenario', body['args'])
    (tmp_path / 'case.json').write_text(json.dumps({'duration_s': 2}))
    args = cli.parser().parse_args(['--scope', 'c1', 'scenario', '--device-id', 'r/e', '--spec', str(tmp_path / 'case.json')])
    assert cli.build_body(args)['args'] == {'duration_s': 2}
    args = cli.parser().parse_args(['--scope', 'c1', 'logs', '--device-id', 'r/e', '--tag', 'A', '--tag', 'B',
                                    '--since', 't:1.5'])
    assert cli.build_body(args)['args'] == {'lines': 300, 'clear': False, 'tags': ['A', 'B'], 'since': 't:1.5'}
    monkeypatch.delenv('LOMA_AUTH_TOKEN')
    with pytest.raises(SystemExit):
        cli.main(['--scope', 'c1', 'list'])


def test_cli_saves_the_scenario_video(tmp_path, monkeypatch):
    monkeypatch.setenv('LOMA_CONVERSATION_DIR', str(tmp_path))
    (tmp_path / 'spec.json').write_text('{"duration_s": 1}')
    args = cli.parser().parse_args(['--user-email', OWNER, '--auth-token', 't', '--scope', 'c1', 'scenario',
                                    '--device-id', 'r/e', '--spec', str(tmp_path / 'spec.json')])
    result = cli.save_media(args, {'mp4_base64': base64.b64encode(b'MP4').decode(), 'steps': []})
    assert open(result['saved_to'], 'rb').read() == b'MP4' and 'mp4_base64' not in result


class ScenarioService(FakeService):
    async def call(self, owner, scope, device_id, op, args):
        _validate(op, args)
        self.calls.append((op, args))
        return ({'steps': [], 'mp4': b'MP4', 'screenshots': [{'name': 'home', 'at_ms': 5, 'png': b'PNG'}]}
                if op == 'scenario' else {'ok': op})


@pytest.mark.asyncio
async def test_isolated_scenario_tool_delivers_the_video():
    artifacts = FakeArtifacts(AUTH)

    async def registry(receipt):
        return {'name': receipt['name'], 'url': '/files/' + receipt['name']}
    service = ScenarioService()
    tools = DeviceTools(None, AUTH, 'conv-1', artifacts=artifacts, service=service, on_artifact=registry)
    result = await tools(AUTH, 'device.scenario', {'device_id': 'r_0123456789abcdef/e', 'duration_s': 3,
                                                   'steps': [{'at_ms': 0, 'action': 'key', 'key': 'back'}]})
    assert result['video']['name'] == 'device-scenario-1.mp4' and artifacts.ingested[0][1] == b'MP4'
    assert service.calls[0][0] == 'scenario' and 'device_id' not in service.calls[0][1]
    assert result['screenshots'][0]['step'] == 'home' and artifacts.ingested[1][1] == b'PNG'
    logs = await tools(AUTH, 'device.observe', {'device_id': 'r_0123456789abcdef/e', 'what': 'logs',
                                                'tags': ['A'], 'since': 't:1'})
    assert logs == {'ok': 'logs'}
    bad = await tools(AUTH, 'device.scenario', {'device_id': 'r_0123456789abcdef/e', 'duration_s': 99})
    assert bad['ok'] is False and 'duration_s' in bad['device_error']


# ── End to end: backend service -> WebSocket -> runner -> fake adb ────────


FAKE_ADB = r'''#!{python}
import os, struct, sys, time
args = sys.argv[1:]
state = os.environ['FAKE_STATE']
def log(line):
    with open(state + '.logcat', 'a') as handle:
        handle.write('%.3f  100  101 I %s\n' % (time.time(), line))
with open(state + '.calls', 'a') as handle:
    handle.write(repr(args) + '\n')
W, H = 60, 120
if args[:2] == ['devices', '-l']:
    print('List of devices attached')
    print('emulator-5554          device product:sdk model:Pixel_8 transport_id:1')
elif args[2:4] == ['shell', 'getprop']:
    print('14')
elif args[2:] == ['exec-out', 'screencap']:
    pixels = bytearray(W * H * 4)
    if os.path.exists(state + '.tapped'):
        for y in range(60, 120):
            for x in range(0, 30):
                pixels[(y * W + x) * 4:(y * W + x) * 4 + 4] = b'\xff\xff\xff\xff'
    sys.stdout.buffer.write(struct.pack('<IIII', W, H, 1, 0) + bytes(pixels))
elif args[2:] == ['exec-out', 'screencap', '-p']:
    sys.stdout.buffer.write(b'\x89PNG\r\n\x1a\n' + bytes(32))
elif args[2:4] == ['shell', 'pidof']:
    print('4242')
elif args[2:] == ['shell', 'date', '+%s.%N']:
    print('%.9f' % time.time())
elif args[2:5] == ['logcat', '-d', '-v']:
    sys.stdout.write(open(state + '.logcat').read() if os.path.exists(state + '.logcat') else '')
elif args[2:4] == ['logcat', '-c']:
    open(state + '.logcat', 'w').close()
elif args[2:4] == ['shell', 'monkey']:
    log('AppTag: first_event')
elif args[2:5] == ['shell', 'input', 'tap']:
    open(state + '.tapped', 'w').close()
    log('ViewTag: content_shown')
elif args[2:4] == ['shell', 'screenrecord']:
    time.sleep(min(int(args[args.index('--time-limit') + 1]), 3) - 0.5)
elif args[2] == 'pull':
    open(args[-1], 'wb').write(b'MP4BYTES')
'''


@pytest.mark.asyncio
async def test_scenario_end_to_end_over_the_runner_websocket(tmp_path, monkeypatch):
    script = tmp_path / 'adb'
    script.write_text(FAKE_ADB.replace('{python}', sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv('FAKE_STATE', str(tmp_path / 'state'))
    db = AsyncMongoMockClient()['loma_devices_scenario']
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
                device = (await service.lease(OWNER, 'conv-1', platform='android'))['device_id']
                result = await service.call(OWNER, 'conv-1', device, 'scenario', {
                    'app_id': 'com.example.demo', 'duration_s': 2, 'record': True, 'sample_ms': 200,
                    'log_tags': ['AppTag', 'ViewTag'],
                    'steps': [{'at_ms': 700, 'action': 'tap', 'x': 10, 'y': 90}]})
                assert result['mp4'] == b'MP4BYTES' and result['launch']['ok']
                assert result['steps'][0]['ok'] and result['steps'][0]['ran_ms'] >= 690
                [period] = result['frames']['changes']
                assert period['from_ms'] >= 600 and period['box'][0] == 0
                assert result['logs']['counts'] == {'AppTag': 1, 'ViewTag': 1}
                assert result['logs']['first_ms']['ViewTag'] >= 600
                calls = open(tmp_path / 'state.calls').read()
                assert "'am', 'force-stop', 'com.example.demo'" in calls and "'logcat', '-c'" in calls
                # The same op as a plain functional case: steps in order, early finish, screenshot, verdict.
                flow = await service.call(OWNER, 'conv-1', device, 'scenario', {
                    'app_id': 'com.example.demo', 'duration_s': 30, 'end_after_steps': True, 'record': True,
                    'log_tags': ['ViewTag'],
                    'steps': [{'action': 'tap', 'x': 10, 'y': 90}, {'action': 'screenshot', 'name': 'after_tap'},
                              {'action': 'key', 'key': 'back', 'after_ms': 100}],
                    'expect': {'app_running': True, 'logs': [{'match': 'content_shown'}, {'match': 'FATAL', 'max': 0}]}})
                assert flow['verdict'] == 'pass' and flow['app_running'] is True and flow['ran_ms'] < 10000
                assert [step['ok'] for step in flow['steps']] == [True, True, True]
                assert flow['screenshots'][0]['name'] == 'after_tap' and flow['screenshots'][0]['png'][:4] == b'\x89PNG'
                assert flow['mp4'] == b'MP4BYTES'  # the recording was stopped early and still pulled
                assert 'pkill -INT screenrecord' in open(tmp_path / 'state.calls').read()
                # Logs cursor round trip through the backend.
                first = await service.call(OWNER, 'conv-1', device, 'logs', {'tags': ['ViewTag']})
                assert first['counts'] == {'ViewTag': 1}
                again = await service.call(OWNER, 'conv-1', device, 'logs', {'since': first['cursor']})
                assert again['lines'] == []
                audit = [a['op'] async for a in db.device_audit.find({'ok': True})]
                assert 'scenario' in audit
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            await server.close()


def test_dashboard_flags_runners_that_need_an_update():
    from device_loader.api.device_routes import _runner_view
    view = _runner_view({'runner_id': 'r_0123456789abcdef', 'owner_email': OWNER, 'version': '1.1.0'}, OWNER)
    assert view['update_available'] is True and view['latest_version'] == ldr.VERSION
    current = _runner_view({'runner_id': 'r_0123456789abcdef', 'owner_email': OWNER, 'version': ldr.VERSION}, OWNER)
    assert current['update_available'] is False
    assert _runner_view({'runner_id': 'r_0123456789abcdef', 'owner_email': OWNER}, OWNER)['update_available'] is False
