"""Mongo persistence for device runners. Secrets and tokens are stored only as SHA-256 hashes."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

ENROLL_PREFIX = 'lde_'
SECRET_PREFIX = 'ldr_'
ENROLL_TTL = timedelta(minutes=30)
RUNNER_ID = re.compile(r'r_[a-f0-9]{16}\Z')
SERIAL = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')
MAX_RUNNERS_PER_USER = 10


def now():
    return datetime.now(timezone.utc)


def aware(value):
    """Mongo returns naive UTC datetimes; normalise before comparing."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def matches(value, expected_hash):
    return isinstance(value, str) and isinstance(expected_hash, str) and hmac.compare_digest(digest(value), expected_hash)


def device_id(runner_id, serial):
    return f'{runner_id}/{serial}'


def split_device_id(value):
    if not isinstance(value, str) or '/' not in value:
        return None
    runner_id, serial = value.split('/', 1)
    if not RUNNER_ID.fullmatch(runner_id) or not SERIAL.fullmatch(serial):
        return None
    return runner_id, serial


async def ensure_indexes(db):
    await db.device_enrollments.create_index('token_hash', unique=True)
    await db.device_enrollments.create_index('expires_at', expireAfterSeconds=24 * 3600)
    await db.device_runners.create_index('runner_id', unique=True)
    await db.device_runners.create_index('owner_email')
    await db.device_runners.create_index('shared_with')
    await db.device_audit.create_index('at', expireAfterSeconds=90 * 24 * 3600)


async def create_enrollment(db, owner_email, name):
    token = ENROLL_PREFIX + secrets.token_urlsafe(32)
    created = now()
    await db.device_enrollments.insert_one({
        'token_hash': digest(token), 'owner_email': owner_email, 'name': name,
        'created_at': created, 'expires_at': created + ENROLL_TTL, 'used_at': None})
    return token, created + ENROLL_TTL


async def _same_owner_previous(db, owner_email, previous):
    """The runner this machine was enrolled as before, if its saved credentials still check out
    and it belongs to the same owner. Anything else (wrong secret, revoked, other owner) is ignored."""
    if not isinstance(previous, dict):
        return None
    runner = await authenticate_runner(db, previous.get('runner_id'), previous.get('secret'))
    if runner is None or str(runner.get('owner_email') or '').lower() != str(owner_email or '').lower():
        return None
    return runner


async def _replace_offline_twins(db, owner_email, name, hostname, is_online, at):
    """Fallback when the machine lost its old config: revoke the same owner's runners with the same
    hostname and name that are offline right now. Hostname is client-supplied, so this never touches
    another owner's runner or a connected one. Returns (replaced ids, their merged sharing list)."""
    if not hostname:
        return [], []
    replaced, shared = [], []
    async for twin in db.device_runners.find({'owner_email': owner_email, 'hostname': hostname, 'name': name,
                                              'revoked': {'$ne': True}}):
        if is_online(twin['runner_id']):
            continue
        await db.device_runners.update_one({'runner_id': twin['runner_id']}, {'$set': {
            'revoked': True, 'revoked_at': at, 'revoked_by': 're-enroll'}})
        await db.device_leases.delete_many({'_id': {'$regex': '^' + re.escape(twin['runner_id']) + '/'}})
        replaced.append(twin['runner_id'])
        shared += [e for e in twin.get('shared_with') or [] if e not in shared]
    return replaced, shared


async def redeem_enrollment(db, token, details, is_online=lambda runner_id: False):
    """Single-use exchange of an enrollment token for a runner id + long-lived secret.

    Running setup with a new token on an already enrolled machine keeps the same runner: if
    `details['previous']` holds that machine's current credentials (same owner), the record is
    updated in place with a fresh secret, so device ids and sharing survive. Without them, the
    same owner's offline runners with the same hostname and name are replaced."""
    if not isinstance(token, str) or not token.startswith(ENROLL_PREFIX) or len(token) > 200:
        return None
    at = now()
    enrollment = await db.device_enrollments.find_one_and_update(
        {'token_hash': digest(token), 'used_at': None, 'expires_at': {'$gt': at}},
        {'$set': {'used_at': at}})
    if enrollment is None:
        return None
    owner = enrollment['owner_email']
    secret = SECRET_PREFIX + secrets.token_urlsafe(40)
    fields = {
        'name': str(details.get('name') or enrollment.get('name') or 'Runner')[:80],
        'hostname': str(details.get('hostname') or '')[:120], 'os': str(details.get('os') or '')[:120],
        'version': str(details.get('version') or '')[:40], 'secret_hash': digest(secret)}
    existing = await _same_owner_previous(db, owner, details.get('previous'))
    if existing is not None:
        await db.device_runners.update_one({'runner_id': existing['runner_id']},
                                           {'$set': {**fields, 're_enrolled_at': at}})
        return {'runner_id': existing['runner_id'], 'secret': secret, 'name': fields['name'], 'reused': True}
    replaced, shared = await _replace_offline_twins(db, owner, fields['name'], fields['hostname'], is_online, at)
    active = await db.device_runners.count_documents({'owner_email': owner, 'revoked': {'$ne': True}})
    if active >= MAX_RUNNERS_PER_USER:
        return {'error': f'Runner limit reached ({MAX_RUNNERS_PER_USER}); revoke an unused runner first'}
    runner = {
        'runner_id': 'r_' + secrets.token_hex(8), 'owner_email': owner, **fields,
        'shared_with': shared, 'devices': [], 'capabilities': [], 'created_at': at, 'last_seen': None,
        'revoked': False}
    await db.device_runners.insert_one(runner)
    return {'runner_id': runner['runner_id'], 'secret': secret, 'name': runner['name'],
            **({'replaced': replaced} if replaced else {})}


async def authenticate_runner(db, runner_id, secret):
    if not isinstance(runner_id, str) or not RUNNER_ID.fullmatch(runner_id):
        return None
    runner = await db.device_runners.find_one({'runner_id': runner_id, 'revoked': {'$ne': True}})
    if runner is None or not matches(secret, runner.get('secret_hash')):
        return None
    return runner


def can_use(runner, user_email):
    if not user_email:
        return False
    email = user_email.lower()
    return (str(runner.get('owner_email') or '').lower() == email
            or email in (runner.get('shared_with') or []))
