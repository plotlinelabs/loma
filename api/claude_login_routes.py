"""Owner-scoped REST login sessions; browser never receives credentials."""
import asyncio
import time

from aiohttp import web
from api.auth_helpers import get_user_email
from isolation import claude_login as login
from observability.db import get_db

SESSIONS = web.AppKey('claude_login_sessions', dict)


def owner(request):
    email = get_user_email(request)
    if not email:
        raise web.HTTPUnauthorized()
    # Preview fallback identities and unknown/deleted users cannot add credentials.
    if request.get('preview_fallback_user'):
        raise web.HTTPForbidden()
    return email


async def allowed(email):
    db = get_db()
    if db is None:
        return False
    user = await db.users.find_one({'email': email, 'deleted': {'$ne': True}}, {'status': 1})
    return bool(user) and user.get('status', 'active') == 'active'


async def current(request):
    email = owner(request)
    if not await allowed(email):
        raise web.HTTPForbidden()
    session = request.app[SESSIONS].get(email)
    if session is None or session.id != request.match_info['session_id'] or session.expires <= time.time():
        raise web.HTTPNotFound()
    return session


async def start(request):
    email = owner(request)
    if not await allowed(email):
        raise web.HTTPForbidden()
    try:
        connection = login.transport()
    except (ValueError, KeyError, OSError):
        return web.json_response({'error': 'Claude login is unavailable. Ask your administrator to start the full Docker Compose stack, including loma-login.'}, status=503)
    sessions = request.app[SESSIONS]
    # No await between limits check and reservation; bounded even with concurrent POSTs.
    for key, item in list(sessions.items()):
        if item.expires <= time.time() and not item.cancelling:
            if item.task:
                item.task.cancel()
            del sessions[key]
    if email in sessions and sessions[email].cancelling:
        return web.json_response({'error': 'Cancellation in progress. Try again shortly.'}, status=409)
    if email in sessions and sessions[email].state in {'starting', 'waiting'}:
        return web.json_response(sessions[email].public(), headers={'Cache-Control': 'no-store'})
    if len(sessions) >= 64 and email not in sessions:
        return web.json_response({'error': 'Login service is busy. Try again later.'}, status=503)
    session = login.Login(email)
    sessions[email] = session
    try:
        async with asyncio.timeout(10):
            await login.begin(email, session.id)
        if sessions.get(email) is not session or session.cancelling:
            return web.json_response({'error': 'Login was cancelled. Please start again.'}, status=409)
        session.task = asyncio.create_task(login.run_login(session, connection, lambda: allowed(email)))
    except BaseException:
        if sessions.get(email) is session:
            sessions.pop(email, None)
        raise
    return web.json_response(session.public(), status=201, headers={'Cache-Control': 'no-store'})


async def active(request):
    email = owner(request)
    if not await allowed(email):
        raise web.HTTPForbidden()
    session = request.app[SESSIONS].get(email)
    data = session.public() if session and session.expires > time.time() and session.state in {'starting', 'waiting'} else None
    return web.json_response(data, headers={'Cache-Control': 'no-store'})


async def status(request):
    session = await current(request)
    return web.json_response(session.public(), headers={'Cache-Control': 'no-store'})


async def submit(request):
    session = await current(request)
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {'code'}:
            raise ValueError()
        code = login.normalize_code(body['code'])
    except (ValueError, KeyError, TypeError):
        return web.json_response({'error': 'Enter the authorization code from Anthropic.'}, status=400)
    if session.state != 'waiting' or session.submitted or session.cancelling:
        return web.json_response({'error': 'This login is not waiting for a code.'}, status=409)
    session.submitted = True
    session.queue.put_nowait(code)
    return web.json_response({'ok': True})


async def cancel(request):
    email = owner(request)
    if not await allowed(email):
        raise web.HTTPForbidden()
    session = request.app[SESSIONS].get(email)
    # A retried DELETE for an already removed session must not affect a new one.
    if session is None or session.id != request.match_info['session_id']:
        return web.json_response({'ok': True})
    if session.cancelling:
        return web.json_response({'error': 'Cancellation in progress. Try again shortly.'}, status=409)
    session.cancelling = True
    try:
        if session.task:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
        # Fence late completion without removing a previous successful connection.
        async with asyncio.timeout(10):
            await login.begin(session.owner, 'cancelled-' + session.id)
        if request.app[SESSIONS].get(email) is session:
            request.app[SESSIONS].pop(email)
    finally:
        session.cancelling = False
    return web.json_response({'ok': True})


async def cleanup(app):
    tasks = [s.task for s in app[SESSIONS].values() if s.task]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def setup_claude_login_routes(app):
    app[SESSIONS] = {}
    app.router.add_post('/api/claude-auth/login', start)
    app.router.add_get('/api/claude-auth/login', active)
    app.router.add_get('/api/claude-auth/login/{session_id}', status)
    app.router.add_post('/api/claude-auth/login/{session_id}/code', submit)
    app.router.add_delete('/api/claude-auth/login/{session_id}', cancel)
    app.on_cleanup.append(cleanup)
