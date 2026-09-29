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
                'volume_up': 24, 'volume_down': 25, 'power': 26}
IOS_BUTTONS = {'home': 'HOME', 'lock': 'LOCK', 'siri': 'SIRI', 'side': 'SIDE_BUTTON', 'apple_pay': 'APPLE_PAY'}
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
        if app_id:
            # Debug keys differ between CI runs: always start from a clean install.
            await run([self.adb, '-s', serial, 'uninstall', app_id], timeout=60, check=False)
        # With an app allowlist, never replace an existing package (no -r) and verify
        # the package that actually got installed, not just the app_id we were told.
        before = await self.packages(serial) if allowed else set()
        flags = ['-t', '-g'] if allowed else ['-r', '-t', '-g']
        _, out, err = await run([self.adb, '-s', serial, 'install', *flags, str(apk)], timeout=300)
        if allowed:
            added = await self.packages(serial) - before
            if not added or not added <= set(allowed):
                for package in added:
                    await run([self.adb, '-s', serial, 'uninstall', package], timeout=60, check=False)
                raise OpError(f'Installed package {sorted(added) or "unknown"} is not in allowed_app_ids; removed it')
        return {'installed': apk.name, 'output': (out.decode('utf-8', 'replace') + err).strip()[-500:]}

    async def uninstall(self, serial, app_id):
        await run([self.adb, '-s', serial, 'uninstall', app_id], timeout=60)
        return {'uninstalled': app_id}

    async def launch(self, serial, app_id):
        await run(self._sh(serial, 'monkey', '-p', app_id, '-c', 'android.intent.category.LAUNCHER', '1'), timeout=30)
        return {'launched': app_id}

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
        path = '/sdcard/loma_ui.xml'
        await run(self._sh(serial, 'rm', '-f', path), timeout=15, check=False)
        _, out, err = await run(self._sh(serial, 'uiautomator', 'dump', '--compressed', path), timeout=30)
        if b'dumped to' not in out and 'dumped to' not in err:
            raise OpError('uiautomator could not capture the screen (UI not idle?); retry: '
                          + (out.decode('utf-8', 'replace') + err).strip()[-300:])
        _, out, _ = await run([self.adb, '-s', serial, 'exec-out', 'cat', path], timeout=30)
        return {'units': 'pixels', 'elements': parse_uiautomator(out)}

    async def tap(self, serial, x, y):
        await run(self._sh(serial, 'input', 'tap', str(x), str(y)), timeout=15)
        return {'tapped': [x, y]}

    async def swipe(self, serial, x1, y1, x2, y2, duration_ms):
        await run(self._sh(serial, 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(duration_ms)), timeout=30)
        return {'swiped': [x1, y1, x2, y2]}

    async def type_text(self, serial, text):
        if not all(32 <= ord(c) < 127 for c in text):
            raise OpError('Android text input supports printable ASCII only')
        await run(self._sh(serial, 'input', 'text', shlex.quote(text.replace(' ', '%s'))), timeout=30)
        return {'typed': len(text)}

    async def key(self, serial, key):
        if key not in ANDROID_KEYS:
            raise OpError('Unsupported key on Android: ' + ', '.join(sorted(ANDROID_KEYS)))
        await run(self._sh(serial, 'input', 'keyevent', str(ANDROID_KEYS[key])), timeout=15)
        return {'key': key}

    async def logs(self, serial, lines, clear):
        if clear:
            await run([self.adb, '-s', serial, 'logcat', '-c'], timeout=15)
            return []
        _, out, _ = await run([self.adb, '-s', serial, 'logcat', '-d', '-v', 'time', '-t', str(lines)],
                              timeout=30, keep='tail')
        return out.decode('utf-8', 'replace').splitlines()


def parse_uiautomator(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise OpError('Could not parse the Android UI hierarchy') from None
    elements = []
    for node in root.iter('node'):
        text, rid, desc = node.get('text', ''), node.get('resource-id', ''), node.get('content-desc', '')
        clickable = node.get('clickable') == 'true'
        if not (text or desc or rid or clickable):
            continue
        match = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', node.get('bounds', ''))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        element = {'type': node.get('class', '').rsplit('.', 1)[-1], 'text': text[:200],
                   'id': rid, 'label': desc[:200], 'clickable': clickable,
                   'bounds': [x1, y1, x2, y2], 'center': [(x1 + x2) // 2, (y1 + y2) // 2]}
        elements.append({k: v for k, v in element.items() if v not in ('', False)})
        if len(elements) >= MAX_UI_ELEMENTS:
            break
    return elements


# ── iOS simulators ────────────────────────────────────────────────────────


class IOS:
    platform = 'ios'

    def __init__(self):
        self.log_start = {}  # serial -> local time of the last logs clear (the unified log cannot be cleared)

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
            raise OpError('iOS UI control needs idb on the runner: brew install idb-companion && pip install fb-idb')
        return 'idb'

    async def install(self, serial, path, app_id, allowed=()):
        app, bundle_id = await asyncio.to_thread(prepare_app_bundle, path)
        if allowed and bundle_id not in allowed:
            raise OpError(f'Bundle {bundle_id} is not in allowed_app_ids; not installed')
        if app_id:
            await run(['xcrun', 'simctl', 'uninstall', serial, app_id], timeout=60, check=False)
        await run(['xcrun', 'simctl', 'install', serial, str(app)], timeout=300)
        return {'installed': app.name, 'bundle_id': bundle_id}

    async def uninstall(self, serial, app_id):
        await run(['xcrun', 'simctl', 'uninstall', serial, app_id], timeout=60)
        return {'uninstalled': app_id}

    async def launch(self, serial, app_id):
        await run(['xcrun', 'simctl', 'launch', serial, app_id], timeout=60)
        return {'launched': app_id}

    async def stop(self, serial, app_id):
        await run(['xcrun', 'simctl', 'terminate', serial, app_id], timeout=30, check=False)
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

    async def ui_tree(self, serial):
        _, out, _ = await run([self._idb(), 'ui', 'describe-all', '--udid', serial, '--json'], timeout=30)
        try:
            raw = json.loads(out)
        except ValueError:
            raw = [json.loads(line) for line in out.decode().splitlines() if line.strip().startswith('{')]
        elements = []
        for node in raw if isinstance(raw, list) else []:
            frame = node.get('frame') or {}
            x, y, w, h = (float(frame.get(k, 0)) for k in ('x', 'y', 'width', 'height'))
            element = {'type': node.get('type', ''), 'text': str(node.get('AXValue') or '')[:200],
                       'label': str(node.get('AXLabel') or '')[:200], 'id': node.get('AXUniqueId') or '',
                       'bounds': [round(x), round(y), round(x + w), round(y + h)],
                       'center': [round(x + w / 2), round(y + h / 2)]}
            elements.append({k: v for k, v in element.items() if v not in ('', None)})
            if len(elements) >= MAX_UI_ELEMENTS:
                break
        return {'units': 'points', 'elements': elements}

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

    async def key(self, serial, key):
        if key not in IOS_BUTTONS:
            raise OpError('Unsupported key on iOS: ' + ', '.join(sorted(IOS_BUTTONS)))
        await run([self._idb(), 'ui', 'button', '--udid', serial, IOS_BUTTONS[key]], timeout=15)
        return {'key': key}

    async def logs(self, serial, lines, clear):
        if clear:
            self.log_start[serial] = time.strftime('%Y-%m-%d %H:%M:%S')
            return []
        window = ['--start', self.log_start[serial]] if serial in self.log_start else ['--last', '2m']
        _, out, _ = await run(['xcrun', 'simctl', 'spawn', serial, 'log', 'show', *window,
                               '--style', 'compact'], timeout=60, keep='tail')
        return out.decode('utf-8', 'replace').splitlines()[-lines:]


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
           'ui_tree', 'tap', 'swipe', 'type', 'key', 'logs', 'run_flow'}
    APP_OPS = {'install', 'uninstall', 'launch', 'stop', 'reset_app'}

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
        if op in self.APP_OPS:
            app_id = need_str(args, 'app_id', APP_ID, 255, optional=op == 'install')
            if self.allowed_apps and app_id not in self.allowed_apps:
                raise OpError(f"App {app_id} is not in this runner's allowed_app_ids (install needs app_id)")
        lock = self.locks.setdefault(serial, asyncio.Lock())
        async with lock:
            return await self._dispatch(driver, op, serial, args, app_id)

    async def _dispatch(self, driver, op, serial, args, app_id):
        if op == 'install':
            with tempfile.TemporaryDirectory(prefix='loma-build-') as tmp:
                path = await self.download(args, Path(tmp))
                return await driver.install(serial, path, app_id, self.allowed_apps)
        if op in ('uninstall', 'launch', 'stop', 'reset_app'):
            return await getattr(driver, op)(serial, app_id)
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
            output = await driver.logs(serial, 5000 if needle else lines, clear)
            if needle:
                output = [line for line in output if needle.lower() in line.lower()]
            return {'lines': [line[:2000] for line in output[-lines:]], 'cleared': clear}
        if op == 'run_flow':
            return await self.run_flow(serial, need_str(args, 'flow', max_len=MAX_FLOW))
        raise OpError('Unsupported operation')

    async def download(self, args, target):
        blob_id = need_str(args, 'blob_id', BLOB_ID)
        expected = need_str(args, 'sha256', SHA256)
        name = need_str(args, 'filename', FILENAME)
        url = f"{self.config['server']}/device-runner/blobs/{blob_id}"
        digest, size = hashlib.sha256(), 0
        path = target / name
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
                    'output': (out.decode('utf-8', 'replace') + err)[-8000:]}

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
            '/device-runner/* from SSO (see docs/devices.md).')


class CfAccessError(Exception):
    pass


def extend_path():
    """Add the default adb/maestro install dirs to PATH, so neither the shell nor the
    launchd/systemd service needs PATH edits. Returns the resulting PATH."""
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or str(
        Path.home() / ('Library/Android/sdk' if sys.platform == 'darwin' else 'Android/Sdk'))
    parts = (os.environ.get('PATH') or '/usr/local/bin:/usr/bin:/bin').split(os.pathsep)
    extra = [str(Path(sdk) / 'platform-tools'), str(Path.home() / '.maestro/bin')]
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
    for tool in ('adb', 'xcrun', 'idb', 'maestro'):
        where = shutil.which(tool)
        print(f'{tool:8} {"found at " + where if where else "not found"}')
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
