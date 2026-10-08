"""Mongo persistence for device-mock sessions.

One document per session in `device_mock_sessions`. The data-plane token is stored only as a
SHA-256 hash; the scenario and a capped request log (last LOG_SIZE entries, `$push`+`$slice`)
live on the same document, so concurrent sessions never share state. A TTL index removes
expired documents; `expires_at` is also checked on every lookup because the TTL monitor
only runs about once a minute.
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

COLLECTION = 'device_mock_sessions'
TOKEN_PREFIX = 'dmt_'
TOKEN = re.compile(r'dmt_[A-Za-z0-9_-]{43}\Z')  # secrets.token_urlsafe(32): 256 bits
SESSION_ID = re.compile(r'dm_[a-f0-9]{16}\Z')
DEFAULT_TTL = timedelta(hours=6)
MAX_TTL = timedelta(hours=24)
MAX_ACTIVE_PER_USER = 10
LOG_SIZE = 200


def now():
    return datetime.now(timezone.utc)


def aware(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _coll(db):
    return db[COLLECTION]


async def ensure_indexes(db):
    await _coll(db).create_index('token_hash', unique=True)
    await _coll(db).create_index('session_id', unique=True)
    await _coll(db).create_index([('owner_email', 1), ('scope', 1)])
    await _coll(db).create_index('expires_at', expireAfterSeconds=0)


async def create(db, owner_email, scope, upstream, public_origin, ttl=DEFAULT_TTL, label=''):
    """Create a session; returns (session_doc, token). The token is never stored in clear."""
    active = await _coll(db).count_documents({'owner_email': owner_email, 'expires_at': {'$gt': now()}})
    if active >= MAX_ACTIVE_PER_USER:
        raise ValueError(f'At most {MAX_ACTIVE_PER_USER} active mock sessions per user; delete one first')
    ttl = max(timedelta(minutes=5), min(ttl, MAX_TTL))
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    created = now()
    doc = {
        'session_id': 'dm_' + secrets.token_hex(8), 'token_hash': digest(token), 'owner_email': owner_email,
        'scope': scope, 'upstream': upstream, 'public_origin': public_origin, 'label': str(label or '')[:80],
        'created_at': created, 'expires_at': created + ttl,
        'scenario': None, 'scenario_version': 0, 'log': [],
    }
    await _coll(db).insert_one(dict(doc))
    return doc, token


async def by_token(db, token):
    """Active session for a data-plane token, or None (unknown, malformed or expired)."""
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return None
    doc = await _coll(db).find_one({'token_hash': digest(token)}, {'log': 0})
    if doc is None or aware(doc['expires_at']) <= now():
        return None
    return doc


async def owned(db, owner_email, scope, session_id):
    """Active session owned by this user AND conversation scope, else None (no existence leak)."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return None
    doc = await _coll(db).find_one({'session_id': session_id, 'owner_email': owner_email, 'scope': scope})
    if doc is None or aware(doc['expires_at']) <= now():
        return None
    return doc


async def list_owned(db, owner_email, scope):
    cursor = _coll(db).find({'owner_email': owner_email, 'scope': scope, 'expires_at': {'$gt': now()}},
                            {'log': 0, 'token_hash': 0})
    return [doc async for doc in cursor]


async def set_scenario(db, session_id, scenario):
    doc = await _coll(db).find_one_and_update(
        {'session_id': session_id}, {'$set': {'scenario': scenario}, '$inc': {'scenario_version': 1}},
        return_document=True, projection={'scenario_version': 1})
    return doc['scenario_version'] if doc else None


async def append_log(db, session_id, entry):
    await _coll(db).update_one({'session_id': session_id},
                               {'$push': {'log': {'$each': [entry], '$slice': -LOG_SIZE}}})


async def delete(db, session_id):
    result = await _coll(db).delete_one({'session_id': session_id})
    return result.deleted_count == 1
