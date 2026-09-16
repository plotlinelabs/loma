"""Shared recall cursors and atomic limits. No signing secrets or source text."""
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class RecallError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


async def ensure_control_indexes(db):
    for name in ('recall_cursors', 'recall_limits'):
        await getattr(db, name).create_index('expires_at', expireAfterSeconds=0)


async def create_cursor(db, state):
    token = secrets.token_urlsafe(32)
    await db.recall_cursors.insert_one({
        '_id': hashlib.sha256(token.encode()).hexdigest(),
        'state': state,
        'expires_at': datetime.fromtimestamp(state['exp'], timezone.utc),
    })
    return token


async def read_cursor(db, token, binding):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise RecallError('cursor_expired')
    row = await db.recall_cursors.find_one({'_id': hashlib.sha256(token.encode()).hexdigest()})
    state = row.get('state') if row else None
    # TTL cleanup is asynchronous; expiration must always be checked on read.
    if not state or state.get('binding') != binding or state.get('exp', 0) <= time.time():
        raise RecallError('cursor_expired')
    return state


async def _consume(db, key, field, amount, maximum, window):
    now = int(time.time())
    slot = now // window
    digest = hashlib.sha256(json.dumps([key, slot], separators=(',', ':')).encode()).hexdigest()
    try:
        # _id uniqueness makes concurrent creation safe, without transactions.
        await db.recall_limits.update_one({'_id': digest}, {'$setOnInsert': {
            field: 0, 'expires_at': datetime.fromtimestamp((slot + 2) * window, timezone.utc),
        }}, upsert=True)
    except DuplicateKeyError:
        pass
    row = await db.recall_limits.find_one_and_update(
        {'_id': digest, field: {'$lte': maximum - amount}},
        {'$inc': {field: amount}}, return_document=ReturnDocument.AFTER)
    if row is None:
        raise RecallError('rate_limited', 429)


async def admit_request(db, identity):
    # Both endpoints and all processes share these limits. Failed requests after
    # authentication count too; a fresh capability does not reset a counter.
    await _consume(db, ['user', identity.user_id], 'calls', 1, 60, 60)
    await _consume(db, ['execution', identity.user_id, identity.execution_id], 'calls', 1, 120, 900)


async def charge_response(db, identity, result):
    size = len(json.dumps(result, ensure_ascii=False))
    await _consume(db, ['output', identity.user_id, identity.execution_id],
                   'characters', size, 240000, 900)
