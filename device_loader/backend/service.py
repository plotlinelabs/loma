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

LEASE_TTL = timedelta(minutes=15)
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
CURSOR = re.compile(r'[tc]:[0-9]{1,20}(?:\.[0-9]{1,6})?\Z')
MAX_LOG_TAGS = 8
# scenario steps reuse the single-op validation below; at_ms is relative to the scenario start.
STEP_ACTIONS = {'tap', 'swipe', 'type', 'key', 'open_url', 'tap_text', 'wait_for'}
MAX_STEPS = 40
MAX_STEP_WAIT = 10

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
    'logs': (set(), {'lines', 'filter', 'clear', 'source', 'tags', 'since'}),
    'run_flow': ({'flow'}, {'verbose'}),
    'set_text': ({'text'}, {'match', 'clear', 'ref'} | SELECTOR),
    'clear_text': (set(), {'match', 'ref'} | SELECTOR),
    'wait_for': ({'match'}, {'timeout_s', 'gone'} | SELECTOR),
    'tap_text': ({'match'}, {'timeout_s'} | SELECTOR),
    'scroll_until_visible': ({'match'}, {'direction', 'max_swipes'} | SELECTOR),
    'burst': ({'count'}, {'interval_ms', 'app_id'} | LAUNCH),
    'record': ({'duration_s'}, {'app_id'} | LAUNCH),
    'animations': ({'enabled'}, set()),
    'scenario': ({'duration_s'}, {'app_id', 'steps', 'record', 'sample_ms', 'log_tags', 'log_source', 'log_lines',
                                  'stop_first', 'console'} | LAUNCH),
}
# Arguments the backend consumes itself; never forwarded to the runner.
BACKEND_ARGS = {'ui_tree': {'compact', 'clickable_only', 'filter'}, 'run_flow': {'verbose'},
                'install': {'wait_s', 'dispatch_workflow'}}
INTS = {'x': (0, 10000), 'y': (0, 10000), 'x1': (0, 10000), 'y1': (0, 10000), 'x2': (0, 10000),
        'y2': (0, 10000), 'duration_ms': (50, 5000), 'lines': (1, 2000), 'timeout_s': (0, 60),
        'max_swipes': (1, 20), 'count': (2, 12), 'interval_ms': (100, 5000), 'duration_s': (1, 20),
        'wait_s': (0, MAX_WAIT), 'sample_ms': (0, 2000), 'log_lines': (1, 2000)}
BOOLS = {'clear', 'exact', 'gone', 'console', 'compact', 'clickable_only', 'force', 'verbose', 'enabled', 'record',
         'stop_first'}
ENUMS = {'by': {'any', 'text', 'id', 'label'}, 'direction': {'down', 'up'}, 'source': {'auto', 'system', 'console'},
         'log_source': {'auto', 'system', 'console'}}
STRS = {'url': 2000, 'text': 500, 'filter': 200, 'flow': 64 * 1024, 'key': 32, 'app_id': 255, 'upload_id': 64,
        'match': 200, 'activity': 255, 'dispatch_workflow': 100, 'ref': 8, 'since': 40}
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
                 'type', 'key', 'set_text', 'clear_text', 'scroll_until_visible', 'run_flow', 'record', 'burst',
                 'scenario'}

# Runner features newer than 1.0.0: an older runner rejects the op or silently ignores the argument,
# so the backend refuses them up front with an upgrade hint (the runner reports VERSION in its hello).
NEEDS_RUNNER = (1, 1, 0)
NEW_RUNNER_OPS = {'set_text', 'clear_text', 'wait_for', 'tap_text', 'scroll_until_visible', 'burst', 'record',
                  'animations'}
NEW_RUNNER_ARGS = {'launch': {'extras', 'bool_extras', 'activity', 'console'},
                   'install': {'grant_appops', 'grant_privacy', 'force'}, 'logs': {'source'}}
# (minimum runner version, ops, op -> arguments) for each runner release after 1.0.0.
RUNNER_GATES = ((NEEDS_RUNNER, NEW_RUNNER_OPS, NEW_RUNNER_ARGS),
                ((1, 2, 0), {'scenario'}, {'logs': {'tags', 'since'}}))


def _version(text):
    return tuple(int(part) for part in re.findall(r'\d+', str(text or ''))[:3])


def _check_runner_version(conn, op, args):
    """args are the runner-bound arguments (backend-only ones already removed)."""
    version = getattr(conn, 'version', None)
    if version is None:
        return
    for needed, ops, new_args in RUNNER_GATES:
        if _version(version) >= needed:
            continue
        newer = sorted(new_args.get(op, set()) & set(args))
        if op not in ops and not newer:
            continue
        what = op + (' with ' + ', '.join(newer) if newer else '')
        need = '.'.join(map(str, needed))
        raise DeviceError(f'Runner too old for {what} (runner {version or "unknown"}); update the Loma Device Runner '
                          f'to >= {need}: download the new loma_device_runner.py from Integrations > Devices and '
                          'run `python3 loma_device_runner.py setup` on that machine')


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
    for key in ('tags', 'log_tags'):
        if key in args:
            _validate_tags(key, args[key])
    if 'since' in args:
        if not CURSOR.fullmatch(args['since']):
            raise DeviceError('Invalid since (use the cursor returned by the previous logs call)')
        if args.get('clear'):
            raise DeviceError('Use since or clear, not both')
    if op == 'scenario':
        _validate_scenario(args)


def _validate_tags(key, tags):
    if (not isinstance(tags, list) or not 1 <= len(tags) <= MAX_LOG_TAGS
            or not all(isinstance(t, str) and 0 < len(t) <= 100 and '\x00' not in t for t in tags)):
        raise DeviceError(f'{key} must be a list of 1-{MAX_LOG_TAGS} strings (max 100 chars each)')


def _validate_scenario(args):
    if 0 < args.get('sample_ms', 0) < 150:
        raise DeviceError('sample_ms must be 0 (off) or 150-2000')
    if 'app_id' not in args and ({'console'} | LAUNCH) & set(args):
        raise DeviceError('extras / activity / console need app_id (the app the scenario launches)')
    if 'log_source' in args or 'log_lines' in args:
        if 'log_tags' not in args:
            raise DeviceError('log_source / log_lines need log_tags (which log lines to keep)')
    steps = args.get('steps', [])
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise DeviceError(f'steps must be a list of at most {MAX_STEPS} steps')
    window, last = args['duration_s'] * 1000, 0
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get('action') not in STEP_ACTIONS:
            raise DeviceError(f'steps[{index}]: action must be one of ' + ', '.join(sorted(STEP_ACTIONS)))
        action, at = step['action'], step.get('at_ms')
        if type(at) is not int or not 0 <= at < window:
            raise DeviceError(f'steps[{index}]: at_ms must be an integer from 0 to {window - 1} (inside duration_s)')
        if at < last:
            raise DeviceError(f'steps[{index}]: steps must be in at_ms order')
        last = at
        rest = {k: v for k, v in step.items() if k not in ('action', 'at_ms')}
        if 'ref' in rest:
            raise DeviceError(f'steps[{index}]: refs cannot be used in a scenario (the screen changes); '
                              'use tap_text or x/y')
        try:
            _validate(action, rest)
        except DeviceError as exc:
            raise DeviceError(f'steps[{index}] ({action}): {exc}') from None
        if rest.get('timeout_s', 0) > MAX_STEP_WAIT:
            raise DeviceError(f'steps[{index}]: timeout_s is at most {MAX_STEP_WAIT} inside a scenario')


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

    async def lease(self, user_email, scope, device_id=None, platform=None):
        self._check_scope(scope)
        if device_id is not None:
            runner, serial = await self._resolve(user_email, device_id)
            if self.hub.get(runner['runner_id']) is None:
                raise DeviceError('That device\'s runner is offline')
            candidates = [store.device_id(runner['runner_id'], serial)]
        else:
            if platform not in (None, 'android', 'ios'):
                raise DeviceError('platform must be android or ios')
            devices = await self.list_devices(user_email)
            candidates = [d['device_id'] for d in devices if d['online']
                          and (platform is None or d['platform'] == platform)]
            if not candidates:
                raise DeviceError(self._no_device_message(devices, platform))
        busy = []
        for candidate in candidates:
            lease = await self._acquire(candidate, user_email, scope)
            if lease is not None:
                await self._audit(user_email, scope, candidate, 'lease', True)
                return {'device_id': candidate, 'expires_at': store.aware(lease['expires_at']).isoformat(),
                        'note': 'Lease renews on every call and expires after 15 idle minutes. Release it when done.'}
            busy.append(candidate)
        raise DeviceError('All matching devices are leased by another session: ' + ', '.join(busy))

    @staticmethod
    def _no_device_message(devices, platform):
        if not devices:
            return ('No devices are registered for you. Enroll a machine under Integrations → Devices and run the '
                    'Loma Device Runner there.')
        kind = f'{platform} ' if platform else ''
        return f'No online {kind}devices. Is the runner machine awake and the emulator/simulator booted?'

    async def release(self, user_email, scope, device_id):
        self._check_scope(scope)
        await self._resolve(user_email, device_id)
        result = await self.db.device_leases.delete_one({'_id': device_id, 'owner_email': user_email, 'scope': scope})
        await self._audit(user_email, scope, device_id, 'release', True)
        return {'released': bool(result.deleted_count)}

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
        lease = await self._acquire(device_id, user_email, scope, LEASE_TTL + timedelta(seconds=worst))
        if lease is None:
            raise DeviceError('Device is leased by another session. Pick another device or wait for it to be released.')
        started = time.monotonic()
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
                await self._audit(user_email, scope, device_id, op, False, message, started)
            except Exception:
                pass
            raise
        await self._audit(user_email, scope, device_id, op, True, None, started)
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
        elif op == 'scenario' and 'mp4_base64' in data:
            data['mp4'] = _decode(data.pop('mp4_base64'), 'recording')
        elif op == 'ui_tree':
            data = compact_tree(data, local.get('compact', False), local.get('clickable_only', False),
                                local.get('filter'))
        elif op == 'run_flow':
            shots = [{'name': s.get('name'), 'png': _decode(s.get('png_base64'), 'flow screenshot')}
                     for s in data.pop('screenshots', None) or []]
            data = dict(data) if local.get('verbose') else summarize_flow(data)
            if shots:
                data['screenshots'] = shots
        return data

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

    async def _audit(self, user_email, scope, device_id, op, ok, error=None, started=None):
        entry = {'at': store.now(), 'device_id': device_id, 'actor': user_email, 'scope': scope, 'op': op, 'ok': ok}
        if error:
            entry['error'] = error[:500]
        if started is not None:
            entry['duration_ms'] = int((time.monotonic() - started) * 1000)
        await self.db.device_audit.insert_one(entry)
