"""Device runner routes.

Three audiences, three auth modes:
  /device-runner/*        the runner daemon (enrollment token, then runner secret). Served
                          outside /api because nginx session-gates /api/*.
  /api/devices*           dashboard users (nginx-injected X-User-Email session identity).
  /internal/devices/*     the legacy agent CLI (device_loader/cli/device.py, via tools/device.py) running inside the backend
                          container: loopback-only AND an HMAC user auth token.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shlex
from pathlib import Path

from aiohttp import web

from api.auth_helpers import get_system_role, get_user_email, is_loopback, require_admin
from device_loader.backend import builds, store
from device_loader.backend.builds import blobs, FILENAME, MAX_BLOB
from device_loader.backend.hub import DeviceError, hub
from device_loader.backend.service import DeviceService
from observability.db import get_db
from tools._auth_token import verify_user_auth_token

logger = logging.getLogger(__name__)

RUNNER_SCRIPT = Path(__file__).resolve().parent.parent / 'runner' / 'loma_device_runner.py'
_RUNNER_RELEASE = {}


def runner_release():
    """(version, sha256) of the runner script this backend serves; read once per process."""
    if not _RUNNER_RELEASE:
        data = RUNNER_SCRIPT.read_bytes()
        found = re.search(rb"^VERSION = '(\d+\.\d+\.\d+)'", data, re.M)
        _RUNNER_RELEASE.update(version=found.group(1).decode() if found else '',
                               sha256=hashlib.sha256(data).hexdigest())
    return _RUNNER_RELEASE['version'], _RUNNER_RELEASE['sha256']


def _older(version, latest):
    parse = lambda text: tuple(int(p) for p in re.findall(r'\d+', text or '')[:3])  # noqa: E731
    return bool(latest) and parse(version) < parse(latest)
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


async def _json_object(request):
    """The JSON object body, or None when it is missing, invalid or not an object."""
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        return None
    return body if isinstance(body, dict) else None


def _bearer(request):
    header = request.headers.get('Authorization', '')
    return header[7:].strip() if header.startswith('Bearer ') else ''


async def _runner_from_request(request, db):
    return await store.authenticate_runner(db, request.headers.get('X-Loma-Runner-Id', ''), _bearer(request))


def _clean_device(device):
    if not isinstance(device, dict) or not store.SERIAL.fullmatch(str(device.get('serial', ''))):
        return None
    return {'serial': device['serial'], 'platform': device.get('platform') if device.get('platform') in ('android', 'ios') else 'unknown',
            'name': str(device.get('name') or '')[:80], 'os_version': str(device.get('os_version') or '')[:20],
            'virtual': bool(device.get('virtual', True))}


def _clean_devices(devices):
    if not isinstance(devices, list):
        return []
    return [d for d in (_clean_device(x) for x in (devices or [])[:50]) if d is not None]


TEMPLATE_NAME = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')


def _clean_templates(templates):
    if not isinstance(templates, list):
        return []
    return [{'name': t['name'], 'platform': t['platform'], 'clean': t.get('clean') is True}
            for t in templates[:10] if isinstance(t, dict) and isinstance(t.get('name'), str)
            and TEMPLATE_NAME.fullmatch(t['name']) and t.get('platform') in ('android', 'ios')]


# ── Runner endpoints ──────────────────────────────────────────────────────


async def handle_enroll(request):
    db = _db_or_503()
    body = await _json_object(request)
    if body is None:
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
    devices = _clean_devices(hello.get('devices'))
    capabilities = hello.get('capabilities') if isinstance(hello.get('capabilities'), list) else []
    version = str(hello.get('version') or '')[:40]
    templates = _clean_templates(hello.get('templates'))
    conn = await hub.attach(runner_id, ws, devices, version, templates)
    try:
        if await db.device_runners.find_one({'runner_id': runner_id, 'revoked': True}, {'_id': 1}):
            await hub.revoke(runner_id)  # revoked while we waited for hello
            return ws
        await db.device_runners.update_one({'runner_id': runner_id}, {'$set': {
            'devices': devices, 'last_seen': store.now(), 'connected_at': store.now(),
            'version': version, 'templates': templates, 'hostname': str(hello.get('hostname') or '')[:120],
            'os': str(hello.get('os') or '')[:120],
            'capabilities': [str(c)[:20] for c in capabilities[:10]]}})
        logger.info('Device runner %s connected with %d device(s)', runner_id, len(devices))
        latest, digest = runner_release()
        if 'self_update' in capabilities and _older(version, latest):
            logger.info('Offering runner %s an update %s -> %s', runner_id, version, latest)
            await conn.send({'type': 'update', 'version': latest, 'sha256': digest})
        last_persist = asyncio.get_running_loop().time()
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
    latest, _ = runner_release()
    return {
        'latest_version': latest, 'update_available': _older(runner.get('version'), latest),
        'self_update': 'self_update' in (runner.get('capabilities') or []),
        'runner_id': runner['runner_id'], 'name': runner.get('name'), 'owner': runner.get('owner_email'),
        'is_owner': runner.get('owner_email') == user_email, 'hostname': runner.get('hostname'),
        'os': runner.get('os'), 'version': runner.get('version'),
        'capabilities': runner.get('capabilities') or [], 'shared_with': runner.get('shared_with') or [],
        'templates': runner.get('templates') or [],
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
    body = await _json_object(request) or {}
    name = str(body.get('name') or '').strip()[:80] or 'My machine'
    token, expires = await store.create_enrollment(db, user_email, name)
    base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/') or f'{request.scheme}://{request.host}'
    download = shlex.quote(base + '/device-runner/download')
    return web.json_response({
        'token': token, 'expires_at': expires.isoformat(), 'server': base,
        # Two steps: download, then `setup` (private venv, enroll, doctor, login service).
        'commands': [
            f'curl -fsSL --max-redirs 0 -o loma_device_runner.py {download}'
            " || echo 'Download was redirected to a login page. Behind Cloudflare Access? Use:"
            f" cloudflared access curl {download} -o loma_device_runner.py' >&2",
            f'python3 loma_device_runner.py setup --server {shlex.quote(base)} --token {token} --name {shlex.quote(name)}']})


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
    body = await _json_object(request)
    if body is None:
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
        update['shared_with'] = sorted({e.strip().lower() for e in emails} - {user_email.lower()})
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
    body = await _json_object(request)
    if body is None:
        return _error('Invalid JSON')
    try:
        return web.json_response(await DeviceService(db).force_release(user_email, body.get('device_id')))
    except DeviceError as exc:
        return _error(str(exc), 403)


async def handle_screen(request):
    """Live view: one PNG frame of a device the user can use."""
    db = _db_or_503()
    user_email = get_user_email(request)
    if not user_email:
        return _error('Authentication required', 401)
    try:
        png, held = await DeviceService(db).screen(user_email, request.query.get('device_id', ''))
    except DeviceError as exc:
        status = (429 if 'slow down' in str(exc) else 404 if 'not found' in str(exc).lower()
                  else 403 if exc.code == 'device_busy' else 409)
        return _error(str(exc), status)
    # X-Device-Held lets the page show the real hold state (after a reload, or once a hold lapses).
    return web.Response(body=png, content_type='image/png',
                        headers={'Cache-Control': 'no-store', 'X-Device-Held': held or 'none'})


async def handle_takeover(request):
    db = _db_or_503()
    user_email = get_user_email(request)
    if not user_email:
        return _error('Authentication required', 401)
    body = await _json_object(request)
    if body is None or body.get('action') not in ('start', 'end', 'input'):
        return _error('action must be start, end or input')
    service = DeviceService(db)
    try:
        if body['action'] == 'start':
            return web.json_response(await service.start_takeover(user_email, body.get('device_id')))
        if body['action'] == 'end':
            return web.json_response(await service.end_takeover(user_email, body.get('device_id')))
        data = await service.takeover_input(user_email, body.get('device_id'), body.get('op'), body.get('args') or {})
        return web.json_response(_encode_media(data))
    except DeviceError as exc:
        return _error(str(exc), 409)


MAX_ACTIVITY = 300


async def handle_activity(request):
    """Session timeline for one device, from device_audit: sessions (by lease scope) and their steps."""
    db = _db_or_503()
    user_email = get_user_email(request)
    if not user_email:
        return _error('Authentication required', 401)
    device_id = request.query.get('device_id', '')
    try:
        runner, _ = await DeviceService(db)._resolve(user_email, device_id)
    except DeviceError as exc:
        return _error(str(exc), 404)
    query = {'device_id': device_id}
    if runner['owner_email'] != user_email:  # people the runner is shared with see only their own sessions
        query['actor'] = user_email
    scope = request.query.get('scope')
    if scope:
        query['scope'] = scope[:200]
    rows = await db.device_audit.find(query, {'_id': 0}).sort('at', -1).to_list(length=MAX_ACTIVITY)
    sessions = {}
    for row in rows:
        at = store.aware(row['at'])
        row['at'] = at.isoformat()
        session = sessions.setdefault(row.get('scope'), {
            'scope': row.get('scope'), 'actor': row.get('actor'), 'started_at': row['at'], 'ended_at': row['at'],
            'ops': 0, 'failures': 0})
        session['started_at'] = row['at']  # rows are newest first
        session['ops'] += 1
        session['failures'] += 0 if row.get('ok') else 1
        conversation = (row.get('scope') or '')[5:] if str(row.get('scope')).startswith('conv:') else None
        if conversation:
            session['conversation_id'] = conversation
    return web.json_response({'device_id': device_id, 'sessions': list(sessions.values()), 'events': rows})


MAX_BUILD_SOURCES = 50


def _build_settings_view(saved, request):
    return {'repos': saved.get('repos') or [], 'workflows': saved.get('workflows') or [],
            'env_repos': sorted(builds.env_repos()), 'env_workflows': sorted(builds.env_workflows()),
            'can_edit': get_system_role(request) == 'admin',
            'updated_by': saved.get('updated_by'),
            'updated_at': store.aware(saved['updated_at']).isoformat() if saved.get('updated_at') else None}


async def handle_get_build_settings(request):
    """Which repos' CI builds devices may install, and which workflows the agent may start. Any user may read."""
    db = _db_or_503()
    if not get_user_email(request):
        return _error('Authentication required', 401)
    saved = await db.device_settings.find_one({'_id': builds.SETTINGS_ID}) or {}
    return web.json_response(_build_settings_view(saved, request))


def _clean_list(value, pattern, lower, what):
    items = builds.split_list(value) if isinstance(value, str) else value
    if not isinstance(items, list) or len(items) > MAX_BUILD_SOURCES:
        raise ValueError(f'{what} must be a list of at most {MAX_BUILD_SOURCES} entries')
    cleaned = []
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        if not pattern.fullmatch(item):
            raise ValueError(f'Invalid {what[:-1]}: {item[:120]}')
        item = item.lower() if lower else item
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


async def handle_put_build_settings(request):
    """Admin-only: these lists decide what code can run on shared devices and which CI workflows start."""
    db = _db_or_503()
    require_admin(request)
    body = await _json_object(request)
    if body is None:
        return _error('Invalid JSON')
    update = {}
    try:
        if 'repos' in body:
            update['repos'] = _clean_list(body['repos'], builds.REPO_NAME, True, 'repos')
        if 'workflows' in body:
            update['workflows'] = _clean_list(body['workflows'], builds.WORKFLOW_NAME, False, 'workflows')
    except ValueError as exc:
        return _error(str(exc))
    if not update:
        return _error('Nothing to update')
    update.update(updated_by=get_user_email(request), updated_at=store.now())
    await db.device_settings.update_one({'_id': builds.SETTINGS_ID}, {'$set': update}, upsert=True)
    builds.invalidate_settings()
    logger.info('Device build sources updated by %s: %s', update['updated_by'],
                {k: v for k, v in update.items() if k in ('repos', 'workflows')})
    saved = await db.device_settings.find_one({'_id': builds.SETTINGS_ID}) or {}
    return web.json_response(_build_settings_view(saved, request))


# ── Internal endpoints for the legacy agent CLI ───────────────────────────


def _internal_identity(request):
    if not is_loopback(request):
        raise web.HTTPForbidden(text=json.dumps({'error': 'Loopback only'}), content_type='application/json')
    user_email = request.headers.get('X-Loma-User', '').strip()
    token = request.headers.get('X-Loma-Auth-Token', '').strip()
    if not user_email or not verify_user_auth_token(token, user_email):
        raise web.HTTPUnauthorized(text=json.dumps({'error': 'Invalid or expired auth token'}), content_type='application/json')
    return user_email


def _encode_media(value):
    """Media bytes (screenshots, burst frames, recordings) become '<key>_base64' strings for the CLI."""
    if isinstance(value, dict):
        return {(f'{key}_base64' if isinstance(item, bytes) else key): _encode_media(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_media(item) for item in value]
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


async def handle_internal_call(request):
    db = _db_or_503()
    user_email = _internal_identity(request)
    body = await _json_object(request)
    if body is None:
        return _error('Invalid JSON')
    service = DeviceService(db)
    scope = body.get('scope')
    action = body.get('action')
    try:
        if action == 'list':
            return web.json_response({'devices': await service.list_devices(user_email),
                                      'templates': await service.templates_for(user_email)})
        if action == 'lease':
            return web.json_response(await service.lease(user_email, scope, body.get('device_id'), body.get('platform'),
                                                         body.get('wait_online_s', 0), body.get('template'),
                                                         body.get('clean', False)))
        if action == 'release':
            return web.json_response(await service.release(user_email, scope, body.get('device_id')))
        if action == 'call':
            data = await service.call(user_email, scope, body.get('device_id'), body.get('op'), body.get('args') or {})
            return web.json_response(_encode_media(data))
        if action == 'sdk_events_check':
            return web.json_response(await service.sdk_events_check(user_email, scope, body.get('device_id'),
                                                                  body.get('args') or {}))
        if action == 'visual_check':
            data = await service.visual_check(user_email, scope, body.get('device_id'), (body.get('args') or {}).get('expect'))
            return web.json_response(_encode_media(data))
    except DeviceError as exc:
        return web.json_response({'error': str(exc), **exc.to_dict()}, status=409)
    return _error('Unknown action')


async def handle_internal_upload(request):
    user_email = _internal_identity(request)
    filename = request.query.get('filename', '')
    if not FILENAME.fullmatch(filename) or not filename.endswith(('.apk', '.zip', '.ipa')):
        return _error('filename must be a simple name ending in .apk, .zip or .ipa')
    try:
        declared = int(request.headers.get('Content-Length', ''))
    except ValueError:
        declared = 0
    if not 0 < declared <= MAX_BLOB:
        return _error('A Content-Length of at most 500 MB is required')
    path, size, digest = blobs.new_path(), 0, hashlib.sha256()
    try:
        with blobs.reservation(user_email, declared), open(path, 'wb') as handle:
            async for chunk in request.content.iter_chunked(1 << 20):
                size += len(chunk)
                if size > declared:
                    raise DeviceError('Upload is larger than its Content-Length')
                digest.update(chunk)
                handle.write(chunk)
        blob_id = blobs.add_file(path, filename, user_email, digest.hexdigest(), size, meta={'uploaded': filename})
    except DeviceError as exc:
        path.unlink(missing_ok=True)
        return _error(str(exc))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
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
    app.router.add_post('/api/devices/release', handle_force_release)
    app.router.add_get('/api/devices/activity', handle_activity)
    app.router.add_get('/api/devices/screen', handle_screen)
    app.router.add_post('/api/devices/takeover', handle_takeover)
    app.router.add_get('/api/devices/build-settings', handle_get_build_settings)
    app.router.add_put('/api/devices/build-settings', handle_put_build_settings)
    app.router.add_post('/internal/devices/call', handle_internal_call)
    app.router.add_post('/internal/devices/upload', handle_internal_upload)
