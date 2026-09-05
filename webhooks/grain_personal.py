"""Owner-bound Grain ingestion. Personal transcripts never enter org changestreams."""
import asyncio
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web

from api.auth_helpers import get_user_email
from api.oauth_helpers import get_valid_provider_token
from observability.db import get_db
from tools import grain
from webhooks.grain import _extract_recording_id
from webhooks.grain_ingestion import normalize_grain_event


def _db():
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(text='Integration storage unavailable')
    return db


async def _active(db, email):
    if not email or not await db.users.find_one({'email': email, 'status': 'active'}):
        raise web.HTTPForbidden(text='Active account required')


async def configure(request):
    db = _db()
    owner = get_user_email(request)
    await _active(db, owner)
    if request.method == 'DELETE':
        await db.grain_webhook_subscriptions.delete_one({'_id': owner})
        return web.json_response({'disabled': True})
    if not await get_valid_provider_token(owner, 'grain', db=db):
        raise web.HTTPConflict(text='Connect your personal Grain account first')
    secret = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(days=90)
    await db.grain_webhook_subscriptions.replace_one({'_id': owner}, {
        '_id': owner, 'token_hash': hashlib.sha256(secret.encode()).hexdigest(),
        'expires_at': expiry,
    }, upsert=True)
    response = web.json_response({
        'path': '/webhooks/grain/personal', 'authorization': 'Bearer ' + secret,
        'expires_at': expiry.isoformat(),
        'notice': 'Save this header now. Rotation immediately invalidates the previous header.',
    })
    response.headers['Cache-Control'] = 'no-store'
    return response


async def ingest(request):
    db = _db()
    authorization = request.headers.get('Authorization', '')
    if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}', authorization):
        raise web.HTTPUnauthorized(text='Webhook authentication required')
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    now = datetime.now(timezone.utc)
    subscription = await db.grain_webhook_subscriptions.find_one({
        'token_hash': digest, 'expires_at': {'$gt': now},
    })
    if not subscription:
        raise web.HTTPUnauthorized(text='Webhook authentication required')
    owner = subscription['_id']
    await _active(db, owner)
    # Read at most a small event payload. Never accept an owner from the body.
    raw = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > 16_384:
            raise web.HTTPRequestEntityTooLarge(max_size=16_384, actual_size=len(raw))
    try:
        recording_id = _extract_recording_id(json.loads(raw))
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text='Invalid event') from None
    if not recording_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', recording_id):
        raise web.HTTPBadRequest(text='Invalid recording id')
    access = await get_valid_provider_token(owner, 'grain', db=db)
    if not access:
        raise web.HTTPConflict(text='Reconnect personal Grain account')
    event_id = hashlib.sha256((owner + '\0' + recording_id).encode()).hexdigest()
    if await db.personal_grain_events.find_one({'_id': event_id, 'owner': owner}, {'_id': 1}):
        return web.json_response({'received': True, 'duplicate': True})
    marker = grain._access_token.set(access)
    try:
        async with asyncio.timeout(45):
            transcript = await grain.get_transcript(recording_id, fmt='text')
            if not isinstance(transcript, dict) or transcript.get('error') or not isinstance(transcript.get('transcript'), str):
                raise web.HTTPBadGateway(text='Personal Grain recording unavailable')
            metadata = await grain.find_recording_by_id(recording_id, days=2)
        metadata = metadata or {'id': recording_id, 'title': 'Untitled meeting'}
        if metadata.get('id') != recording_id:
            raise web.HTTPBadGateway(text='Personal Grain recording unavailable')
        # Do not persist arbitrary sender-controlled fields or credentials.
        event = normalize_grain_event({'recording_id': recording_id}, metadata, transcript['transcript'])
        if not event or len(json.dumps(event, default=str).encode()) > 4_000_000:
            raise web.HTTPBadGateway(text='Recording exceeds ingestion limits')
        event.update({'owner': owner, 'visibility': 'private'})
        await _active(db, owner)
        if not await db.grain_webhook_subscriptions.find_one({'_id': owner, 'token_hash': digest, 'expires_at': {'$gt': datetime.now(timezone.utc)}}):
            raise web.HTTPUnauthorized(text='Webhook revoked')
        # The _id unique index provides per-owner idempotency on concurrent deliveries.
        await db.personal_grain_events.update_one({'_id': event_id}, {'$setOnInsert': event}, upsert=True)
    except web.HTTPException:
        raise
    except Exception:
        raise web.HTTPBadGateway(text='Personal Grain ingestion unavailable') from None
    finally:
        grain._access_token.reset(marker)
    return web.json_response({'received': True})


async def recordings(request):
    db = _db()
    owner = get_user_email(request)
    await _active(db, owner)
    events = await db.personal_grain_events.find({'owner': owner}, {'_id': 0}).sort('ingested_at', -1).limit(50).to_list(length=50)
    return web.json_response({'recordings': events}, dumps=lambda value: json.dumps(value, default=str))


def setup_routes(app):
    app.router.add_put('/api/integrations/grain/webhook', configure)
    app.router.add_delete('/api/integrations/grain/webhook', configure)
    app.router.add_get('/api/integrations/grain/recordings', recordings)
    app.router.add_post('/webhooks/grain/personal', ingest)
