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

VERSION = '1.0.0'
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
ANDROID_WAKEUP = 'input keyevent 224; wm dismiss-keyguard'
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
    return {'allow_physical_devices': False, 'allowed_app_ids': [], 'allow_maestro_scripts': False}


# ── Subprocess helper ─────────────────────────────────────────────────────


async def run(args, *, timeout=60, check=True, keep='head', cwd=None):
    """Run a fixed argv (never a host shell string). Returns (code, stdout bytes, stderr text).

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
    out = out[-MAX_OUTPUT:] if keep == 'tail' else out[:MAX_OUTPUT]
    err_text = err.decode('utf-8', 'replace')[-4000:]
    if check and proc.returncode != 0:
        detail = (err_text or out.decode('utf-8', 'replace')[-2000:]).strip()
        raise OpError(f'{Path(args[0]).name} failed (exit {proc.returncode}): {detail[:1500]}')
    return proc.returncode, out, err_text


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
            package = app_id or (found.group(1) if found else None)
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

    async def launch(self, serial, app_id, extras=None, bool_extras=None, activity=None, console=False):
        extras, bool_extras = extras or {}, bool_extras or {}
        if not (extras or bool_extras or activity):
            await run(self._sh(serial, 'monkey', '-p', app_id, '-c', 'android.intent.category.LAUNCHER', '1'),
                      timeout=30)
            return {'launched': app_id}
        component = f'{app_id}/{activity or await self._launcher_activity(serial, app_id)}'
        # adb joins shell args into one device-side shell string: every value is quoted as one word.
        argv = ['am', 'start', '-W', '-S', '-n', shlex.quote(component)]
        for key, value in extras.items():
            argv += ['--es', key, shlex.quote(value)]
        for key, value in bool_extras.items():
            argv += ['--ez', key, 'true' if value else 'false']
        _, out, _ = await run(self._sh(serial, *argv), timeout=45)
        text = out.decode('utf-8', 'replace')
        if 'Error' in text:
            raise OpError(text.strip()[-500:])
        return {'launched': app_id, 'component': component, 'extras': sorted(extras) + sorted(bool_extras)}

    async def _launcher_activity(self, serial, app_id):
        _, out, _ = await run(self._sh(serial, 'cmd', 'package', 'resolve-activity', '--brief',
                                       '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER',
                                       app_id), timeout=30, check=False)
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
        if 'Error' in text:
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

    async def capture(self, serial):
        return 'png', await self.screenshot(serial)

    async def record(self, serial, seconds, started=None):
        path = '/sdcard/loma_rec.mp4'
        task = asyncio.ensure_future(run(self._sh(serial, 'screenrecord', '--time-limit', str(seconds),
                                                  '--bit-rate', '4000000', path), timeout=seconds + 30))
        await asyncio.sleep(0.8)  # screenrecord needs a moment before frames flow
        if started is not None:
            await started()
        await task
        _, out, _ = await run([self.adb, '-s', serial, 'exec-out', 'cat', path], timeout=60)
        await run(self._sh(serial, 'rm', '-f', path), timeout=15, check=False)
        return out

    async def logs(self, serial, lines, clear, source='auto'):
        if clear:
            await run([self.adb, '-s', serial, 'logcat', '-c'], timeout=15)
            return []
        _, out, _ = await run([self.adb, '-s', serial, 'logcat', '-d', '-v', 'time', '-t', str(lines)],
                              timeout=30, keep='tail')
        return out.decode('utf-8', 'replace').splitlines()


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

    async def launch(self, serial, app_id, extras=None, bool_extras=None, activity=None, console=False):
        args = self.launch_arguments(extras, bool_extras)
        await self._stop_console(serial)
        restart = ['--terminate-running-process'] if args or console else []
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

    async def record(self, serial, seconds, started=None):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'rec.mp4'
            proc = await asyncio.create_subprocess_exec(
                'xcrun', 'simctl', 'io', serial, 'recordVideo', '--codec=h264', '--force', str(target),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                await asyncio.sleep(0.8)
                if started is not None:
                    await started()
                await asyncio.sleep(seconds)
                proc.send_signal(2)  # SIGINT finalises the movie file
                await asyncio.wait_for(proc.wait(), 20)
            except BaseException:
                if proc.returncode is None:
                    proc.kill()
                raise
            if not target.exists():
                raise OpError('simctl recordVideo produced no file')
            return target.read_bytes()

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
           'set_text', 'clear_text', 'wait_for', 'tap_text', 'scroll_until_visible', 'burst', 'record'}
    APP_OPS = {'install', 'uninstall', 'launch', 'stop', 'reset_app'}
    LAUNCHING_OPS = {'burst', 'record'}  # may launch app_id right before capturing
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
            lines = need_int(args, 'lines', 1, 2000, default=300)
            clear = args.get('clear', False)
            if type(clear) is not bool:
                raise OpError('Invalid clear')
            needle = need_str(args, 'filter', max_len=200, optional=True)
            source = args.get('source', 'auto')
            if source not in ('auto', 'system', 'console'):
                raise OpError('Invalid source (auto, system or console)')
            output = await driver.logs(serial, 5000 if needle else lines, clear, source)
            if needle:
                output = [line for line in output if needle.lower() in line.lower()]
            return {'lines': [line[:2000] for line in output[-lines:]], 'cleared': clear}
        if op == 'run_flow':
            return await self.run_flow(serial, need_str(args, 'flow', max_len=MAX_FLOW))
        raise OpError('Unsupported operation')

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
        swipes = 0
        while True:
            tree = await driver.ui_tree(serial)
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
            tmp = self.cache_dir / (sha256 + '.part')
            shutil.copyfile(path, tmp)
            os.replace(tmp, self.cache_dir / sha256)
            entries = sorted((p for p in self.cache_dir.iterdir() if SHA256.fullmatch(p.name)),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            for old in entries[self.CACHE_KEEP:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    async def download(self, args, target):
        blob_id = need_str(args, 'blob_id', BLOB_ID)
        expected = need_str(args, 'sha256', SHA256)
        name = need_str(args, 'filename', FILENAME)
        path = target / name
        cached = self._cached(expected)
        if cached is not None:
            await asyncio.to_thread(shutil.copyfile, cached, path)
            os.utime(cached)  # most recently used
            return path
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
