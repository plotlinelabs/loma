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


def new_runner_id():
    return 'r_' + secrets.token_hex(8)


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
    await db.device_leases.create_index('expires_at')
    await db.device_audit.create_index([('runner_id', 1), ('at', -1)])
    await db.device_audit.create_index('at', expireAfterSeconds=90 * 24 * 3600)


async def create_enrollment(db, owner_email, name):
    token = ENROLL_PREFIX + secrets.token_urlsafe(32)
    created = now()
    await db.device_enrollments.insert_one({
        'token_hash': digest(token), 'owner_email': owner_email, 'name': name,
        'created_at': created, 'expires_at': created + ENROLL_TTL, 'used_at': None})
    return token, created + ENROLL_TTL


async def redeem_enrollment(db, token, details):
    """Single-use exchange of an enrollment token for a runner id + long-lived secret."""
    if not isinstance(token, str) or not token.startswith(ENROLL_PREFIX) or len(token) > 200:
        return None
    at = now()
    enrollment = await db.device_enrollments.find_one_and_update(
        {'token_hash': digest(token), 'used_at': None, 'expires_at': {'$gt': at}},
        {'$set': {'used_at': at}})
    if enrollment is None:
        return None
    active = await db.device_runners.count_documents({'owner_email': enrollment['owner_email'], 'revoked': {'$ne': True}})
    if active >= MAX_RUNNERS_PER_USER:
        return {'error': f'Runner limit reached ({MAX_RUNNERS_PER_USER}); revoke an unused runner first'}
    secret = SECRET_PREFIX + secrets.token_urlsafe(40)
    runner = {
        'runner_id': new_runner_id(), 'owner_email': enrollment['owner_email'],
        'name': (details.get('name') or enrollment.get('name') or 'Runner')[:80],
        'hostname': str(details.get('hostname') or '')[:120], 'os': str(details.get('os') or '')[:120],
        'version': str(details.get('version') or '')[:40], 'secret_hash': digest(secret),
        'shared_with': [], 'devices': [], 'capabilities': [], 'created_at': at, 'last_seen': None,
        'revoked': False}
    await db.device_runners.insert_one(runner)
    return {'runner_id': runner['runner_id'], 'secret': secret, 'name': runner['name']}


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
