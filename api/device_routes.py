"""Device runner routes.

Three audiences, three auth modes:
  /device-runner/*        the runner daemon (enrollment token, then runner secret). Served
                          outside /api because nginx session-gates /api/*.
  /api/devices*           dashboard users (nginx-injected X-User-Email session identity).
  /internal/devices/*     the legacy agent CLI (tools/device.py) running inside the backend
                          container: loopback-only AND an HMAC user auth token.
"""
import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path

from aiohttp import web

from api.auth_helpers import get_user_email
from devices import store
from devices.builds import blobs, FILENAME, MAX_BLOB
from devices.hub import DeviceError, hub
from devices.service import DeviceService
from observability.db import get_db

logger = logging.getLogger(__name__)

RUNNER_SCRIPT = Path(__file__).resolve().parent.parent / 'device_runner' / 'loma_device_runner.py'
MAX_RUNNER_FRAME = 24 * 1024 * 1024
HELLO_TIMEOUT = 10
EMAIL = re.compile(r'[^@\s]{1,64}@[^@\s]{1,190}\Z')


def _db_or_503():
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(text=json.dumps({'error': 'Database not configured'}),
                                         content_type='application/json')
    return db


def _error(message, status=400):
    return web.json_response({'error': message}, status=status)


def _bearer(request):
    header = request.headers.get('Authorization', '')
    return header[7:].strip() if header.startswith('Bearer ') else ''


async def _runner_from_request(request, db):
    return await store.authenticate_runner(db, request.headers.get('X-Loma-Runner-Id', ''), _bearer(request))


def _public_base():
    return os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')


def _clean_device(device):
    if not isinstance(device, dict) or not store.SERIAL.fullmatch(str(device.get('serial', ''))):
        return None
    return {'serial': device['serial'], 'platform': device.get('platform') if device.get('platform') in ('android', 'ios') else 'unknown',
            'name': str(device.get('name') or '')[:80], 'os_version': str(device.get('os_version') or '')[:20],
            'virtual': bool(device.get('virtual', True))}


def _clean_devices(devices):
    return [d for d in (_clean_device(x) for x in (devices or [])[:50]) if d is not None]


# ── Runner endpoints ──────────────────────────────────────────────────────


async def handle_enroll(request):
    db = _db_or_503()
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        return _error('Invalid JSON')
    if not isinstance(body, dict):
        return _error('Invalid JSON')
    result = await store.redeem_enrollment(db, body.get('token'), body)
    if result is None:
        return _error('Enrollment token is invalid, expired or already used', 401)
    if 'error' in result:
        return _error(result['error'], 409)
    logger.info('Device runner %s enrolled', result['runner_id'])
    return web.json_response(result)


async def handle_runner_ws(request):
    db = _db_or_503()
    runner = await _runner_from_request(request, db)
    if runner is None:
        return _error('Invalid runner credentials', 401)
    runner_id = runner['runner_id']
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=MAX_RUNNER_FRAME)
    await ws.prepare(request)
    try:
        first = await asyncio.wait_for(ws.receive(), HELLO_TIMEOUT)
        hello = json.loads(first.data) if first.type == web.WSMsgType.TEXT else None
    except (asyncio.TimeoutError, ValueError, TypeError):
        hello = None
    if not isinstance(hello, dict) or hello.get('type') != 'hello':
        await ws.close(code=4002, message=b'expected hello')
        return ws
    hello['devices'] = _clean_devices(hello.get('devices'))
    conn = await hub.attach(runner_id, ws, hello)
    await db.device_runners.update_one({'runner_id': runner_id}, {'$set': {
        'devices': hello['devices'], 'last_seen': store.now(), 'connected_at': store.now(),
        'version': str(hello.get('version') or '')[:40], 'hostname': str(hello.get('hostname') or '')[:120],
        'os': str(hello.get('os') or '')[:120],
        'capabilities': [str(c)[:20] for c in (hello.get('capabilities') or [])[:10]]}})
    logger.info('Device runner %s connected with %d device(s)', runner_id, len(hello['devices']))
    last_persist = asyncio.get_running_loop().time()
    try:
        async for message in ws:
            if message.type != web.WSMsgType.TEXT:
                break
            try:
                frame = json.loads(message.data)
            except ValueError:
                continue
            if isinstance(frame, dict) and frame.get('type') == 'devices':
                frame['devices'] = _clean_devices(frame.get('devices'))
            updated = hub.on_frame(conn, frame)
            now = asyncio.get_running_loop().time()
            if updated is not None and now - last_persist > 60:
                last_persist = now
                await db.device_runners.update_one({'runner_id': runner_id}, {'$set': {
                    'devices': updated, 'last_seen': store.now()}})
    finally:
        hub.detach(conn)
        try:
            await db.device_runners.update_one({'runner_id': runner_id}, {'$set': {'last_seen': store.now()}})
        except Exception:
            logger.warning('Could not record runner %s disconnect', runner_id)
        logger.info('Device runner %s disconnected', runner_id)
    return ws


async def handle_runner_blob(request):
    db = _db_or_503()
    runner = await _runner_from_request(request, db)
    if runner is None:
        return _error('Invalid runner credentials', 401)
    blob = blobs.open_for_runner(request.match_info['blob_id'], runner['runner_id'])
    if blob is None:
        return _error('Build not found or expired', 404)
    return web.FileResponse(blob['path'], headers={'Content-Type': 'application/octet-stream',
                                                   'Cache-Control': 'no-store'})


async def handle_runner_download(request):
    return web.FileResponse(RUNNER_SCRIPT, headers={
        'Content-Type': 'text/x-python; charset=utf-8',
        'Content-Disposition': 'attachment; filename="loma_device_runner.py"'})


# ── Dashboard endpoints (session identity) ────────────────────────────────


def _runner_view(runner, user_email):
    conn = hub.get(runner['runner_id'])
    last_seen = store.aware(runner.get('last_seen'))
    return {
        'runner_id': runner['runner_id'], 'name': runner.get('name'), 'owner': runner.get('owner_email'),
        'is_owner': runner.get('owner_email') == user_email, 'hostname': runner.get('hostname'),
        'os': runner.get('os'), 'version': runner.get('version'),
        'capabilities': runner.get('capabilities') or [], 'shared_with': runner.get('shared_with') or [],
        'online': conn is not None, 'last_seen': last_seen.isoformat() if last_seen else None,
        'created_at': store.aware(runner['created_at']).isoformat() if runner.get('created_at') else None}


async def handle_list(request):
    db = _db_or_503()
    user_email = get_user_email(request)
    if not user_email:
        return _error('Authentication required', 401)
    service = DeviceService(db)
    runners = await service.runners_for(user_email)
    return web.json_response({'runners': [_runner_view(r, user_email) for r in runners],
                              'devices': await service.list_devices(user_email)})


async def handle_create_enrollment(request):
    db = _db_or_503()
    user_email = get_user_email(request)
    if not user_email:
        return _error('Authentication required', 401)
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        body = {}
    name = str((body or {}).get('name') or '').strip()[:80] or 'My machine'
    token, expires = await store.create_enrollment(db, user_email, name)
    base = _public_base() or f'{request.scheme}://{request.host}'
    return web.json_response({
        'token': token, 'expires_at': expires.isoformat(), 'server': base,
        'commands': [
            "python3 -m pip install --user 'aiohttp>=3.9,<4'",
            f'curl -fsSLo loma_device_runner.py {base}/device-runner/download',
            f'python3 loma_device_runner.py enroll --server {base} --token {token} --name "{name}"',
            'python3 loma_device_runner.py doctor',
            'python3 loma_device_runner.py run']})


async def _owned_runner(db, request):
    user_email = get_user_email(request)
    if not user_email:
        raise web.HTTPUnauthorized(text=json.dumps({'error': 'Authentication required'}), content_type='application/json')
    runner = await db.device_runners.find_one({'runner_id': request.match_info['runner_id'], 'revoked': {'$ne': True}})
    if runner is None or runner.get('owner_email') != user_email:
        raise web.HTTPNotFound(text=json.dumps({'error': 'Runner not found'}), content_type='application/json')
    return user_email, runner


async def handle_update_runner(request):
    db = _db_or_503()
    user_email, runner = await _owned_runner(db, request)
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        return _error('Invalid JSON')
    update = {}
    if 'name' in body:
        name = str(body['name'] or '').strip()[:80]
        if not name:
            return _error('Name cannot be empty')
        update['name'] = name
    if 'shared_with' in body:
        emails = body['shared_with']
        if (not isinstance(emails, list) or len(emails) > 50
                or not all(isinstance(e, str) and EMAIL.fullmatch(e.strip()) for e in emails)):
            return _error('shared_with must be a list of email addresses')
        update['shared_with'] = sorted({e.strip().lower() for e in emails} - {user_email})
    if not update:
        return _error('Nothing to update')
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': update})
    runner.update(update)
    return web.json_response(_runner_view(runner, user_email))


async def handle_revoke_runner(request):
    db = _db_or_503()
    user_email, runner = await _owned_runner(db, request)
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {
        'revoked': True, 'revoked_at': store.now(), 'revoked_by': user_email}})
    await db.device_leases.delete_many({'_id': {'$regex': '^' + re.escape(runner['runner_id']) + '/'}})
    await hub.revoke(runner['runner_id'])
    return web.json_response({'revoked': True})


async def handle_force_release(request):
    db = _db_or_503()
    user_email = get_user_email(request)
    if not user_email:
        return _error('Authentication required', 401)
    try:
        body = await request.json()
        result = await DeviceService(db).force_release(user_email, body.get('device_id'))
    except (ValueError, UnicodeError, AttributeError):
        return _error('Invalid JSON')
    except DeviceError as exc:
        return _error(str(exc), 403)
    return web.json_response(result)


async def handle_audit(request):
    db = _db_or_503()
    user_email, runner = await _owned_runner(db, request)
    rows = await db.device_audit.find({'runner_id': runner['runner_id']}, {'_id': 0}).sort('at', -1).to_list(length=100)
    for row in rows:
        row['at'] = store.aware(row['at']).isoformat()
    return web.json_response({'events': rows})


# ── Internal endpoints for the legacy agent CLI ───────────────────────────


def _internal_identity(request):
    if request.remote not in ('127.0.0.1', '::1'):
        raise web.HTTPForbidden(text=json.dumps({'error': 'Loopback only'}), content_type='application/json')
    from tools._auth_token import verify_user_auth_token
    user_email = request.headers.get('X-Loma-User', '').strip()
    token = request.headers.get('X-Loma-Auth-Token', '').strip()
    if not user_email or not verify_user_auth_token(token, user_email):
        raise web.HTTPUnauthorized(text=json.dumps({'error': 'Invalid or expired auth token'}), content_type='application/json')
    return user_email


async def handle_internal_call(request):
    db = _db_or_503()
    user_email = _internal_identity(request)
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        return _error('Invalid JSON')
    if not isinstance(body, dict):
        return _error('Invalid JSON')
    service = DeviceService(db)
    scope = body.get('scope') or 'cli'
    action = body.get('action')
    try:
        if action == 'list':
            return web.json_response({'devices': await service.list_devices(user_email)})
        if action == 'lease':
            return web.json_response(await service.lease(user_email, scope, body.get('device_id'), body.get('platform')))
        if action == 'release':
            return web.json_response(await service.release(user_email, scope, body.get('device_id')))
        if action == 'call':
            data = await service.call(user_email, scope, body.get('device_id'), body.get('op'), body.get('args') or {})
            if 'png' in data:
                data['png_base64'] = base64.b64encode(data.pop('png')).decode()
            return web.json_response(data)
    except DeviceError as exc:
        return _error(str(exc), 409)
    return _error('Unknown action')


async def handle_internal_upload(request):
    _db_or_503()
    user_email = _internal_identity(request)
    filename = request.query.get('filename', '')
    if not FILENAME.fullmatch(filename) or not filename.endswith(('.apk', '.zip', '.ipa')):
        return _error('filename must be a simple name ending in .apk, .zip or .ipa')
    path, size = blobs.new_path(), 0
    try:
        with open(path, 'wb') as handle:
            async for chunk in request.content.iter_chunked(1 << 20):
                size += len(chunk)
                if size > MAX_BLOB:
                    raise DeviceError('Build is larger than 500 MB')
                handle.write(chunk)
        blob_id = blobs.add_file(path, filename, user_email, meta={'uploaded': filename})
    except DeviceError as exc:
        path.unlink(missing_ok=True)
        return _error(str(exc))
    return web.json_response({'upload_id': blob_id, 'size': size})


def setup_device_routes(app):
    app.router.add_post('/device-runner/enroll', handle_enroll)
    app.router.add_get('/device-runner/ws', handle_runner_ws)
    app.router.add_get('/device-runner/blobs/{blob_id}', handle_runner_blob)
    app.router.add_get('/device-runner/download', handle_runner_download)
    app.router.add_get('/api/devices', handle_list)
    app.router.add_post('/api/devices/enrollments', handle_create_enrollment)
    app.router.add_patch('/api/devices/runners/{runner_id}', handle_update_runner)
    app.router.add_delete('/api/devices/runners/{runner_id}', handle_revoke_runner)
    app.router.add_get('/api/devices/runners/{runner_id}/audit', handle_audit)
    app.router.add_post('/api/devices/release', handle_force_release)
    app.router.add_post('/internal/devices/call', handle_internal_call)
    app.router.add_post('/internal/devices/upload', handle_internal_upload)
