"""Device-mock routes: a per-session Plotline API proxy served from Loma's own origin.

Two planes, two auth modes:
  /device-mock/<token>/<path>      data plane. Public, but only with an unguessable per-session
                                   token (256 bits, stored hashed, TTL). The app's SDK endpoint is
                                   pointed here. Forwards only to the session's allowlisted
                                   upstream and applies the session's scenario.
  /internal/device-mock/call       control plane for the agent CLI (tools/device_mock.py):
                                   loopback-only AND the HMAC personal-tool token, like
                                   /internal/devices/*. Sessions are bound to user + conversation.
See docs/device-mock.md.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.parse
from datetime import timedelta

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

from api.auth_helpers import is_loopback
from device_mock import core, store
from observability.db import get_db
from tools._auth_token import verify_user_auth_token

logger = logging.getLogger(__name__)

PREFIX = '/device-mock/'
MAX_REQUEST_BODY = 1024 * 1024          # SDK requests are small JSON
MAX_UPSTREAM_BODY = 32 * 1024 * 1024    # /init bodies are a few MB at most
UPSTREAM_TIMEOUT = 60
RATE_PER_MINUTE = int(os.environ.get('LOMA_DEVICE_MOCK_RATE_PER_MINUTE', '600'))
MAX_IN_FLIGHT = 64                      # delayed asset requests hold connections open
SECURE_HEADERS = {
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    # Upstream bytes are served from Loma's origin: never let them run as a page here.
    'Content-Security-Policy': "sandbox; default-src 'none'", 'Referrer-Policy': 'no-referrer',
    'X-Robots-Tag': 'noindex',
}

_presets = None


def presets():
    global _presets
    if _presets is None:
        _presets = core.load_presets()
    return _presets


class _Limiter:
    """Per-session sliding-window request limit and in-flight cap (process-local)."""

    def __init__(self):
        self.hits = {}
        self.in_flight = {}

    def allow(self, key, limit=None, window=60.0):
        limit = RATE_PER_MINUTE if limit is None else limit
        stamp = time.monotonic()
        recent = [t for t in self.hits.get(key, ()) if stamp - t < window]
        if len(recent) >= limit:
            self.hits[key] = recent
            return False
        recent.append(stamp)
        self.hits[key] = recent
        if len(self.hits) > 10_000:  # drop idle keys
            self.hits = {k: v for k, v in self.hits.items() if v and stamp - v[-1] < window}
        return True


limiter = _Limiter()


def _db_or_503():
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(text=json.dumps({'error': 'Database not configured'}),
                                         content_type='application/json')
    return db


def _error(message, status=400):
    return web.json_response({'error': message}, status=status)


def _not_found():
    return web.json_response({'error': 'Not Found'}, status=404, headers=SECURE_HEADERS)


def public_origin(request):
    base = (os.environ.get('LOMA_DEVICE_MOCK_BASE_URL', '').strip()
            or os.environ.get('PUBLIC_BASE_URL', '').strip())
    return (base or f'{request.scheme}://{request.host}').rstrip('/')


# ── Data plane ────────────────────────────────────────────────────────────


def _split(request):
    """(token, raw tail path starting with '/') from the raw, still-encoded request path."""
    raw = request.rel_url.raw_path
    rest = raw[len(PREFIX):] if raw.startswith(PREFIX) else ''
    token, _, tail = rest.partition('/')
    return token, '/' + tail.lstrip('/')


async def _read_body(request):
    if request.content_length is not None and request.content_length > MAX_REQUEST_BODY:
        return None
    chunks, size = [], 0
    async for chunk in request.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_REQUEST_BODY:
            return None
        chunks.append(chunk)
    return b''.join(chunks)


async def _log(db, session, entry):
    scenario = session.get('scenario') or {}
    entry = {'at': store.now().isoformat(), 'scenario': scenario.get('name'),
             'scenario_version': session.get('scenario_version', 0) if scenario else None, **entry}
    try:
        await store.append_log(db, session['session_id'], entry)
    except Exception:
        logger.warning('device-mock: could not append log for %s', session['session_id'], exc_info=True)


async def _sleep_rule(rule):
    if rule and rule.get('delay_ms'):
        await asyncio.sleep(rule['delay_ms'] / 1000)


async def _handle_asset(request, db, session):
    url = request.query.get('u', '')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or (parsed.hostname or '').lower() not in core.asset_host_allowlist():
        return _error('Asset host not allowed', 400)
    # The CURRENT scenario decides, so URLs the app cached from an older /init follow it too.
    rule = core.first_rule((session.get('scenario') or {}).get('rules'), 'asset', url)
    await _sleep_rule(rule)
    status = rule['status'] if rule and rule.get('status') else 302
    await _log(db, session, {'method': request.method, 'path': '/' + core.ASSET_PATH, 'asset': url[:300],
                             'status': status, 'applied': 'asset_rule' if rule else None,
                             'delay_ms': rule['delay_ms'] if rule else 0})
    if status != 302:
        return web.Response(status=status, headers=SECURE_HEADERS)
    return web.Response(status=302, headers={**SECURE_HEADERS, 'Location': url})


def _init_request_body(body, scenario):
    patch = scenario.get('request_patch')
    if not patch or not body:
        return body
    try:
        parsed = json.loads(body)
    except ValueError:
        return body
    return json.dumps(core.merge_patch(parsed, patch)).encode() if isinstance(parsed, dict) else body


async def handle_data_plane(request):
    token, path = _split(request)
    db = _db_or_503()
    session = await store.by_token(db, token)
    if session is None:
        return _not_found()
    key = session['session_id']
    if not limiter.allow(key):
        return web.json_response({'error': 'Rate limit exceeded'}, status=429, headers=SECURE_HEADERS)
    if limiter.in_flight.get(key, 0) >= MAX_IN_FLIGHT:
        return web.json_response({'error': 'Too many concurrent requests'}, status=429, headers=SECURE_HEADERS)
    limiter.in_flight[key] = limiter.in_flight.get(key, 0) + 1
    try:
        if path == '/' + core.ASSET_PATH:
            return await _handle_asset(request, db, session)
        return await _proxy(request, db, session, token, path)
    finally:
        limiter.in_flight[key] -= 1
        if limiter.in_flight[key] <= 0:
            limiter.in_flight.pop(key, None)


async def _proxy(request, db, session, token, path):
    upstream = session.get('upstream')
    if upstream not in core.upstream_allowlist():  # allowlist may shrink after creation
        await _log(db, session, {'method': request.method, 'path': path, 'status': 502, 'applied': 'blocked'})
        return web.json_response({'error': 'Upstream no longer allowed'}, status=502, headers=SECURE_HEADERS)
    scenario = session.get('scenario') or {}
    body = await _read_body(request)
    if body is None:
        return web.json_response({'error': 'Request body too large'}, status=413, headers=SECURE_HEADERS)

    decoded_path = urllib.parse.unquote(path)
    api_rule = core.first_rule(scenario.get('rules'), 'api', decoded_path)
    await _sleep_rule(api_rule)
    if api_rule and api_rule.get('status'):
        await _log(db, session, {'method': request.method, 'path': decoded_path, 'status': api_rule['status'],
                                 'applied': 'api_rule', 'delay_ms': api_rule['delay_ms']})
        return web.json_response({'error': 'Mocked by device-mock scenario'}, status=api_rule['status'],
                                 headers=SECURE_HEADERS)

    is_init = bool(scenario) and core.matches(scenario.get('init_path', '/sdk/init'), decoded_path)
    if is_init:
        body = _init_request_body(body, scenario)

    origin = URL(upstream)
    target = URL.build(scheme=origin.scheme, host=origin.host, port=origin.explicit_port, path=path,
                       query_string=request.rel_url.raw_query_string, encoded=True)
    if target.host != origin.host or target.scheme != origin.scheme:
        return _not_found()
    started = time.monotonic()
    try:
        async with request.app[CLIENT].request(
                request.method, target, data=body or None, headers=core.request_headers(request.headers),
                allow_redirects=False) as upstream_resp:
            chunks, size = [], 0
            async for chunk in upstream_resp.content.iter_chunked(256 * 1024):
                size += len(chunk)
                if size > MAX_UPSTREAM_BODY:
                    raise ValueError('upstream response too large')
                chunks.append(chunk)
            data = b''.join(chunks)
            status, headers = upstream_resp.status, core.response_headers(upstream_resp.headers)
            content_type = upstream_resp.headers.get('Content-Type', '')
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        await _log(db, session, {'method': request.method, 'path': decoded_path, 'status': 502,
                                 'applied': None, 'error': type(exc).__name__})
        return web.json_response({'error': 'Upstream request failed'}, status=502, headers=SECURE_HEADERS)
    upstream_ms = int((time.monotonic() - started) * 1000)

    applied = 'api_rule' if api_rule else None
    if is_init and status == 200 and 'json' in content_type.lower():
        try:
            parsed = json.loads(data)
            asset_base = f"{session['public_origin']}{PREFIX}{token}/{core.ASSET_PATH}?v={session.get('scenario_version', 0)}&u="
            data = json.dumps(core.apply_init(parsed, scenario, core.asset_host_allowlist(), asset_base)).encode()
            applied = 'init'
        except ValueError:
            applied = 'init_parse_error'
    entry = {'method': request.method, 'path': decoded_path, 'status': status, 'applied': applied,
             'upstream_ms': upstream_ms}
    if applied == 'init':
        entry['body_sha256'] = hashlib.sha256(data).hexdigest()[:16]
        entry['served'] = _served_values(json.loads(data), scenario)
    await _log(db, session, entry)
    out = CIMultiDict(headers)
    out.update(SECURE_HEADERS)
    return web.Response(status=status, body=data, headers=out)


def _patched_paths(patch, prefix='', depth=0):
    for key, value in patch.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict) and value and depth < 1:
            yield from _patched_paths(value, path, depth + 1)
        else:
            yield path


def _served_values(body, scenario):
    """What the app actually received at each patched path (up to two levels deep), for the log."""
    served = {}
    for patch in scenario.get('init_patches') or []:
        for path in _patched_paths(patch):
            node = body
            for part in path.split('.'):
                node = node.get(part, 'absent') if isinstance(node, dict) else 'absent'
            text = json.dumps(node)
            served[path] = node if len(text) <= 500 else text[:500] + '...'
    return served


CLIENT = web.AppKey('device_mock_client', aiohttp.ClientSession)


async def _open_client(app):
    # One upstream client per app/event loop. No cookie jar: upstream cookies are never kept.
    app[CLIENT] = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=UPSTREAM_TIMEOUT), cookie_jar=aiohttp.DummyCookieJar(),
        auto_decompress=True, trust_env=False)


async def _close_client(app):
    await app[CLIENT].close()


# ── Control plane (agent CLI) ─────────────────────────────────────────────


def _internal_identity(request):
    if not is_loopback(request):
        raise web.HTTPForbidden(text=json.dumps({'error': 'Loopback only'}), content_type='application/json')
    user_email = request.headers.get('X-Loma-User', '').strip()
    token = request.headers.get('X-Loma-Auth-Token', '').strip()
    if not user_email or not verify_user_auth_token(token, user_email):
        raise web.HTTPUnauthorized(text=json.dumps({'error': 'Invalid or expired auth token'}),
                                   content_type='application/json')
    return user_email


def _view(doc, origin_token=None):
    scenario = doc.get('scenario') or {}
    view = {'session_id': doc['session_id'], 'upstream': doc['upstream'], 'label': doc.get('label') or '',
            'expires_at': store.aware(doc['expires_at']).isoformat(),
            'scenario': scenario.get('name'), 'scenario_version': doc.get('scenario_version', 0)}
    if origin_token:
        view['base_url'] = f"{doc['public_origin']}{PREFIX}{origin_token}"
    return view


async def handle_control(request):
    user_email = _internal_identity(request)
    db = _db_or_503()
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        body = None
    if not isinstance(body, dict):
        return _error('Invalid JSON')
    scope = str(body.get('scope') or '').strip()
    if not scope or len(scope) > 200:
        return _error('scope (conversation id) is required')
    if not limiter.allow(f'control:{user_email}', limit=120):
        return _error('Rate limit exceeded', 429)
    action = body.get('action')

    if action == 'presets':
        try:
            available = presets()
        except (OSError, ValueError) as exc:
            return _error(f'Could not load presets: {exc}', 500)
        if body.get('verbose'):
            return web.json_response({'presets': available, 'upstreams': core.upstream_allowlist()})
        return web.json_response({'presets': {n: p['description'] for n, p in available.items()},
                                  'upstreams': core.upstream_allowlist()})
    if action == 'create':
        allowed = core.upstream_allowlist()
        upstream = core.normalize_origin(body.get('upstream')) if body.get('upstream') else allowed[0]
        if upstream not in allowed:
            return _error(f'Upstream not allowed. Allowed: {", ".join(allowed)} (LOMA_DEVICE_MOCK_UPSTREAMS)', 403)
        try:
            ttl = timedelta(hours=float(body.get('ttl_hours') or 6))
            doc, token = await store.create(db, user_email, scope, upstream, public_origin(request), ttl,
                                            body.get('label'))
        except (TypeError, ValueError) as exc:
            return _error(str(exc), 409)
        logger.info('device-mock session %s created by %s (upstream %s)', doc['session_id'], user_email, upstream)
        view = _view(doc, token)
        view['note'] = ('Set the app SDK API endpoint to base_url. Anyone with this URL can reach the '
                        'upstream through Loma until it expires or is deleted; do not paste it publicly.')
        return web.json_response(view)
    if action == 'list':
        return web.json_response({'sessions': [_view(d) for d in await store.list_owned(db, user_email, scope)]})

    session = await store.owned(db, user_email, scope, body.get('session_id'))
    if session is None:
        return _error('Session not found', 404)
    if action == 'show':
        return web.json_response({**_view(session), 'scenario_detail': session.get('scenario'),
                                  'log_entries': len(session.get('log') or [])})
    if action == 'set_scenario':
        # Takes effect on the next request; already-rewritten asset URLs follow the new rules too.
        try:
            scenario = core.compose_scenario(presets(), body.get('preset'), body.get('scenario'),
                                             body.get('init_patch'), body.get('name'))
        except (OSError, ValueError) as exc:
            return _error(str(exc))
        version = await store.set_scenario(db, session['session_id'], scenario)
        return web.json_response({'session_id': session['session_id'], 'scenario': scenario['name'],
                                  'scenario_version': version, 'applied': scenario})
    if action == 'log':
        try:
            limit = max(1, min(int(body.get('limit') or 50), store.LOG_SIZE))
        except (TypeError, ValueError):
            return _error('limit must be an integer')
        log = session.get('log') or []
        try:
            matched = core.filter_log(log, body.get('filters'))
        except ValueError as exc:
            return _error(str(exc))
        return web.json_response({**_view(session), 'total': len(log), 'matched': len(matched),
                                  'latest_at': log[-1]['at'] if log else None, 'entries': matched[-limit:]})
    if action == 'delete':
        await store.delete(db, session['session_id'])
        logger.info('device-mock session %s deleted by %s', session['session_id'], user_email)
        return web.json_response({'deleted': True, 'session_id': session['session_id']})
    return _error('Unknown action')


def setup_device_mock_routes(app):
    app.router.add_route('*', PREFIX + '{token}/{tail:.*}', handle_data_plane)
    app.router.add_post('/internal/device-mock/call', handle_control)
    app.on_startup.append(_open_client)
    app.on_cleanup.append(_close_client)
