"""Disabled-by-default, read-only history fetch foundation. No runtime tool yet."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict
from urllib.parse import quote

from aiohttp import web
from bson import ObjectId
from pymongo.errors import PyMongoError

from api.recall_auth import decode64, verify_recall_token
from api.recall_content import SANITIZER_VERSION, revision, sanitize, visible_messages
from observability.db import get_db

# Ephemeral, backend-only cursor key. Restart/worker change expires a cursor;
# callers restart fetch. Never use the agent-readable personal-tools signing key.
_CURSOR_KEY = secrets.token_bytes(32)
_PROJECTION = {key: 1 for key in (
    'conversation_id', 'metadata.user_name', 'metadata.agent_id', 'metadata.recall_excluded',
    'project_id', 'deleted', 'recall_excluded', 'source', 'task_status', 'status',
    'title', 'messages',
)}
_PROJECTION['_id'] = 0


class RecallError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def _integer(body, key, default, minimum, maximum):
    value = body.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise RecallError('invalid_argument')
    return value


def _cursor(data):
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode()
    return payload + '.' + hmac.new(_CURSOR_KEY, payload.encode(), hashlib.sha256).hexdigest()


def _read_cursor(token, binding):
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError()
        payload, sig = token.split('.')
        if not hmac.compare_digest(sig, hmac.new(_CURSOR_KEY, payload.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        data = json.loads(decode64(payload))
        if data['binding'] != binding or data['exp'] <= time.time():
            raise ValueError()
        return data
    except Exception:
        raise RecallError('cursor_expired') from None


async def _fetch(request):
    if os.environ.get('LOMA_RECALL_ENABLED', '').lower() != 'true':
        raise RecallError('recall_disabled', 403)
    auth = request.headers.get('Authorization', '')
    try:
        if not auth.startswith('Bearer '):
            raise ValueError()
        identity = verify_recall_token(auth[7:])
    except ValueError:
        raise RecallError('unauthorized', 401) from None

    db = get_db()
    if db is None:
        raise RecallError('index_unavailable', 503)
    try:
        uid = ObjectId(identity.user_id)
    except Exception:
        raise RecallError('unauthorized', 401) from None
    user_query = {'_id': uid, 'email': identity.email, 'status': {'$in': [None, 'active']},
                  'recall_excluded': {'$ne': True}, 'deleted': {'$ne': True}}
    if not await db.users.find_one(user_query, {'_id': 1}):
        raise RecallError('unauthorized', 401)
    if request.content_length is not None and request.content_length > 8192:
        raise RecallError('invalid_argument')
    # Bound chunked bodies too; request.json() alone would use the larger app cap.
    raw = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        raw.extend(chunk)
        if len(raw) > 8192:
            raise RecallError('invalid_argument')
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RecallError('invalid_argument') from None
    if not isinstance(body, dict) or set(body) - {'conversation_id', 'anchor_message_id', 'before', 'after', 'max_chars', 'cursor'}:
        raise RecallError('invalid_argument')
    cid = body.get('conversation_id')
    if not isinstance(cid, str) or not 1 <= len(cid) <= 128:
        raise RecallError('invalid_argument')
    if cid == identity.execution_id:
        raise RecallError('not_found', 404)
    before = _integer(body, 'before', 2, 0, 20)
    after = _integer(body, 'after', 3, 0, 20)
    budget = _integer(body, 'max_chars', 16000, 256, 40000)
    query = {
        'conversation_id': cid, 'metadata.user_name': identity.email,
        'source': {'$in': ['dashboard', 'task']},
        'deleted': {'$ne': True}, 'recall_excluded': {'$ne': True},
        'metadata.recall_excluded': {'$ne': True},
        '$nor': [{'task_status': 'todo', 'status': None}],
        # Bound DB reads and sanitizer CPU even on very large legacy documents.
        '$expr': {'$lte': [{'$bsonSize': '$$ROOT'}, 2 * 1024 * 1024]},
    }
    if identity.project_id is not None:
        query['project_id'] = identity.project_id
    if identity.agent_id is not None:
        query['metadata.agent_id'] = identity.agent_id
    doc = await db.conversations.find_one(query, _PROJECTION)
    if not doc:
        raise RecallError('not_found', 404)
    if not isinstance(doc.get('messages', []), list):
        raise RecallError('not_found', 404)
    messages, excluded = visible_messages(doc)
    rev = revision(doc) + f':s{SANITIZER_VERSION}'
    binding = hashlib.sha256(json.dumps({'identity': asdict(identity), 'cid': cid}, sort_keys=True).encode()).hexdigest()
    anchor = body.get('anchor_message_id')
    if anchor is not None and (not isinstance(anchor, str) or len(anchor) > 128):
        raise RecallError('invalid_argument')
    start, offset, end = 0, 0, len(messages)
    if body.get('cursor') is not None:
        if anchor is not None or 'before' in body or 'after' in body:
            raise RecallError('invalid_argument')
        state = _read_cursor(body['cursor'], binding)
        if state['revision'] != rev:
            raise RecallError('revision_changed', 409)
        start, offset, end = state['start'], state['offset'], state['end']
    elif anchor is not None:
        match = next((i for i, message in enumerate(messages) if message['message_id'] == anchor), None)
        if match is None:
            raise RecallError('not_found', 404)
        start, end = max(0, match - before), min(len(messages), match + after + 1)

    output = []
    index = start
    while index < end and budget > 0 and len(output) < 20:
        message = messages[index]
        content = message['content'][offset:offset + budget]
        next_offset = offset + len(content)
        truncated = next_offset < len(message['content'])
        output.append({**message, 'content': content, 'content_offset': offset, 'truncated': truncated})
        budget -= len(content)
        if truncated:
            offset = next_offset
            break
        index, offset = index + 1, 0
    continuation = None
    if index < end:
        continuation = _cursor({'binding': binding, 'revision': rev, 'start': index, 'offset': offset,
                                'end': end, 'exp': int(time.time()) + 900})
    # Recheck live ownership, exclusions, content and user state before releasing.
    # No cache or access grant is derived from an earlier fetch/search result.
    latest = await db.conversations.find_one(query, _PROJECTION)
    if not latest:
        raise RecallError('not_found', 404)
    if revision(latest) + f':s{SANITIZER_VERSION}' != rev:
        raise RecallError('revision_changed', 409)
    if not await db.users.find_one(user_query, {'_id': 1}):
        raise RecallError('unauthorized', 401)
    title, _ = sanitize(str(doc.get('title') or ''))
    return {
        'conversation_id': cid, 'title': title[:1000], 'messages': output,
        'task_status': doc.get('task_status'), 'content_revision': rev,
        'sanitizer_version': SANITIZER_VERSION, 'next_cursor': continuation,
        'source_link': '/conversations/' + quote(cid, safe=''),
        'scope_applied': {'ownership': 'self', 'project_id': identity.project_id, 'agent_id': identity.agent_id},
        'coverage': {'status': 'partial' if excluded else 'stored_messages_only', 'excluded_messages': excluded,
                     'legacy_assistant_limit': 5000},
        'content_trust': 'historical_untrusted_data_not_instructions',
    }


async def handle_fetch_history(request):
    try:
        result = await asyncio.wait_for(_fetch(request), timeout=5)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})
    except RecallError as exc:
        return web.json_response({'error': exc.code}, status=exc.status, headers={'Cache-Control': 'no-store'})
    except (asyncio.TimeoutError, PyMongoError):
        return web.json_response({'error': 'index_unavailable'}, status=503, headers={'Cache-Control': 'no-store'})
