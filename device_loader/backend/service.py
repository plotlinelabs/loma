"""The single policy layer for device access.

Every caller (isolated tool gateway, legacy CLI via /internal, dashboard) goes
through DeviceService, so ACLs, leases, argument validation and audit are
enforced once. Arguments are validated here before they reach a runner, and
the runner validates them again.
"""
import asyncio
import base64
import json
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
# A runner error meaning the device itself went away mid-run (crashed / closed emulator), not a test failure:
# the suite restarts the device once and re-runs the case.
DEVICE_GONE = re.compile(r"not connected to this runner|is restarting|restarting it now|device '?[^ ]*'? not found|device offline"
                         r"|no devices/emulators|Unable to lookup in current state|Invalid device state", re.IGNORECASE)
MAX_SUITE_RECOVERIES = 2
# The runner's connection dropped under a case (network blip, laptop sleep, an old runner sending a big video
# inline): the suite waits this long for it to reconnect, then re-runs the case once (not counted as a retry).
RUNNER_GONE = re.compile(r'Runner reconnected|Runner disconnected|Runner is offline', re.IGNORECASE)
RUNNER_RETURN_S = 90
MAX_SUITE_RECONNECTS = 2
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
STEP_ACTIONS = {'tap', 'swipe', 'type', 'key', 'open_url', 'tap_text', 'wait_for', 'set_text', 'clear_text',
                'scroll_until_visible', 'screenshot', 'launch_app', 'stop_app'}
MAX_STEPS = 40
MAX_STEP_WAIT = 120
MAX_SCENARIO_SECONDS = 120
MAX_SCENARIO_SHOTS = 6
SHOT_NAME = re.compile(r'[A-Za-z0-9_-]{1,40}\Z')
EXPECT_KEYS = {'steps_ok', 'app_running', 'settled_by_ms', 'max_drift_ms', 'logs', 'log_order', 'screens'}
# expect.screens: compare a screenshot step's fingerprint with a known-good (like) / known-bad (unlike) one.
FINGERPRINT = re.compile(r'v1:[0-9]{1,5}x[0-9]{1,5}:[0-9]{1,5},[0-9]{1,5},[0-9]{1,5},[0-9]{1,5}:[0-9a-f]{288}\Z')
LOG_EXPECT_INTS = {'min': 100000, 'max': 100000, 'by_ms': MAX_SCENARIO_SECONDS * 1000,
                   'after_ms': MAX_SCENARIO_SECONDS * 1000}
# Number checks on a log rule: the first number after the literal text number_after in each matching line.
LOG_EXPECT_NUMBERS = ('value_min', 'value_max', 'after_reaching', 'last_min', 'last_max')
MAX_VALUE = 10 ** 12
MAX_LOG_EXPECTS = 24
LAUNCH_STEP = {'app_id', 'activity', 'extras', 'bool_extras', 'restart'}
PREFLIGHT_KEYS = {'url', 'name', 'method', 'headers', 'body', 'status', 'contains', 'timeout_s'}
HEADER_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}\Z")
MAX_PREFLIGHT = 4
MAX_PREFLIGHT_BODY = 4096
# suite: several scenario cases in one call (the backend runs them one after the other), one summary.
SUITE_KEYS = {'cases', 'defaults', 'platform_defaults', 'reset', 'stop_on_fail', 'keep_video', 'setup', 'teardown',
              'retries', 'health_check', 'keep_log_lines'}
CASE_NAME = re.compile(r'[A-Za-z0-9_.-]{1,60}\Z')
MAX_SUITE_CASES = 20
MAX_SUITE_SECONDS = 900  # duration_s of every case, counting its retries
MAX_RETRIES = 2
# Stop starting new cases after this long, so a suite always answers before the CLI's 4200 s deadline
# (a case already running can still take a reset, 420 s, and a scenario, 300 s).
SUITE_DEADLINE_S = 3300
# setup / teardown: plain device ops run once around the cases (wake, animations, clear logs, reset, ...).
SUITE_SETUP_OPS = {'key', 'animations', 'logs', 'reset_app', 'stop', 'launch', 'open_url', 'uninstall'}
MAX_SETUP_STEPS = 8
MAX_KEEP_LOG_LINES = 40
MAX_MATRIX_DEVICES = 4
MAX_SUITE_MEDIA = 64 * 1024 * 1024  # videos + screenshots kept across the whole suite

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
    'health': (set(), set()),
    'recover': (set(), {'cold'}),
    'installed': (set(), set()),
    'scenario': ({'duration_s'}, {'app_id', 'steps', 'record', 'sample_ms', 'sample_region', 'sample_min_change',
                                  'log_tags', 'log_source', 'log_lines', 'stop_first', 'stop_on_fail', 'end_after_steps',
                                  'console',
                                  'expect', 'preflight'} | LAUNCH),
}
# Arguments the backend consumes itself; never forwarded to the runner.
BACKEND_ARGS = {'ui_tree': {'compact', 'clickable_only', 'filter'}, 'run_flow': {'verbose'},
                'install': {'wait_s', 'dispatch_workflow'}}
INTS = {'x': (0, 10000), 'y': (0, 10000), 'x1': (0, 10000), 'y1': (0, 10000), 'x2': (0, 10000),
        'y2': (0, 10000), 'duration_ms': (50, 5000), 'lines': (1, 2000), 'timeout_s': (0, 120),
        'max_swipes': (1, 20), 'count': (2, 12), 'interval_ms': (100, 5000), 'duration_s': (1, 120),
        'wait_s': (0, MAX_WAIT), 'sample_ms': (0, 2000), 'log_lines': (1, 2000)}
BOOLS = {'cold', 'clear', 'exact', 'gone', 'console', 'compact', 'clickable_only', 'force', 'verbose', 'enabled', 'record',
         'stop_first', 'stop_on_fail', 'end_after_steps'}
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
                 'scenario', 'recover'}

# Runner features newer than 1.0.0: an older runner rejects the op or silently ignores the argument,
# so the backend refuses them up front with an upgrade hint (the runner reports VERSION in its hello).
NEEDS_RUNNER = (1, 1, 0)
NEW_RUNNER_OPS = {'set_text', 'clear_text', 'wait_for', 'tap_text', 'scroll_until_visible', 'burst', 'record',
                  'animations'}
NEW_RUNNER_ARGS = {'launch': {'extras', 'bool_extras', 'activity', 'console'},
                   'install': {'grant_appops', 'grant_privacy', 'force'}, 'logs': {'source'}}
# (minimum runner version, ops, op -> arguments) for each runner release after 1.0.0.
RUNNER_GATES = ((NEEDS_RUNNER, NEW_RUNNER_OPS, NEW_RUNNER_ARGS),
                ((1, 2, 0), {'scenario'}, {'logs': {'tags', 'since'}}),
                ((1, 3, 0), set(), {'scenario': {'preflight'}}),
                ((1, 4, 0), {'health', 'recover'}, {}),
                ((1, 5, 0), {'installed', 'boot', 'shutdown'}, {}))
SCENARIO_1_3 = (1, 3, 0)
SCENARIO_1_4 = (1, 4, 0)
RUNNER_1_5 = (1, 5, 0)


def _limits_1_5_parts(op, args):
    """Limits raised in runner 1.5.0 (a 1.4.0 runner rejects the larger values)."""
    parts = []
    if op in ('wait_for', 'tap_text') and args.get('timeout_s', 0) > 60:
        parts.append('timeout_s over 60')
    if op != 'scenario':
        return parts
    if args.get('duration_s', 0) > 60:
        parts.append('duration_s over 60')
    if any(isinstance(step, dict) and isinstance(step.get('timeout_s'), int) and step['timeout_s'] > 30
           for step in args.get('steps') or []):
        parts.append('step timeout_s over 30')
    expect = args.get('expect') if isinstance(args.get('expect'), dict) else {}
    if len(expect.get('logs') or []) > 12 or len(expect.get('log_order') or []) > 12:
        parts.append('more than 12 log rules')
    return parts


def _newer_scenario_parts(args):
    """Scenario features nested inside steps / expect that need runner 1.3.0 (a 1.2.0 runner rejects them)."""
    parts = []
    expect = args.get('expect') if isinstance(args.get('expect'), dict) else {}
    if 'max_drift_ms' in expect:
        parts.append('expect.max_drift_ms')
    if any(isinstance(rule, dict) and 'number_after' in rule for rule in expect.get('logs') or []):
        parts.append('expect.logs number_after')
    if any(isinstance(step, dict) and step.get('action') == 'launch_app' and set(step) & (LAUNCH_STEP - {'app_id'})
           for step in args.get('steps') or []):
        parts.append('launch_app options')
    return parts


def _scenario_1_4_parts(args):
    """Scenario features that need runner 1.4.0."""
    expect = args.get('expect') if isinstance(args.get('expect'), dict) else {}
    parts = ['expect.screens'] if expect.get('screens') else []
    if any(isinstance(step, dict) and step.get('action') == 'screenshot' and {'fingerprint', 'region'} & set(step)
           for step in args.get('steps') or []):
        parts.append('screenshot fingerprint')
    return parts


def _too_old(version, what, needed):
    need = '.'.join(map(str, needed))
    return DeviceError(f'Runner too old for {what} (runner {version or "unknown"}); update the Loma Device Runner '
                       f'to >= {need}: download the new loma_device_runner.py from Integrations > Devices and '
                       'run `python3 loma_device_runner.py setup` on that machine')


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
        raise _too_old(version, op + (' with ' + ', '.join(newer) if newer else ''), needed)
    if op == 'scenario' and _version(version) < SCENARIO_1_3:
        parts = _newer_scenario_parts(args)
        if parts:
            raise _too_old(version, 'scenario with ' + ', '.join(parts), SCENARIO_1_3)
    if op == 'scenario' and _version(version) < SCENARIO_1_4:
        parts = _scenario_1_4_parts(args)
        if parts:
            raise _too_old(version, 'scenario with ' + ', '.join(parts), SCENARIO_1_4)
    if _version(version) < RUNNER_1_5:
        parts = _limits_1_5_parts(op, args)
        if parts:
            raise _too_old(version, f'{op} with ' + ', '.join(parts), RUNNER_1_5)


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
    if op == 'record' and args['duration_s'] > 20:
        raise DeviceError('duration_s must be an integer between 1 and 20')
    if op == 'scenario':
        _validate_scenario(args)


def _validate_tags(key, tags):
    if (not isinstance(tags, list) or not 1 <= len(tags) <= MAX_LOG_TAGS
            or not all(isinstance(t, str) and 0 < len(t) <= 100 and '\x00' not in t for t in tags)):
        raise DeviceError(f'{key} must be a list of 1-{MAX_LOG_TAGS} strings (max 100 chars each)')


def _validate_scenario(args):
    """Same rules as the runner (need_steps / need_expect), so a bad spec fails before it reaches a device."""
    if args['duration_s'] > MAX_SCENARIO_SECONDS:
        raise DeviceError(f'duration_s is at most {MAX_SCENARIO_SECONDS} in a scenario')
    sampling = args.get('sample_ms', 0)
    if 0 < sampling < 150:
        raise DeviceError('sample_ms must be 0 (off) or 150-2000')
    if not sampling and {'sample_region', 'sample_min_change'} & set(args):
        raise DeviceError('sample_region / sample_min_change need sample_ms')
    if args.get('sample_region') is not None:
        _validate_region(args['sample_region'], 'sample_region')
    floor = args.get('sample_min_change', 0)
    if type(floor) not in (int, float) or not 0 <= floor <= 1:
        raise DeviceError('sample_min_change must be a fraction from 0 to 1')
    if 'app_id' not in args and ({'console'} | LAUNCH) & set(args):
        raise DeviceError('extras / activity / console need app_id (the app the scenario launches)')
    if 'log_source' in args or 'log_lines' in args:
        if 'log_tags' not in args:
            raise DeviceError('log_source / log_lines need log_tags (which log lines to keep)')
    steps = args.get('steps', [])
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise DeviceError(f'steps must be a list of at most {MAX_STEPS} steps')
    window, last, shots, fingerprinted = args['duration_s'] * 1000, 0, 0, set()
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get('action') not in STEP_ACTIONS:
            raise DeviceError(f'steps[{index}]: action must be one of ' + ', '.join(sorted(STEP_ACTIONS)))
        action = step['action']
        if 'at_ms' in step and 'after_ms' in step:
            raise DeviceError(f'steps[{index}]: use at_ms (fixed offset from the start) or after_ms '
                              '(delay after the previous step), not both')
        for key in ('at_ms', 'after_ms'):
            if key in step and (type(step[key]) is not int or not 0 <= step[key] < window):
                raise DeviceError(f'steps[{index}]: {key} must be an integer from 0 to {window - 1} (inside duration_s)')
        if 'at_ms' in step:
            if step['at_ms'] < last:
                raise DeviceError(f'steps[{index}]: at_ms steps must be in at_ms order')
            last = step['at_ms']
        rest = {k: v for k, v in step.items() if k not in ('action', 'at_ms', 'after_ms')}
        if 'ref' in rest:
            raise DeviceError(f'steps[{index}]: refs cannot be used in a scenario (the screen changes); '
                              'use match or x/y')
        try:
            if action == 'screenshot':
                shots += 1
                if set(rest) - {'name', 'fingerprint', 'region'} or ('name' in rest and not (
                        isinstance(rest['name'], str) and SHOT_NAME.fullmatch(rest['name']))):
                    raise DeviceError('takes only name (letters, digits, - and _), fingerprint, region')
                if 'fingerprint' in rest and type(rest['fingerprint']) is not bool:
                    raise DeviceError('fingerprint must be true or false')
                if 'region' in rest:
                    _validate_region(rest['region'], 'region')
                if rest.get('fingerprint') or 'region' in rest:
                    if 'name' not in rest:
                        raise DeviceError('a fingerprinted screenshot needs a name (expect.screens refers to it)')
                    fingerprinted.add(rest['name'])
                if shots > MAX_SCENARIO_SHOTS:
                    raise DeviceError(f'at most {MAX_SCENARIO_SHOTS} screenshot steps per scenario')
            elif action in ('launch_app', 'stop_app'):
                allowed = LAUNCH_STEP if action == 'launch_app' else {'app_id'}
                if set(rest) - allowed:
                    raise DeviceError('takes only ' + ', '.join(sorted(allowed)))
                target = rest.get('app_id', args.get('app_id'))
                if not isinstance(target, str) or not APP_ID.fullmatch(target):
                    raise DeviceError('needs app_id (on the step or on the scenario)')
                if 'restart' in rest and type(rest['restart']) is not bool:
                    raise DeviceError('restart must be true or false')
                if action == 'launch_app':  # activity / extras: the same rules as the launch op
                    _validate('launch', {'app_id': target, **{k: v for k, v in rest.items() if k in LAUNCH}})
            else:
                _validate(action, rest)
        except DeviceError as exc:
            raise DeviceError(f'steps[{index}] ({action}): {exc}') from None
        if rest.get('timeout_s', 0) > MAX_STEP_WAIT:
            raise DeviceError(f'steps[{index}]: timeout_s is at most {MAX_STEP_WAIT} inside a scenario')
    if 'preflight' in args:
        _validate_preflight(args['preflight'])
    if 'expect' in args:
        _validate_expect(args, fingerprinted)


def _validate_region(region, name):
    if (not isinstance(region, list) or len(region) != 4 or not all(type(v) is int and 0 <= v <= 10000 for v in region)
            or region[2] <= region[0] or region[3] <= region[1]):
        raise DeviceError(f'{name} must be [x1, y1, x2, y2] in screen pixels')


def _validate_preflight(checks):
    """HTTP checks the runner makes before the scenario (same rules as the runner's need_preflight)."""
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_PREFLIGHT:
        raise DeviceError(f'preflight must be a list of 1-{MAX_PREFLIGHT} checks')
    for index, check in enumerate(checks, 1):
        where = f'preflight[{index}]'
        if not isinstance(check, dict) or not {'url'} <= set(check) <= PREFLIGHT_KEYS:
            raise DeviceError(f'{where}: needs url, may take ' + ', '.join(sorted(PREFLIGHT_KEYS - {'url'})))
        url = check['url']
        if (not isinstance(url, str) or len(url) > 2000 or any(c.isspace() for c in url)
                or not re.match(r'https?://[^/?#]', url)):
            raise DeviceError(f'{where}: url must be an http(s) URL')
        method = check.get('method', 'GET')
        if method not in ('GET', 'POST'):
            raise DeviceError(f'{where}: method must be GET or POST')
        headers = check.get('headers', {})
        if (not isinstance(headers, dict) or len(headers) > MAX_EXTRAS or not all(
                isinstance(k, str) and HEADER_NAME.fullmatch(k) and isinstance(v, str) and len(v) <= 2000
                and not set(v) & set('\r\n\x00') for k, v in headers.items())):
            raise DeviceError(f'{where}: headers must be an object of at most {MAX_EXTRAS} name: value strings')
        body = check.get('body')
        if body is not None and (method != 'POST' or not isinstance(body, (str, dict))
                                 or len(body if isinstance(body, str) else json.dumps(body)) > MAX_PREFLIGHT_BODY):
            raise DeviceError(f'{where}: body needs method POST and is text or a JSON object '
                              f'(max {MAX_PREFLIGHT_BODY} chars)')
        contains = check.get('contains', [])
        if (not isinstance(contains, list) or len(contains) > 8
                or not all(isinstance(text, str) and 0 < len(text) <= 200 for text in contains)):
            raise DeviceError(f'{where}: contains must be a list of at most 8 strings')
        for key, low, high in (('status', 100, 599), ('timeout_s', 1, 20)):
            if key in check and (type(check[key]) is not int or not low <= check[key] <= high):
                raise DeviceError(f'{where}: {key} must be an integer between {low} and {high}')
        if 'name' in check and not (isinstance(check['name'], str) and 0 < len(check['name']) <= 60):
            raise DeviceError(f'{where}: name must be text (max 60 chars)')


def _validate_expect(args, fingerprinted=frozenset()):
    expect = args['expect']
    if not isinstance(expect, dict) or not set(expect) <= EXPECT_KEYS:
        raise DeviceError('expect may contain: ' + ', '.join(sorted(EXPECT_KEYS)))
    for key in ('steps_ok', 'app_running'):
        if key in expect and type(expect[key]) is not bool:
            raise DeviceError(f'expect.{key} must be true or false')
    if 'app_running' in expect and 'app_id' not in args:
        raise DeviceError('expect.app_running needs app_id')
    if 'settled_by_ms' in expect:
        value = expect['settled_by_ms']
        if type(value) is not int or not 0 <= value <= MAX_SCENARIO_SECONDS * 1000:
            raise DeviceError('expect.settled_by_ms must be an integer number of milliseconds')
        if not args.get('sample_ms'):
            raise DeviceError('expect.settled_by_ms needs sample_ms (the screen-change timeline)')
    if 'max_drift_ms' in expect:
        value = expect['max_drift_ms']
        if type(value) is not int or not 0 <= value <= MAX_SCENARIO_SECONDS * 1000:
            raise DeviceError('expect.max_drift_ms must be an integer number of milliseconds')
        if not any(isinstance(step, dict) and 'at_ms' in step for step in args.get('steps', [])):
            raise DeviceError('expect.max_drift_ms needs at least one at_ms step (drift is how late such a step ran)')
    rules, order = expect.get('logs', []), expect.get('log_order', [])
    if (rules or order) and 'log_tags' not in args:
        raise DeviceError('expect.logs / expect.log_order need log_tags (which log lines to capture)')
    if not isinstance(rules, list) or len(rules) > MAX_LOG_EXPECTS:
        raise DeviceError(f'expect.logs must be a list of at most {MAX_LOG_EXPECTS} rules')
    for index, rule in enumerate(rules, 1):
        allowed = {'match', 'number_after'} | set(LOG_EXPECT_INTS) | set(LOG_EXPECT_NUMBERS)
        if (not isinstance(rule, dict) or not {'match'} <= set(rule) <= allowed
                or not isinstance(rule['match'], str) or not 0 < len(rule['match']) <= 200
                or any(type(rule[k]) is not int or not 0 <= rule[k] <= high
                       for k, high in LOG_EXPECT_INTS.items() if k in rule)):
            raise DeviceError(f'expect.logs[{index}]: needs match (text), may take min, max, by_ms, after_ms (integers), '
                              'number_after (text) with ' + ', '.join(LOG_EXPECT_NUMBERS) + ' (numbers)')
        marker = rule.get('number_after')
        if marker is not None and not (isinstance(marker, str) and 0 < len(marker) <= 100):
            raise DeviceError(f'expect.logs[{index}]: number_after is the text right before the number (max 100 chars)')
        for key in LOG_EXPECT_NUMBERS:
            if key in rule and (marker is None or type(rule[key]) not in (int, float)
                                or not -MAX_VALUE <= rule[key] <= MAX_VALUE):
                raise DeviceError(f'expect.logs[{index}]: {key} must be a number and needs number_after')
    if (not isinstance(order, list) or len(order) > MAX_LOG_EXPECTS
            or not all(isinstance(m, str) and 0 < len(m) <= 200 for m in order)):
        raise DeviceError(f'expect.log_order must be a list of at most {MAX_LOG_EXPECTS} strings')
    screens = expect.get('screens', [])
    if not isinstance(screens, list) or len(screens) > MAX_SCENARIO_SHOTS:
        raise DeviceError(f'expect.screens must be a list of at most {MAX_SCENARIO_SHOTS} rules')
    for index, rule in enumerate(screens, 1):
        where = f'expect.screens[{index}]'
        if not isinstance(rule, dict) or not {'shot'} <= set(rule) <= {'shot', 'like', 'unlike', 'max_diff'}:
            raise DeviceError(f'{where}: needs shot, may take like, unlike, max_diff')
        if rule['shot'] not in fingerprinted:
            raise DeviceError(f'{where}: no screenshot step named {rule["shot"]!r} with fingerprint: true')
        if not {'like', 'unlike'} & set(rule):
            raise DeviceError(f'{where}: needs like (a known-good fingerprint) and/or unlike (a known-bad one)')
        for key in ('like', 'unlike'):
            if key in rule and not (isinstance(rule[key], str) and FINGERPRINT.fullmatch(rule[key])):
                raise DeviceError(f'{where}: {key} must be a fingerprint returned by a screenshot step')
        if 'max_diff' in rule and (type(rule['max_diff']) not in (int, float) or not 0 <= rule['max_diff'] <= 1):
            raise DeviceError(f'{where}: max_diff is a fraction from 0 to 1')


def _setup_plan(name, steps):
    """suite setup / teardown: [{action: op, ...op args}] run once around the cases."""
    if not isinstance(steps, list) or len(steps) > MAX_SETUP_STEPS:
        raise DeviceError(f'{name} must be a list of at most {MAX_SETUP_STEPS} steps')
    plan = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get('action') not in SUITE_SETUP_OPS:
            raise DeviceError(f'{name}[{index}]: action must be one of ' + ', '.join(sorted(SUITE_SETUP_OPS)))
        op, args = step['action'], {k: v for k, v in step.items() if k != 'action'}
        try:
            _validate(op, args)
        except DeviceError as exc:
            raise DeviceError(f'{name}[{index}] ({op}): {exc}') from None
        plan.append((op, args))
    return plan


def suite_plan(args, platform=None):
    """Validated suite: ([(name, scenario args)], reset, stop_on_fail, keep_video).

    Each case is `defaults` overlaid with the case (expect is merged key by key), and must carry
    an expect: a suite is a regression run, so every case has to decide pass/fail on its own.
    """
    if not isinstance(args, dict) or not {'cases'} <= set(args) <= SUITE_KEYS:
        raise DeviceError('suite needs cases, may take ' + ', '.join(sorted(SUITE_KEYS - {'cases'})))
    defaults, cases = args.get('defaults', {}), args['cases']
    if not isinstance(defaults, dict) or 'name' in defaults or 'steps' in defaults:
        raise DeviceError('defaults must be an object of scenario fields shared by the cases (not name or steps)')
    by_platform = args.get('platform_defaults', {})
    if (not isinstance(by_platform, dict) or not set(by_platform) <= {'android', 'ios'}
            or not all(isinstance(v, dict) and not {'name', 'steps'} & set(v) for v in by_platform.values())):
        raise DeviceError('platform_defaults must be {android: {...}, ios: {...}} with scenario fields (not name or steps)')
    if platform is not None:
        defaults = {**defaults, **by_platform.get(platform, {})}
    reset, keep_video = args.get('reset', 'none'), args.get('keep_video', 'failed')
    if reset not in ('none', 'reset_app'):
        raise DeviceError('reset must be none or reset_app (clear the app data before each case)')
    retries = args.get('retries', 0)
    if type(retries) is not int or not 0 <= retries <= MAX_RETRIES:
        raise DeviceError(f'retries must be an integer from 0 to {MAX_RETRIES}')
    keep_lines = args.get('keep_log_lines', 0)
    if type(keep_lines) is not int or not 0 <= keep_lines <= MAX_KEEP_LOG_LINES:
        raise DeviceError(f'keep_log_lines must be an integer from 0 to {MAX_KEEP_LOG_LINES} (matched log lines kept '
                          'per case, also for cases that passed)')
    if type(args.get('health_check', True)) is not bool:
        raise DeviceError('health_check must be true or false')
    if keep_video not in ('failed', 'all'):
        raise DeviceError('keep_video must be failed (default) or all')
    if type(args.get('stop_on_fail', False)) is not bool:
        raise DeviceError('stop_on_fail must be true or false')
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_SUITE_CASES:
        raise DeviceError(f'cases must be a list of 1-{MAX_SUITE_CASES} cases')
    _setup_plan('setup', args.get('setup', []))
    _setup_plan('teardown', args.get('teardown', []))
    plan, seconds, names = [], 0, set()
    for index, case in enumerate(cases, 1):
        name = case.get('name') if isinstance(case, dict) else None
        if not isinstance(name, str) or not CASE_NAME.fullmatch(name):
            raise DeviceError(f'cases[{index}]: needs a name (letters, digits, . - _; max 60 chars)')
        if name in names:
            raise DeviceError(f'cases[{index}]: the name {name} is used twice')
        names.add(name)
        only = case.get('only')
        if only is not None and (not isinstance(only, list) or not only or not set(only) <= {'android', 'ios'}):
            raise DeviceError(f'cases[{index}] ({name}): only must be a list of platforms (android, ios)')
        if only is not None and platform is not None and platform not in only:
            continue  # this case does not apply to this device's platform
        spec = {**defaults, **{k: v for k, v in case.items() if k not in ('name', 'only')}}
        if isinstance(defaults.get('expect'), dict) and isinstance(case.get('expect'), dict):
            spec['expect'] = {**defaults['expect'], **case['expect']}
        try:
            if 'expect' not in spec:
                raise DeviceError('needs expect (every suite case must decide pass/fail)')
            if reset == 'reset_app' and 'app_id' not in spec:
                raise DeviceError('reset: reset_app needs app_id')
            _validate('scenario', spec)
        except DeviceError as exc:
            raise DeviceError(f'cases[{index}] ({name}): {exc}') from None
        seconds += spec['duration_s'] * (1 + retries)
        plan.append((name, spec))
    if seconds > MAX_SUITE_SECONDS:
        raise DeviceError(f'The cases add up to {seconds} s of duration_s (retries included); a suite is at most '
                          f'{MAX_SUITE_SECONDS} s (use end_after_steps and smaller duration_s, fewer retries, '
                          'or split the suite)')
    return plan, reset, args.get('stop_on_fail', False), keep_video


def suite_options(args):
    """(setup, teardown, retries, health_check); suite_plan validated the rest."""
    return (_setup_plan('setup', args.get('setup', [])), _setup_plan('teardown', args.get('teardown', [])),
            args.get('retries', 0), args.get('health_check', True))


def _case_row(name, data, keep_video, budget, keep_lines=0):
    """One suite row: the verdict and, for a case that did not pass, what is needed to see why.
    keep_lines: the last N matched log lines, for every case (evidence for a pass, not only for a failure)."""
    verdict = data.get('verdict', 'fail')
    row = {'name': name, 'verdict': verdict}
    if data.get('attempts', 1) > 1:
        row['attempts'] = data['attempts']
    row.update({k: data[k] for k in ('failed', 'ran_ms', 'max_drift_ms', 'app_running', 'video_error', 'media_error',
                                     'device_restarted', 'runner_reconnected') if k in data})
    if keep_lines and (data.get('logs') or {}).get('lines'):
        row['log_lines'] = data['logs']['lines'][-keep_lines:]
    if verdict != 'pass':
        row.update({k: data[k] for k in ('preflight', 'launch') if k in data})
        bad = [step for step in data.get('steps') or [] if not step.get('ok')]
        if bad:
            row['steps_not_ok'] = bad[:10]
        logs = data.get('logs') or {}
        if logs:
            row['logs'] = {k: logs[k] for k in ('counts', 'first_ms', 'values') if k in logs}
    media = [('mp4', data['mp4'])] if 'mp4' in data and (keep_video == 'all' or verdict not in ('pass',)) else []
    shots = data.get('screenshots') or []
    size = sum(len(blob) for _, blob in media) + sum(len(shot['png']) for shot in shots)
    if size > budget:
        row['media_dropped'] = 'the suite media budget is used up; run this case alone with scenario for its video'
        return row, 0
    row.update(dict(media), **({'screenshots': shots} if shots else {}))
    return row, size


def suite_summary(rows, not_run, reason=None):
    """Counts per verdict, the overall verdict and a markdown table (ready for a PR or a report).

    not_run: names of cases that never started; reason says why (stop_on_fail, the suite deadline,
    a failed setup or health check), so a half-finished matrix is visible as such in the table.
    """
    counts = {}
    for row in rows:
        counts[row['verdict']] = counts.get(row['verdict'], 0) + 1
    lines = ['| Case | Verdict | Ran (ms) | First reason |', '|---|---|---|---|']
    for row in rows:
        first = (row.get('failed') or [''])[0]
        if row['verdict'] == 'flaky':
            first = f"passed on attempt {row.get('attempts')}; " + first
        first = first.replace('|', '/').replace('\n', ' ')[:140]
        lines.append(f"| {row['name']} | {row['verdict']} | {row.get('ran_ms', '')} | {first} |")
    why = (reason or '').replace('|', '/').replace('\n', ' ')[:140]
    lines += [f'| {name} | not run | | {why} |' for name in not_run]
    passed = counts.get('pass', 0)
    # pass only when every case passed; otherwise the worst outcome: fail, error, blocked, then flaky
    # (passed only on a retry). The counts stay nested: a top-level 'error' key is read as a broker
    # denial by the isolated worker.
    if rows and passed == len(rows) and not not_run:
        verdict = 'pass'
    elif not_run and not rows:
        verdict = 'blocked' if reason else 'fail'
    else:
        verdict = next((v for v in ('fail', 'error', 'blocked', 'flaky') if counts.get(v)), 'fail')
    return {'verdict': verdict, 'total': len(rows) + len(not_run), 'passed': passed,
            'counts': dict(sorted(counts.items())),
            **({'not_run': not_run} if not_run else {}), **({'not_run_reason': reason} if not_run and reason else {}),
            'table': '\n'.join(lines)}


def suite_junit(rows, not_run, reason=None, suite_name='loma-device-suite'):
    """JUnit XML of a suite result, for CI dashboards and PR checks."""
    root = ET.Element('testsuite', name=suite_name, tests=str(len(rows) + len(not_run)),
                      failures=str(sum(1 for r in rows if r['verdict'] == 'fail')),
                      errors=str(sum(1 for r in rows if r['verdict'] in ('error', 'blocked'))),
                      skipped=str(len(not_run)))
    for row in rows:
        case = ET.SubElement(root, 'testcase', name=row['name'], classname=suite_name,
                             time=f"{row.get('ran_ms', 0) / 1000:.3f}")
        message = '; '.join(row.get('failed') or [])[:2000]
        if row['verdict'] == 'fail':
            ET.SubElement(case, 'failure', message=message[:500]).text = message
        elif row['verdict'] in ('error', 'blocked'):
            ET.SubElement(case, 'error', message=f"{row['verdict']}: {message[:500]}").text = message
        elif row['verdict'] == 'flaky':
            ET.SubElement(case, 'system-out').text = f"flaky: passed on attempt {row.get('attempts')}. {message}"
    for name in not_run:
        ET.SubElement(ET.SubElement(root, 'testcase', name=name, classname=suite_name), 'skipped',
                      message=reason or 'not run')
    return ET.tostring(root, encoding='unicode')


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
    if isinstance(value, (bytes, bytearray)):  # uploaded out of band (runner >= 1.5.0), resolved by the hub
        return bytes(value)
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
                    # ok | recovering (the runner is restarting it) | down (crashed/closed, restart failed or not tried)
                    'state': device.get('state', 'ok') if conn is not None else 'offline',
                    **({'error': str(device['error'])[:300]} if device.get('error') else {}),
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

    async def lease(self, user_email, scope, device_id=None, platform=None, recover=False, cold=False):
        """recover=True: restart the (crashed / hung) device after leasing it and wait for it."""
        self._check_scope(scope)
        if recover and device_id is None:
            raise DeviceError('recover needs a device_id')
        if device_id is not None:
            runner, serial = await self._resolve(user_email, device_id)
            if self.hub.get(runner['runner_id']) is None:
                raise DeviceError('That device\'s runner is offline')
            candidates = [store.device_id(runner['runner_id'], serial)]
        else:
            if platform not in (None, 'android', 'ios'):
                raise DeviceError('platform must be android or ios')
            devices = await self.list_devices(user_email)
            matching = [d for d in devices if d['online'] and (platform is None or d['platform'] == platform)]
            # Running devices first; a down/recovering one is still a candidate (the lease then restarts it).
            candidates = [d['device_id'] for d in sorted(matching, key=lambda d: d.get('state', 'ok') != 'ok')]
            if not candidates:
                raise DeviceError(self._no_device_message(devices, platform))
        busy = []
        for candidate in candidates:
            lease = await self._acquire(candidate, user_email, scope)
            if lease is not None:
                await self._audit(user_email, scope, candidate, 'lease', True)
                result = {'device_id': candidate, 'expires_at': store.aware(lease['expires_at']).isoformat(),
                          'note': 'Lease renews on every call and expires after 15 idle minutes. Release it when done.'}
                health = None if recover else await self.health(user_email, scope, candidate)
                if recover or (health is not None and not health.get('ok')):
                    recovery = await self.heal(user_email, scope, candidate, cold)
                    if recovery is not None:
                        result['recovery'] = recovery
                        if recovery.get('recovered'):
                            health = recovery.get('health') or await self.health(user_email, scope, candidate)
                installed = await self.installed(user_email, scope, candidate)
                if installed is not None:
                    result['installed'] = installed
                if health is not None:
                    result['health'] = health
                    if not health.get('ok'):
                        result['note'] += (' This device is too slow or unresponsive to test on right now (see '
                                           'health.reasons and recovery): lease another device, or ask the user to '
                                           'check the runner machine.')
                return result
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

    async def release_all(self, user_email, scope):
        """Release every device this user's scope (one conversation) holds; used when a run ends."""
        self._check_scope(scope)
        held = await self.db.device_leases.find({'owner_email': user_email, 'scope': scope}).to_list(length=50)
        released = []
        for lease in held:
            result = await self.db.device_leases.delete_one({'_id': lease['_id'], 'owner_email': user_email,
                                                             'scope': scope})
            if result.deleted_count:
                released.append(lease['_id'])
                await self._audit(user_email, scope, lease['_id'], 'release', True)
        return {'released': released}

    async def health(self, user_email, scope, device_id):
        """The runner's speed check, or None when the runner is too old or the check could not run."""
        try:
            return await self.call(user_email, scope, device_id, 'health', {})
        except DeviceError as exc:
            if 'too old' in str(exc) or 'Unsupported' in str(exc):
                return None
            return {'ok': False, 'reasons': [str(exc)[:300]]}

    async def installed(self, user_email, scope, device_id):
        """Apps on the device and the build checksum of those the runner installed (None on older runners)."""
        try:
            return await self.call(user_email, scope, device_id, 'installed', {})
        except DeviceError as exc:
            if 'too old' in str(exc) or 'Unsupported' in str(exc):
                return None
            return {'error': str(exc)[:200]}

    async def heal(self, user_email, scope, device_id, cold=False):
        """Ask the runner to restart a crashed / hung emulator or simulator and wait for it. None when the
        runner is too old to do it; otherwise the runner's result, or {'recovered': False, 'error'}."""
        try:
            return await self.call(user_email, scope, device_id, 'recover', {'cold': True} if cold else {})
        except DeviceError as exc:
            if 'too old' in str(exc) or 'Unsupported' in str(exc):
                return None
            return {'recovered': False, 'error': str(exc)[:500]}

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

    async def suite(self, user_email, scope, device_id, args, platform=None):
        """Run several scenario cases one after the other and return one summary.

        Each case is an ordinary scenario call (same validation, lease, audit); a case that cannot
        run (runner error, timeout) is reported as verdict 'error' and the suite carries on.
        Before the cases: a device health check (a slow device is reported as blocked, not as failed
        tests) and the setup ops; after them, the teardown ops (best effort). A case that fails or
        errors is re-run up to `retries` times; one that passes on a retry is reported as flaky.
        """
        self._check_scope(scope)
        if platform is None:
            platform = await self._platform(user_email, device_id)
        plan, reset, stop_on_fail, keep_video = suite_plan(args, platform)
        setup, teardown, retries, health_check = suite_options(args)
        names = [name for name, _ in plan]
        extra = {**({'platform': platform} if platform else {})}
        if not plan:  # every case is `only` for another platform
            return {**self._suite_result([], [], None, extra), 'verdict': 'pass',
                    'note': f'No case applies to {platform}'}
        recoveries = []
        if health_check:
            health = await self.health(user_email, scope, device_id)
            if health is not None and not health.get('ok'):  # crashed, hung or slow: restart it once first
                recovery = await self.heal(user_email, scope, device_id)
                if recovery is not None:
                    recoveries.append({'before': 'cases', **recovery})
                    if recovery.get('recovered'):
                        health = recovery.get('health') or await self.health(user_email, scope, device_id)
            if health is not None:
                extra['health'] = health
                if not health.get('ok'):
                    reason = 'device_slow: ' + '; '.join(health.get('reasons') or ['health check failed'])
                    if recoveries and recoveries[-1].get('error'):
                        reason += f"; restart failed: {recoveries[-1].get('error', '')[:200]}"
                    extra['recoveries'] = recoveries
                    return self._suite_result([], names, reason, extra)
        if recoveries:
            extra['recoveries'] = recoveries
        for index, (op, op_args) in enumerate(setup, 1):
            try:
                await self.call(user_email, scope, device_id, op, dict(op_args))
            except DeviceError as exc:
                return self._suite_result([], names, f'setup[{index}] ({op}) failed: {str(exc)[:200]}', extra)
        loop = asyncio.get_running_loop()
        deadline, rows, budget, reason = loop.time() + SUITE_DEADLINE_S, [], MAX_SUITE_MEDIA, None
        reconnects = 0
        try:
            for name, spec in plan:
                if loop.time() > deadline:
                    reason = f'the suite ran past its {SUITE_DEADLINE_S} s deadline; split it'
                    break
                data, attempts, healed, resumed = None, 0, False, False
                while True:
                    attempts += 1
                    try:
                        if reset == 'reset_app':
                            await self.call(user_email, scope, device_id, 'reset_app', {'app_id': spec['app_id']})
                        data = await self.call(user_email, scope, device_id, 'scenario', dict(spec))
                    except DeviceError as exc:
                        data = {'verdict': 'error', 'failed': [str(exc)[:300]]}
                        # The runner's connection dropped: wait for it to come back and re-run this case.
                        if (RUNNER_GONE.search(str(exc)) and not resumed and reconnects < MAX_SUITE_RECONNECTS
                                and loop.time() < deadline):
                            reconnects += 1
                            back = await self._await_runner(user_email, device_id, RUNNER_RETURN_S)
                            extra.setdefault('reconnects', []).append({'before': name, 'runner_back': back})
                            if back:
                                resumed = True
                                attempts -= 1
                                continue
                        # The device died under this case: restart it and re-run the case (not a retry).
                        if (DEVICE_GONE.search(str(exc)) and not healed and len(recoveries) < MAX_SUITE_RECOVERIES
                                and loop.time() < deadline):
                            recovery = await self.heal(user_email, scope, device_id)
                            if recovery is not None:
                                recoveries.append({'before': name, **recovery})
                                extra['recoveries'] = recoveries
                                if recovery.get('recovered'):
                                    healed = True
                                    attempts -= 1
                                    continue
                    if data.get('verdict') not in ('fail', 'error') or attempts > retries or loop.time() > deadline:
                        break
                if healed:
                    data = {**data, 'device_restarted': True}
                if resumed:
                    data = {**data, 'runner_reconnected': True}
                if attempts > 1:
                    data = {**data, 'attempts': attempts}
                    if data.get('verdict') == 'pass':
                        data['verdict'] = 'flaky'
                row, used = _case_row(name, data, keep_video, budget, args.get('keep_log_lines', 0))
                budget -= used
                rows.append(row)
                if stop_on_fail and row['verdict'] not in ('pass', 'flaky'):
                    reason = 'stop_on_fail: an earlier case did not pass'
                    break
        finally:
            for op, op_args in teardown:  # best effort: restore the device even when a case raised
                try:
                    await self.call(user_email, scope, device_id, op, dict(op_args))
                except DeviceError:
                    pass
        return self._suite_result(rows, names[len(rows):], reason, extra)

    async def _await_runner(self, user_email, device_id, timeout):
        """True once the device's runner is connected again (polls the hub; no device call is made)."""
        try:
            runner, _ = await self._resolve(user_email, device_id)
        except DeviceError:
            return False
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if self.hub.get(runner['runner_id']) is not None:
                await asyncio.sleep(1)  # let the new connection send its device list
                return True
            await asyncio.sleep(2)
        return False

    @staticmethod
    def _suite_result(rows, not_run, reason, extra):
        return {**suite_summary(rows, not_run, reason if not_run else None), **extra, 'cases': rows,
                'junit': suite_junit(rows, not_run, reason)}

    async def _platform(self, user_email, device_id):
        """android / ios for platform_defaults and only, from the runner's device list (None if unknown)."""
        try:
            runner, serial = await self._resolve(user_email, device_id)
        except DeviceError:
            return None
        conn = self.hub.get(runner['runner_id'])
        listed = conn.devices if conn is not None else (runner.get('devices') or [])
        return next((d.get('platform') for d in listed if d.get('serial') == serial), None)

    async def matrix(self, user_email, scope, device_ids, args):
        """The same suite on several devices at once (one per platform/OS), one combined table."""
        self._check_scope(scope)
        if (not isinstance(device_ids, list) or not 2 <= len(device_ids) <= MAX_MATRIX_DEVICES
                or len(set(device_ids)) != len(device_ids) or not all(isinstance(d, str) for d in device_ids)):
            raise DeviceError(f'device_ids must be a list of 2-{MAX_MATRIX_DEVICES} different devices')
        suite_plan(args)  # fail fast on a bad spec, before any device is touched

        async def one(device_id):
            try:
                return await self.suite(user_email, scope, device_id, args)
            except DeviceError as exc:
                return {'verdict': 'error', 'counts': {'error': 1}, 'cases': [], 'failed': [str(exc)[:300]]}
        results = await asyncio.gather(*(one(device_id) for device_id in device_ids))
        lines = ['| Device | Platform | Verdict | Passed | Counts |', '|---|---|---|---|---|']
        for device_id, result in zip(device_ids, results):
            counts = ', '.join(f'{k} {v}' for k, v in sorted((result.get('counts') or {}).items()))
            lines.append(f"| {device_id} | {result.get('platform', '')} | {result['verdict']} | "
                         f"{result.get('passed', 0)}/{result.get('total', 0)} | {counts} |")
        verdicts = [result['verdict'] for result in results]
        verdict = 'pass' if all(v == 'pass' for v in verdicts) else next(
            (v for v in ('fail', 'error', 'blocked', 'flaky') if v in verdicts), 'fail')
        return {'verdict': verdict, 'table': '\n'.join(lines),
                'devices': [{'device_id': device_id, **result} for device_id, result in zip(device_ids, results)]}

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
        elif op == 'scenario':
            if 'mp4_base64' in data:
                data['mp4'] = _decode(data.pop('mp4_base64'), 'recording')
            if data.get('screenshots'):
                data['screenshots'] = [{'name': shot.get('name'), 'at_ms': shot.get('at_ms'),
                                        'png': _decode(shot.get('png_base64'), 'scenario screenshot')}
                                       for shot in data['screenshots']]
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
