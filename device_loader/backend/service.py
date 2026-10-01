"""The single policy layer for device access.

Every caller (isolated tool gateway, legacy CLI via /internal, dashboard) goes
through DeviceService, so ACLs, leases, argument validation and audit are
enforced once. Arguments are validated here before they reach a runner, and
the runner validates them again.
"""
import asyncio
import base64
import re
import time
import xml.etree.ElementTree as ET
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from device_loader.backend import store
from device_loader.backend.builds import GITHUB_FETCH_TIMEOUT, blobs as default_blobs
from device_loader.backend.hub import DEFAULT_TIMEOUT, OP_TIMEOUTS, DeviceError, hub as default_hub
from device_loader.backend.verify import plotline_activity, summarize_network, visual_check as judge_screenshot

LEASE_TTL = timedelta(minutes=15)
HOLD_TTL = timedelta(minutes=10)  # a dashboard take-over lapses this long after the person's last input
TAKEOVER = 'takeover:'
TAKEOVER_OPS = {'tap', 'swipe', 'key', 'type', 'open_url'}
SCREEN_MIN_INTERVAL = 0.7  # seconds between live-view frames per (user, device)
_SCREENS = {}  # device_id -> {'pixels': (w, h), 'points': (w, h)} for live-view coordinate mapping
_LAST_FRAME = {}
MAX_WAIT_ONLINE = 600
WAIT_ONLINE_POLL = 2
OFFLINE_NOTIFY_EVERY = timedelta(minutes=30)
APP_ID = re.compile(r'[A-Za-z0-9_]+(?:[.-][A-Za-z0-9_]+)*\Z')
KEYS = {'back', 'home', 'enter', 'delete', 'tab', 'app_switch', 'volume_up', 'volume_down', 'power',
        'lock', 'siri', 'side', 'apple_pay', 'escape', 'wakeup'}
SCOPE = re.compile(r'[A-Za-z0-9:_.@-]{1,200}\Z')
REPO = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z')
ARTIFACT_NAME = re.compile(r'[A-Za-z0-9._ -]{1,200}\Z')
EXTRA_KEY = re.compile(r'[A-Za-z0-9_.]{1,100}\Z')
ACTIVITY = re.compile(r'[A-Za-z0-9_.$]{1,255}\Z')
APPOP = re.compile(r'[A-Z][A-Z0-9_]{2,63}\Z')
WORKFLOW = re.compile(r'[A-Za-z0-9_.-]{1,100}\.ya?ml\Z')
IOS_PRIVACY = {'all', 'calendar', 'contacts-limited', 'contacts', 'location', 'location-always', 'photos-add',
               'photos', 'media-library', 'microphone', 'motion', 'reminders', 'siri'}
MAX_EXTRAS = 20
MAX_WAIT = 1200

SELECTOR = {'by', 'exact'}
LAUNCH = {'extras', 'bool_extras', 'activity'}
# op: (required, optional). Values are type-checked by _validate.
OPS = {
    'install': (set(), {'app_id', 'build', 'upload_id', 'grant_appops', 'grant_privacy', 'force',
                        'wait_s', 'dispatch_workflow'}),
    'uninstall': ({'app_id'}, set()),
    'launch': ({'app_id'}, LAUNCH | {'console'}),
    'stop': ({'app_id'}, set()),
    'reset_app': ({'app_id'}, set()),
    'open_url': ({'url'}, set()),
    'screenshot': (set(), set()),
    'ui_tree': (set(), {'compact', 'clickable_only', 'filter'}),
    'tap': (set(), {'x', 'y', 'ref'}),
    'swipe': ({'x1', 'y1', 'x2', 'y2'}, {'duration_ms'}),
    'type': ({'text'}, set()),
    'key': ({'key'}, set()),
    'logs': (set(), {'lines', 'filter', 'clear', 'source'}),
    'run_flow': ({'flow'}, {'verbose'}),
    'set_text': ({'text'}, {'match', 'clear', 'ref'} | SELECTOR),
    'clear_text': (set(), {'match', 'ref'} | SELECTOR),
    'wait_for': ({'match'}, {'timeout_s', 'gone'} | SELECTOR),
    'tap_text': ({'match'}, {'timeout_s'} | SELECTOR),
    'scroll_until_visible': ({'match'}, {'direction', 'max_swipes'} | SELECTOR),
    'burst': ({'count'}, {'interval_ms', 'app_id'} | LAUNCH),
    'record': ({'duration_s'}, {'app_id'} | LAUNCH),
    'animations': ({'enabled'}, set()),
    # Device settings for a test (locale, time, location, appearance, permissions). reset=true restores
    # everything this runner changed on the device; release does that automatically.
    'configure': (set(), {'locale', 'timezone', 'clock_offset_s', 'location', 'dark_mode', 'font_scale',
                          'app_id', 'grant', 'revoke', 'reset'}),
    # Android network capture through a loopback mitmproxy on the runner machine.
    'netcap': ({'action'}, {'filter', 'limit'}),
}
CONFIGURE_SETTINGS = {'locale', 'timezone', 'clock_offset_s', 'location', 'dark_mode', 'font_scale', 'grant', 'revoke'}
LOCALE = re.compile(r'[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8}){0,2}\Z')
TIMEZONE = re.compile(r'[A-Za-z][A-Za-z0-9_+-]{0,30}(?:/[A-Za-z0-9_+-]{1,30}){0,2}\Z')
PERMISSION = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{1,99}\Z')
MAX_CLOCK_OFFSET = 400 * 24 * 3600
# Arguments the backend consumes itself; never forwarded to the runner.
BACKEND_ARGS = {'ui_tree': {'compact', 'clickable_only', 'filter'}, 'run_flow': {'verbose'},
                'install': {'wait_s', 'dispatch_workflow'}}
INTS = {'x': (0, 10000), 'y': (0, 10000), 'x1': (0, 10000), 'y1': (0, 10000), 'x2': (0, 10000),
        'y2': (0, 10000), 'duration_ms': (50, 5000), 'lines': (1, 2000), 'timeout_s': (0, 60),
        'max_swipes': (1, 20), 'count': (2, 12), 'interval_ms': (100, 5000), 'duration_s': (1, 20),
        'wait_s': (0, MAX_WAIT), 'clock_offset_s': (-MAX_CLOCK_OFFSET, MAX_CLOCK_OFFSET), 'limit': (1, 200)}
BOOLS = {'clear', 'exact', 'gone', 'console', 'compact', 'clickable_only', 'force', 'verbose', 'enabled',
         'dark_mode', 'reset'}
ENUMS = {'by': {'any', 'text', 'id', 'label'}, 'direction': {'down', 'up'}, 'source': {'auto', 'system', 'console'},
         'action': {'start', 'stop', 'read'}}
STRS = {'url': 2000, 'text': 500, 'filter': 200, 'flow': 64 * 1024, 'key': 32, 'app_id': 255, 'upload_id': 64,
        'match': 200, 'activity': 255, 'dispatch_workflow': 100, 'ref': 8, 'locale': 35, 'timezone': 64}
REF = re.compile(r'e[1-9][0-9]{0,3}\Z')
# Element refs (e1, e2, ...) from the latest ui_tree, per (user, scope, device). Module level:
# the HTTP routes build a new DeviceService per request. The model taps by ref instead of copying
# coordinates (fewer tokens, no mis-typed coordinates); the backend resolves the ref to the centre.
REF_TTL = 120
REF_KEYS_MAX = 500
_REFS = {}
# Ops after which the screen may be different, so earlier refs must not be reused. Cleared before
# the op is sent, so a failed or timed-out attempt (e.g. a half-done install) also invalidates them.
REF_RESET_OPS = {'install', 'uninstall', 'launch', 'stop', 'reset_app', 'open_url', 'tap', 'tap_text', 'swipe',
                 'type', 'key', 'set_text', 'clear_text', 'scroll_until_visible', 'run_flow', 'record', 'burst'}

# Runner features newer than 1.0.0: an older runner rejects the op or silently ignores the argument,
# so the backend refuses them up front with an upgrade hint (the runner reports VERSION in its hello).
NEEDS_RUNNER = (1, 1, 0)
NEW_RUNNER_OPS = {'set_text', 'clear_text', 'wait_for', 'tap_text', 'scroll_until_visible', 'burst', 'record',
                  'animations'}
NEW_RUNNER_ARGS = {'launch': {'extras', 'bool_extras', 'activity', 'console'},
                   'install': {'grant_appops', 'grant_privacy', 'force'}, 'logs': {'source'}}
# Minimum runner version per op; ops not listed work on every runner.
OP_MIN_RUNNER = {**{op: NEEDS_RUNNER for op in NEW_RUNNER_OPS}, 'configure': (1, 2, 0), 'boot': (1, 2, 0),
                 'shutdown': (1, 2, 0), 'netcap': (1, 2, 0)}
TEMPLATE = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')


def _version(text):
    return tuple(int(part) for part in re.findall(r'\d+', str(text or ''))[:3])


def runner_supports(conn, op):
    version = getattr(conn, 'version', None)
    return version is None or _version(version) >= OP_MIN_RUNNER.get(op, ())


def _check_runner_version(conn, op, args):
    """args are the runner-bound arguments (backend-only ones already removed)."""
    version = getattr(conn, 'version', None)
    if version is None:
        return
    newer = sorted(NEW_RUNNER_ARGS.get(op, set()) & set(args))
    need = max(OP_MIN_RUNNER.get(op, ()), NEEDS_RUNNER if newer else ())
    if _version(version) >= need:
        return
    what = op + (' with ' + ', '.join(newer) if newer else '')
    raise DeviceError(f'Runner too old for {what} (runner {version or "unknown"}); update the Loma Device Runner '
                      f'to >= {".".join(map(str, need))}. Runners from 1.2.0 update themselves; older ones need '
                      'the new loma_device_runner.py from Integrations > Devices and '
                      '`python3 loma_device_runner.py setup` on that machine')


def _validate(op, args):
    if op not in OPS:
        raise DeviceError('Unsupported device operation')
    if not isinstance(args, dict):
        raise DeviceError('Invalid arguments')
    required, optional = OPS[op]
    if not required <= set(args) <= required | optional:
        missing, extra = required - set(args), set(args) - required - optional
        raise DeviceError(f'Invalid arguments for {op}' + (f'; missing {sorted(missing)}' if missing else '')
                          + (f'; unexpected {sorted(extra)}' if extra else ''))
    for name, value in args.items():
        if name in INTS:
            low, high = INTS[name]
            if type(value) is not int or not low <= value <= high:
                raise DeviceError(f'{name} must be an integer between {low} and {high}')
        elif name in BOOLS:
            if type(value) is not bool:
                raise DeviceError(f'{name} must be true or false')
        elif name in ENUMS:
            if value not in ENUMS[name]:
                raise DeviceError(f'{name} must be one of ' + ', '.join(sorted(ENUMS[name])))
        elif name == 'build':
            _validate_build(value)
        elif name in ('extras', 'bool_extras'):
            _validate_extras(name, value)
        elif name == 'location':
            if (not isinstance(value, dict) or set(value) != {'lat', 'lon'}
                    or not all(type(value[k]) in (int, float) for k in ('lat', 'lon'))
                    or not -90 <= value['lat'] <= 90 or not -180 <= value['lon'] <= 180):
                raise DeviceError('location must be {lat, lon} in degrees')
        elif name == 'font_scale':
            if type(value) not in (int, float) or not 0.85 <= value <= 2.0:
                raise DeviceError('font_scale must be a number between 0.85 and 2.0')
        elif name in ('grant', 'revoke'):
            if (not isinstance(value, list) or not 1 <= len(value) <= 10
                    or not all(isinstance(v, str) and PERMISSION.fullmatch(v) for v in value)):
                raise DeviceError(f'{name} must be a list of permission names (Android: CAMERA or '
                                  f'android.permission.CAMERA; iOS: ' + ', '.join(sorted(IOS_PRIVACY)) + ')')
        elif name in ('grant_appops', 'grant_privacy'):
            if (not isinstance(value, list) or not 1 <= len(value) <= 10
                    or not all(isinstance(v, str) and (APPOP.fullmatch(v) if name == 'grant_appops'
                                                       else v in IOS_PRIVACY) for v in value)):
                raise DeviceError(f'Invalid {name}' + (' (app-op names like SCHEDULE_EXACT_ALARM)'
                                                       if name == 'grant_appops' else
                                                       ': ' + ', '.join(sorted(IOS_PRIVACY))))
        elif name in STRS:
            if not isinstance(value, str) or not value or len(value) > STRS[name] or '\x00' in value:
                raise DeviceError(f'Invalid {name}')
    if 'locale' in args and not LOCALE.fullmatch(args['locale']):
        raise DeviceError('Invalid locale (e.g. ar, ar-SA, en_IN)')
    if 'timezone' in args and not TIMEZONE.fullmatch(args['timezone']):
        raise DeviceError('Invalid timezone (an IANA name, e.g. Asia/Kolkata)')
    if op == 'configure':
        settings = CONFIGURE_SETTINGS & set(args)
        if args.get('reset') and (settings or 'app_id' in args):
            raise DeviceError('reset restores the device on its own; call configure again to change settings')
        if not args.get('reset') and not settings:
            raise DeviceError('configure needs at least one of ' + ', '.join(sorted(CONFIGURE_SETTINGS))
                              + ', or reset=true')
        if ({'grant', 'revoke'} & settings or 'locale' in settings) and 'app_id' not in args:
            raise DeviceError('grant / revoke / locale need app_id (locale is set per app)')
    if 'app_id' in args and not APP_ID.fullmatch(args['app_id']):
        raise DeviceError('Invalid app_id (expected a package name / bundle id)')
    if 'activity' in args and not ACTIVITY.fullmatch(args['activity']):
        raise DeviceError('Invalid activity (e.g. .MainActivity or com.example.MainActivity)')
    if len(args.get('extras') or {}) + len(args.get('bool_extras') or {}) > MAX_EXTRAS:
        raise DeviceError(f'At most {MAX_EXTRAS} extras')
    if op in ('burst', 'record') and LAUNCH & set(args) and 'app_id' not in args:
        raise DeviceError('extras / activity need app_id (the app to launch when capture starts)')
    if op == 'install':
        if 'grant_appops' in args and 'app_id' not in args:
            raise DeviceError('grant_appops needs app_id')
        if ('wait_s' in args or 'dispatch_workflow' in args) and 'build' not in args:
            raise DeviceError('wait_s / dispatch_workflow only apply to CI builds (build)')
        if 'dispatch_workflow' in args:
            if not WORKFLOW.fullmatch(args['dispatch_workflow']) or 'pr' not in args.get('build', {}):
                raise DeviceError('dispatch_workflow must be a workflow file name (e.g. build.yml) and needs build.pr')
    if 'ref' in args and not REF.fullmatch(args['ref']):
        raise DeviceError('Invalid ref (e.g. e3, from ui_tree)')
    if op == 'tap' and ('ref' in args) == ('x' in args or 'y' in args):
        raise DeviceError('tap needs either ref, or x and y')
    if op == 'tap' and 'ref' not in args and not {'x', 'y'} <= set(args):
        raise DeviceError('tap needs both x and y')
    if op in ('set_text', 'clear_text') and 'ref' in args and 'match' in args:
        raise DeviceError('Use either ref or match, not both')
    if op == 'key' and args['key'] not in KEYS:
        raise DeviceError('Unsupported key: ' + ', '.join(sorted(KEYS)))
    if op == 'open_url':
        url = args['url']
        if any(c.isspace() for c in url) or any(c in url for c in '"\'`\\') or not re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', url):
            raise DeviceError('Invalid url (needs a scheme, no spaces or quotes)')
    if op == 'install' and ('build' in args) == ('upload_id' in args):
        raise DeviceError('install needs exactly one of build or upload_id')


# Arguments shown in the session timeline. Never typed text (may be a password) or flow bodies.
AUDIT_ARGS = ('app_id', 'match', 'ref', 'x', 'y', 'key', 'url', 'action', 'enabled', 'duration_s', 'count',
              'locale', 'timezone', 'clock_offset_s', 'dark_mode', 'font_scale', 'grant', 'revoke', 'reset')


def audit_detail(op, args):
    detail = {k: args[k] for k in AUDIT_ARGS if k in args}
    if 'url' in detail:
        detail['url'] = str(detail['url']).split('?', 1)[0][:200]  # query strings may carry tokens
    if isinstance(args.get('build'), dict):
        detail['build'] = {k: args['build'][k] for k in ('repo', 'artifact_name', 'pr', 'run_id') if k in args['build']}
    if op in ('set_text', 'type'):
        detail['chars'] = len(args.get('text') or '')
    if 'location' in args:
        detail['location'] = True
    return detail or None


class DeviceOffline(DeviceError):
    """No usable device right now; `runners` are the ones whose owner could fix that."""

    def __init__(self, message, runners):
        super().__init__(message)
        self.runners = runners


def _validate_extras(name, extras):
    if not isinstance(extras, dict) or not extras:
        raise DeviceError(f'{name} must be a non-empty object')
    for key, value in extras.items():
        if not isinstance(key, str) or not EXTRA_KEY.fullmatch(key):
            raise DeviceError(f'Invalid {name} key {str(key)[:40]!r} (letters, digits, _ and . only)')
        if name == 'bool_extras' and type(value) is not bool:
            raise DeviceError('bool_extras values must be true or false')
        if name == 'extras' and (not isinstance(value, str) or len(value) > 1000 or '\x00' in value):
            raise DeviceError('extras values must be strings (max 1000 chars)')


def compact_tree(data, compact=False, clickable_only=False, needle=None):
    """Shrink a ui_tree result: filter elements, number them (e1, e2, ...), optionally one line each.

    Refs are numbered after filtering, so every ref the model sees is one it can tap. The
    returned '_refs' map (ref -> centre) is stripped by DeviceService.call before replying.
    """
    elements = data.get('elements') or []
    if clickable_only:
        elements = [e for e in elements if e.get('clickable')]
    if needle:
        low = needle.lower()
        elements = [e for e in elements if any(low in str(e.get(k) or '').lower() for k in ('text', 'label', 'id'))]
    elements = [{'ref': f'e{i}', **e} for i, e in enumerate(elements, 1)]
    refs = {e['ref']: e['center'] for e in elements if isinstance(e.get('center'), list) and len(e['center']) == 2}
    result = {k: v for k, v in data.items() if k != 'elements'}
    if not compact:
        return {**result, 'elements': elements, '_refs': refs}
    lines = []
    for e in elements:
        parts = [e['ref'], e.get('type') or '?']
        if e.get('text'):
            parts.append(repr(e['text'][:80]))
        if e.get('label') and e.get('label') != e.get('text'):
            parts.append('label=' + repr(e['label'][:80]))
        if e.get('id'):
            parts.append('#' + e['id'].rsplit('/', 1)[-1])
        center = e.get('center') or ['?', '?']
        parts.append(f'@{center[0]},{center[1]}' + (' *' if e.get('clickable') else ''))
        lines.append(' '.join(parts))
    return {**result, 'count': len(lines), 'tree': '\n'.join(lines), '_refs': refs,
            'legend': f'ref type text [label=] [#id] @center_x,center_y (* = clickable). '
                      f'Tap with tap --ref e3; refs expire after {REF_TTL}s or a screen-changing op'}


def _ref_key(user_email, scope, device_id):
    return (user_email, scope, device_id)


def remember_refs(user_email, scope, device_id, refs):
    if len(_REFS) >= REF_KEYS_MAX:
        for key in sorted(_REFS, key=lambda k: _REFS[k][0])[:len(_REFS) // 2]:
            _REFS.pop(key, None)
    _REFS[_ref_key(user_email, scope, device_id)] = (time.monotonic(), refs)


def resolve_ref(user_email, scope, device_id, ref):
    entry = _REFS.get(_ref_key(user_email, scope, device_id))
    if entry is None or time.monotonic() - entry[0] > REF_TTL:
        raise DeviceError(f'Ref {ref} is unknown or expired; read ui_tree again')
    center = entry[1].get(ref)
    if center is None:
        raise DeviceError(f'No element {ref} in the latest ui_tree ({len(entry[1])} refs); read ui_tree again')
    return center


FAILURE_LINE = re.compile(r'FAILED|❌|Assertion|not visible|not found|Exception|Error', re.I)


def summarize_flow(data):
    """Maestro result without the noise: pass/fail, counts and only the failed steps."""
    summary = {'passed': data.get('passed'), 'exit_code': data.get('exit_code')}
    try:
        root = ET.fromstring(data.get('report') or '<x/>')
        cases = list(root.iter('testcase'))
        failures = [(c.get('name'), (f.get('message') or f.text or '').strip()[:500])
                    for c in cases for f in list(c.iter('failure')) + list(c.iter('error'))]
        summary.update(tests=len(cases), failures=[{'flow': n, 'message': m} for n, m in failures])
    except ET.ParseError:
        pass
    lines = [line.strip() for line in (data.get('output') or '').splitlines() if line.strip()]
    if not data.get('passed'):
        summary['failed_steps'] = [line[:300] for line in lines if FAILURE_LINE.search(line)][:15]
        summary['output_tail'] = [line[:300] for line in lines[-8:]]
    summary['note'] = 'Pass verbose=true for the full Maestro output and JUnit report'
    return summary


def _decode(value, what):
    try:
        return base64.b64decode(value, validate=True)
    except (TypeError, ValueError):
        raise DeviceError(f'Runner returned an invalid {what}') from None


def _validate_build(build):
    if not isinstance(build, dict) or not {'repo', 'artifact_name'} <= set(build) <= {'repo', 'artifact_name', 'pr', 'run_id'}:
        raise DeviceError('build must be {repo, artifact_name, pr?|run_id?}')
    if not isinstance(build['repo'], str) or not REPO.fullmatch(build['repo']):
        raise DeviceError('Invalid build.repo (owner/name)')
    if not isinstance(build['artifact_name'], str) or not ARTIFACT_NAME.fullmatch(build['artifact_name']):
        raise DeviceError('Invalid build.artifact_name')
    if 'pr' in build and 'run_id' in build:
        raise DeviceError('Use build.pr or build.run_id, not both')
    for key in ('pr', 'run_id'):
        if key in build and (type(build[key]) is not int or not 1 <= build[key] <= 10 ** 12):
            raise DeviceError(f'Invalid build.{key}')


class DeviceService:
    def __init__(self, db, hub=None, blobs=None):
        self.db = db
        self.hub = hub or default_hub
        self.blobs = blobs or default_blobs

    # ── Discovery ─────────────────────────────────────────────────────────

    async def runners_for(self, user_email):
        cursor = self.db.device_runners.find(
            {'revoked': {'$ne': True}, '$or': [{'owner_email': user_email}, {'shared_with': user_email.lower()}]},
            {'secret_hash': 0, '_id': 0})
        return await cursor.to_list(length=100)

    async def list_devices(self, user_email):
        runners = await self.runners_for(user_email)
        # Live device list when the runner is connected, last persisted list otherwise.
        listing = []
        for runner in runners:
            conn = self.hub.get(runner['runner_id'])
            listing.append((runner, conn, conn.devices if conn is not None else (runner.get('devices') or [])))
        ids = [store.device_id(r['runner_id'], d.get('serial', '')) for r, _, listed in listing for d in listed]
        leases = {l['_id']: l for l in await self.db.device_leases.find({'_id': {'$in': ids}}).to_list(length=500)}
        at = store.now()
        devices = []
        for runner, conn, listed in listing:
            for device in listed:
                did = store.device_id(runner['runner_id'], device.get('serial', ''))
                lease = leases.get(did)
                held = lease is not None and store.aware(lease['expires_at']) > at
                devices.append({
                    'device_id': did, 'platform': device.get('platform'), 'name': device.get('name'),
                    'os_version': device.get('os_version'), 'virtual': device.get('virtual', True),
                    'runner': runner.get('name'), 'owner': runner.get('owner_email'),
                    'online': conn is not None,
                    'leased_by': ({'owner': lease['owner_email'], 'scope': lease['scope'],
                                   'expires_at': store.aware(lease['expires_at']).isoformat()} if held else None)})
        return devices

    async def templates_for(self, user_email):
        """Device templates the user's runners can boot (live from connected runners)."""
        templates = []
        for runner in await self.runners_for(user_email):
            conn = self.hub.get(runner['runner_id'])
            for template in (getattr(conn, 'templates', None) or [] if conn is not None else runner.get('templates') or []):
                templates.append({'template': template['name'], 'platform': template['platform'],
                                  'clean': bool(template.get('clean')), 'runner': runner.get('name'),
                                  'runner_id': runner['runner_id'], 'online': conn is not None})
        return templates

    async def _resolve(self, user_email, device_id):
        parts = store.split_device_id(device_id)
        if parts is None:
            raise DeviceError('Invalid device_id; use one returned by device list')
        runner_id, serial = parts
        runner = await self.db.device_runners.find_one({'runner_id': runner_id, 'revoked': {'$ne': True}})
        if runner is None or not store.can_use(runner, user_email):
            raise DeviceError('Device not found or not shared with you')
        return runner, serial

    # ── Leases ────────────────────────────────────────────────────────────

    async def _acquire(self, device_id, user_email, scope, ttl=LEASE_TTL):
        # Two attempts: concurrent first acquires by the SAME holder race on the
        # upsert insert; the retry then matches the winner's document.
        for _ in range(2):
            at = store.now()
            try:
                return await self.db.device_leases.find_one_and_update(
                    {'_id': device_id, '$or': [{'expires_at': {'$lt': at}},
                                               {'owner_email': user_email, 'scope': scope}]},
                    {'$set': {'owner_email': user_email, 'scope': scope, 'expires_at': at + ttl},
                     '$setOnInsert': {'acquired_at': at}},
                    upsert=True, return_document=ReturnDocument.AFTER)
            except DuplicateKeyError:
                continue
        return None

    async def lease(self, user_email, scope, device_id=None, platform=None, wait_online_s=0, template=None,
                    clean=False):
        """Reserve a device. With wait_online_s, wait (bounded) for an offline runner / unbooted device;
        either way the owners of the offline runners get a Loma inbox notification (rate-limited).

        template (a name from device.list) boots a new device from it; clean boots it from the template's
        clean state (a read-only snapshot / a throwaway simulator clone). Without either, a free booted
        device is used, and if there is none an online runner's template is booted automatically.
        """
        self._check_scope(scope)
        if platform not in (None, 'android', 'ios'):
            raise DeviceError('platform must be android or ios')
        if type(wait_online_s) is not int or not 0 <= wait_online_s <= MAX_WAIT_ONLINE:
            raise DeviceError(f'wait_online_s must be an integer between 0 and {MAX_WAIT_ONLINE}')
        if template is not None and (not isinstance(template, str) or not TEMPLATE.fullmatch(template)):
            raise DeviceError('Invalid template name; use one listed by device list')
        if type(clean) is not bool or (device_id is not None and (template is not None or clean)):
            raise DeviceError('clean must be true or false, and template / clean cannot be combined with device_id')
        deadline, notified = time.monotonic() + wait_online_s, False
        while True:
            try:
                if template is not None or clean:
                    return await self._boot_and_lease(user_email, scope, template, platform, clean)
                return await self._lease_once(user_email, scope, device_id, platform)
            except DeviceOffline as exc:
                if not notified:
                    notified = True
                    await self._notify_offline(user_email, scope, exc.runners, platform)
                if time.monotonic() + WAIT_ONLINE_POLL > deadline:
                    if wait_online_s:
                        raise DeviceError(f'{exc} (waited {wait_online_s}s; the runner owner was notified)') from None
                    raise
                await asyncio.sleep(WAIT_ONLINE_POLL)

    async def _lease_once(self, user_email, scope, device_id, platform):
        if device_id is not None:
            runner, serial = await self._resolve(user_email, device_id)
            if self.hub.get(runner['runner_id']) is None:
                raise DeviceOffline('That device\'s runner is offline', [runner])
            candidates = [store.device_id(runner['runner_id'], serial)]
        else:
            devices = await self.list_devices(user_email)
            candidates = [d['device_id'] for d in devices if d['online']
                          and (platform is None or d['platform'] == platform)]
            if not candidates:
                bootable = await self._bootable(user_email, platform)
                if bootable:  # nothing running: boot one, clean when the template supports it
                    return await self._boot_and_lease(user_email, scope, bootable['template'], platform,
                                                      bootable['clean'], bootable['runner_id'])
                message = self._no_device_message(devices, platform)
                runners = await self.runners_for(user_email)
                if not runners:
                    raise DeviceError(message)
                # Runners that could help: offline ones, and online ones without a matching booted device.
                raise DeviceOffline(message, runners)
        busy = []
        for candidate in candidates:
            lease = await self._acquire(candidate, user_email, scope)
            if lease is not None:
                await self._audit(user_email, scope, candidate, 'lease', True)
                return {'device_id': candidate, 'expires_at': store.aware(lease['expires_at']).isoformat(),
                        'note': 'Lease renews on every call and expires after 15 idle minutes. Release it when done.'}
            busy.append(candidate)
        bootable = await self._bootable(user_email, platform) if device_id is None else None
        if bootable:  # every running device is taken: start another one
            return await self._boot_and_lease(user_email, scope, bootable['template'], platform,
                                              bootable['clean'], bootable['runner_id'])
        raise DeviceError('All matching devices are leased by another session: ' + ', '.join(busy))

    async def _bootable(self, user_email, platform):
        for template in await self.templates_for(user_email):
            if (template['online'] and (platform is None or template['platform'] == platform)
                    and runner_supports(self.hub.get(template['runner_id']), 'boot')):
                return template
        return None

    async def _boot_and_lease(self, user_email, scope, template, platform, clean, runner_id=None):
        templates = [t for t in await self.templates_for(user_email)
                     if (template is None or t['template'] == template) and (platform is None or t['platform'] == platform)
                     and (runner_id is None or t['runner_id'] == runner_id) and (t['clean'] or not clean)]
        if not templates:
            names = sorted({t['template'] for t in await self.templates_for(user_email)})
            raise DeviceError(f'No {"clean-capable " if clean else ""}device template matches'
                              + (f' {template!r}' if template else '') + f'; available: {names or "none"} '
                              '(templates are set in the runner config, see the runner README)')
        online = [t for t in templates if t['online']]
        if not online:
            runners = [r for r in await self.runners_for(user_email)
                       if r['runner_id'] in {t['runner_id'] for t in templates}]
            raise DeviceOffline(f'The runner with template {templates[0]["template"]} is offline', runners)
        choice = online[0]
        _check_runner_version(self.hub.get(choice['runner_id']), 'boot', {})
        started = time.monotonic()
        detail = {'template': choice['template'], 'clean': clean}
        try:
            data = await self.hub.call(choice['runner_id'], 'boot', '-', {'template': choice['template'], 'clean': clean})
            serial = data.get('serial')
            if not isinstance(serial, str) or not store.SERIAL.fullmatch(serial):
                raise DeviceError('Runner returned an invalid device after boot')
        except DeviceError as exc:
            await self._audit(user_email, scope, None, 'boot', False, str(exc), started, detail)
            raise
        device_id = store.device_id(choice['runner_id'], serial)
        await self._audit(user_email, scope, device_id, 'boot', True, None, started, detail)
        lease = await self._acquire(device_id, user_email, scope)
        if lease is None:
            raise DeviceError(f'{device_id} was booted but another session leased it first; lease again')
        booted = bool(data.get('booted'))
        await self.db.device_leases.update_one({'_id': device_id}, {'$set': {'booted': booted, 'clean': clean}})
        await self._audit(user_email, scope, device_id, 'lease', True)
        return {'device_id': device_id, 'expires_at': store.aware(lease['expires_at']).isoformat(),
                'template': choice['template'], 'booted': booted, 'clean': clean,
                'note': ('Booted from template ' + choice['template'] + (' in a clean state' if clean else '')
                         + '. release shuts it down (so does 30 idle minutes on the runner).') if booted else
                        'The template device was already running; it was leased as is.'}

    @staticmethod
    def _no_device_message(devices, platform):
        if not devices:
            return ('No devices are registered for you. Enroll a machine under Integrations → Devices and run the '
                    'Loma Device Runner there.')
        kind = f'{platform} ' if platform else ''
        return f'No online {kind}devices. Is the runner machine awake and the emulator/simulator booted?'

    async def _notify_offline(self, user_email, scope, runners, platform):
        """Tell each runner owner (at most every 30 min per runner) that an agent is waiting for a device."""
        from observability.notifications import create_notification
        at = store.now()
        conversation_id = scope[5:] if scope.startswith('conv:') else None
        kind = {'android': 'an Android ', 'ios': 'an iOS '}.get(platform, 'a ')
        for runner in runners:
            claimed = await self.db.device_runners.find_one_and_update(
                {'runner_id': runner['runner_id'], '$or': [
                    {'offline_notified_at': {'$exists': False}},
                    {'offline_notified_at': {'$lt': at - OFFLINE_NOTIFY_EVERY}}]},
                {'$set': {'offline_notified_at': at}})
            if claimed is None:
                continue
            online = self.hub.get(runner['runner_id']) is not None
            action = ('boot an emulator/simulator on it' if online
                      else 'wake the machine and check the runner service is running')
            try:
                await create_notification(
                    self.db, user_email=runner['owner_email'], source='system',
                    title=f"Loma needs {kind}device on {runner.get('name') or 'your runner'}",
                    body=(f'{user_email} started a device session, but "{runner.get("name")}" has no usable '
                          f'device. Please {action}. The agent keeps the request open for a few minutes.'),
                    conversation_id=conversation_id if runner['owner_email'] == user_email else None)
            except Exception:
                pass  # a notification failure must never fail the lease

    async def release(self, user_email, scope, device_id):
        self._check_scope(scope)
        runner, serial = await self._resolve(user_email, device_id)
        lease = await self.db.device_leases.find_one_and_delete(
            {'_id': device_id, 'owner_email': user_email, 'scope': scope})
        result = {'released': lease is not None}
        hold = (lease or {}).get('hold')
        if hold and store.aware(hold['until']) > store.now():
            # A person is driving it from the dashboard: hand the lease to them, touch nothing on the device.
            await self.db.device_leases.replace_one({'_id': device_id}, {
                'owner_email': hold['by'], 'scope': TAKEOVER + hold['by'], 'acquired_at': store.now(),
                'expires_at': hold['until'], 'hold': hold}, upsert=True)
            await self._audit(user_email, scope, device_id, 'release', True)
            return {**result, 'note': f"{hold['by']} has taken over this device; it stays with them"}
        if lease is not None and lease.get('netcap'):  # never leave a device pointing at the capture proxy
            try:
                await self.hub.call(runner['runner_id'], 'netcap', serial, {'action': 'stop'})
                result['capture_stopped'] = True
            except DeviceError:
                result['capture_stopped'] = False
        # A clean boot is discarded at shutdown anyway, so only restore settings on devices that persist.
        if lease is not None and lease.get('configured') and not (lease.get('booted') and lease.get('clean')):
            result['settings_restored'] = await self._restore_settings(runner['runner_id'], serial)
        if lease is not None and lease.get('booted'):
            result['shutdown'] = await self._shutdown(user_email, scope, device_id, runner['runner_id'], serial)
        await self._audit(user_email, scope, device_id, 'release', True)
        return result

    async def _shutdown(self, user_email, scope, device_id, runner_id, serial):
        if not runner_supports(self.hub.get(runner_id), 'shutdown'):
            return False
        started = time.monotonic()
        try:
            await self.hub.call(runner_id, 'shutdown', serial, {})
        except DeviceError as exc:  # the runner's idle timer still shuts it down later
            await self._audit(user_email, scope, device_id, 'shutdown', False, str(exc), started)
            return False
        await self._audit(user_email, scope, device_id, 'shutdown', True, None, started)
        return True

    async def _restore_settings(self, runner_id, serial):
        """Best effort: undo configure (clock, locale, ...) so the next session starts from defaults."""
        if not runner_supports(self.hub.get(runner_id), 'configure'):
            return False
        try:
            await self.hub.call(runner_id, 'configure', serial, {'reset': True})
            return True
        except DeviceError:
            return False

    async def force_release(self, user_email, device_id):
        runner, _ = await self._resolve(user_email, device_id)
        if runner['owner_email'] != user_email:
            raise DeviceError('Only the runner owner can force-release a device')
        await self.db.device_leases.delete_one({'_id': device_id})
        await self._audit(user_email, 'dashboard', device_id, 'force_release', True)
        return {'released': True}

    @staticmethod
    def _check_scope(scope):
        if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
            raise DeviceError('Invalid lease scope')

    # ── Operations ────────────────────────────────────────────────────────

    async def call(self, user_email, scope, device_id, op, args):
        """Validate, lease (implicitly if the device is free), dispatch, audit.

        Returns the runner's data dict. Screenshots come back as raw bytes under
        'png' (never base64 in the caller's hands) so callers decide delivery.
        """
        self._check_scope(scope)
        _validate(op, args)
        runner, serial = await self._resolve(user_email, device_id)
        # Hold the lease for the op's worst case too, so a long install is never taken over mid-way.
        worst = OP_TIMEOUTS.get(op, DEFAULT_TIMEOUT) + (
            GITHUB_FETCH_TIMEOUT + args.get('wait_s', 0) if op == 'install' else 0)
        local = {k: args[k] for k in BACKEND_ARGS.get(op, ()) if k in args}
        args = {k: v for k, v in args.items() if k not in local}
        _check_runner_version(self.hub.get(runner['runner_id']), op, args)
        ref = args.pop('ref', None)
        tap_first = None
        if ref is not None:
            center = resolve_ref(user_email, scope, device_id, ref)
            if op == 'tap':
                args.update(x=int(center[0]), y=int(center[1]))
            else:  # set_text / clear_text: focus the field by ref, then edit the focused field
                tap_first = {'x': int(center[0]), 'y': int(center[1])}
        await self._check_hold(device_id, scope)
        lease = await self._acquire(device_id, user_email, scope, LEASE_TTL + timedelta(seconds=worst))
        if lease is None:
            raise DeviceError('Device is leased by another session. Pick another device or wait for it to be released.')
        started = time.monotonic()
        detail = audit_detail(op, {**args, **({'ref': ref} if ref else {})})
        try:
            if op in REF_RESET_OPS:
                _REFS.pop(_ref_key(user_email, scope, device_id), None)
            build_meta = None
            if op == 'install':
                args, build_meta = await self._prepare_install(user_email, runner['runner_id'], args, local)
            if tap_first:
                await self.hub.call(runner['runner_id'], 'tap', serial, tap_first)
                await asyncio.sleep(0.3)
            data = await self.hub.call(runner['runner_id'], op, serial, args)
            if op == 'configure':  # release restores the device only when this session changed it
                await self.db.device_leases.update_one({'_id': device_id}, {'$set': {
                    'configured': not args.get('reset', False)}})
            if op == 'netcap' and args['action'] in ('start', 'stop'):  # release stops a capture left running
                await self.db.device_leases.update_one({'_id': device_id}, {'$set': {
                    'netcap': args['action'] == 'start'}})
            if ref is not None:
                data = {**data, 'ref': ref}
            if build_meta:
                data['build'] = build_meta
            data = self._shape(op, data, local)
            refs = data.pop('_refs', None) if isinstance(data, dict) else None
            if refs is not None:
                remember_refs(user_email, scope, device_id, refs)
        except Exception as exc:
            message = str(exc) if isinstance(exc, DeviceError) else f'internal error: {type(exc).__name__}'
            try:
                await self._audit(user_email, scope, device_id, op, False, message, started, detail)
            except Exception:
                pass
            raise
        await self._audit(user_email, scope, device_id, op, True, None, started, detail)
        return data

    @staticmethod
    def _shape(op, data, local):
        """Decode media to bytes (callers decide delivery) and trim results for the model."""
        if op == 'screenshot':
            if 'png_base64' not in data:
                raise DeviceError('Runner returned an invalid screenshot')
            data['png'] = _decode(data.pop('png_base64'), 'screenshot')
        elif op == 'burst':
            data['frames'] = [{**{k: v for k, v in f.items() if k != 'png_base64'},
                               'png': _decode(f.get('png_base64'), 'screenshot')} for f in data.get('frames') or []]
        elif op == 'record':
            data['mp4'] = _decode(data.pop('mp4_base64', None), 'recording')
        elif op == 'ui_tree':
            data = compact_tree(data, local.get('compact', False), local.get('clickable_only', False),
                                local.get('filter'))
        elif op == 'netcap' and 'flows' in data:
            data = summarize_network(data)
        elif op == 'run_flow':
            shots = [{'name': s.get('name'), 'png': _decode(s.get('png_base64'), 'flow screenshot')}
                     for s in data.pop('screenshots', None) or []]
            data = dict(data) if local.get('verbose') else summarize_flow(data)
            if shots:
                data['screenshots'] = shots
        return data

    # ── Live view and take-over (dashboard) ───────────────────────────────

    async def _check_hold(self, device_id, scope):
        lease = await self.db.device_leases.find_one({'_id': device_id}, {'hold': 1})
        hold = (lease or {}).get('hold')
        if hold and store.aware(hold['until']) > store.now() and scope != TAKEOVER + hold['by']:
            raise DeviceError(f"{hold['by']} took over this device from the Loma dashboard. Wait for them to hand "
                              'it back (about a minute), then retry; do not switch to another device mid-test.')

    async def screen(self, user_email, device_id):
        """One live-view frame (PNG bytes). Read-only: no lease needed, rate-limited per viewer."""
        runner, serial = await self._resolve(user_email, device_id)
        key = (user_email, device_id)
        now = time.monotonic()
        if now - _LAST_FRAME.get(key, 0) < SCREEN_MIN_INTERVAL:
            raise DeviceError('Too many frames; slow down')
        _LAST_FRAME[key] = now
        if len(_LAST_FRAME) > 1000:
            _LAST_FRAME.clear()
        data = await self.hub.call(runner['runner_id'], 'screenshot', serial, {})
        png = _decode(data.get('png_base64'), 'screenshot')
        _SCREENS.setdefault(device_id, {})['pixels'] = (data.get('width'), data.get('height'))
        return png

    async def start_takeover(self, user_email, device_id):
        """Hold the device for a person: agent calls on it fail with a clear message until hand-back."""
        runner, _ = await self._resolve(user_email, device_id)
        at = store.now()
        lease = await self.db.device_leases.find_one({'_id': device_id})
        active = lease is not None and store.aware(lease['expires_at']) > at
        if active and lease['owner_email'] != user_email and runner['owner_email'] != user_email:
            raise DeviceError('Only the runner owner or the person whose session holds the device can take it over')
        hold = {'by': user_email, 'since': at, 'until': at + HOLD_TTL}
        if active:
            await self.db.device_leases.update_one({'_id': device_id}, {'$set': {'hold': hold}})
        else:
            await self.db.device_leases.replace_one({'_id': device_id}, {
                'owner_email': user_email, 'scope': TAKEOVER + user_email, 'acquired_at': at,
                'expires_at': at + HOLD_TTL, 'hold': hold}, upsert=True)
        await self._audit(user_email, TAKEOVER + user_email, device_id, 'takeover', True)
        return {'held': True, 'until': hold['until'].isoformat(),
                'paused_session': lease['scope'] if active and not lease['scope'].startswith(TAKEOVER) else None}

    async def end_takeover(self, user_email, device_id):
        runner, _ = await self._resolve(user_email, device_id)
        who = [user_email] if runner['owner_email'] != user_email else None
        query = {'_id': device_id, **({'hold.by': {'$in': who}} if who else {'hold': {'$exists': True}})}
        await self.db.device_leases.delete_one({**query, 'scope': TAKEOVER + user_email})
        result = await self.db.device_leases.update_one(query, {'$unset': {'hold': ''}})
        await self._audit(user_email, TAKEOVER + user_email, device_id, 'hand_back', True)
        return {'held': False, 'resumed_session': bool(result.modified_count)}

    async def takeover_input(self, user_email, device_id, op, args):
        """A person's input during a take-over. tap/swipe take fractions of the screen (fx, fy in 0..1)."""
        if op not in TAKEOVER_OPS or not isinstance(args, dict):
            raise DeviceError('Unsupported input')
        runner, serial = await self._resolve(user_email, device_id)
        at = store.now()
        lease = await self.db.device_leases.find_one_and_update(
            {'_id': device_id, 'hold.by': user_email, 'hold.until': {'$gt': at}},
            {'$set': {'hold.until': at + HOLD_TTL}, '$max': {'expires_at': at + HOLD_TTL}})
        if lease is None:
            raise DeviceError('Take over the device first (or your take-over lapsed)')
        if op in ('tap', 'swipe'):
            args = await self._to_device_units(runner['runner_id'], serial, device_id, op, args)
        _validate(op, args)
        started = time.monotonic()
        scope = TAKEOVER + user_email
        try:
            data = await self.hub.call(runner['runner_id'], op, serial, args)
        except DeviceError as exc:
            await self._audit(user_email, scope, device_id, op, False, str(exc), started, audit_detail(op, args))
            raise
        _REFS.pop(_ref_key(lease['owner_email'], lease['scope'], device_id), None)  # the agent's refs are stale now
        await self._audit(user_email, scope, device_id, op, True, None, started, audit_detail(op, args))
        return data

    async def _to_device_units(self, runner_id, serial, device_id, op, args):
        names = ('fx', 'fy') if op == 'tap' else ('fx1', 'fy1', 'fx2', 'fy2')
        if set(args) != set(names) or not all(type(args[n]) in (int, float) and 0 <= args[n] <= 1 for n in names):
            raise DeviceError(f'{op} takes {", ".join(names)} as fractions of the screen (0..1)')
        screens = _SCREENS.setdefault(device_id, {})
        platform = next((d.get('platform') for d in getattr(self.hub.get(runner_id), 'devices', None) or []
                         if d.get('serial') == serial), None)
        if platform == 'ios':  # idb taps in points; screenshots are in pixels
            if 'points' not in screens:
                tree = await self.hub.call(runner_id, 'ui_tree', serial, {})
                screens['points'] = tuple(tree.get('screen') or (0, 0))
            width, height = screens['points']
        else:
            width, height = screens.get('pixels') or (0, 0)
        if not width or not height:
            raise DeviceError('Screen size unknown; wait for the live view to load a frame')
        scale = lambda fraction, size: min(int(fraction * size), size - 1)  # noqa: E731
        if op == 'tap':
            return {'x': scale(args['fx'], width), 'y': scale(args['fy'], height)}
        return {'x1': scale(args['fx1'], width), 'y1': scale(args['fy1'], height),
                'x2': scale(args['fx2'], width), 'y2': scale(args['fy2'], height), 'duration_ms': 300}

    # ── Verification (backend-side, text results only) ───────────────────

    async def _hold(self, user_email, scope, device_id):
        """These checks belong to a device session: the caller must hold (or be able to take) the lease."""
        self._check_scope(scope)
        await self._resolve(user_email, device_id)
        if await self._acquire(device_id, user_email, scope) is None:
            raise DeviceError('Device is leased by another session. Pick another device or wait for it to be released.')

    async def plotline_check(self, user_email, scope, device_id, args):
        await self._hold(user_email, scope, device_id)
        started = time.monotonic()
        detail = {k: args[k] for k in ('product_id', 'flow_id', 'since_s') if k in args}
        try:
            result = await plotline_activity(args)
        except DeviceError as exc:
            await self._audit(user_email, scope, device_id, 'plotline_check', False, str(exc), started, detail)
            raise
        await self._audit(user_email, scope, device_id, 'plotline_check', True, None, started, detail)
        return result

    async def visual_check(self, user_email, scope, device_id, expect):
        """Screenshot (through the normal op path, so it is leased and audited) judged by a vision model."""
        shot = await self.call(user_email, scope, device_id, 'screenshot', {})
        started = time.monotonic()
        try:
            verdict = await judge_screenshot(shot['png'], expect)
        except DeviceError as exc:
            await self._audit(user_email, scope, device_id, 'visual_check', False, str(exc), started)
            raise
        await self._audit(user_email, scope, device_id, 'visual_check', True, None, started,
                          {'passed': verdict['passed']})
        return {**verdict, 'png': shot['png']}

    async def _prepare_install(self, user_email, runner_id, args, local=None):
        local = local or {}
        app_id = args.get('app_id')
        if 'build' in args:
            build = args['build']
            blob_id, blob = await self.blobs.from_github(user_email, build['repo'], build['artifact_name'],
                                                         pr=build.get('pr'), run_id=build.get('run_id'),
                                                         wait_s=local.get('wait_s', 0),
                                                         dispatch_workflow=local.get('dispatch_workflow'))
        else:
            blob_id = args['upload_id']
            blob = self.blobs.get(blob_id, user_email)
            if blob is None:
                raise DeviceError('Upload not found or expired; upload the build again')
        self.blobs.bind(blob_id, runner_id)
        runner_args = {'blob_id': blob_id, 'sha256': blob['sha256'], 'filename': blob['filename']}
        runner_args.update({k: args[k] for k in ('app_id', 'grant_appops', 'grant_privacy', 'force') if k in args})
        if app_id:
            runner_args['app_id'] = app_id
        return runner_args, dict(blob['meta'], sha256=blob['sha256'], size=blob['size'])

    async def _audit(self, user_email, scope, device_id, op, ok, error=None, started=None, detail=None):
        entry = {'at': store.now(), 'device_id': device_id, 'actor': user_email, 'scope': scope, 'op': op, 'ok': ok}
        if error:
            entry['error'] = error[:500]
        if detail:
            entry['detail'] = detail
        if started is not None:
            entry['duration_ms'] = int((time.monotonic() - started) * 1000)
        await self.db.device_audit.insert_one(entry)
