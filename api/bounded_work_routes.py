"""Human control plane for bounded work. Requires a signed dashboard session.

Legacy X-User-Email alone cannot approve, change grants or read private work.
The planner has no API caller, session cookie, signing secret or HTTP tool.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time

from aiohttp import web
from pymongo import ReturnDocument
from api.auth_helpers import get_user_email, require_operator_or_above
from api.agent_identity_routes import _serialize
from observability.db import get_db
from autonomy import core
from autonomy.worker import tick, planner_ready

logger = logging.getLogger(__name__)


def enabled():
    return os.getenv('LOMA_BOUNDED_WORK_ENABLED', '').lower() == 'true'


async def verify(request):
    if not enabled():
        raise web.HTTPNotFound(text='Bounded work is not enabled')
    key = os.getenv('LOMA_WORK_GATEWAY_SECRET', '')
    stamp = request.headers.get('X-Work-Time', '')
    email = get_user_email(request)
    if request.content_length and request.content_length > 100000:
        raise web.HTTPRequestEntityTooLarge(max_size=100000, actual_size=request.content_length)
    raw = await request.read()
    try:
        fresh = abs(time.time() - int(stamp)) < 60
    except ValueError:
        fresh = False
    payload = '\n'.join([stamp, request.method, request.path, email, hashlib.sha256(raw).hexdigest()])
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if len(key) < 32 or not fresh or not email or not hmac.compare_digest(signature, request.headers.get('X-Work-Signature', '')):
        raise web.HTTPUnauthorized(text='Verified dashboard session required')
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(text='Database unavailable')
    await core.authority_account(db, email)
    require_operator_or_above(request)
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        raise ValueError('Invalid JSON') from None
    if not isinstance(body, dict):
        raise ValueError('Expected an object')
    return db, email, body


async def handle(request):
    try:
        db, owner, body = await verify(request)
        route = request.match_info.get('tail', '')
        parts = route.split('/')
        method = request.method
        if method == 'GET' and route == 'overview':
            work = await db.agent_work.find({'owner': owner, 'parent_only': {'$ne': True}}).sort('created_at', -1).limit(100).to_list(100)
            schedules = await db.flows.find({'bounded_work_id': {'$in': [w['work_id'] for w in work]}},
                                           {'bounded_work_id': 1, 'next_run_at': 1, 'last_error': 1}).to_list(100)
            by_work = {f['bounded_work_id']: f for f in schedules}
            for job in work:
                scheduled = by_work.get(job['work_id'], {})
                job['next_run_at'] = scheduled.get('next_run_at')
                job['schedule_error'] = scheduled.get('last_error')
            runs = await db.agent_runs.find({'owner': owner}).sort('created_at', -1).limit(100).to_list(100)
            pending = await db.agent_approvals.find({'owner': owner, 'status': 'pending'}).sort('created_at', 1).limit(200).to_list(200)
            recent = await db.agent_approvals.find({'owner': owner, 'status': {'$ne': 'pending'}}).sort('created_at', -1).limit(100).to_list(100)
            approvals = pending + recent
            return response({'work': work, 'runs': runs, 'approvals': approvals, 'model_ready': planner_ready(),
                             'google_connected': bool(await db.oauth_tokens.find_one({'user_email': owner, 'provider': 'google'}, {'_id': 1})),
                             'worker_enabled': os.getenv('LOMA_ENABLE_SCHEDULER', 'true').lower() == 'true'})
        if route == 'work' and method == 'POST':
            return response(await core.create_work(db, owner, body), 201)
        if len(parts) == 3 and parts[0] == 'work' and method == 'POST':
            work = await db.agent_work.find_one({'work_id': parts[1], 'owner': owner})
            if not work:
                raise ValueError('Work not found')
            action = parts[2]
            if action in ('enable', 'pause', 'revoke'):
                await core.authority(db, owner, work['agent_id'])
                if action == 'enable' and work.get('revoked'):
                    raise ValueError('Revoked work cannot be enabled. Create new work.')
                if action == 'enable' and not planner_ready():
                    raise ValueError('Model connection is missing. Ask an admin to configure bounded work.')
                changes = {'paused': action != 'enable'}
                if action == 'revoke':
                    changes['revoked'] = True
                    for run in await db.agent_runs.find({'work_id': work['work_id'], 'status': {'$nin': list(core.TERMINAL)}}).to_list(100):
                        await core.cancel(db, owner, run['run_id'])
                result = await db.agent_work.find_one_and_update({'work_id': work['work_id'], 'owner': owner}, {'$set': changes}, return_document=ReturnDocument.AFTER)
                if work.get('flow_id'):
                    from scheduler.engine import add_flow_to_scheduler, remove_flow_from_scheduler, get_next_run_time
                    flow = await db.flows.find_one_and_update({'flow_id': work['flow_id']}, {'$set': {'status': 'active' if action == 'enable' else 'paused'}}, return_document=ReturnDocument.AFTER)
                    if flow:
                        if action == 'enable':
                            await add_flow_to_scheduler(flow)
                        else:
                            await remove_flow_from_scheduler(flow['flow_id'])
                        await db.flows.update_one({'flow_id': flow['flow_id']}, {'$set': {'next_run_at': get_next_run_time(flow['flow_id'])}})
                return response(result)
            if action == 'schedule':
                from scheduler.agent_work import prepare_agent_work
                from scheduler.models import create_flow, update_flow
                from scheduler.engine import remove_flow_from_scheduler
                data = await prepare_agent_work(db, {
                    'name': work['title'], 'prompt': work['instructions'], 'agent_id': work['agent_id'],
                    'run_as': owner, 'created_by': {'source': owner}, 'visibility': 'private',
                    'status': 'paused', 'trigger_type': 'scheduled', 'schedule_type': 'recurring',
                    'cron': body.get('cron'), 'timezone': body.get('timezone'),
                }, owner)
                existing = await db.flows.find_one({'bounded_work_id': work['work_id']})
                if existing:
                    # Editing a schedule always pauses it for explicit re-enablement.
                    flow = await update_flow(db, existing['flow_id'], {
                        'cron': data['cron'], 'timezone': data['timezone'],
                        'status': 'paused', 'next_run_at': None})
                    await remove_flow_from_scheduler(existing['flow_id'])
                else:
                    data['bounded_work_id'] = work['work_id']
                    flow = await create_flow(db, data)
                await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'flow_id': flow['flow_id'], 'cron': flow['cron'], 'timezone': flow['timezone'], 'paused': True}})
                return response(flow, 201)
            if action in ('run', 'test', 'event'):
                if not planner_ready():
                    raise ValueError('Model connection is missing. Ask an admin to configure bounded work.')
                key = core.text(body.get('event_key'), 'Event ID', 200)
                return response(await core.enqueue(db, work, key, dry_run=action == 'test'))
        if len(parts) == 3 and parts[0] == 'runs' and method == 'POST':
            run_id, action = parts[1:]
            if action == 'cancel':
                await core.cancel(db, owner, run_id)
                return response({'ok': True})
            if action == 'answer':
                run = await db.agent_runs.find_one({'run_id': run_id, 'owner': owner})
                if not run:
                    raise ValueError('Run not found')
                await core.current_authority(db, run, check_lease=False)
                answer = core.text(body.get('answer'), 'Answer', 4000)
                result = await db.agent_runs.find_one_and_update({'run_id': run_id, 'owner': owner, 'status': 'needs_input'},
                    {'$set': {'status': 'queued', 'wake_at': core.now(), 'decision': None, 'question': None},
                     '$inc': {'step': 1}, '$push': {'history': {'kind': 'answer', 'answer': answer, 'at': core.now()}}},
                    return_document=ReturnDocument.AFTER)
                if not result:
                    raise ValueError('Question is no longer waiting for an answer')
                return response(result)
        if len(parts) == 2 and parts[0] == 'approvals' and method == 'POST':
            return response(await core.decide(db, owner, parts[1], body.get('version'), body.get('decision'), body.get('args')))
        if len(parts) >= 2 and parts[0] == 'notes':
            agent_id = parts[1]
            await core.authority(db, owner, agent_id)
            if method == 'GET':
                return response({'notes': await db.agent_notes.find({'owner': owner, 'agent_id': agent_id}).limit(10).to_list(10)})
            if method == 'POST':
                note_id = core.text(body.get('note_id'), 'Note ID', 100)
                title = core.text(body.get('title'), 'Note title', 120)
                content = core.text(body.get('content'), 'Note content', 6000)
                # Updating an existing note retains its stable identity.
                exists = await db.agent_notes.find_one({'owner': owner, 'agent_id': agent_id, 'note_id': note_id})
                if not exists and await db.agent_notes.count_documents({'owner': owner, 'agent_id': agent_id}) >= 10:
                    raise ValueError('Keep up to 10 notes per agent')
                await db.agent_notes.update_one({'owner': owner, 'agent_id': agent_id, 'note_id': note_id},
                    {'$set': {'title': title, 'content': content, 'updated_at': core.now()}}, upsert=True)
                return response({'ok': True})
            if method == 'DELETE' and len(parts) == 3:
                await db.agent_notes.delete_one({'owner': owner, 'agent_id': agent_id, 'note_id': parts[2]})
                return response({'ok': True})
        raise web.HTTPNotFound()
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)


def response(value, status=200):
    return web.json_response(_serialize(value), status=status)


async def lifecycle(app):
    task = None
    if enabled():
        db = get_db()
        if db is not None:
            await core.indexes(db)
        if os.getenv('LOMA_ENABLE_SCHEDULER', 'true').lower() == 'true':
            async def work_loop():
                while True:
                    try:
                        if db is not None:
                            await tick(db)
                    except Exception:
                        logger.exception('Bounded worker tick failed')
                    await asyncio.sleep(2)
            task = asyncio.create_task(work_loop())
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def setup_bounded_work_routes(app):
    app.router.add_route('*', '/api/bounded-work/{tail:.*}', handle)
    app.cleanup_ctx.append(lifecycle)
