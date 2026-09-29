"""The single policy layer for device access.

Every caller (isolated tool gateway, legacy CLI via /internal, dashboard) goes
through DeviceService, so ACLs, leases, argument validation and audit are
enforced once. Arguments are validated here before they reach a runner, and
the runner validates them again.
"""
import base64
import re
import time
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from devices import store
from devices.builds import blobs as default_blobs, ARTIFACT_NAME, REPO
from devices.hub import DeviceError, hub as default_hub

LEASE_TTL = timedelta(minutes=15)
APP_ID = re.compile(r'[A-Za-z0-9_]+(?:[.-][A-Za-z0-9_]+)*\Z')
KEYS = {'back', 'home', 'enter', 'delete', 'tab', 'app_switch', 'volume_up', 'volume_down', 'power',
        'lock', 'siri', 'side', 'apple_pay'}
SCOPE = re.compile(r'[A-Za-z0-9:_.@-]{1,200}\Z')

# op: (required, optional). Values are type-checked by _validate.
OPS = {
    'install': (set(), {'app_id', 'build', 'upload_id'}),
    'uninstall': ({'app_id'}, set()),
    'launch': ({'app_id'}, set()),
    'stop': ({'app_id'}, set()),
    'reset_app': ({'app_id'}, set()),
    'open_url': ({'url'}, set()),
    'screenshot': (set(), set()),
    'ui_tree': (set(), set()),
    'tap': ({'x', 'y'}, set()),
    'swipe': ({'x1', 'y1', 'x2', 'y2'}, {'duration_ms'}),
    'type': ({'text'}, set()),
    'key': ({'key'}, set()),
    'logs': (set(), {'lines', 'filter', 'clear'}),
    'run_flow': ({'flow'}, set()),
}
INTS = {'x': (0, 10000), 'y': (0, 10000), 'x1': (0, 10000), 'y1': (0, 10000), 'x2': (0, 10000),
        'y2': (0, 10000), 'duration_ms': (50, 5000), 'lines': (1, 2000)}
STRS = {'url': 2000, 'text': 500, 'filter': 200, 'flow': 64 * 1024, 'key': 32, 'app_id': 255, 'upload_id': 64}


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
        elif name == 'clear':
            if type(value) is not bool:
                raise DeviceError('clear must be true or false')
        elif name == 'build':
            _validate_build(value)
        elif name in STRS:
            if not isinstance(value, str) or not value or len(value) > STRS[name] or '\x00' in value:
                raise DeviceError(f'Invalid {name}')
    if 'app_id' in args and not APP_ID.fullmatch(args['app_id']):
        raise DeviceError('Invalid app_id (expected a package name / bundle id)')
    if op == 'key' and args['key'] not in KEYS:
        raise DeviceError('Unsupported key: ' + ', '.join(sorted(KEYS)))
    if op == 'open_url':
        url = args['url']
        if any(c.isspace() for c in url) or any(c in url for c in '"\'`\\') or not re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', url):
            raise DeviceError('Invalid url (needs a scheme, no spaces or quotes)')
    if op == 'install' and ('build' in args) == ('upload_id' in args):
        raise DeviceError('install needs exactly one of build or upload_id')


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

    async def _acquire(self, device_id, user_email, scope):
        # Two attempts: concurrent first acquires by the SAME holder race on the
        # upsert insert; the retry then matches the winner's document.
        for _ in range(2):
            at = store.now()
            try:
                return await self.db.device_leases.find_one_and_update(
                    {'_id': device_id, '$or': [{'expires_at': {'$lt': at}},
                                               {'owner_email': user_email, 'scope': scope}]},
                    {'$set': {'owner_email': user_email, 'scope': scope, 'expires_at': at + LEASE_TTL},
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
            return ('No devices are registered for you. Enroll a machine in Loma → Devices and run the '
                    'Loma Device Runner there.')
        kind = f'{platform} ' if platform else ''
        return f'No online {kind}devices. Is the runner machine awake and the emulator/simulator booted?'

    async def release(self, user_email, scope, device_id):
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
        args = dict(args or {})
        _validate(op, args)
        runner, serial = await self._resolve(user_email, device_id)
        lease = await self._acquire(device_id, user_email, scope)
        if lease is None:
            raise DeviceError('Device is leased by another session. Pick another device or wait for it to be released.')
        started = time.monotonic()
        try:
            build_meta = None
            if op == 'install':
                args, build_meta = await self._prepare_install(user_email, runner['runner_id'], args)
            data = await self.hub.call(runner['runner_id'], op, serial, args)
            if build_meta:
                data['build'] = build_meta
        except DeviceError as exc:
            await self._audit(user_email, scope, device_id, op, False, str(exc), started)
            raise
        await self._audit(user_email, scope, device_id, op, True, None, started)
        if op == 'screenshot':
            try:
                data = {'png': base64.b64decode(data.pop('png_base64'), validate=True), **data}
            except (KeyError, ValueError):
                raise DeviceError('Runner returned an invalid screenshot') from None
        return data

    async def _prepare_install(self, user_email, runner_id, args):
        app_id = args.get('app_id')
        if 'build' in args:
            build = args['build']
            blob_id, blob = await self.blobs.from_github(user_email, build['repo'], build['artifact_name'],
                                                         pr=build.get('pr'), run_id=build.get('run_id'))
        else:
            blob_id = args['upload_id']
            blob = self.blobs.get(blob_id, owner=user_email)
            if blob is None:
                raise DeviceError('Upload not found or expired; upload the build again')
        self.blobs.bind(blob_id, runner_id)
        runner_args = {'blob_id': blob_id, 'sha256': blob['sha256'], 'filename': blob['filename']}
        if app_id:
            runner_args['app_id'] = app_id
        return runner_args, dict(blob.get('meta') or {}, sha256=blob['sha256'], size=blob['size'])

    async def _audit(self, user_email, scope, device_id, op, ok, error=None, started=None):
        runner_id = device_id.split('/', 1)[0] if isinstance(device_id, str) else None
        entry = {'at': store.now(), 'runner_id': runner_id, 'device_id': device_id, 'actor': user_email,
                 'scope': scope, 'op': op, 'ok': ok}
        if error:
            entry['error'] = error[:500]
        if started is not None:
            entry['duration_ms'] = int((time.monotonic() - started) * 1000)
        await self.db.device_audit.insert_one(entry)
