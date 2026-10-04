#!/usr/bin/env python3
"""Loma Device Runner: lets a Loma agent drive local Android emulators and iOS simulators.

The runner runs on a machine you control (for example your Mac). It makes ONE
outbound WebSocket connection to your Loma server. It never listens on a port,
so no tunnel, DNS record or firewall change is needed.

Security model (read before running):
- The runner executes a FIXED allowlist of device operations (install, launch,
  open_url, screenshot, ui_tree, tap, swipe, type, key, logs, run_flow, ...).
  There is no shell or file-read operation. Every argument is type-checked here
  as well as on the server.
- Physical devices are hidden unless you set allow_physical_devices=true, so a
  personal phone plugged in over USB is never exposed by accident.
- Optional allowed_app_ids restricts which app ids the agent may install, launch,
  stop, reset or uninstall.
- Maestro flows are screened against a command allowlist: runScript/evalScript/
  runFlow/addMedia, file: sub-flows and inline JavaScript (${...}) are rejected
  unless allow_maestro_scripts=true, because Maestro JavaScript can make HTTP
  calls from this machine.
- Revoking the runner in the Loma dashboard (or stopping this process) cuts off
  access immediately.

Usage:
  python3 loma_device_runner.py setup --server https://loma.example.com --token lde_...
  python3 loma_device_runner.py setup        # upgrade/restart an enrolled runner
  python3 loma_device_runner.py doctor
  python3 loma_device_runner.py uninstall

`setup` copies the runner and its dependencies (aiohttp, PyYAML) into ~/.loma-device-runner
(a private virtualenv), enrolls this machine, checks tooling and starts a login service
(launchd on macOS, systemd --user on Linux). `--foreground` runs it in the terminal instead.

Requires Python 3.9+ (the macOS system python3 works). Device tooling is optional and
detected at runtime: adb (Android), xcrun simctl (iOS simulators), idb (iOS taps / UI tree),
maestro (run_flow). adb/maestro are also found in their default install dirs.
"""
import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import platform
import plistlib
import random
import re
import shlex
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlparse

VERSION = '1.3.0'  # the backend gates newer ops/arguments on this (device_loader/backend/service.py RUNNER_GATES)
PROTOCOL = 1
CONFIG_DIR = Path(os.environ.get('LOMA_DEVICE_RUNNER_HOME', Path.home() / '.loma-device-runner'))
CONFIG_PATH = CONFIG_DIR / 'config.json'
HEARTBEAT_SECONDS = 15
MAX_WS_MESSAGE = 24 * 1024 * 1024
MAX_BLOB = 500 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
MAX_FLOW = 64 * 1024
MAX_TEXT = 500
MAX_UI_ELEMENTS = 400
LABEL = 'so.loma.device-runner'

SERIAL = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')
APP_ID = re.compile(r'[A-Za-z0-9_]+(?:[.-][A-Za-z0-9_]+)*\Z')
BLOB_ID = re.compile(r'[A-Za-z0-9_-]{8,64}\Z')
SHA256 = re.compile(r'[a-f0-9]{64}\Z')
FILENAME = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
ANDROID_KEYS = {'back': 4, 'home': 3, 'enter': 66, 'delete': 67, 'tab': 61, 'app_switch': 187,
                'volume_up': 24, 'volume_down': 25, 'power': 26, 'escape': 111, 'wakeup': 224}
IOS_BUTTONS = {'home': 'HOME', 'lock': 'LOCK', 'siri': 'SIRI', 'side': 'SIDE_BUTTON', 'apple_pay': 'APPLE_PAY'}
# USB HID usage codes for `idb ui key` / `idb ui key-sequence` (simulator hardware keyboard).
IOS_HID_KEYS = {'enter': 40, 'escape': 41, 'delete': 42, 'tab': 43}
IOS_FORWARD_DELETE = 76
ANDROID_MOVE_END, ANDROID_FORWARD_DEL = 123, 112
EXTRA_KEY = re.compile(r'[A-Za-z0-9_.]{1,100}\Z')
ACTIVITY = re.compile(r'[A-Za-z0-9_.$]{1,255}\Z')
APPOP = re.compile(r'[A-Z][A-Z0-9_]{2,63}\Z')
# Services accepted by `xcrun simctl privacy <udid> grant <service> <bundle>`.
IOS_PRIVACY = {'all', 'calendar', 'contacts-limited', 'contacts', 'location', 'location-always', 'photos-add',
               'photos', 'media-library', 'microphone', 'motion', 'reminders', 'siri'}
SELECT_BY = {'any', 'text', 'id', 'label'}
MAX_EXTRAS = 20
MAX_MEDIA_BYTES = 16 * 1024 * 1024  # burst frames / recordings / flow screenshots per result (WS frame: 24 MiB)
POLL_SECONDS = 0.4
SWIPE_MS = 1000  # scroll_until_visible: slow, fixed-distance swipes give the same scroll every run
# One adb round trip for the UI tree: dump and cat in a single device-side shell (a constant string).
ANDROID_UI_DUMP = ('rm -f /sdcard/loma_ui.xml; uiautomator dump --compressed /sdcard/loma_ui.xml >&2 '
                   '&& cat /sdcard/loma_ui.xml')
# `am start` failures: "Error: Activity class {...} does not exist." / "Error type 3" line prefixes
# (not 'Error' anywhere, which would match an activity named e.g. .ErrorReportActivity).
AM_ERROR = re.compile(r'^\s*Error(?::| type\b)', re.M)
ANDROID_WAKEUP = 'input keyevent 224; wm dismiss-keyguard'
# Animation scales off (0) or back to the default (1), in one round trip. With animations off,
# uiautomator reaches "idle" quickly (a running animation blocks ui_tree for seconds), and taps
# and screenshots don't land mid-transition. Turn them back on to test an animation itself.
ANDROID_ANIMATION_SCALES = ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale')
MAX_EXTRACT_BYTES = 2 * MAX_BLOB
MAX_EXTRACT_MEMBERS = 20000
MAX_NESTED_ZIPS = 5
BLOCKED_URL_SCHEMES = {'file', 'javascript', 'data'}
# Maestro commands a flow may use by default. Anything else (runScript, evalScript,
# runFlow, addMedia, startRecording, assertWithAI, ...) needs allow_maestro_scripts,
# because those can run JavaScript with HTTP access or read/write host files.
FLOW_COMMANDS = {
    'launchApp', 'stopApp', 'killApp', 'clearState', 'clearKeychain', 'tapOn', 'doubleTapOn', 'longPressOn',
    'inputText', 'inputRandomText', 'inputRandomNumber', 'inputRandomEmail', 'inputRandomPersonName',
    'eraseText', 'copyTextFrom', 'pasteText', 'assertVisible', 'assertNotVisible', 'scroll', 'scrollUntilVisible',
    'swipe', 'back', 'pressKey', 'hideKeyboard', 'openLink', 'waitForAnimationToEnd', 'extendedWaitUntil',
    'takeScreenshot', 'repeat', 'retry', 'setAirplaneMode', 'toggleAirplaneMode', 'setLocation', 'travel',
}
FLOW_CONFIG_KEYS = {'appId', 'name', 'tags', 'env', 'onFlowStart', 'onFlowComplete'}
APP_COMMANDS = {'launchApp', 'stopApp', 'killApp', 'clearState'}
# logs: tags (any of, case-insensitive) and a cursor ('t:<epoch>' time, 'c:<offset>' iOS console bytes)
# that the next logs call passes as since, to get only the lines written after this one.
MAX_LOG_TAGS = 8
CURSOR = re.compile(r'[tc]:[0-9]{1,20}(?:\.[0-9]{1,6})?\Z')
EPOCH_LINE = re.compile(r'\s*([0-9]{9,11}\.[0-9]+)\s')  # logcat -v epoch
IOS_LOG_TIME = re.compile(r'([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]+)?')  # log show compact
# scenario: one call runs timed steps while the runner records video, samples screen changes and
# captures logs, so the agent spends one model turn per test case instead of ~10.
# Nothing here is specific to one kind of test: steps are the same inputs as the single ops, timed
# either at a fixed offset (at_ms) or one after the other (after_ms), and expect turns the result
# into pass/fail on the runner.
SCENARIO_STEPS = {'tap': ({'x', 'y'}, set()), 'swipe': ({'x1', 'y1', 'x2', 'y2'}, {'duration_ms'}),
                  'type': ({'text'}, set()), 'key': ({'key'}, set()), 'open_url': ({'url'}, set()),
                  'tap_text': ({'match'}, {'by', 'exact', 'timeout_s'}),
                  'wait_for': ({'match'}, {'by', 'exact', 'timeout_s', 'gone'}),
                  'set_text': ({'text'}, {'match', 'by', 'exact', 'clear'}),
                  'clear_text': (set(), {'match', 'by', 'exact'}),
                  'scroll_until_visible': ({'match'}, {'by', 'exact', 'direction', 'max_swipes'}),
                  'screenshot': (set(), {'name'}),
                  'launch_app': (set(), {'app_id', 'activity', 'extras', 'bool_extras', 'restart'}),
                  'stop_app': (set(), {'app_id'})}
STEP_TIMING = {'at_ms', 'after_ms'}
MAX_SCENARIO_STEPS = 40
MAX_SCENARIO_SECONDS = 60
MAX_SCENARIO_SHOTS = 6
MAX_STEP_WAIT = 30
END_TAIL = 0.7  # end_after_steps: keep capturing this long after the last step, to catch its effect
SHOT_NAME = re.compile(r'[A-Za-z0-9_-]{1,40}\Z')
EXPECT_KEYS = {'steps_ok', 'app_running', 'settled_by_ms', 'max_drift_ms', 'logs', 'log_order'}
# A log rule can also read a number out of each matching line (the first number after the
# literal text number_after; no regular expressions from the agent) and check it.
VALUE_KEYS = ('value_min', 'value_max', 'after_reaching', 'last_min', 'last_max')
LOG_EXPECT_KEYS = {'match', 'min', 'max', 'by_ms', 'after_ms', 'number_after', *VALUE_KEYS}
NUMBER = re.compile(r'\s*[:=]?\s*([-+]?[0-9]+(?:\.[0-9]+)?)')
MAX_VALUE = 10 ** 12
# preflight: HTTP checks made from the runner before the device is touched, so a broken test
# environment (backend down, a config flag wiped) is reported as "blocked", not as a test failure.
# Only the status and which `contains` texts were missing are returned, never the response body.
PREFLIGHT_KEYS = {'url', 'name', 'method', 'headers', 'body', 'status', 'contains', 'timeout_s'}
PREFLIGHT_MODES = ('public', 'any', 'off')
HEADER_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}\Z")
MAX_PREFLIGHT = 4
MAX_PREFLIGHT_BODY = 4096
MAX_PREFLIGHT_READ = 256 * 1024
# A recording over MAX_MEDIA_BYTES is re-encoded smaller on the runner instead of being dropped.
VIDEO_PRESETS = ('Preset1280x720', 'Preset960x540', 'Preset640x480')  # macOS avconvert
REENCODE_SECONDS = 90
MAX_LOG_EXPECTS = 12
SCENARIO_GRACE = 30  # steps still running at the end of the window get this long, then are cancelled
MIN_SAMPLE_MS = 150
MAX_RAW_FRAME = 64 * 1024 * 1024  # an uncompressed screencap (1440x3200 RGBA is ~18 MB)
LOG_LINE_CHARS = 300
# Screen-change sampling: a small grey grid per frame instead of images, so the model gets
# "the screen changed at 1050 ms in this box" rather than frames it must look at.
GRID_COLS, GRID_ROWS = 24, 48
TOP_CROP = 0.05  # default region skips the status bar (clock, battery); sample_region overrides it
CELL_DELTA = 24  # grey-level difference (0-255) for one grid cell to count as changed


class OpError(Exception):
    """A user-facing operation failure. The message is returned to the agent."""


class RevokedError(Exception):
    """The server told this runner it has been revoked."""


# ── Config ────────────────────────────────────────────────────────────────


def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit('Not enrolled. Copy the setup command from Loma → Integrations → Devices.')
    if CONFIG_PATH.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f'{CONFIG_PATH} is readable by other users; run: chmod 600 {CONFIG_PATH}')
    config = json.loads(CONFIG_PATH.read_text())
    config.setdefault('policy', {})
    return config


def save_config(config):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(config, handle, indent=2)
    os.replace(tmp, CONFIG_PATH)


def normalize_server(value, allow_http=False):
    parsed = urlparse(value.strip().rstrip('/'))
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if parsed.scheme not in ('https', 'http') or not parsed.hostname:
        raise SystemExit('Server must be a URL like https://loma.example.com')
    if parsed.scheme == 'http' and not (local or allow_http):
        raise SystemExit('Refusing plain http for a non-local server; use https (or --allow-http for testing)')
    return f'{parsed.scheme}://{parsed.netloc}'


def default_policy():
    return {'allow_physical_devices': False, 'allowed_app_ids': [], 'allow_maestro_scripts': False,
            'keep_awake': True, 'preflight': 'public'}


class KeepAwake:
    """macOS: hold a `caffeinate` assertion while devices are in use (renewed on every call).

    An idle-sleeping Mac drops the WebSocket, which fails every in-flight device call and leaves
    the agent retrying against an offline runner. The assertion lapses HOLD seconds after the
    last call, so an idle runner still lets the machine sleep. A closed laptop lid on battery
    still sleeps; that is macOS policy, not something an assertion can override.
    """
    HOLD = 900
    RENEW = 300

    def __init__(self, enabled=True):
        self.enabled = bool(enabled) and sys.platform == 'darwin'
        self.proc, self.since = None, 0.0

    def touch(self):
        if not self.enabled:
            return
        now = time.monotonic()
        if self.proc is not None and self.proc.poll() is None and now - self.since < self.RENEW:
            return
        if shutil.which('caffeinate') is None:
            self.enabled = False
            return
        old = self.proc
        try:  # -i: no idle sleep; -s: no system sleep on AC power; -t: lapse on its own
            self.proc = subprocess.Popen(['caffeinate', '-i', '-s', '-t', str(self.HOLD)], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.enabled = False
            return
        self.since = now
        if old is not None and old.poll() is None:
            old.terminate()
            try:
                old.wait(2)
            except subprocess.TimeoutExpired:
                old.kill()


# ── Subprocess helper ─────────────────────────────────────────────────────


async def run(args, *, timeout=60, check=True, keep='head', cwd=None, limit=None):
    """Run a fixed argv (never a host shell string). Returns (code, stdout bytes, stderr text).

    stdout is capped at MAX_OUTPUT bytes (or limit, for raw screen frames).

    The child is always killed if the call is cancelled (e.g. the WebSocket dropped),
    so a device lock is never released while a command is still running.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise OpError(f'{args[0]} is not installed on the runner machine') from None
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as exc:
        if proc.returncode is None:
            proc.kill()
            await asyncio.shield(proc.wait())
        if isinstance(exc, asyncio.TimeoutError):
            raise OpError(f'{Path(args[0]).name} timed out after {timeout}s') from None
        raise
    cap = limit or MAX_OUTPUT
    out = out[-cap:] if keep == 'tail' else out[:cap]
    err_text = err.decode('utf-8', 'replace')[-4000:]
    if check and proc.returncode != 0:
        detail = (err_text or out.decode('utf-8', 'replace')[-2000:]).strip()
        raise OpError(f'{Path(args[0]).name} failed (exit {proc.returncode}): {detail[:1500]}')
    return proc.returncode, out, err_text


def reencode_commands(source, target, seconds):
    """Commands that write a smaller copy of a recording, best first: ffmpeg, then macOS avconvert."""
    commands = []
    if shutil.which('ffmpeg'):
        kbps = max(300, int(MAX_MEDIA_BYTES * 8 * 0.85 / max(seconds, 1) / 1000))
        commands.append(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(source), '-an',
                         '-vf', 'scale=-2:min(1280\\,ih)', '-c:v', 'libx264', '-preset', 'veryfast',
                         '-b:v', f'{kbps}k', '-maxrate', f'{kbps}k', '-bufsize', f'{2 * kbps}k',
                         '-movflags', '+faststart', str(target)])
    if shutil.which('avconvert'):
        commands += [['avconvert', '--preset', preset, '--source', str(source), '--output', str(target), '--replace']
                     for preset in VIDEO_PRESETS]
    return commands


async def fit_media(path, what, seconds=60):
    """Read a captured recording. One over MAX_MEDIA_BYTES is re-encoded smaller (never truncated);
    if no encoder is installed, or it is still too large, the call fails with what to do about it."""
    size, megabyte = path.stat().st_size, 1024 * 1024
    if size <= MAX_MEDIA_BYTES:
        return path.read_bytes()
    small = path.with_name('reencoded.mp4')
    commands = reencode_commands(path, small, seconds)
    deadline = time.monotonic() + REENCODE_SECONDS
    for argv in commands:
        left = deadline - time.monotonic()
        if left < 5:
            break
        try:
            code, _, _ = await run(argv, timeout=left, check=False)
        except OpError:
            code = 1
        if code == 0 and small.exists() and 0 < small.stat().st_size <= MAX_MEDIA_BYTES:
            return small.read_bytes()
        if small.exists():
            small.unlink()
    raise OpError(f'{what} is {size // megabyte} MB, over the {MAX_MEDIA_BYTES // megabyte} MB limit'
                  + (' and could not be re-encoded smaller' if commands else
                     ' (install ffmpeg on the runner machine to have long recordings re-encoded)')
                  + '; use a shorter duration')


def png_size(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) < 24:
        raise OpError('Device returned an invalid screenshot')
    return struct.unpack('>II', data[16:24])


# ── Validation (runner-side; the server validates too) ────────────────────


def need_str(args, key, pattern=None, max_len=1000, optional=False):
    value = args.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > max_len or '\x00' in value:
        raise OpError(f'Invalid {key}')
    if pattern is not None and not pattern.fullmatch(value):
        raise OpError(f'Invalid {key}')
    return value


def need_int(args, key, low, high, default=None):
    value = args.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise OpError(f'Invalid {key}')
    return value


def need_bool(args, key, default=False):
    value = args.get(key, default)
    if type(value) is not bool:
        raise OpError(f'Invalid {key}')
    return value


def need_selector(args):
    """(match, by, exact) for the element-finding ops."""
    match = need_str(args, 'match', max_len=200)
    by = args.get('by', 'any')
    if by not in SELECT_BY:
        raise OpError('Invalid by (any, text, id or label)')
    return match, by, need_bool(args, 'exact')


def need_launch(args):
    """Validated launch options: string extras, bool extras, explicit activity, console capture."""
    extras, flags = args.get('extras') or {}, args.get('bool_extras') or {}
    if not isinstance(extras, dict) or not isinstance(flags, dict) or len(extras) + len(flags) > MAX_EXTRAS:
        raise OpError('Invalid extras')
    for key, value in list(extras.items()) + list(flags.items()):
        if not isinstance(key, str) or not EXTRA_KEY.fullmatch(key):
            raise OpError('Invalid extra key (letters, digits, _ and . only)')
    for value in extras.values():
        if not isinstance(value, str) or len(value) > 1000 or '\x00' in value:
            raise OpError('Invalid extra value')
    if any(type(value) is not bool for value in flags.values()):
        raise OpError('Invalid bool_extras value')
    return {'extras': extras, 'bool_extras': flags,
            'activity': need_str(args, 'activity', ACTIVITY, 255, optional=True),
            'console': need_bool(args, 'console')}


def find_elements(elements, match, by='any', exact=False):
    """Elements whose text / id / label matches; exact (case-insensitive) matches first.

    An Android resource-id also matches by its short name (com.app:id/endpoint -> endpoint).
    """
    fields = ('text', 'label', 'id') if by == 'any' else (by,)
    needle = match.lower()
    ranked = []
    for element in elements:
        best = None
        for field in fields:
            value = str(element.get(field) or '')
            names = {value.lower(), value.rsplit('/', 1)[-1].lower()} if field == 'id' else {value.lower()}
            if needle in names:
                best = 0
                break
            if not exact and value and needle in value.lower():
                best = 1
        if best is not None:
            ranked.append((best, not element.get('clickable', False), len(ranked), element))
    return [item[-1] for item in sorted(ranked, key=lambda item: item[:3])]


def check_url(url):
    match = re.match(r'([A-Za-z][A-Za-z0-9+.-]*):', url)
    if (len(url) > 2000 or any(c.isspace() for c in url) or any(c in url for c in '"\'`\\') or not match):
        raise OpError('Invalid url')
    if match.group(1).lower() in BLOCKED_URL_SCHEMES:
        raise OpError(f'{match.group(1)}: URLs are not allowed on this runner')
    return url


def need_tags(args, key):
    """Optional list of log tags (substrings): a line is kept if it contains any of them."""
    tags = args.get(key)
    if tags is None:
        return []
    if (not isinstance(tags, list) or not 1 <= len(tags) <= MAX_LOG_TAGS
            or not all(isinstance(t, str) and 0 < len(t) <= 100 and '\x00' not in t for t in tags)):
        raise OpError(f'Invalid {key} (1-{MAX_LOG_TAGS} strings)')
    return tags


def need_log_source(args, key='source'):
    source = args.get(key, 'auto')
    if source not in ('auto', 'system', 'console'):
        raise OpError(f'Invalid {key} (auto, system or console)')
    return source


def tag_filter(lines, tags, needle=None, text=lambda line: line):
    """Lines matching needle (if any) and any of tags (if any); plus per-tag counts."""
    lowered = [t.lower() for t in tags]
    kept, counts = [], dict.fromkeys(tags, 0)
    for item in lines:
        low = text(item).lower()
        if needle and needle.lower() not in low:
            continue
        hit = [tag for tag, t in zip(tags, lowered) if t in low]
        if tags and not hit:
            continue
        for tag in hit:
            counts[tag] += 1
        kept.append(item)
    return kept, counts


def need_number(args, key, low, high, default):
    value = args.get(key, default)
    if type(value) not in (int, float) or not low <= value <= high:
        raise OpError(f'Invalid {key} ({low}-{high})')
    return float(value)


def need_region(args, key):
    """Optional [x1, y1, x2, y2] in screen pixels: the part of the screen to watch for changes."""
    region = args.get(key)
    if region is None:
        return None
    if (not isinstance(region, list) or len(region) != 4 or not all(type(v) is int and 0 <= v <= 10000 for v in region)
            or region[2] <= region[0] or region[3] <= region[1]):
        raise OpError(f'Invalid {key} (expected [x1, y1, x2, y2] in screen pixels)')
    return tuple(region)


def need_steps(args, window_ms, app_id=None, allowed_apps=()):
    """Scenario steps: [{action, at_ms | after_ms, ...}].

    at_ms runs the step at a fixed offset from t0 (timing tests); after_ms (default 0) runs it
    that long after the previous step finished (ordinary flows, where each step waits for the last).
    """
    steps = args.get('steps') or []
    if not isinstance(steps, list) or len(steps) > MAX_SCENARIO_STEPS:
        raise OpError(f'steps must be a list of at most {MAX_SCENARIO_STEPS} steps')
    checked, last, shots = [], 0, 0
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get('action') not in SCENARIO_STEPS:
            raise OpError(f'steps[{index}]: action must be one of ' + ', '.join(sorted(SCENARIO_STEPS)))
        action = step['action']
        required, optional = SCENARIO_STEPS[action]
        given = set(step) - {'action'} - STEP_TIMING
        if not required <= given <= required | optional:
            raise OpError(f'steps[{index}] ({action}): needs {sorted(required)}'
                          + (f', may take {sorted(optional)}' if optional else ''))
        item = {'action': action}
        if 'at_ms' in step and 'after_ms' in step:
            raise OpError(f'steps[{index}]: use at_ms or after_ms, not both')
        if 'at_ms' in step:
            item['at_ms'] = need_int(step, 'at_ms', 0, window_ms - 1)
            if item['at_ms'] < last:
                raise OpError(f'steps[{index}]: at_ms steps must be in at_ms order')
            last = item['at_ms']
        else:
            item['after_ms'] = need_int(step, 'after_ms', 0, window_ms - 1, default=0)
        if action == 'tap':
            item.update(x=need_int(step, 'x', 0, 10000), y=need_int(step, 'y', 0, 10000))
        elif action == 'swipe':
            item.update({k: need_int(step, k, 0, 10000) for k in ('x1', 'y1', 'x2', 'y2')},
                        duration_ms=need_int(step, 'duration_ms', 50, 5000, default=300))
        elif action == 'type':
            item['text'] = need_str(step, 'text', max_len=MAX_TEXT)
        elif action == 'key':
            item['key'] = need_str(step, 'key', max_len=32)
        elif action == 'open_url':
            item['url'] = check_url(need_str(step, 'url', max_len=2000))
        elif action in ('tap_text', 'wait_for'):
            item['selector'] = need_selector(step)
            item['timeout_s'] = need_int(step, 'timeout_s', 0, MAX_STEP_WAIT, default=5)
            item['gone'] = need_bool(step, 'gone') if action == 'wait_for' else False
        elif action in ('set_text', 'clear_text'):
            if action == 'set_text':
                need_str(step, 'text', max_len=MAX_TEXT)
                need_bool(step, 'clear', default=True)
            if 'match' in step:
                need_selector(step)
            item['args'] = {k: step[k] for k in given}
        elif action == 'scroll_until_visible':
            item['selector'] = need_selector(step)
            item['direction'] = step.get('direction', 'down')
            if item['direction'] not in ('down', 'up'):
                raise OpError(f'steps[{index}]: direction must be down or up')
            item['max_swipes'] = need_int(step, 'max_swipes', 1, 20, default=8)
        elif action == 'screenshot':
            shots += 1
            if shots > MAX_SCENARIO_SHOTS:
                raise OpError(f'At most {MAX_SCENARIO_SHOTS} screenshot steps per scenario')
            item['name'] = need_str(step, 'name', SHOT_NAME, 40, optional=True) or f'step{index}'
        else:  # launch_app / stop_app: the scenario's app unless the step names another one
            target = need_str(step, 'app_id', APP_ID, 255, optional=True) or app_id
            if not target:
                raise OpError(f'steps[{index}] ({action}): needs app_id (none set on the scenario)')
            if allowed_apps and target not in allowed_apps:
                raise OpError(f"steps[{index}]: app {target} is not in this runner's allowed_app_ids")
            item['app_id'] = target
            if action == 'launch_app':
                # restart=false (default) brings a running app back to the front (background/foreground
                # cases); restart=true stops it first, like the launch at the start of the scenario.
                item['launch'] = {**need_launch({k: step[k] for k in ('activity', 'extras', 'bool_extras')
                                                 if k in step}), 'restart': need_bool(step, 'restart')}
        checked.append(item)
    return checked


def need_preflight(args, mode='public'):
    """Optional HTTP checks run before the scenario: [{url, method, headers, body, status, contains, timeout_s}]."""
    checks = args.get('preflight')
    if checks is None:
        return []
    if mode == 'off':
        raise OpError("preflight is turned off on this runner (policy preflight: 'off')")
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_PREFLIGHT:
        raise OpError(f'preflight must be a list of 1-{MAX_PREFLIGHT} checks')
    out = []
    for index, check in enumerate(checks, 1):
        where = f'preflight[{index}]'
        if not isinstance(check, dict) or not {'url'} <= set(check) <= PREFLIGHT_KEYS:
            raise OpError(f'{where}: needs url, may take ' + ', '.join(sorted(PREFLIGHT_KEYS - {'url'})))
        url = need_str(check, 'url', max_len=2000)
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or any(c.isspace() for c in url):
            raise OpError(f'{where}: url must be an http(s) URL')
        method = check.get('method', 'GET')
        if method not in ('GET', 'POST'):
            raise OpError(f'{where}: method must be GET or POST')
        headers = check.get('headers') or {}
        if (not isinstance(headers, dict) or len(headers) > MAX_EXTRAS or not all(
                isinstance(k, str) and HEADER_NAME.fullmatch(k) and isinstance(v, str) and len(v) <= 2000
                and not set(v) & set('\r\n\x00') for k, v in headers.items())):
            raise OpError(f'{where}: headers must be an object of at most {MAX_EXTRAS} name: value strings')
        body = check.get('body')
        if body is not None and (method != 'POST' or not isinstance(body, (str, dict))
                                 or len(body if isinstance(body, str) else json.dumps(body)) > MAX_PREFLIGHT_BODY):
            raise OpError(f'{where}: body needs method POST and is text or a JSON object (max {MAX_PREFLIGHT_BODY} chars)')
        contains = check.get('contains') or []
        if (not isinstance(contains, list) or len(contains) > 8
                or not all(isinstance(text, str) and 0 < len(text) <= 200 for text in contains)):
            raise OpError(f'{where}: contains must be a list of at most 8 strings')
        out.append({'url': url, 'host': parsed.hostname, 'port': parsed.port, 'method': method, 'headers': headers,
                    'body': body, 'contains': contains,
                    'name': need_str(check, 'name', max_len=60, optional=True) or f'{method} {parsed.hostname}{parsed.path}'[:60],
                    'status': need_int(check, 'status', 100, 599, default=200),
                    'timeout_s': need_int(check, 'timeout_s', 1, 20, default=10)})
    return out


async def private_host(host, port):
    """True when host is, or resolves to, a loopback / private / link-local address."""
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            found = await asyncio.get_running_loop().getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except OSError:
            return False  # does not resolve: the request itself reports that
        addresses = [ipaddress.ip_address(item[4][0].split('%')[0]) for item in found]
    return any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_unspecified
               for a in addresses)


def need_expect(args, has_logs, has_frames, has_app, has_timed=False):
    """Optional pass/fail rules, checked on the runner so the model reads a verdict, not raw data."""
    expect = args.get('expect')
    if expect is None:
        return None
    if not isinstance(expect, dict) or not set(expect) <= EXPECT_KEYS:
        raise OpError('expect may contain: ' + ', '.join(sorted(EXPECT_KEYS)))
    out = {'steps_ok': need_bool(expect, 'steps_ok', default=True)}
    if 'app_running' in expect:
        if not has_app:
            raise OpError('expect.app_running needs app_id')
        out['app_running'] = need_bool(expect, 'app_running')
    if 'settled_by_ms' in expect:
        if not has_frames:
            raise OpError('expect.settled_by_ms needs sample_ms (the screen-change timeline)')
        out['settled_by_ms'] = need_int(expect, 'settled_by_ms', 0, MAX_SCENARIO_SECONDS * 1000)
    if 'max_drift_ms' in expect:
        if not has_timed:
            raise OpError('expect.max_drift_ms needs at least one at_ms step (drift is how late such a step ran)')
        out['max_drift_ms'] = need_int(expect, 'max_drift_ms', 0, MAX_SCENARIO_SECONDS * 1000)
    rules, order = expect.get('logs') or [], expect.get('log_order') or []
    if (rules or order) and not has_logs:
        raise OpError('expect.logs / expect.log_order need log_tags (which log lines to capture)')
    if not isinstance(rules, list) or len(rules) > MAX_LOG_EXPECTS:
        raise OpError(f'expect.logs must be a list of at most {MAX_LOG_EXPECTS} rules')
    out['logs'] = []
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict) or not {'match'} <= set(rule) <= LOG_EXPECT_KEYS:
            raise OpError(f'expect.logs[{index}]: needs match, may take min, max, by_ms, after_ms, number_after '
                          'with ' + ', '.join(VALUE_KEYS))
        item = {'match': need_str(rule, 'match', max_len=200)}
        if 'number_after' in rule:
            item['number_after'] = need_str(rule, 'number_after', max_len=100)
        for key in VALUE_KEYS:
            if key in rule:
                if 'number_after' not in rule:
                    raise OpError(f'expect.logs[{index}]: {key} needs number_after (the text right before the number)')
                item[key] = need_number(rule, key, -MAX_VALUE, MAX_VALUE, None)
        for key, high in (('min', 100000), ('max', 100000), ('by_ms', MAX_SCENARIO_SECONDS * 1000),
                          ('after_ms', MAX_SCENARIO_SECONDS * 1000)):
            if key in rule:
                item[key] = need_int(rule, key, 0, high)
        item.setdefault('min', 0 if 'max' in item else 1)
        out['logs'].append(item)
    if (not isinstance(order, list) or len(order) > MAX_LOG_EXPECTS
            or not all(isinstance(m, str) and 0 < len(m) <= 200 for m in order)):
        raise OpError(f'expect.log_order must be a list of at most {MAX_LOG_EXPECTS} strings')
    out['log_order'] = order
    return out


def log_values(rule, entries):
    """[(t_ms, number)]: the first number after rule['number_after'] in each line matching rule['match']."""
    match, marker = rule['match'].lower(), rule['number_after'].lower()
    values = []
    for at, line in entries:
        low = line.lower()
        index = low.find(marker) if match in low else -1
        found = NUMBER.match(low, index + len(marker)) if index >= 0 else None
        if found:
            values.append((at, float(found.group(1))))
    return values


def value_stats(expect, entries):
    """Per number_after rule: how many values were read and their min / max / first / last."""
    stats = {}
    for rule in expect['logs']:
        if 'number_after' in rule:
            numbers = [value for _, value in log_values(rule, entries)]
            stats[rule['match']] = ({'count': len(numbers), 'min': min(numbers), 'max': max(numbers),
                                     'first': numbers[0], 'last': numbers[-1]} if numbers else {'count': 0})
    return stats


def judge_values(rule, entries, name):
    """Failures of one rule's number checks (value_min / value_max / after_reaching / last_min / last_max)."""
    values = log_values(rule, entries)
    if not values:
        return [f"log {name}: no number found after {rule['number_after']!r}"]
    failed, checked = [], values
    if 'after_reaching' in rule:  # only values from the first one at or above this level are range-checked
        start = next((i for i, (_, value) in enumerate(values) if value >= rule['after_reaching']), None)
        if start is None:
            return [f"log {name}: never reached {rule['after_reaching']:g} "
                    f"(highest value {max(value for _, value in values):g})"]
        checked = values[start:]
    for key, bad, word in (('value_min', lambda v, limit: v < limit, 'below'),
                           ('value_max', lambda v, limit: v > limit, 'above')):
        if key in rule:
            wrong = [(at, value) for at, value in checked if bad(value, rule[key])]
            if wrong:
                failed.append(f'log {name}: value {wrong[0][1]:g} at {wrong[0][0]} ms is {word} {rule[key]:g}'
                              + (f' ({len(wrong)} such values)' if len(wrong) > 1 else ''))
    last = values[-1][1]
    if 'last_min' in rule and last < rule['last_min']:
        failed.append(f"log {name}: last value {last:g} is below {rule['last_min']:g}")
    if 'last_max' in rule and last > rule['last_max']:
        failed.append(f"log {name}: last value {last:g} is above {rule['last_max']:g}")
    return failed


def judge(expect, result, entries):
    """Reasons the scenario failed its expect rules (empty list = pass). entries: [(t_ms, line)]."""
    failed = []
    launch = result.get('launch')
    if launch is not None and not launch['ok']:
        failed.append(f"launch failed: {launch.get('error')}")
    if expect['steps_ok']:
        for step in result.get('steps') or []:
            if step.get('skipped'):
                failed.append(f"step {step['i']} ({step['action']}) did not run")
            elif not step.get('ok'):
                failed.append(f"step {step['i']} ({step['action']}): {step.get('error')}")
    if 'app_running' in expect and result.get('app_running') is not expect['app_running']:
        state = {True: 'running', False: 'not running', None: 'unknown'}[result.get('app_running')]
        failed.append(f"app is {state} at the end (expected {'running' if expect['app_running'] else 'stopped'})")
    if 'settled_by_ms' in expect:
        frames = result.get('frames') or {}
        last = frames.get('last_change_ms')
        if not frames.get('samples'):
            failed.append('no screen samples were captured, so settled_by_ms cannot be checked')
        elif last is not None and last > expect['settled_by_ms']:
            failed.append(f"screen still changing at {last} ms (expected settled by {expect['settled_by_ms']} ms)")
    if 'max_drift_ms' in expect:  # a timed step that ran late means the timing under test was not the one asked for
        for step in result.get('steps') or []:
            if step.get('drift_ms', 0) > expect['max_drift_ms']:
                failed.append(f"step {step['i']} ({step['action']}) ran {step['drift_ms']} ms late (at_ms "
                              f"{step['at_ms']}, ran at {step['ran_ms']} ms; expected at most {expect['max_drift_ms']} ms)")

    def hits(match):
        return [at for at, line in entries if match.lower() in line.lower()]
    for rule in expect['logs']:
        found, name = hits(rule['match']), repr(rule['match'])
        if len(found) < rule['min']:
            failed.append(f"log {name}: {len(found)} lines (expected at least {rule['min']})")
        if 'max' in rule and len(found) > rule['max']:
            failed.append(f"log {name}: {len(found)} lines (expected at most {rule['max']})")
        if found and 'by_ms' in rule and found[0] > rule['by_ms']:
            failed.append(f"log {name}: first at {found[0]} ms (expected by {rule['by_ms']} ms)")
        if found and 'after_ms' in rule and found[0] < rule['after_ms']:
            failed.append(f"log {name}: first at {found[0]} ms (expected after {rule['after_ms']} ms)")
        if found and 'number_after' in rule:
            failed += judge_values(rule, entries, name)
    previous = None
    for match in expect['log_order']:
        found = hits(match)
        if not found:
            failed.append(f'log order: {match!r} never appeared')
            break
        if previous is not None and found[0] < previous[1]:
            failed.append(f'log order: {match!r} ({found[0]} ms) came before {previous[0]!r} ({previous[1]} ms)')
        previous = (match, found[0])
    return failed


# ── Screen-change signatures (pure Python: no image library on the runner) ──


def grid_signature(width, height, grey_at, region=None):
    """Mean grey level (0-255) of 3x3 samples in each grid cell of the watched region.

    region is (x1, y1, x2, y2) in screen pixels, clamped to the frame; by default the whole
    screen below the status bar. grey_at(x, y) returns r + g + b of one pixel. ~10k reads per frame.
    """
    if region is None:
        left, top, right, bottom = 0, int(height * TOP_CROP), width, height
    else:
        left, top, right, bottom = (min(region[0], width), min(region[1], height),
                                    min(region[2], width), min(region[3], height))
    span_x, span_y = right - left, bottom - top
    if span_x < GRID_COLS or span_y < GRID_ROWS:
        raise OpError(f'sample_region is too small or off screen (screen is {width}x{height}, '
                      f'need at least {GRID_COLS}x{GRID_ROWS} px)')
    cells = []
    for row in range(GRID_ROWS):
        ys = [top + (row * 4 + k) * span_y // (GRID_ROWS * 4) for k in (1, 2, 3)]
        for col in range(GRID_COLS):
            xs = [left + (col * 4 + k) * span_x // (GRID_COLS * 4) for k in (1, 2, 3)]
            cells.append(sum(grey_at(x, y) for y in ys for x in xs) // 27)
    return {'width': width, 'height': height, 'area': (left, top, right, bottom), 'cells': cells}


def android_raw_signature(data, region=None):
    """`screencap` without -p: a 12- or 16-byte header (width, height, format[, colorspace]) + RGBA rows."""
    if len(data) < 16:
        raise OpError('Empty screen frame')
    width, height = struct.unpack_from('<II', data)
    if not (0 < width <= 10000 and 0 < height <= 10000):
        raise OpError('Unexpected screencap header')
    header = len(data) - width * height * 4
    if header not in (12, 16):
        raise OpError('Unexpected screencap format (expected 32-bit RGBA)')
    stride = width * 4

    def grey_at(x, y):
        i = header + y * stride + x * 4
        return data[i] + data[i + 1] + data[i + 2]
    return grid_signature(width, height, grey_at, region)


def bmp_signature(data, region=None):
    """`simctl io screenshot --type=bmp`: an uncompressed 24/32-bit BMP (bottom-up or top-down rows)."""
    if data[:2] != b'BM' or len(data) < 30:
        raise OpError('Unexpected simulator frame (not a BMP)')
    offset = struct.unpack_from('<I', data, 10)[0]
    width, height = struct.unpack_from('<ii', data, 18)
    bpp = struct.unpack_from('<H', data, 28)[0]
    rows = abs(height)
    if bpp not in (24, 32) or not (0 < width <= 10000 and 0 < rows <= 10000):
        raise OpError('Unsupported BMP frame')
    stride, step = (width * bpp + 31) // 32 * 4, bpp // 8
    if offset + stride * rows > len(data):
        raise OpError('Truncated BMP frame')

    def grey_at(x, y):
        i = offset + (rows - 1 - y if height > 0 else y) * stride + x * step
        return data[i] + data[i + 1] + data[i + 2]
    return grid_signature(width, rows, grey_at, region)


def merge_changes(samples, spacing_ms):
    """Consecutive changed samples -> periods {from_ms, to_ms, frames, max_changed, box}.

    An animation or a transition changes every frame for a while; one period per burst of
    change reads as "animating from 300 to 1500 ms in this box", not 20 separate events.
    """
    periods = []
    for sample in samples:
        last = periods[-1] if periods else None
        if last is not None and sample['at_ms'] - last['to_ms'] <= 2 * spacing_ms:
            box = last['box']
            last.update(to_ms=sample['at_ms'], frames=last['frames'] + 1,
                        max_changed=max(last['max_changed'], sample['changed']),
                        box=[min(box[0], sample['box'][0]), min(box[1], sample['box'][1]),
                             max(box[2], sample['box'][2]), max(box[3], sample['box'][3])])
        else:
            periods.append({'from_ms': sample['at_ms'], 'to_ms': sample['at_ms'], 'frames': 1,
                            'max_changed': sample['changed'], 'box': list(sample['box'])})
    return periods


def frame_change(previous, current):
    """(fraction of grid cells that changed, bounding box in screen pixels or None)."""
    if previous is None or (previous['width'], previous['height'], previous['area']) != (
            current['width'], current['height'], current['area']):
        return None
    changed = [i for i, (a, b) in enumerate(zip(previous['cells'], current['cells'])) if abs(a - b) >= CELL_DELTA]
    if not changed:
        return 0.0, None
    rows, cols = [i // GRID_COLS for i in changed], [i % GRID_COLS for i in changed]
    left, top, right, bottom = current['area']
    span_x, span_y = right - left, bottom - top
    box = [left + min(cols) * span_x // GRID_COLS, top + min(rows) * span_y // GRID_ROWS,
           left + (max(cols) + 1) * span_x // GRID_COLS, top + (max(rows) + 1) * span_y // GRID_ROWS]
    return round(len(changed) / len(current['cells']), 3), box


def screen_flow(flow, policy):
    """Parse the flow and allow only safe Maestro commands and allowed app ids."""
    if len(flow.encode()) > MAX_FLOW:
        raise OpError('Flow is too large (64 KiB max)')
    try:
        import yaml
    except ImportError:
        raise OpError('run_flow needs PyYAML on the runner: python3 -m pip install --user pyyaml') from None
    try:
        # Aliases expand exponentially while screening walks the tree (a ~1 KiB "billion laughs").
        if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(flow)):
            raise OpError('YAML anchors/aliases are not supported in flows')
        docs = [d for d in yaml.safe_load_all(flow) if d is not None]
    except yaml.YAMLError as exc:
        raise OpError(f'Flow is not valid YAML: {str(exc)[:300]}') from None
    if not docs or len(docs) > 2:
        raise OpError('Flow must be "config --- commands" or a single command list')
    config, commands = (docs[0], docs[1]) if len(docs) == 2 else ({}, docs[0])
    if not isinstance(config, dict) or not isinstance(commands, list):
        raise OpError('Flow must be a config mapping followed by a list of commands')
    scripts = bool(policy.get('allow_maestro_scripts'))
    allowed_apps = set(policy.get('allowed_app_ids') or [])

    def arg(args, key):  # a step's argument is either a scalar or a mapping
        return args if isinstance(args, str) else args.get(key) if isinstance(args, dict) else None

    def check_app(app_id):
        if allowed_apps and app_id not in allowed_apps:
            raise OpError(f"App {app_id} is not in this runner's allowed_app_ids")

    def check_strings(value):
        if isinstance(value, str):
            if not scripts and '${' in value:
                raise OpError('Inline JavaScript (${...}) is not allowed on this runner')
        elif isinstance(value, dict):
            for key, item in value.items():
                check_strings(key)
                check_strings(item)
        elif isinstance(value, list):
            for item in value:
                check_strings(item)

    def check_commands(items, depth=0):
        if not isinstance(items, list) or depth > 5:
            raise OpError('Invalid command list')
        for item in items:
            if isinstance(item, str):
                command, args = item, None
            elif isinstance(item, dict) and len(item) == 1:
                command, args = next(iter(item.items()))
            else:
                raise OpError('Each flow step must be a command name or a single-key mapping')
            if not isinstance(command, str) or (not scripts and command not in FLOW_COMMANDS):
                raise OpError(f'Maestro command {command!r} is not allowed on this runner '
                              '(set allow_maestro_scripts=true in the runner config to permit it)')
            if command in APP_COMMANDS:
                check_app(arg(args, 'appId') or config.get('appId'))
            if command == 'openLink':
                check_url(str(arg(args, 'link') or ''))
            if command == 'takeScreenshot':
                name = arg(args, 'path')
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
                    raise OpError('takeScreenshot needs a simple name (letters, digits, - and _)')
            if isinstance(args, dict) and 'file' in args and not scripts:
                raise OpError(f'{command} with file: is not allowed on this runner (it runs an unscreened flow)')
            if isinstance(args, dict) and 'commands' in args:
                check_commands(args['commands'], depth + 1)
            check_strings(args)

    unknown = set(config) - FLOW_CONFIG_KEYS
    if unknown and not scripts:
        raise OpError(f'Unsupported flow config keys: {sorted(unknown)}')
    if 'appId' in config:
        check_app(str(config['appId']))
    check_strings(config)
    for hook in ('onFlowStart', 'onFlowComplete'):
        if hook in config:
            check_commands(config[hook])
    check_commands(commands)


# ── Android ───────────────────────────────────────────────────────────────


class Android:
    platform = 'android'

    def __init__(self, adb='adb'):
        self.adb = adb

    def available(self):
        return shutil.which(self.adb) is not None

    async def list(self):
        _, out, _ = await run([self.adb, 'devices', '-l'], timeout=15)
        devices = []
        for line in out.decode('utf-8', 'replace').splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] != 'device' or not SERIAL.fullmatch(parts[0]):
                continue
            serial = parts[0]
            info = dict(p.split(':', 1) for p in parts[2:] if ':' in p)
            emulator = serial.startswith('emulator-')
            try:  # one unresponsive device must not hide the others
                if not emulator:
                    code, qemu, _ = await run([self.adb, '-s', serial, 'shell', 'getprop', 'ro.kernel.qemu'],
                                              timeout=10, check=False)
                    emulator = code == 0 and qemu.strip() == b'1'
                _, release, _ = await run([self.adb, '-s', serial, 'shell', 'getprop', 'ro.build.version.release'],
                                          timeout=10, check=False)
            except OpError:
                continue
            devices.append({'serial': serial, 'platform': 'android', 'virtual': emulator,
                            'name': info.get('model', serial).replace('_', ' '),
                            'os_version': release.decode('utf-8', 'replace').strip()})
        return devices

    def _sh(self, serial, *argv):
        return [self.adb, '-s', serial, 'shell', *argv]

    async def packages(self, serial):
        _, out, _ = await run(self._sh(serial, 'pm', 'list', 'packages'), timeout=30)
        return {line[8:].strip() for line in out.decode('utf-8', 'replace').splitlines() if line.startswith('package:')}

    async def install(self, serial, path, app_id, allowed=()):
        apk = await asyncio.to_thread(find_file, path, '.apk')  # big archives: keep the loop (heartbeats) free
        if allowed:
            # With an app allowlist, never replace an existing package (no -r) and verify
            # the package that actually got installed, not just the app_id we were told.
            if app_id:
                await run([self.adb, '-s', serial, 'uninstall', app_id], timeout=60, check=False)
            before = await self.packages(serial)
            _, out, err = await run([self.adb, '-s', serial, 'install', '-t', '-g', str(apk)], timeout=300)
            added = await self.packages(serial) - before
            if not added or not added <= set(allowed):
                for package in added:
                    await run([self.adb, '-s', serial, 'uninstall', package], timeout=60, check=False)
                raise OpError(f'Installed package {sorted(added) or "unknown"} is not in allowed_app_ids; removed it')
            return {'installed': apk.name, 'data_kept': False,
                    'output': (out.decode('utf-8', 'replace') + err).strip()[-500:]}
        # Update in place first: keeps app data (config, logins) and pre-grants runtime permissions.
        # Only a signature change (each CI debug build has its own key) forces uninstall + install.
        argv = [self.adb, '-s', serial, 'install', '-r', '-d', '-t', '-g', str(apk)]
        code, out, err = await run(argv, timeout=300, check=False)
        text = (out.decode('utf-8', 'replace') + err).strip()
        data_kept = True
        if code != 0 or 'Success' not in text:
            if 'INSTALL_FAILED_UPDATE_INCOMPATIBLE' not in text:
                raise OpError(f'adb install failed: {text[-1500:]}')
            found = re.search(r'(?:Existing package|Package) ([A-Za-z0-9_.]+) signatures', text)
            # Uninstall only the package adb says conflicts (it is what the APK really installs).
            package = found.group(1) if found else app_id
            if found and app_id and package != app_id:
                raise OpError(f'The build installs {package}, not app_id {app_id}, and {package} is already installed '
                              f'with a different signature. Not uninstalling anything: pass app_id {package} if it '
                              'should be replaced (its data will be lost)')
            if not package or not APP_ID.fullmatch(package):
                raise OpError('Installed app has a different signature; pass app_id so it can be reinstalled')
            await run([self.adb, '-s', serial, 'uninstall', package], timeout=60, check=False)
            _, out, err = await run(argv, timeout=300)
            text, data_kept = (out.decode('utf-8', 'replace') + err).strip(), False
        return {'installed': apk.name, 'data_kept': data_kept, 'output': text[-500:]}

    async def install_stamp(self, serial, package):
        """Identifies the installed build of a package (None if not installed)."""
        code, out, _ = await run(self._sh(serial, 'dumpsys', 'package', package), timeout=30, check=False)
        found = re.search(rb'lastUpdateTime=([^\r\n]+)', out) if code == 0 else None
        return found.group(1).decode('utf-8', 'replace').strip() if found else None

    async def grant(self, serial, package, appops=(), privacy=()):
        granted = []
        for op in appops:
            await run(self._sh(serial, 'appops', 'set', package, op, 'allow'), timeout=30)
            granted.append(op)
        return {'granted': granted, **({'ignored': sorted(privacy)} if privacy else {})}

    async def uninstall(self, serial, app_id):
        await run([self.adb, '-s', serial, 'uninstall', app_id], timeout=60)
        return {'uninstalled': app_id}

    async def launch(self, serial, app_id, extras=None, bool_extras=None, activity=None, console=False,
                     restart=None):
        # restart: True stops the app first, False never does (a running app comes to the front),
        # None keeps the long-standing default (a launch with extras/activity restarts the app).
        extras, bool_extras = extras or {}, bool_extras or {}
        if not (extras or bool_extras or activity):
            if restart:
                await self.stop(serial, app_id)
            await run(self._sh(serial, 'monkey', '-p', app_id, '-c', 'android.intent.category.LAUNCHER', '1'),
                      timeout=30)
            return {'launched': app_id}
        component = f'{app_id}/{activity or await self._launcher_activity(serial, app_id)}'
        # adb joins shell args into one device-side shell string: every value is quoted as one word.
        argv = ['am', 'start', '-W'] + ([] if restart is False else ['-S'])
        if restart is False and not activity:  # the launcher's own intent: resumes the task, no second activity
            argv += ['-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER']
        argv += ['-n', shlex.quote(component)]
        for key, value in extras.items():
            argv += ['--es', key, shlex.quote(value)]
        for key, value in bool_extras.items():
            argv += ['--ez', key, 'true' if value else 'false']
        # Worst case (resolve 10 s + start 40 s) stays under the runner's 55 s default call deadline.
        _, out, _ = await run(self._sh(serial, *argv), timeout=40)
        text = out.decode('utf-8', 'replace')
        if AM_ERROR.search(text):
            raise OpError(text.strip()[-500:])
        return {'launched': app_id, 'component': component, 'extras': sorted(extras) + sorted(bool_extras)}

    async def _launcher_activity(self, serial, app_id):
        _, out, _ = await run(self._sh(serial, 'cmd', 'package', 'resolve-activity', '--brief',
                                       '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER',
                                       app_id), timeout=10, check=False)
        for line in reversed(out.decode('utf-8', 'replace').splitlines()):
            name = line.strip().partition('/')[2]
            if line.strip().startswith(app_id + '/') and ACTIVITY.fullmatch(name):
                return name
        raise OpError(f'Could not find the launcher activity of {app_id}; pass activity')

    async def stop(self, serial, app_id):
        await run(self._sh(serial, 'am', 'force-stop', app_id), timeout=30)
        return {'stopped': app_id}

    async def reset_app(self, serial, app_id):
        await run(self._sh(serial, 'pm', 'clear', app_id), timeout=60)
        return {'cleared': app_id}

    async def open_url(self, serial, url):
        # adb joins shell args into one device-side shell string: quote the URL.
        _, out, _ = await run(self._sh(serial, 'am', 'start', '-W', '-a', 'android.intent.action.VIEW',
                                       '-d', shlex.quote(url)), timeout=30)
        text = out.decode('utf-8', 'replace')
        if AM_ERROR.search(text):
            raise OpError(text.strip()[-500:])
        return {'opened': url}

    async def screenshot(self, serial):
        _, out, _ = await run([self.adb, '-s', serial, 'exec-out', 'screencap', '-p'], timeout=30)
        return out

    async def ui_tree(self, serial):
        # One adb round trip (was three: rm, dump, cat). uiautomator itself still waits for the UI
        # to go idle, so a running animation (shimmer, video) can make any dump slow.
        _, out, err = await run(self._sh(serial, ANDROID_UI_DUMP), timeout=30, check=False)
        start, end = out.find(b'<?xml'), out.rfind(b'</hierarchy>')
        start = out.find(b'<hierarchy') if start < 0 else start
        if start < 0 or end < 0:
            raise OpError('uiautomator could not capture the screen (UI not idle?); retry: '
                          + (out.decode('utf-8', 'replace') + err).strip()[-300:])
        elements, screen = parse_uiautomator(out[start:end + len(b'</hierarchy>')], with_screen=True)
        return {'units': 'pixels', 'screen': screen, 'elements': elements}

    async def tap(self, serial, x, y):
        await run(self._sh(serial, 'input', 'tap', str(x), str(y)), timeout=15)
        return {'tapped': [x, y]}

    async def swipe(self, serial, x1, y1, x2, y2, duration_ms):
        await run(self._sh(serial, 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(duration_ms)), timeout=30)
        return {'swiped': [x1, y1, x2, y2]}

    async def type_text(self, serial, text):
        if not all(32 <= ord(c) < 127 for c in text):
            raise OpError('Android text input supports printable ASCII only')
        if '%s' in text:
            raise OpError('Android `input text` cannot type a literal "%s"')
        # The whole string in ONE `input text` call, quoted as one device-side shell word
        # (spaces are %s for `input text`), instead of one key press per character.
        await run(self._sh(serial, 'input', 'text', shlex.quote(text.replace(' ', '%s'))), timeout=30)
        return {'typed': len(text)}

    async def clear_text(self, serial, length):
        # Cursor to the end, then enough DEL + forward-DEL presses, all in ONE `input keyevent` call.
        codes = [ANDROID_MOVE_END] + [ANDROID_KEYS['delete']] * length + [ANDROID_FORWARD_DEL] * min(length, 20)
        await run(self._sh(serial, 'input', 'keyevent', *map(str, codes)), timeout=60)
        return {'cleared': True}

    async def key(self, serial, key):
        if key not in ANDROID_KEYS:
            raise OpError('Unsupported key on Android: ' + ', '.join(sorted(ANDROID_KEYS)))
        if key == 'wakeup':  # wake the screen and dismiss a swipe-only keyguard in one round trip
            await run(self._sh(serial, ANDROID_WAKEUP), timeout=15)
        else:
            await run(self._sh(serial, 'input', 'keyevent', str(ANDROID_KEYS[key])), timeout=15)
        return {'key': key}

    async def animations(self, serial, enabled):
        scale = '1' if enabled else '0'
        script = '; '.join(f'settings put global {name} {scale}' for name in ANDROID_ANIMATION_SCALES)
        await run(self._sh(serial, script), timeout=15)
        return {'animations': enabled}

    async def capture(self, serial):
        return 'png', await self.screenshot(serial)

    async def record(self, serial, seconds, started=None, bitrate=4000000, stop=None):
        path = '/sdcard/loma_rec.mp4'
        task = asyncio.ensure_future(run(self._sh(serial, 'screenrecord', '--time-limit', str(seconds),
                                                  '--bit-rate', str(int(bitrate)), path), timeout=seconds + 30))
        try:
            await asyncio.sleep(0.8)  # screenrecord needs a moment before frames flow
            if started is not None:
                await started()
            if stop is None:
                await task
            else:  # stop: an asyncio.Event that ends the recording early (scenario end_after_steps)
                waiter = asyncio.ensure_future(stop.wait())
                await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                if task.done():
                    task.result()
                else:
                    await run(self._sh(serial, 'pkill -INT screenrecord'), timeout=15, check=False)
                    await asyncio.gather(task, return_exceptions=True)  # interrupted on purpose
                    await asyncio.sleep(0.5)  # let screenrecord finish writing the mp4
            # adb pull to a file: run() caps stdout at MAX_OUTPUT, which would silently cut the mp4.
            with tempfile.TemporaryDirectory(prefix='loma-rec-') as tmp:
                target = Path(tmp) / 'rec.mp4'
                await run([self.adb, '-s', serial, 'pull', path, str(target)], timeout=60)
                return await fit_media(target, 'Recording', seconds)
        except BaseException:
            # A failed launch callback (or a cancelled call) must not leave screenrecord running on the device.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await run(self._sh(serial, 'pkill -INT screenrecord'), timeout=15, check=False)
            raise
        finally:
            await run(self._sh(serial, 'rm', '-f', path), timeout=15, check=False)

    async def logs(self, serial, lines, clear, source='auto'):
        if clear:
            await run([self.adb, '-s', serial, 'logcat', '-c'], timeout=15)
            return []
        _, out, _ = await run([self.adb, '-s', serial, 'logcat', '-d', '-v', 'time', '-t', str(lines)],
                              timeout=30, keep='tail')
        return out.decode('utf-8', 'replace').splitlines()


    async def frame(self, serial, region=None):
        """Screen-change signature from an uncompressed screencap (no PNG encode on the device)."""
        _, out, _ = await run([self.adb, '-s', serial, 'exec-out', 'screencap'], timeout=15, limit=MAX_RAW_FRAME)
        return await asyncio.to_thread(android_raw_signature, out, region)

    async def is_running(self, serial, app_id):
        """True / False, or None when it cannot be told (used to spot a crash during a scenario)."""
        code, out, _ = await run(self._sh(serial, 'pidof', app_id), timeout=10, check=False)
        text = out.decode('utf-8', 'replace').strip()
        if code == 0 and re.fullmatch(r'[0-9 ]+', text):
            return True
        return False if code == 1 and not text else None

    async def clock_skew(self, serial):
        """Device clock minus this machine's clock, in seconds (0 when they agree or it can't be read)."""
        before = time.time()
        code, out, _ = await run(self._sh(serial, 'date', '+%s.%N'), timeout=10, check=False)
        after = time.time()
        text = out.decode('utf-8', 'replace').strip()
        if code != 0 or not re.fullmatch(r'[0-9]{9,11}(\.[0-9]+)?', text.split('.N')[0]):
            return 0.0
        if re.fullmatch(r'[0-9]{9,11}\.[0-9]{3,}', text):
            return float(text) - (before + after) / 2
        # Whole seconds only (an old toybox without %N): trust it only for a clearly different clock.
        skew = int(text.split('.')[0]) - (before + after) / 2
        return float(round(skew)) if abs(skew) > 2 else 0.0

    async def timed_logs(self, serial, since, source='auto'):
        """[(host epoch seconds, line)] written at or after since (host epoch)."""
        skew = await self.clock_skew(serial)
        _, out, _ = await run([self.adb, '-s', serial, 'logcat', '-d', '-v', 'epoch'], timeout=30, keep='tail')
        entries = []
        for line in out.decode('utf-8', 'replace').splitlines():
            found = EPOCH_LINE.match(line)
            if found and float(found.group(1)) - skew >= since:
                entries.append((float(found.group(1)) - skew, line.strip()))
        return entries

def parse_uiautomator(xml_bytes, with_screen=False):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise OpError('Could not parse the Android UI hierarchy') from None
    elements, screen = [], [0, 0]
    for node in root.iter('node'):
        match = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', node.get('bounds', ''))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        screen = [max(screen[0], x2), max(screen[1], y2)]
        text, rid, desc = node.get('text', ''), node.get('resource-id', ''), node.get('content-desc', '')
        clickable = node.get('clickable') == 'true'
        if not (text or desc or rid or clickable) or len(elements) >= MAX_UI_ELEMENTS:
            continue
        element = {'type': node.get('class', '').rsplit('.', 1)[-1], 'text': text[:200],
                   'id': rid, 'label': desc[:200], 'clickable': clickable,
                   'bounds': [x1, y1, x2, y2], 'center': [(x1 + x2) // 2, (y1 + y2) // 2]}
        elements.append({k: v for k, v in element.items() if v not in ('', False)})
    return (elements, screen) if with_screen else elements


# ── iOS simulators ────────────────────────────────────────────────────────


class IOS:
    platform = 'ios'

    def __init__(self):
        self.log_start = {}  # serial -> local time of the last logs clear (the unified log cannot be cleared)
        self.consoles = {}  # serial -> (process, file path) of an app launched with console capture

    def available(self):
        return sys.platform == 'darwin' and shutil.which('xcrun') is not None

    async def list(self):
        _, out, _ = await run(['xcrun', 'simctl', 'list', 'devices', 'booted', '--json'], timeout=20)
        devices = []
        for runtime, items in json.loads(out or b'{}').get('devices', {}).items():
            version = runtime.rsplit('.', 1)[-1].replace('iOS-', '').replace('-', '.')
            for item in items:
                if item.get('state') == 'Booted' and SERIAL.fullmatch(item.get('udid', '')):
                    devices.append({'serial': item['udid'], 'platform': 'ios', 'virtual': True,
                                    'name': item.get('name', 'Simulator'), 'os_version': version})
        return devices

    def _idb(self):
        if shutil.which('idb') is None:
            raise OpError('iOS UI control needs idb on the runner: re-run `setup` on it (installs idb automatically)')
        return 'idb'

    async def install(self, serial, path, app_id, allowed=()):
        app, bundle_id = await asyncio.to_thread(prepare_app_bundle, path)
        if allowed and bundle_id not in allowed:
            raise OpError(f'Bundle {bundle_id} is not in allowed_app_ids; not installed')
        # Simulators do not check signatures: install over the old app and keep its data.
        # Fall back to a clean install only if the in-place update fails.
        code, _, err = await run(['xcrun', 'simctl', 'install', serial, str(app)], timeout=300, check=False)
        data_kept = True
        if code != 0:
            if bundle_id or app_id:
                await run(['xcrun', 'simctl', 'uninstall', serial, bundle_id or app_id], timeout=60, check=False)
            await run(['xcrun', 'simctl', 'install', serial, str(app)], timeout=300)
            data_kept = False
        return {'installed': app.name, 'bundle_id': bundle_id, 'data_kept': data_kept}

    async def install_stamp(self, serial, package):
        code, out, _ = await run(['xcrun', 'simctl', 'get_app_container', serial, package, 'app'],
                                 timeout=30, check=False)
        path = Path(out.decode('utf-8', 'replace').strip()) if code == 0 else None
        try:
            return f'{path}@{path.stat().st_mtime_ns}' if path else None
        except OSError:
            return None

    async def grant(self, serial, package, appops=(), privacy=()):
        granted = []
        for service in privacy:
            await run(['xcrun', 'simctl', 'privacy', serial, 'grant', service, package], timeout=30)
            granted.append(service)
        return {'granted': granted, **({'ignored': sorted(appops)} if appops else {})}

    async def uninstall(self, serial, app_id):
        await run(['xcrun', 'simctl', 'uninstall', serial, app_id], timeout=60)
        return {'uninstalled': app_id}

    @staticmethod
    def launch_arguments(extras=None, bool_extras=None):
        """UserDefaults argument domain: `-key value` overrides UserDefaults.standard for this launch."""
        argv = []
        for key, value in (extras or {}).items():
            argv += ['-' + key, value]
        for key, value in (bool_extras or {}).items():
            argv += ['-' + key, 'YES' if value else 'NO']
        return argv

    async def launch(self, serial, app_id, extras=None, bool_extras=None, activity=None, console=False,
                     restart=None):
        # restart=False on a running app only brings it to the front: iOS then ignores the launch arguments.
        args = self.launch_arguments(extras, bool_extras)
        await self._stop_console(serial)
        restart = ['--terminate-running-process'] if restart or (restart is None and (args or console)) else []
        if not console:
            await run(['xcrun', 'simctl', 'launch', *restart, serial, app_id, *args], timeout=60)
            return {'launched': app_id, **({'extras': sorted(extras or {}) + sorted(bool_extras or {})} if args else {})}
        # --console-pty keeps simctl attached to the app's stdout/stderr through a pty, so Swift
        # `print` is line-buffered and lands in this file as it happens; `logs` reads it back.
        folder = Path(tempfile.gettempdir()) / f'loma-console-{os.getuid()}'
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / f'{serial}.log'
        path.write_bytes(b'')
        with open(path, 'ab') as handle:  # O_APPEND: `logs clear` can truncate it while the app writes
            proc = await asyncio.create_subprocess_exec(
                'xcrun', 'simctl', 'launch', '--console-pty', *restart, serial, app_id, *args,
                stdin=asyncio.subprocess.DEVNULL, stdout=handle, stderr=handle)
        self.consoles[serial] = (proc, path)
        await asyncio.sleep(1.5)
        if proc.returncode not in (None, 0):
            raise OpError('simctl launch failed: ' + path.read_text(errors='replace')[-500:])
        return {'launched': app_id, 'console': True, **({'extras': sorted(extras or {}) + sorted(bool_extras or {})}
                                                          if args else {})}

    async def _stop_console(self, serial):
        proc, _ = self.consoles.get(serial, (None, None))
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()

    async def stop(self, serial, app_id):
        await run(['xcrun', 'simctl', 'terminate', serial, app_id], timeout=30, check=False)
        await self._stop_console(serial)
        return {'stopped': app_id}

    async def reset_app(self, serial, app_id):
        raise OpError("iOS simulators cannot clear one app's data; reinstall it with install instead")

    async def open_url(self, serial, url):
        await run(['xcrun', 'simctl', 'openurl', serial, url], timeout=30)
        return {'opened': url}

    async def screenshot(self, serial):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'shot.png'
            await run(['xcrun', 'simctl', 'io', serial, 'screenshot', '--type=png', str(target)], timeout=30)
            return target.read_bytes()

    async def capture(self, serial):
        return 'png', await self.screenshot(serial)

    async def record(self, serial, seconds, started=None, bitrate=None, stop=None):  # simctl has no bitrate option
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'rec.mp4'
            proc = await asyncio.create_subprocess_exec(
                'xcrun', 'simctl', 'io', serial, 'recordVideo', '--codec=h264', '--force', str(target),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                await asyncio.sleep(0.8)
                if started is not None:
                    await started()
                if stop is None:
                    await asyncio.sleep(seconds)
                else:
                    try:
                        await asyncio.wait_for(stop.wait(), seconds)
                    except asyncio.TimeoutError:
                        pass
                proc.send_signal(2)  # SIGINT finalises the movie file
                await asyncio.wait_for(proc.wait(), 20)
            except BaseException:
                if proc.returncode is None:
                    proc.kill()
                raise
            if not target.exists():
                raise OpError('simctl recordVideo produced no file')
            return await fit_media(target, 'Recording', seconds)

    async def ui_tree(self, serial):
        _, out, _ = await run([self._idb(), 'ui', 'describe-all', '--udid', serial, '--json'], timeout=30)
        try:
            raw = json.loads(out)
        except ValueError:
            raw = [json.loads(line) for line in out.decode().splitlines() if line.strip().startswith('{')]
        elements, screen = [], [0, 0]
        for node in raw if isinstance(raw, list) else []:
            frame = node.get('frame') or {}
            x, y, w, h = (float(frame.get(k, 0)) for k in ('x', 'y', 'width', 'height'))
            screen = [max(screen[0], round(x + w)), max(screen[1], round(y + h))]
            element = {'type': node.get('type', ''), 'text': str(node.get('AXValue') or '')[:200],
                       'label': str(node.get('AXLabel') or '')[:200], 'id': node.get('AXUniqueId') or '',
                       'bounds': [round(x), round(y), round(x + w), round(y + h)],
                       'center': [round(x + w / 2), round(y + h / 2)]}
            if len(elements) < MAX_UI_ELEMENTS:
                elements.append({k: v for k, v in element.items() if v not in ('', None)})
        return {'units': 'points', 'screen': screen, 'elements': elements}

    async def animations(self, serial, enabled):
        raise OpError('animations is Android-only; iOS simulators have no global animation switch')

    async def tap(self, serial, x, y):
        await run([self._idb(), 'ui', 'tap', '--udid', serial, str(x), str(y)], timeout=15)
        return {'tapped': [x, y]}

    async def swipe(self, serial, x1, y1, x2, y2, duration_ms):
        await run([self._idb(), 'ui', 'swipe', '--udid', serial, '--duration', str(duration_ms / 1000),
                   str(x1), str(y1), str(x2), str(y2)], timeout=30)
        return {'swiped': [x1, y1, x2, y2]}

    async def type_text(self, serial, text):
        await run([self._idb(), 'ui', 'text', '--udid', serial, '--', text], timeout=30)
        return {'typed': len(text)}

    async def clear_text(self, serial, length):
        # Backspace then forward-delete: clears the field wherever the tap left the cursor. One call.
        codes = [IOS_HID_KEYS['delete']] * length + [IOS_FORWARD_DELETE] * length
        await run([self._idb(), 'ui', 'key-sequence', '--udid', serial, *map(str, codes)], timeout=60)
        return {'cleared': True}

    async def key(self, serial, key):
        if key == 'wakeup':
            return {'key': key, 'note': 'Simulators do not sleep; nothing to do'}
        if key in IOS_HID_KEYS:
            await run([self._idb(), 'ui', 'key', '--udid', serial, str(IOS_HID_KEYS[key])], timeout=15)
            return {'key': key}
        if key not in IOS_BUTTONS:
            raise OpError('Unsupported key on iOS: ' + ', '.join(sorted({*IOS_BUTTONS, *IOS_HID_KEYS, 'wakeup'})))
        await run([self._idb(), 'ui', 'button', '--udid', serial, IOS_BUTTONS[key]], timeout=15)
        return {'key': key}

    def console_lines(self, serial, clear=False):
        """stdout/stderr of the app last launched with console capture, or None if there is none."""
        _, path = self.consoles.get(serial, (None, None))
        if path is None or not path.exists():
            return None
        if clear:
            with open(path, 'r+b') as handle:
                handle.truncate(0)
            return []
        with open(path, 'rb') as handle:
            handle.seek(max(0, path.stat().st_size - MAX_OUTPUT))
            return handle.read().decode('utf-8', 'replace').replace('\r\n', '\n').splitlines()

    async def logs(self, serial, lines, clear, source='auto'):
        console = self.console_lines(serial, clear) if source in ('auto', 'console') else None
        if source == 'console' and console is None:
            raise OpError('No console capture on this simulator: launch the app with console=true first')
        if clear:
            self.log_start[serial] = time.strftime('%Y-%m-%d %H:%M:%S')
            return []
        if console is not None:
            return console[-lines:]
        window = ['--start', self.log_start[serial]] if serial in self.log_start else ['--last', '2m']
        _, out, _ = await run(['xcrun', 'simctl', 'spawn', serial, 'log', 'show', *window,
                               '--style', 'compact'], timeout=60, keep='tail')
        return out.decode('utf-8', 'replace').splitlines()[-lines:]


    async def frame(self, serial, region=None):
        """Screen-change signature from an uncompressed BMP screenshot."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'frame.bmp'
            await run(['xcrun', 'simctl', 'io', serial, 'screenshot', '--type=bmp', str(target)], timeout=15)
            data = target.read_bytes()
        return await asyncio.to_thread(bmp_signature, data, region)

    async def is_running(self, serial, app_id):
        code, out, _ = await run(['xcrun', 'simctl', 'spawn', serial, 'launchctl', 'list'], timeout=15, check=False)
        if code != 0:
            return None
        return f'UIKitApplication:{app_id}[' in out.decode('utf-8', 'replace')

    async def clock_skew(self, serial):
        return 0.0  # simulators use this machine's clock

    def console_size(self, serial):
        _, path = self.consoles.get(serial, (None, None))
        try:
            return path.stat().st_size if path is not None else None
        except OSError:
            return None

    def console_since(self, serial, offset):
        """(new console lines after byte offset, end offset). A truncated file (logs clear) restarts at 0."""
        _, path = self.consoles.get(serial, (None, None))
        if path is None or not path.exists():
            raise OpError('No console capture on this simulator: launch the app with console=true first')
        size = path.stat().st_size
        offset = 0 if offset > size else max(offset, size - MAX_OUTPUT)
        with open(path, 'rb') as handle:
            handle.seek(offset)
            data = handle.read(size - offset)
        end = data.rfind(b'\n') + 1  # only complete lines; the rest is read next time
        text = data[:end].decode('utf-8', 'replace').replace('\r\n', '\n')
        return text.splitlines(), offset + end

    async def timed_logs(self, serial, since, source='auto'):
        """[(epoch seconds, line)] from the simulator's unified log at or after since."""
        start = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(since)))
        _, out, _ = await run(['xcrun', 'simctl', 'spawn', serial, 'log', 'show', '--start', start,
                               '--style', 'compact'], timeout=60, keep='tail')
        entries = []
        for line in out.decode('utf-8', 'replace').splitlines():
            found = IOS_LOG_TIME.match(line)
            if not found:
                continue
            at = time.mktime(time.strptime(found.group(1), '%Y-%m-%d %H:%M:%S')) + float(found.group(2) or 0)
            if at >= since:
                entries.append((at, line.strip()))
        return entries

def collect_screenshots(folder):
    """takeScreenshot outputs, read before the flow's temp dir is deleted (16 MB budget)."""
    shots, total = [], 0
    for path in sorted(folder.rglob('*.png'), key=lambda p: p.stat().st_mtime):
        data = path.read_bytes()
        total += len(data)
        if total > MAX_MEDIA_BYTES:
            break
        shots.append({'name': path.name, 'png_base64': base64.b64encode(data).decode()})
    return shots


# ── Build files ───────────────────────────────────────────────────────────


def prepare_app_bundle(path):
    """Find the .app in a build; return (app, bundle id). CI artifact zips carry no unix
    modes, so restore the executable bit on the app's main binary."""
    app = find_file(path, '.app')
    try:
        with open(app / 'Info.plist', 'rb') as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        raise OpError('Could not read the app bundle identifier (Info.plist)') from None
    executable = app / str(info.get('CFBundleExecutable') or '')
    if executable != app and executable.is_file():
        executable.chmod(executable.stat().st_mode | 0o755)
    return app, str(info.get('CFBundleIdentifier') or '')


class ExtractBudget:
    """One byte/member budget shared by an archive and every zip nested in it."""

    def __init__(self):
        self.bytes, self.members, self.archives = 0, 0, 0


def safe_extract(archive, target, budget=None):
    budget = budget or ExtractBudget()
    budget.archives += 1
    if budget.archives > MAX_NESTED_ZIPS + 1:
        raise OpError('Build archive nests too many zips')
    target = Path(target).resolve()
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        for member in members:
            name = member.filename
            if (name.startswith('/') or '\\' in name or '..' in Path(name).parts
                    or not (target / name).resolve().is_relative_to(target)):
                raise OpError('Build archive contains an unsafe path')
            if stat.S_ISLNK(member.external_attr >> 16):
                raise OpError('Build archive contains a symlink; refusing to extract')
            budget.bytes += member.file_size
            budget.members += 1
            if budget.bytes > MAX_EXTRACT_BYTES or budget.members > MAX_EXTRACT_MEMBERS:
                raise OpError('Build archive expands beyond the size limit')
        zf.extractall(target)
        # Preserve the executable bit so iOS .app bundles still launch.
        for member in members:
            mode = (member.external_attr >> 16) & 0o777
            path = target / member.filename
            if mode and not member.is_dir() and path.is_file():
                os.chmod(path, mode | stat.S_IRUSR | stat.S_IWUSR)


def find_file(path, suffix):
    """Resolve an .apk file or an .app bundle inside a downloaded build (zips nested up to 5 deep)."""
    path = Path(path)
    if path.suffix == suffix:
        return path
    if not (path.is_file() and zipfile.is_zipfile(path)):
        raise OpError(f'Build is not an {suffix} or a zip containing one')

    def shallow(items):
        return sorted((p for p in items if '__MACOSX' not in p.parts), key=lambda p: (len(p.parts), str(p)))

    budget = ExtractBudget()
    queue = [(path, path.parent / (path.stem + '-x'))]
    while queue:
        archive, out = queue.pop(0)
        safe_extract(archive, out, budget)
        matches = shallow(out.rglob('*' + suffix))
        if matches:
            return matches[0]
        queue += [(nested, nested.parent / (nested.stem + '-x')) for nested in shallow(out.rglob('*.zip'))]
    raise OpError(f'No {suffix} found in the build archive')


# ── Runner ────────────────────────────────────────────────────────────────


class Runner:
    OPS = {'install', 'uninstall', 'launch', 'stop', 'reset_app', 'open_url', 'screenshot',
           'ui_tree', 'tap', 'swipe', 'type', 'key', 'logs', 'run_flow',
           'set_text', 'clear_text', 'wait_for', 'tap_text', 'scroll_until_visible', 'burst', 'record',
           'animations', 'scenario'}
    APP_OPS = {'install', 'uninstall', 'launch', 'stop', 'reset_app'}
    LAUNCHING_OPS = {'burst', 'record', 'scenario'}  # may launch app_id right before capturing
    CACHE_KEEP = 4

    def __init__(self, config, drivers=None, session=None):
        self.config = config
        self.policy = {**default_policy(), **config.get('policy', {})}
        self.allowed_apps = tuple(self.policy['allowed_app_ids'] or ())
        self.drivers = drivers if drivers is not None else [d for d in (Android(), IOS()) if d.available()]
        self.session = session
        self.locks = {}
        self.inventory = {}
        self.send_lock = None  # created per connection: on 3.9 a Lock binds to the loop current at creation
        self.cf_headers = {}
        self.installed = {}  # (serial, package) -> (build sha256, install stamp) of builds this runner installed
        self.cache_dir = Path(config.get('cache_dir') or CONFIG_DIR / 'build-cache')
        self.keep_awake = KeepAwake(self.policy.get('keep_awake', True))

    def capabilities(self):
        caps = [d.platform for d in self.drivers]
        caps += [tool for tool in ('maestro', 'idb') if shutil.which(tool)]
        return caps

    async def refresh(self):
        inventory = {}
        for driver in self.drivers:
            try:
                devices = await driver.list()
            except (OpError, ValueError):
                continue
            for device in devices:
                if device['virtual'] or self.policy['allow_physical_devices']:
                    inventory[device['serial']] = (driver, device)
        self.inventory = inventory
        return [device for _, device in inventory.values()]

    async def call(self, op, serial, args):
        if op not in self.OPS:
            raise OpError('Unsupported operation')
        if not isinstance(serial, str) or not SERIAL.fullmatch(serial) or not isinstance(args, dict):
            raise OpError('Invalid device or arguments')
        if serial not in self.inventory:
            await self.refresh()
        if serial not in self.inventory:
            raise OpError('Device is not connected to this runner (is the emulator/simulator running?)')
        driver, _ = self.inventory[serial]
        app_id = None
        if op in self.APP_OPS or op in self.LAUNCHING_OPS:
            app_id = need_str(args, 'app_id', APP_ID, 255, optional=op in ('install', *self.LAUNCHING_OPS))
            if self.allowed_apps and app_id not in self.allowed_apps and (app_id or op in self.APP_OPS):
                raise OpError(f"App {app_id} is not in this runner's allowed_app_ids (install needs app_id)")
        self.keep_awake.touch()
        lock = self.locks.setdefault(serial, asyncio.Lock())
        async with lock:
            return await self._dispatch(driver, op, serial, args, app_id)

    async def _dispatch(self, driver, op, serial, args, app_id):
        if op == 'install':
            return await self.install(driver, serial, args, app_id)
        if op == 'launch':
            return await driver.launch(serial, app_id, **need_launch(args))
        if op in ('uninstall', 'stop', 'reset_app'):
            return await getattr(driver, op)(serial, app_id)
        if op in ('set_text', 'clear_text'):
            return await self.set_text(driver, serial, args, op == 'set_text')
        if op in ('wait_for', 'tap_text'):
            timeout = need_int(args, 'timeout_s', 0, 60, default=10 if op == 'wait_for' else 5)
            gone = need_bool(args, 'gone') if op == 'wait_for' else False
            found = await self.wait_for(driver, serial, need_selector(args), timeout, gone)
            if op == 'wait_for':
                return found
            if not found['found']:
                raise OpError(f"No element matching {args.get('match')!r}. Visible: {found['visible']}")
            element = found['element']
            await driver.tap(serial, *element['center'])
            return {'tapped': element['center'], 'element': element, 'elapsed_ms': found['elapsed_ms']}
        if op == 'scroll_until_visible':
            direction = args.get('direction', 'down')
            if direction not in ('down', 'up'):
                raise OpError('Invalid direction (down or up)')
            return await self.scroll_until_visible(driver, serial, need_selector(args), direction,
                                                   need_int(args, 'max_swipes', 1, 20, default=8))
        if op == 'burst':
            return await self.burst(driver, serial, need_int(args, 'count', 2, 12),
                                    need_int(args, 'interval_ms', 100, 5000, default=500), self._launcher(driver, serial, args, app_id))
        if op == 'record':
            seconds = need_int(args, 'duration_s', 1, 20)
            data = await driver.record(serial, seconds, self._launcher(driver, serial, args, app_id))
            if len(data) > MAX_MEDIA_BYTES:
                raise OpError('Recording is larger than 16 MB; use a shorter duration')
            return {'mp4_base64': base64.b64encode(data).decode(), 'bytes': len(data), 'duration_s': seconds}
        if op == 'animations':
            return await driver.animations(serial, need_bool(args, 'enabled'))
        if op == 'open_url':
            return await driver.open_url(serial, check_url(need_str(args, 'url', max_len=2000)))
        if op == 'screenshot':
            data = await driver.screenshot(serial)
            width, height = png_size(data)
            return {'png_base64': base64.b64encode(data).decode(), 'width': width, 'height': height}
        if op == 'ui_tree':
            return await driver.ui_tree(serial)
        if op == 'tap':
            return await driver.tap(serial, need_int(args, 'x', 0, 10000), need_int(args, 'y', 0, 10000))
        if op == 'swipe':
            return await driver.swipe(serial, *(need_int(args, k, 0, 10000) for k in ('x1', 'y1', 'x2', 'y2')),
                                      need_int(args, 'duration_ms', 50, 5000, default=300))
        if op == 'type':
            return await driver.type_text(serial, need_str(args, 'text', max_len=MAX_TEXT))
        if op == 'key':
            return await driver.key(serial, need_str(args, 'key', max_len=32))
        if op == 'logs':
            return await self.logs(driver, serial, args)
        if op == 'scenario':
            return await self.scenario(driver, serial, args, app_id)
        if op == 'run_flow':
            return await self.run_flow(serial, need_str(args, 'flow', max_len=MAX_FLOW))
        raise OpError('Unsupported operation')

    # ── Logs: tag filters and a cursor, so repeated reads return only new lines ──

    def _console(self, driver, serial, source):
        """True when logs should come from the iOS console capture."""
        has = driver.platform == 'ios' and driver.console_size(serial) is not None
        if source == 'console' and not has:
            raise OpError('No console capture on this simulator: launch the app with console=true first')
        return has and source in ('auto', 'console')

    def _cursor(self, driver, serial, source):
        if self._console(driver, serial, source):
            return f'c:{driver.console_size(serial)}'
        return f't:{time.time():.3f}'

    async def logs(self, driver, serial, args):
        lines = need_int(args, 'lines', 1, 2000, default=300)
        clear = need_bool(args, 'clear')
        needle = need_str(args, 'filter', max_len=200, optional=True)
        source = need_log_source(args)
        tags = need_tags(args, 'tags')
        since = need_str(args, 'since', CURSOR, 40, optional=True)
        if since and clear:
            raise OpError('Use since or clear, not both')
        cursor = self._cursor(driver, serial, source)  # taken first: a later read never misses a line
        if since is None:
            output = await driver.logs(serial, 5000 if needle or tags else lines, clear, source)
        elif since.startswith('c:'):
            if not self._console(driver, serial, source):
                raise OpError('A c: cursor is for iOS console logs; launch the app with console=true')
            output, end = driver.console_since(serial, int(since[2:]))
            cursor = f'c:{end}'
        else:
            output = [line for _, line in await driver.timed_logs(serial, float(since[2:]), source)]
        output, counts = tag_filter(output, tags, needle)
        result = {'lines': [line[:2000] for line in output[-lines:]], 'cleared': clear}
        if not clear:
            result['cursor'] = cursor
        if tags:
            result['counts'] = counts
        return result

    # ── Scenario: timed steps + video + screen-change timeline + logs, in one call ──

    async def scenario(self, driver, serial, args, app_id):
        duration = need_int(args, 'duration_s', 1, MAX_SCENARIO_SECONDS)
        record = need_bool(args, 'record')
        sample_ms = need_int(args, 'sample_ms', 0, 2000, default=0)
        if 0 < sample_ms < MIN_SAMPLE_MS:
            raise OpError(f'sample_ms must be 0 (off) or {MIN_SAMPLE_MS}-2000')
        if not sample_ms and {'sample_region', 'sample_min_change'} & set(args):
            raise OpError('sample_region / sample_min_change need sample_ms')
        tags = need_tags(args, 'log_tags')
        if not app_id and {'extras', 'bool_extras', 'activity', 'console'} & set(args):
            raise OpError('extras / activity / console need app_id')
        steps = need_steps(args, duration * 1000, app_id, self.allowed_apps)
        checks = need_preflight(args, self.policy.get('preflight', 'public'))
        plan = {
            'app_id': app_id, 'duration': duration, 'sample_ms': sample_ms, 'tags': tags,
            'steps': steps,
            'region': need_region(args, 'sample_region'),
            'min_change': need_number(args, 'sample_min_change', 0, 1, 0),
            'source': need_log_source(args, 'log_source'),
            'log_lines': need_int(args, 'log_lines', 1, 2000, default=200),
            'stop_on_fail': need_bool(args, 'stop_on_fail'),
            'end_after_steps': need_bool(args, 'end_after_steps'),
            'stop_recording': asyncio.Event(),
            'launch': need_launch(args) if app_id else None,
            'expect': need_expect(args, bool(tags), bool(sample_ms), bool(app_id),
                                  any('at_ms' in step for step in steps)),
        }
        stop_first = need_bool(args, 'stop_first', default=True)
        loop = asyncio.get_running_loop()
        checked = await self.preflight(checks) if checks else []
        blocked = [f"preflight {check['name']}: {check['error']}" for check in checked if not check['ok']]
        if blocked:  # the environment is not ready: the device is not touched and nothing is judged
            return {'duration_s': duration, 'verdict': 'blocked', 'failed': blocked, 'preflight': checked}
        if app_id and stop_first:
            await driver.stop(serial, app_id)
        if tags:
            try:
                await driver.logs(serial, 1, True, plan['source'])
            except OpError:  # iOS console: there is none until the launch below creates a fresh one
                pass
        started = asyncio.Event()

        async def mark_started():
            started.set()
        recording, result = None, {'duration_s': duration, **({'preflight': checked} if checked else {})}
        if record:  # recording first, so the launch is on video; t0 is when frames start flowing
            began = loop.time()
            # Longer windows record at a lower bitrate so the file stays under the media limit.
            bitrate = min(4000000, MAX_MEDIA_BYTES * 6 // (duration + 1))
            recording = asyncio.ensure_future(driver.record(serial, duration + 1, mark_started, bitrate=bitrate,
                                                            stop=plan['stop_recording']))
            waiter = asyncio.ensure_future(started.wait())
            await asyncio.wait({recording, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if not started.is_set():
                waiter.cancel()
                recording.result()  # raises the recording failure
                raise OpError('Recording ended before it started')
            result['video_offset_ms'] = int((loop.time() - began) * 1000)
        try:
            return await self._scenario_window(driver, serial, plan, recording, result)
        except BaseException:
            if recording is not None and not recording.done():
                recording.cancel()
                await asyncio.gather(recording, return_exceptions=True)
            raise

    async def preflight(self, checks):
        """Run the HTTP checks; each result is {name, ok, status?, error?} (the response body is never returned)."""
        import aiohttp
        mode, results = self.policy.get('preflight', 'public'), []
        session = self.session or aiohttp.ClientSession()
        try:
            for check in checks:
                entry = {'name': check['name'], 'ok': False}
                results.append(entry)
                if mode != 'any' and await private_host(check['host'], check['port']):
                    entry['error'] = ("host is on a private or local network; the runner owner can allow that "
                                      "with policy preflight: 'any'")
                    continue
                body = check['body']
                try:
                    async with session.request(
                            check['method'], check['url'], headers=check['headers'], allow_redirects=False,
                            json=body if isinstance(body, dict) else None,
                            data=body if isinstance(body, str) else None,
                            timeout=aiohttp.ClientTimeout(total=check['timeout_s'])) as response:
                        raw = b''
                        async for chunk in response.content.iter_chunked(65536):
                            raw += chunk
                            if len(raw) >= MAX_PREFLIGHT_READ:
                                break
                        entry['status'] = response.status
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                    entry['error'] = f'request failed ({type(exc).__name__}): {str(exc)[:200]}'
                    continue
                text = raw[:MAX_PREFLIGHT_READ].decode('utf-8', 'replace')
                missing = [needle for needle in check['contains'] if needle not in text]
                if entry['status'] != check['status']:
                    entry['error'] = f"status {entry['status']} (expected {check['status']})"
                elif missing:
                    entry['error'] = 'response does not contain ' + ', '.join(repr(needle) for needle in missing)
                else:
                    entry['ok'] = True
        finally:
            if session is not self.session:
                await session.close()
        return results

    async def _scenario_window(self, driver, serial, plan, recording, result):
        loop = asyncio.get_running_loop()
        app_id, steps, duration, sample_ms, tags = (plan[k] for k in ('app_id', 'steps', 'duration', 'sample_ms', 'tags'))
        start, wall = loop.time(), time.time()
        window_end = start + duration
        frames = {'interval_ms': sample_ms, 'samples': 0, 'changes': []}
        console, step_results, shots, background, use_console = [], [], [], [], False
        try:
            if sample_ms:
                background.append(asyncio.ensure_future(self._sample_frames(
                    driver, serial, start, window_end, sample_ms, frames, plan['region'], plan['min_change'])))
            if app_id:
                try:
                    await driver.launch(serial, app_id, **plan['launch'])
                    result['launch'] = {'ok': True, 'took_ms': int((loop.time() - start) * 1000)}
                except OpError as exc:
                    result['launch'] = {'ok': False, 'error': str(exc)[:300]}
            if tags and driver.platform == 'ios' and self._console_ready(driver, serial, plan['source']):
                use_console = True
                fresh = bool(app_id and plan['launch'].get('console'))  # this launch started a new capture file
                background.append(asyncio.ensure_future(self._tail_console(driver, serial, start, console, fresh)))
            runner = asyncio.ensure_future(self._run_steps(driver, serial, steps, start, window_end, step_results,
                                                           shots, plan['stop_on_fail']))
            background.append(runner)
            if plan['end_after_steps'] and steps:  # duration_s is then only an upper bound
                await asyncio.wait({runner}, timeout=max(0.0, window_end - loop.time()))
                if runner.done():
                    await asyncio.sleep(max(0.0, min(END_TAIL, window_end - loop.time())))
                    plan['stop_recording'].set()
            else:
                await asyncio.sleep(max(0.0, window_end - loop.time()))
            if not runner.done():
                await asyncio.wait({runner}, timeout=SCENARIO_GRACE)
            result['ran_ms'] = int((loop.time() - start) * 1000)
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
        for entry in step_results:
            if 'ok' not in entry:  # still running when the window (plus grace) ended
                entry.update(ok=False, error='cancelled: still running at the end of the window')
        done = len(step_results)
        for index, step in enumerate(steps[done:], done + 1):
            skipped = {'i': index, 'action': step['action'], 'skipped': True}
            step_results.append({**skipped, 'at_ms': step['at_ms']} if 'at_ms' in step else skipped)
        if steps:
            result['steps'] = step_results
            drifts = [entry['drift_ms'] for entry in step_results if 'drift_ms' in entry]
            if drifts:  # how late the at_ms steps ran: a large value means the timing was not the one asked for
                result['max_drift_ms'] = max(drifts)
        if app_id:
            try:
                result['app_running'] = await driver.is_running(serial, app_id)
            except OpError:
                result['app_running'] = None
        if sample_ms:
            changes = merge_changes(frames['changes'], max(sample_ms, frames.get('capture_ms', 0)))
            frames.update(changes=changes[:40], units='pixels',
                          last_change_ms=changes[-1]['to_ms'] if changes else None)
            if len(changes) > 40:
                frames['changes_dropped'] = len(changes) - 40
            result['frames'] = frames
        entries = []
        if tags:
            result['logs'], entries = await self._scenario_logs(driver, serial, plan['source'], tags, wall,
                                                                console if use_console else None, plan['log_lines'])
        if plan['expect'] is not None:
            values = value_stats(plan['expect'], entries)
            if values:
                result['logs']['values'] = values
            failed = judge(plan['expect'], result, entries)
            result.update(verdict='fail' if failed else 'pass', **({'failed': failed[:20]} if failed else {}))
        budget = MAX_MEDIA_BYTES - sum(len(shot['png_base64']) * 3 // 4 for shot in shots)
        if shots:
            result['screenshots'] = shots
        if recording is not None:  # a video that cannot be delivered never loses the rest of the result
            try:
                data = await asyncio.wait_for(recording, duration + 40 + REENCODE_SECONDS)
                if len(data) > budget:
                    result['video_error'] = (f'recording is {len(data) // (1024 * 1024)} MB, over the media limit; '
                                             'use a shorter duration_s or fewer screenshot steps')
                else:
                    result.update(mp4_base64=base64.b64encode(data).decode(), video_bytes=len(data))
            except (OpError, asyncio.TimeoutError) as exc:
                result['video_error'] = str(exc)[:300] or 'recording did not finish'
        return result

    def _console_ready(self, driver, serial, source):
        try:
            return self._console(driver, serial, source)
        except OpError:
            return False

    async def _run_steps(self, driver, serial, steps, start, window_end, results, shots, stop_on_fail):
        loop = asyncio.get_running_loop()
        previous_end = loop.time()
        for index, step in enumerate(steps, 1):
            due = start + step['at_ms'] / 1000 if 'at_ms' in step else previous_end + step['after_ms'] / 1000
            if due >= window_end:  # no time left in the window: this and later steps are reported as skipped
                return
            await asyncio.sleep(max(0.0, due - loop.time()))
            began = loop.time()
            entry = {'i': index, 'action': step['action'], 'ran_ms': int((began - start) * 1000)}
            if 'at_ms' in step:
                entry.update(at_ms=step['at_ms'], drift_ms=entry['ran_ms'] - step['at_ms'])
            results.append(entry)
            try:
                entry.update(await self._scenario_step(driver, serial, step, entry['ran_ms'], shots), ok=True)
            except OpError as exc:
                entry.update(ok=False, error=str(exc)[:300])
            previous_end = loop.time()
            entry['took_ms'] = int((previous_end - began) * 1000)
            if not entry['ok'] and stop_on_fail:
                return

    async def _scenario_step(self, driver, serial, step, ran_ms, shots):
        action = step['action']
        if action == 'tap':
            await driver.tap(serial, step['x'], step['y'])
            return {}
        if action == 'swipe':
            await driver.swipe(serial, step['x1'], step['y1'], step['x2'], step['y2'], step['duration_ms'])
            return {}
        if action == 'type':
            await driver.type_text(serial, step['text'])
            return {}
        if action == 'key':
            await driver.key(serial, step['key'])
            return {}
        if action == 'open_url':
            await driver.open_url(serial, step['url'])
            return {}
        if action in ('set_text', 'clear_text'):
            await self.set_text(driver, serial, step['args'], action == 'set_text')
            return {}
        if action == 'scroll_until_visible':
            found = await self.scroll_until_visible(driver, serial, step['selector'], step['direction'],
                                                    step['max_swipes'])
            if not found['found']:
                raise OpError(f"{step['selector'][0]!r} not found after {found['swipes']} swipes. "
                              f"Visible: {found['visible'][:15]}")
            return {'swipes': found['swipes']}
        if action == 'screenshot':
            data = await driver.screenshot(serial)
            if sum(len(shot['png_base64']) for shot in shots) * 3 // 4 + len(data) > MAX_MEDIA_BYTES // 2:
                raise OpError('Screenshot budget for this scenario is used up')
            shots.append({'name': step['name'], 'at_ms': ran_ms, 'png_base64': base64.b64encode(data).decode()})
            return {'name': step['name']}
        if action == 'launch_app':
            await driver.launch(serial, step['app_id'], **step['launch'])
            return {}
        if action == 'stop_app':
            await driver.stop(serial, step['app_id'])
            return {}
        found = await self.wait_for(driver, serial, step['selector'], step['timeout_s'], step['gone'])
        if action == 'wait_for':
            if not found['found']:
                raise OpError(f"{step['selector'][0]!r} {'still visible' if step['gone'] else 'not found'} "
                              f"after {step['timeout_s']}s")
            return {'found_ms': found['elapsed_ms']}
        if not found['found']:
            raise OpError(f"No element matching {step['selector'][0]!r}. Visible: {found['visible'][:15]}")
        element = found['element']
        await driver.tap(serial, *element['center'])
        return {'tapped': element.get('text') or element.get('label') or element.get('id'), 'at': element['center']}

    async def _sample_frames(self, driver, serial, start, until, interval_ms, out, region=None, min_change=0.0):
        """Fills out['changes'] with {at_ms, changed (fraction of the watched region), box} as frames differ."""
        loop = asyncio.get_running_loop()
        previous, failures, capture_s, tick = None, 0, 0.0, start
        while loop.time() < until:
            began = loop.time()
            try:
                signature = await driver.frame(serial, region)
            except OpError as exc:
                failures += 1
                out['error'] = str(exc)[:200]
                if failures >= 3:
                    return
                await asyncio.sleep(0.05)
                continue
            capture_s += loop.time() - began
            out['samples'] += 1
            out['capture_ms'] = int(capture_s * 1000 / out['samples'])
            out['region'] = list(signature['area'])
            change = frame_change(previous, signature)
            if change is not None and change[0] > 0 and change[0] >= min_change:
                out['changes'].append({'at_ms': int((began - start) * 1000), 'changed': change[0], 'box': change[1]})
            previous = signature
            tick = max(tick + interval_ms / 1000, loop.time())
            await asyncio.sleep(max(0.0, tick - loop.time()))

    async def _tail_console(self, driver, serial, start, out, fresh):
        """Timestamp iOS console lines (print() has no timestamps) as they are written."""
        loop = asyncio.get_running_loop()
        offset = 0 if fresh else (driver.console_size(serial) or 0)
        while True:
            lines, offset = driver.console_since(serial, offset)
            at = int((loop.time() - start) * 1000)
            out.extend((at, line) for line in lines)
            await asyncio.sleep(0.1)

    async def _scenario_logs(self, driver, serial, source, tags, wall, console, limit):
        if console is not None:
            entries, timing = console, 'arrival (console lines carry no timestamp; +-100 ms)'
        else:
            raw = await driver.timed_logs(serial, wall - 1, source)
            entries, timing = [(int((at - wall) * 1000), line) for at, line in raw], 'log timestamp'
        kept, counts = tag_filter(entries, tags, text=lambda entry: entry[1])
        first = {}
        for at, line in kept:
            for tag in tags:
                if tag not in first and tag.lower() in line.lower():
                    first[tag] = at
        return {'counts': counts, 'first_ms': first, 't_ms': timing, 'total': len(kept),
                'lines': [[at, line[:LOG_LINE_CHARS]] for at, line in kept[-limit:]]}, kept

    # ── Compound ops: one round trip from the agent, polling here on the runner machine ──

    def _launcher(self, driver, serial, args, app_id):
        """For burst/record: a callback that launches app_id right when capture starts."""
        if not app_id:
            return None
        options = need_launch(args)

        async def launch():
            await driver.launch(serial, app_id, **options)
        return launch

    async def wait_for(self, driver, serial, selector, timeout, gone=False):
        loop = asyncio.get_running_loop()
        start, polls, visible, error = loop.time(), 0, [], None
        while True:
            polls += 1
            try:
                tree = await driver.ui_tree(serial)
                hits = find_elements(tree['elements'], *selector)
                visible = [e.get('text') or e.get('label') for e in tree['elements'] if e.get('text') or e.get('label')]
                error = None
            except OpError as exc:  # UI not idle (animations): keep polling until the deadline
                hits, error = None, str(exc)[:300]
            elapsed = loop.time() - start
            if hits is not None and bool(hits) != gone:
                result = {'found': True, 'elapsed_ms': int(elapsed * 1000), 'polls': polls}
                return {**result, 'element': hits[0], 'matches': len(hits)} if hits else result
            if elapsed >= timeout:
                return {'found': False, 'elapsed_ms': int(elapsed * 1000), 'polls': polls,
                        'visible': [v[:60] for v in visible[:40]], **({'last_error': error} if error else {})}
            await asyncio.sleep(POLL_SECONDS)

    async def set_text(self, driver, serial, args, typing):
        text = need_str(args, 'text', max_len=MAX_TEXT) if typing else None
        clear = need_bool(args, 'clear', default=True) if typing else True
        length, element = 64, None
        if args.get('match') is not None:
            found = await self.wait_for(driver, serial, need_selector(args), 5)
            if not found['found']:
                raise OpError(f"No element matching {args.get('match')!r}. Visible: {found['visible']}")
            element = found['element']
            await driver.tap(serial, *element['center'])
            await asyncio.sleep(0.3)
            length = min(len(element.get('text') or '') + 8, MAX_TEXT)
        result = {'focused': element} if element else {}
        if clear:
            result.update(await driver.clear_text(serial, length))
        if typing:
            result.update(await driver.type_text(serial, text))
        return result

    async def scroll_until_visible(self, driver, serial, selector, direction, max_swipes):
        swipes, misses = 0, 0
        while True:
            try:
                tree = await driver.ui_tree(serial)
            except OpError:  # UI not idle (a running animation): look again, like wait_for
                misses += 1
                if misses > 3:
                    raise
                await asyncio.sleep(POLL_SECONDS)
                continue
            misses = 0
            hits = find_elements(tree['elements'], *selector)
            if hits:
                return {'found': True, 'element': hits[0], 'swipes': swipes}
            if swipes >= max_swipes:
                visible = [e.get('text') or e.get('label') for e in tree['elements'] if e.get('text') or e.get('label')]
                return {'found': False, 'swipes': swipes, 'visible': [v[:60] for v in visible[:40]]}
            width, height = tree.get('screen') or [0, 0]
            if width < 10 or height < 10:
                raise OpError('Could not determine the screen size from the UI tree')
            x, top, bottom = width // 2, height * 30 // 100, height * 70 // 100
            start, end = (bottom, top) if direction == 'down' else (top, bottom)
            await driver.swipe(serial, x, start, x, end, SWIPE_MS)
            swipes += 1
            await asyncio.sleep(0.5)  # let the list settle before looking again

    async def burst(self, driver, serial, count, interval_ms, launch=None):
        """count screenshots at fixed offsets, timed here (not by model turns). Capture calls overlap
        if one takes longer than the interval, so each frame starts on schedule; at_ms is its offset."""
        loop = asyncio.get_running_loop()
        if launch is not None:
            await launch()
        start = loop.time()

        async def shot(index):
            await asyncio.sleep(max(0.0, start + index * interval_ms / 1000 - loop.time()))
            at = int((loop.time() - start) * 1000)
            _, data = await driver.capture(serial)
            return at, data

        frames, total, dropped = [], 0, 0
        for at, data in await asyncio.gather(*(shot(i) for i in range(count))):
            total += len(data)
            if total > MAX_MEDIA_BYTES:
                dropped += 1
                continue
            width, height = png_size(data)
            frames.append({'png_base64': base64.b64encode(data).decode(), 'at_ms': at, 'width': width, 'height': height})
        return {'frames': frames, 'interval_ms': interval_ms, **({'dropped': dropped} if dropped else {}),
                **({'launched': True} if launch is not None else {})}

    # ── Install: update in place, skip identical builds, cache downloads ──

    async def install(self, driver, serial, args, app_id):
        expected = need_str(args, 'sha256', SHA256)
        force = need_bool(args, 'force')
        appops, privacy = args.get('grant_appops') or [], args.get('grant_privacy') or []
        if (not isinstance(appops, list) or not isinstance(privacy, list) or len(appops) > 10 or len(privacy) > 10
                or not all(isinstance(o, str) and APPOP.fullmatch(o) for o in appops)
                or not all(s in IOS_PRIVACY for s in privacy)):
            raise OpError('Invalid grant_appops / grant_privacy')
        if not force:
            for (seen_serial, package), (sha, stamp) in list(self.installed.items()):
                if seen_serial == serial and sha == expected and (not app_id or package == app_id):
                    if stamp is not None and await driver.install_stamp(serial, package) == stamp:
                        result = {'installed': need_str(args, 'filename', FILENAME), 'skipped': True,
                                  'note': 'This exact build is already installed; pass force to reinstall',
                                  'package': package}
                        return await self._grant(driver, serial, package, appops, privacy, result)
        with tempfile.TemporaryDirectory(prefix='loma-build-') as tmp:
            path = await self.download(args, Path(tmp))
            result = await driver.install(serial, path, app_id, self.allowed_apps)
        package = result.get('bundle_id') or app_id
        if package:
            self.installed[(serial, package)] = (expected, await driver.install_stamp(serial, package))
        return await self._grant(driver, serial, package, appops, privacy, result)

    async def _grant(self, driver, serial, package, appops, privacy, result):
        if appops or privacy:
            if not package:
                raise OpError('Granting permissions needs app_id')
            result.update(await driver.grant(serial, package, appops, privacy))
        return result

    def _cached(self, sha256):
        path = self.cache_dir / sha256
        return path if path.is_file() else None

    def _store_in_cache(self, path, sha256):
        """Best effort: keep the last few verified builds, keyed by checksum."""
        try:
            self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            # A unique temp name: two installs of the same build on two devices may cache it at once.
            fd, tmp = tempfile.mkstemp(prefix=sha256[:16] + '.', suffix='.part', dir=self.cache_dir)
            try:
                with os.fdopen(fd, 'wb') as handle, open(path, 'rb') as source:
                    shutil.copyfileobj(source, handle)
                os.replace(tmp, self.cache_dir / sha256)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            entries = sorted((p for p in self.cache_dir.iterdir() if SHA256.fullmatch(p.name)),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            for old in entries[self.CACHE_KEEP:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _copy_verified(source, target, sha256):
        digest = hashlib.sha256()
        try:
            with open(source, 'rb') as reader, open(target, 'wb') as writer:
                for chunk in iter(lambda: reader.read(1 << 20), b''):
                    digest.update(chunk)
                    writer.write(chunk)
        except OSError:
            return False
        return digest.hexdigest() == sha256

    async def download(self, args, target):
        blob_id = need_str(args, 'blob_id', BLOB_ID)
        expected = need_str(args, 'sha256', SHA256)
        name = need_str(args, 'filename', FILENAME)
        path = target / name
        cached = self._cached(expected)
        if cached is not None:
            # Re-verify: the cache is plain files on disk, and a checksum is what install trusts.
            if await asyncio.to_thread(self._copy_verified, cached, path, expected):
                os.utime(cached)  # most recently used
                return path
            cached.unlink(missing_ok=True)  # corrupt or tampered: drop it and download again
        url = f"{self.config['server']}/device-runner/blobs/{blob_id}"
        digest, size = hashlib.sha256(), 0
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
        async with self.session.get(url, headers=self.auth_headers(), timeout=timeout) as response:
            if response.status != 200:
                raise OpError(f'Build download failed (HTTP {response.status})')
            with open(path, 'wb') as handle:
                async for chunk in response.content.iter_chunked(1 << 20):
                    size += len(chunk)
                    if size > MAX_BLOB:
                        raise OpError('Build is larger than the 500 MB limit')
                    digest.update(chunk)
                    handle.write(chunk)
        if digest.hexdigest() != expected:
            raise OpError('Build checksum mismatch; refusing to install')
        await asyncio.to_thread(self._store_in_cache, path, expected)
        return path

    async def run_flow(self, serial, flow):
        screen_flow(flow, self.policy)
        if shutil.which('maestro') is None:
            raise OpError('maestro is not installed on the runner: curl -fsSL "https://get.maestro.mobile.dev" | bash')
        with tempfile.TemporaryDirectory(prefix='loma-flow-') as tmp:
            flow_path, report = Path(tmp) / 'flow.yaml', Path(tmp) / 'report.xml'
            flow_path.write_text(flow)
            code, out, err = await run(['maestro', '--device', serial, 'test', str(flow_path), '--format', 'junit',
                                        '--output', str(report)], timeout=600, check=False, cwd=tmp)
            return {'passed': code == 0, 'exit_code': code,
                    'report': report.read_text()[-20000:] if report.exists() else '',
                    'output': (out.decode('utf-8', 'replace') + err)[-8000:],
                    'screenshots': collect_screenshots(Path(tmp))}

    def auth_headers(self):
        return {'Authorization': 'Bearer ' + self.config['secret'], 'X-Loma-Runner-Id': self.config['runner_id'],
                **self.cf_headers}

    async def send(self, ws, frame):
        async with self.send_lock:
            if ws.closed:
                return
            try:
                await ws.send_str(json.dumps(frame))
            except (ConnectionError, RuntimeError):
                pass  # socket is closing; the reconnect loop takes over

    async def handle_call(self, ws, frame):
        call_id = frame.get('id')
        timeout = frame.get('timeout')
        # Finish (or give up) before the server's deadline, so a call it already timed out
        # never runs later, e.g. a tap queued behind a slow install on the device lock.
        deadline = timeout - 5 if type(timeout) is int and 15 <= timeout <= 3600 else 55
        try:
            data = await asyncio.wait_for(self.call(frame.get('op'), frame.get('device'), frame.get('args') or {}),
                                          deadline)
            reply = {'type': 'result', 'id': call_id, 'ok': True, 'data': data}
        except asyncio.TimeoutError:
            reply = {'type': 'result', 'id': call_id, 'ok': False, 'error': f'Timed out on the runner after {deadline}s'}
        except OpError as exc:
            reply = {'type': 'result', 'id': call_id, 'ok': False, 'error': str(exc)[:2000]}
        except Exception as exc:  # one bad call must never drop the connection
            reply = {'type': 'result', 'id': call_id, 'ok': False, 'error': f'Runner error: {type(exc).__name__}'}
        await self.send(ws, reply)

    async def heartbeat(self, ws):
        while not ws.closed:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                devices = await asyncio.wait_for(self.refresh(), HEARTBEAT_SECONDS * 2)
            except Exception:  # slow or failing scan: still heartbeat with the last inventory
                devices = [device for _, device in self.inventory.values()]
            await self.send(ws, {'type': 'devices', 'devices': devices})

    async def connect_once(self):
        import aiohttp
        url = self.config['server'].replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
        self.cf_headers = await asyncio.to_thread(cf_access_headers, self.config)  # per connect; cloudflared caches it
        self.send_lock = asyncio.Lock()
        devices = await self.refresh()  # before connecting: the server waits only 10 s for hello
        async with self.session.ws_connect(url + '/device-runner/ws', headers=self.auth_headers(),
                                           heartbeat=30, max_msg_size=MAX_WS_MESSAGE) as ws:
            await self.send(ws, {
                'type': 'hello', 'protocol': PROTOCOL, 'version': VERSION, 'hostname': socket.gethostname(),
                'os': f'{platform.system()} {platform.release()}', 'capabilities': self.capabilities(),
                'devices': devices})
            print(f'Connected to {self.config["server"]} with {len(devices)} device(s)', flush=True)
            beat = asyncio.create_task(self.heartbeat(ws))
            # A dead heartbeat would leave a silent socket the server marks offline: reconnect instead.
            beat.add_done_callback(lambda task: task.cancelled() or asyncio.ensure_future(ws.close()))
            tasks = set()
            try:
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        break
                    try:
                        frame = json.loads(message.data)
                    except ValueError:
                        continue
                    if not isinstance(frame, dict):
                        continue
                    if frame.get('type') == 'call':
                        task = asyncio.create_task(self.handle_call(ws, frame))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    elif frame.get('type') == 'revoked':
                        raise RevokedError('Runner was revoked in Loma')
            finally:
                beat.cancel()
                for task in list(tasks):
                    task.cancel()

    async def serve(self):
        import aiohttp
        delay = 1
        loop = asyncio.get_running_loop()
        async with aiohttp.ClientSession() as session:
            self.session = session
            while True:
                started = loop.time()
                try:
                    await self.connect_once()
                except aiohttp.WSServerHandshakeError as exc:
                    if exc.status == 401:  # 403 can come from a proxy/WAF or an expired SSO login: retry
                        print('Loma rejected this runner (revoked or invalid secret). Re-enroll to continue.', flush=True)
                        return 0  # exit 0 so launchd/systemd do not restart-loop
                    print(f'Handshake failed: HTTP {exc.status}'
                          + (f'. {SSO_HINT}' if 300 <= exc.status < 400 else ''), flush=True)
                except RevokedError as exc:
                    print(str(exc), flush=True)
                    return 0
                except CfAccessError as exc:
                    print(str(exc), flush=True)
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                    print(f'Connection lost: {type(exc).__name__}', flush=True)
                # Only a connection that stayed up resets the backoff, so a server
                # that accepts and immediately closes cannot cause a 1/s reconnect loop.
                if loop.time() - started >= 60:
                    delay = 1
                await asyncio.sleep(delay + random.random())
                delay = min(delay * 2, 60)


# ── CLI ───────────────────────────────────────────────────────────────────


VENV = CONFIG_DIR / 'venv'
INSTALLED_SCRIPT = CONFIG_DIR / 'loma_device_runner.py'
REQUIREMENTS = ('aiohttp>=3.9,<4', 'pyyaml>=6,<7')
SERVICE_NAME = 'loma-device-runner'
SSO_HINT = ('Loma redirected this request to a login page (an SSO proxy). Ask your admin to exempt '
            '/device-runner/* from SSO, or for Cloudflare Access install cloudflared, run '
            '`cloudflared access login <server>` and re-run setup.')


class CfAccessError(Exception):
    pass


def extend_path():
    """Add the default adb/maestro install dirs to PATH, so neither the shell nor the
    launchd/systemd service needs PATH edits. Returns the resulting PATH."""
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or str(
        Path.home() / ('Library/Android/sdk' if sys.platform == 'darwin' else 'Android/Sdk'))
    parts = (os.environ.get('PATH') or '/usr/local/bin:/usr/bin:/bin').split(os.pathsep)
    extra = [str(Path(sdk) / 'platform-tools'), str(Path.home() / '.maestro/bin'), str(VENV / 'bin')]
    if sys.platform == 'darwin':  # Homebrew (idb_companion, cloudflared) is missing from launchd's default PATH
        extra += ['/opt/homebrew/bin', '/usr/local/bin']
    os.environ['PATH'] = os.pathsep.join(parts + [d for d in extra if d not in parts])
    return os.environ['PATH']


def behind_cf_access(headers):
    return ('cloudflareaccess.com' in headers.get('Location', '')
            or headers.get('WWW-Authenticate', '').startswith('Cloudflare-Access'))


def cf_access_headers(config, interactive=False):
    """Cloudflare Access login token for SSO-protected servers (config['cf_access'], set by setup).

    interactive=True (setup only) opens `cloudflared access login` once when no token is cached.
    """
    if not config.get('cf_access'):
        return {}
    if shutil.which('cloudflared') is None:
        raise CfAccessError('Loma is behind Cloudflare Access: install cloudflared (brew install cloudflared) and retry')
    for attempt in range(2):
        try:
            proc = subprocess.run(['cloudflared', 'access', 'token', '-app=' + config['server']],
                                  capture_output=True, text=True, timeout=30)
            token = proc.stdout.strip()
            if proc.returncode == 0 and token and ' ' not in token:
                return {'cf-access-token': token}
            if not interactive or attempt:
                break
            print('Log in to Cloudflare Access in the browser window that opens ...', flush=True)
            subprocess.run(['cloudflared', 'access', 'login', config['server']], timeout=300)
        except subprocess.TimeoutExpired:
            break
    raise CfAccessError(f"No Cloudflare Access login. Run: cloudflared access login {config['server']}")


async def enroll(server, token, name, cf_access=False):
    """Redeem a one-time enrollment token and save the runner credentials (keeping any existing policy)."""
    import aiohttp
    try:
        headers = cf_access_headers({'server': server, 'cf_access': cf_access}, interactive=True)
    except CfAccessError as exc:
        raise SystemExit(f'Enrollment failed: {exc}')
    async with aiohttp.ClientSession() as session:
        async with session.post(server + '/device-runner/enroll', headers=headers, json={
                'token': token, 'name': name, 'hostname': socket.gethostname(),
                'os': f'{platform.system()} {platform.release()}', 'version': VERSION},
                allow_redirects=False) as response:
            if 300 <= response.status < 400:
                if not cf_access and behind_cf_access(response.headers):
                    print('Loma is behind Cloudflare Access; using your cloudflared login.', flush=True)
                    return await enroll(server, token, name, cf_access=True)
                raise SystemExit(f'Enrollment failed: {SSO_HINT}')
            try:
                body = await response.json(content_type=None)
            except ValueError:
                raise SystemExit(f'Enrollment failed: HTTP {response.status} (not a Loma response)')
            if response.status != 200 or not isinstance(body, dict):
                error = body.get('error') if isinstance(body, dict) else None
                raise SystemExit(f'Enrollment failed: {error or response.status}')
    previous = load_config() if CONFIG_PATH.exists() else {}
    config = {'server': server, 'runner_id': body['runner_id'], 'secret': body['secret'],
              'name': body.get('name', name), 'cf_access': cf_access,
              'policy': previous.get('policy') or default_policy()}
    save_config(config)
    print(f'Enrolled as {config["runner_id"]} ({config["name"]}).')


def bootstrap(argv):
    """Install this script and its dependencies into a private virtualenv under CONFIG_DIR,
    then re-exec from there. Stdlib only, so it works before aiohttp/PyYAML exist."""
    python = VENV / 'bin' / 'python3'
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if Path(__file__).resolve() != INSTALLED_SCRIPT.resolve():
        shutil.copyfile(Path(__file__).resolve(), INSTALLED_SCRIPT)
    try:
        if not python.exists():
            print(f'Creating a private Python environment in {VENV} ...', flush=True)
            subprocess.run([sys.executable, '-m', 'venv', str(VENV)], check=True)
        print('Installing dependencies (aiohttp, pyyaml) ...', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
                        *REQUIREMENTS], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        shutil.rmtree(VENV, ignore_errors=True)  # never leave a half-built env that the next run would trust
        raise SystemExit(f'Could not set up the Python environment: {exc}\n'
                         'On Debian/Ubuntu install python3-venv first.')
    os.execv(str(python), [str(python), str(INSTALLED_SCRIPT), *argv])


IDB_COMPANION = 'facebook/fb/idb-companion'
TOOL_HINTS = {
    'adb': 'install Android Studio and create an emulator',
    'xcrun': 'install Xcode',
    'idb': 're-run setup (installs it for iOS taps / UI tree)',
    'maestro': 'optional, for scripted flows: curl -fsSL "https://get.maestro.mobile.dev" | bash',
}


def install_ios_tools():
    """Best effort: install idb (iOS taps, typing, UI tree) when Xcode is present.

    The idb client goes into the private virtualenv; idb_companion comes from Meta's Homebrew
    tap, which newer Homebrew only installs once that one formula is trusted. A failure only
    prints a hint, since Android and screenshot-only iOS work without idb.
    """
    if sys.platform != 'darwin' or shutil.which('xcrun') is None:
        return
    steps = []
    if not (VENV / 'bin' / 'idb').exists():
        steps.append([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', 'fb-idb'])
    if shutil.which('idb_companion') is None:
        if shutil.which('brew') is None:
            print('idb      skipped: install Homebrew (https://brew.sh) and re-run setup for iOS taps / UI tree')
            return
        steps += [['brew', 'tap', 'facebook/fb'], ['brew', 'trust', '--formula', IDB_COMPANION],
                  ['brew', 'install', IDB_COMPANION]]
    if steps:
        print('Installing idb for iOS simulator control (a few minutes the first time) ...', flush=True)
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True)
        # `brew trust` only exists on Homebrew versions that enforce tap trust; older ones skip it.
        if result.returncode != 0 and step[:2] != ['brew', 'trust']:
            print(f'idb      could not install ({" ".join(step)}): {result.stderr.strip()[-300:]}\n'
                  '         Android and iOS screenshots still work; fix this and re-run setup for iOS taps.')
            return


def service_file():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    return Path.home() / '.config/systemd/user' / f'{SERVICE_NAME}.service'


def install_service():
    """Write and (re)start the login service that runs `run` from the private environment."""
    foreground = f'{sys.executable} {INSTALLED_SCRIPT} run'
    env = {'PATH': os.environ['PATH'], 'LOMA_DEVICE_RUNNER_HOME': str(CONFIG_DIR)}
    path = service_file()
    if sys.platform == 'darwin':
        log = CONFIG_DIR / 'runner.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps({
            'Label': LABEL, 'ProgramArguments': [sys.executable, str(INSTALLED_SCRIPT), 'run'],
            'EnvironmentVariables': env, 'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
            'StandardOutPath': str(log), 'StandardErrorPath': str(log)}))
        domain = f'gui/{os.getuid()}'
        subprocess.run(['launchctl', 'bootout', f'{domain}/{LABEL}'], capture_output=True)  # fine if not loaded
        commands, logs = [['launchctl', 'bootstrap', domain, str(path)]], f'Logs: {log}'
    elif sys.platform.startswith('linux') and shutil.which('systemctl'):
        path.parent.mkdir(parents=True, exist_ok=True)
        environment = ' '.join(f'"{key}={value}"' for key, value in env.items())
        path.write_text(f'[Unit]\nDescription=Loma Device Runner\nAfter=network-online.target\n\n'
                        f'[Service]\nEnvironment={environment}\nExecStart={foreground}\n'
                        f'Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n')
        commands = [['systemctl', '--user', 'daemon-reload'], ['systemctl', '--user', 'enable', SERVICE_NAME],
                    ['systemctl', '--user', 'restart', SERVICE_NAME]]
        logs = f'Logs: journalctl --user -u {SERVICE_NAME} -f'
    else:
        print(f'No launchd/systemd here. Keep this running in a terminal: {foreground}')
        return
    for command in commands:
        for attempt in range(3):  # launchd can briefly refuse a bootstrap right after bootout
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise SystemExit(f'Could not start the service ({" ".join(command)}): {result.stderr.strip()}\n'
                             f'Run it in the foreground instead: {foreground}')
    print(f'The runner is running in the background and starts again at login. {logs}')


def uninstall():
    if sys.platform == 'darwin':
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)
    elif shutil.which('systemctl'):
        subprocess.run(['systemctl', '--user', 'disable', '--now', SERVICE_NAME], capture_output=True)
    service_file().unlink(missing_ok=True)
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)
    print('Removed the runner from this machine. Revoke it under Loma → Integrations → Devices if it is still listed.')


async def doctor():
    for tool, hint in TOOL_HINTS.items():
        where = shutil.which(tool)
        print(f'{tool:8} {"found at " + where if where else "not found: " + hint}')
    config = load_config() if CONFIG_PATH.exists() else {}
    devices = await Runner({'policy': config.get('policy', {})}).refresh()
    print(f'{len(devices)} usable device(s) (physical devices are hidden unless allow_physical_devices=true):')
    for device in devices:
        print(f"  {device['platform']:8} {device['serial']:40} {device['name']} {device['os_version']}")
    if not devices:
        print('  Boot an Android emulator or iOS simulator; it shows up in Loma within ~15 seconds.')
    print(f"Enrolled as {config['runner_id']} against {config['server']}" if config else 'Not enrolled yet.')


def run_forever():
    """Serve until revoked. One runner per config: two processes sharing a runner identity
    would keep replacing each other's connection on the server."""
    import fcntl
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(CONFIG_DIR / 'runner.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit('Another runner is already running on this machine (the login service or a terminal).')
        return asyncio.run(Runner(load_config()).serve())


def setup(args, argv):
    server = normalize_server(args.server, args.allow_http) if args.server else None
    if args.token and not server:
        raise SystemExit('--server is required with --token')
    if not args.token and not CONFIG_PATH.exists():
        raise SystemExit('Not enrolled yet. Copy the setup command from Loma → Integrations → Devices.')
    bootstrap(argv)  # returns only when running from the private environment
    if args.token:
        asyncio.run(enroll(server, args.token.strip(), args.name[:80]))
    if not args.skip_ios_tools:
        install_ios_tools()
    asyncio.run(doctor())
    if args.foreground:
        return run_forever()
    return install_service()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(description='Loma Device Runner ' + VERSION)
    sub = parser.add_subparsers(dest='command', required=True)
    p_setup = sub.add_parser('setup', help='Install, enroll and start the runner (re-run without --token to upgrade)')
    p_setup.add_argument('--server', help='Your Loma URL')
    p_setup.add_argument('--token', help='One-time token from Loma → Integrations → Devices')
    p_setup.add_argument('--name', default=socket.gethostname())
    p_setup.add_argument('--allow-http', action='store_true', help='Allow plain http (testing only)')
    p_setup.add_argument('--foreground', action='store_true', help='Run in this terminal instead of as a service')
    p_setup.add_argument('--skip-ios-tools', action='store_true', help='Do not install idb on macOS')
    sub.add_parser('run', help='Connect to Loma and serve device requests (what the service runs)')
    sub.add_parser('doctor', help='Check tooling and list usable devices')
    sub.add_parser('uninstall', help='Stop the service and delete ' + str(CONFIG_DIR))
    args = parser.parse_args(argv)
    extend_path()
    if args.command == 'setup':
        return setup(args, argv)
    if args.command == 'run':
        return run_forever()
    if args.command == 'doctor':
        return asyncio.run(doctor())
    return uninstall()


if __name__ == '__main__':
    sys.exit(main())
