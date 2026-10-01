import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient

from api import human_tasks as h, human_task_worker as worker
from api.session_gateway import verify_dashboard_signature
from tools.human_tasks import parser

OWNER = 'requester@example.com'
ASSIGNEE = 'finance@example.com'


async def setup():
    db = AsyncMongoMockClient().test
    await db.users.insert_many([{'email': e, 'status': 'active'} for e in (OWNER, ASSIGNEE, 'other@example.com')])
    await db.conversations.insert_one({'conversation_id': 'source', 'metadata': {'user_name': OWNER},
                                       'source': 'dashboard', 'prompt': 'Original request', 'status': 'completed'})
    return db


def spec(**kwargs):
    return {'source_conversation_id': 'source', 'assignee': ASSIGNEE,
            'request_key': 'invoice-123-reissue', 'kind': 'approval',
            'title': 'Approve invoice correction', 'details': 'Approve a credit of INR 100 for invoice 123',
            'ticket_url': 'https://app.usepylon.com/issues/123', **kwargs}


async def new(db, **kwargs):
    return (await h.create(db, OWNER, spec(**kwargs)))[0]


@pytest.mark.asyncio
async def test_create_deduplicates_even_concurrently():
    db = await setup()
    results = await asyncio.gather(*(h.create(db, OWNER, spec()) for _ in range(10)))
    assert sum(created for _, created in results) == 1
    assert len({d['conversation_id'] for d, _ in results}) == 1
    doc = results[0][0]
    assert doc['task_status'] == 'active' and doc['status'] == 'completed'
    assert doc['metadata']['user_name'] == ASSIGNEE
    assert doc['human_task']['decision'] is None
    assert await db.conversations.count_documents({'human_task': {'$exists': True}}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'details': 'different amount'}, {'kind': 'information'}, {'title': 'different'}])
async def test_key_cannot_change_request(change):
    db = await setup()
    await new(db)
    with pytest.raises(web.HTTPConflict):
        await h.create(db, OWNER, spec(**change))


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('assignee', 'unknown@example.com'), ('source_conversation_id', 'private')])
async def test_unknown_assignee_or_source_rejected(field, value):
    db = await setup()
    with pytest.raises(web.HTTPException):
        await h.create(db, OWNER, spec(**{field: value}))


@pytest.mark.asyncio
async def test_shared_source_does_not_grant_execution_identity():
    db = await setup()
    await db.conversations.update_one({'conversation_id': 'source'}, {'$set': {'metadata.visibility': 'shared'}})
    with pytest.raises(web.HTTPNotFound):
        await h.create(db, ASSIGNEE, spec())


@pytest.mark.asyncio
async def test_disabled_requester_and_assignee():
    db = await setup()
    for email in (OWNER, ASSIGNEE):
        await db.users.update_one({'email': email}, {'$set': {'status': 'disabled'}})
        with pytest.raises(web.HTTPForbidden):
            await new(db)
        await db.users.update_one({'email': email}, {'$set': {'status': 'active'}})


@pytest.mark.asyncio
async def test_only_assignee_can_decide_and_only_requester_can_assign():
    db = await setup(); doc = await new(db); cid = doc['conversation_id']
    with pytest.raises(web.HTTPForbidden):
        await h.respond(db, OWNER, cid, {'decision': 'approve', 'response': 'yes', 'version': 1})
    with pytest.raises(web.HTTPNotFound):
        await h.get(db, 'other@example.com', cid)
    with pytest.raises(web.HTTPForbidden):
        await h.assign(db, ASSIGNEE, cid, OWNER, 1)


@pytest.mark.asyncio
async def test_reassign_invalidates_old_assignee_and_version():
    db = await setup(); doc = await new(db); cid = doc['conversation_id']
    reassigned = await h.assign(db, OWNER, cid, 'other@example.com', 1)
    assert reassigned['human_task']['version'] == 2
    with pytest.raises(web.HTTPNotFound):
        await h.respond(db, ASSIGNEE, cid, {'decision': 'approve', 'response': 'yes', 'version': 1})
    with pytest.raises(web.HTTPConflict):
        await h.respond(db, 'other@example.com', cid, {'decision': 'approve', 'response': 'yes', 'version': 1})


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['approve', 'reject', 'provide_information'])
async def test_decision_is_explicit_immutable_and_retry_safe(decision):
    db = await setup(); doc = await new(db); cid = doc['conversation_id']
    payload = {'decision': decision, 'response': 'Reviewed request', 'version': 1}
    first = await h.respond(db, ASSIGNEE, cid, payload)
    second = await h.respond(db, ASSIGNEE, cid, payload)
    assert first == second
    assert first['human_task']['responded_by'] == ASSIGNEE
    assert first['human_task']['resume_state'] == 'queued'
    assert first['task_status'] == 'done'
    with pytest.raises(web.HTTPConflict):
        await h.respond(db, ASSIGNEE, cid, {**payload, 'response': 'a different response'})
    with pytest.raises(web.HTTPConflict):
        await h.assign(db, OWNER, cid, OWNER, 2)


@pytest.mark.asyncio
async def test_information_cannot_be_approved():
    db = await setup(); doc = await new(db, kind='information')
    with pytest.raises(ValueError):
        await h.respond(db, ASSIGNEE, doc['conversation_id'], {'decision': 'approve', 'response': 'yes', 'version': 1})


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [None, True, {'$gt': 0}, '1', -1])
async def test_version_validation(version):
    db = await setup(); doc = await new(db)
    with pytest.raises(ValueError):
        await h.respond(db, ASSIGNEE, doc['conversation_id'], {'decision': 'approve', 'response': 'yes', 'version': version})
    with pytest.raises(ValueError):
        await h.assign(db, OWNER, doc['conversation_id'], OWNER, version)


@pytest.mark.asyncio
async def test_done_is_not_approval():
    db = await setup(); doc = await new(db)
    await db.conversations.update_one({'_id': doc['_id']}, {'$set': {'task_status': 'done'}})
    updated = await h.get(db, OWNER, doc['conversation_id'])
    assert updated['human_task']['state'] == 'pending'
    assert updated['human_task']['decision'] is None
    assert updated['human_task']['resume_state'] == 'waiting'


@pytest.mark.asyncio
async def test_notification_retry_deduplicates(monkeypatch):
    monkeypatch.setattr('observability.notifications.fire_user_push', lambda *a, **k: None)
    db = await setup(); doc = await new(db)
    await worker.notify_assignment(db, doc)
    await worker.notify_assignment(db, doc)
    assert await db.notifications.count_documents({}) == 1
    notice = await db.notifications.find_one({})
    assert notice['user_email'] == ASSIGNEE
    assert notice['conversation_id'] == doc['conversation_id']


async def answered(db):
    doc = await new(db)
    return await h.respond(db, ASSIGNEE, doc['conversation_id'], {'decision': 'approve', 'response': 'Reviewed', 'version': 1})


@pytest.mark.asyncio
async def test_dispatch_once_and_failure_never_replayed():
    db = await setup(); doc = await answered(db)
    runner = AsyncMock(side_effect=RuntimeError('provider timeout'))
    await worker.dispatch(db, doc, runner)
    await worker.dispatch(db, doc, runner)
    runner.assert_awaited_once()
    latest = await h.get(db, OWNER, doc['conversation_id'])
    assert latest['human_task']['resume_state'] == 'needs_review'


@pytest.mark.asyncio
async def test_busy_source_remains_queued(monkeypatch):
    db = await setup(); doc = await answered(db)
    monkeypatch.setattr('agent.active_streams.try_claim', AsyncMock(return_value=False))
    runner = AsyncMock()
    await worker.dispatch(db, doc, runner)
    runner.assert_not_called()
    assert (await h.get(db, OWNER, doc['conversation_id']))['human_task']['resume_state'] == 'queued'


@pytest.mark.asyncio
async def test_resume_success_notifies_requester(monkeypatch):
    monkeypatch.setattr('observability.notifications.fire_user_push', lambda *a, **k: None)
    db = await setup(); doc = await answered(db); runner = AsyncMock()
    await worker.tick(db, runner)
    await worker.tick(db, runner)
    await worker.tick(db, runner)
    runner.assert_awaited_once()
    assert await db.notifications.count_documents({'user_email': OWNER}) == 1
    assert (await h.get(db, OWNER, doc['conversation_id']))['human_task']['resume_state'] == 'completed'


@pytest.mark.asyncio
async def test_stopped_source_never_runs():
    db = await setup(); doc = await answered(db)
    await db.conversations.update_one({'conversation_id': 'source'}, {'$set': {'error': 'Stopped by user'}})
    runner = AsyncMock(); await worker.dispatch(db, doc, runner)
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_requester_cannot_resume_after_source_ownership_changes():
    db = await setup(); doc = await answered(db)
    await db.conversations.update_one({'conversation_id': 'source'}, {'$set': {'metadata.user_name': ASSIGNEE}})
    runner = AsyncMock(); await worker.dispatch(db, doc, runner)
    runner.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', ['none', 'email', 'body', 'path', 'expired', 'missing'])
async def test_dashboard_signature(monkeypatch, tamper):
    key = 'x' * 32; monkeypatch.setenv('LOMA_WORK_GATEWAY_SECRET', key)
    stamp = str(int(time.time()) - (120 if tamper == 'expired' else 0))
    raw = b'{"decision":"approve"}'
    path = '/api/human-tasks/123'
    payload = '\n'.join([stamp, 'POST', path, ASSIGNEE, hashlib.sha256(raw).hexdigest()])
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    request = SimpleNamespace(headers={'X-Work-Time': stamp, 'X-Work-Signature': signature},
                              content_length=len(raw), method='POST', path=path,
                              read=AsyncMock(return_value=raw), get=lambda *a: ASSIGNEE)
    if tamper == 'email': request.get = lambda *a: OWNER
    if tamper == 'body': request.read = AsyncMock(return_value=b'{}')
    if tamper == 'path': request.path += '4'
    if tamper == 'missing': request.headers.clear()
    if tamper == 'none':
        assert await verify_dashboard_signature(request) == (ASSIGNEE, raw)
    else:
        with pytest.raises(web.HTTPUnauthorized): await verify_dashboard_signature(request)


def test_cli_has_no_decision_command():
    with pytest.raises(SystemExit):
        parser().parse_args(['--user-email', OWNER, '--auth-token', 't', 'approve'])


@pytest.mark.asyncio
async def test_run_source_preserves_owner_history_model_and_scope(monkeypatch):
    import agent.client
    import observability.observer
    db = await setup(); doc = await answered(db)
    await db.conversations.update_one({'conversation_id': 'source'}, {'$set': {
        'messages': [{'role': 'assistant', 'content': 'Already fetched invoice'}],
        'model': 'test/model', 'tool_config': {'skills': ['account-receivables']}}})
    source = await db.conversations.find_one({'conversation_id': 'source'})
    calls = []
    class Observer:
        def __init__(self, db, **kwargs): self.metadata = kwargs['metadata']; self.conversation_id = kwargs['conversation_id']
        async def resume(self): pass
        def _stop_heartbeat(self): pass
    async def stream(**kwargs):
        calls.append(kwargs)
        yield 'completed'
    monkeypatch.setattr(agent.client, 'stream_agent', stream)
    monkeypatch.setattr(observability.observer, 'ConversationObserver', Observer)
    await worker.run_source(db, source, doc)
    assert calls[0]['user_email'] == OWNER
    assert calls[0]['selected_model'] == 'test/model'
    assert calls[0]['tool_config'] == source['tool_config']
    assert 'Already fetched invoice' in calls[0]['conversation_context']
    assert 'Original request' in calls[0]['conversation_context']
    assert 'Decision: approve' in calls[0]['prompt']
    assert 'Rejection does not authorize' in calls[0]['prompt']
    updated = await db.conversations.find_one({'conversation_id': 'source'})
    assert updated['metadata']['human_task_resume_id'] == doc['conversation_id']


@pytest.mark.asyncio
async def test_restart_marks_dispatched_review_not_replay(monkeypatch):
    db = await setup(); doc = await answered(db)
    await db.conversations.update_one({'_id': doc['_id']}, {'$set': {'human_task.resume_state': 'running'}})
    monkeypatch.setattr('observability.db.get_db', lambda: db)
    monkeypatch.setenv('LOMA_ENABLE_SCHEDULER', 'true')
    monkeypatch.setattr(worker, 'tick', AsyncMock())
    lifecycle = worker.lifecycle(None)
    await anext(lifecycle)
    latest = await h.get(db, OWNER, doc['conversation_id'])
    assert latest['human_task']['resume_state'] == 'needs_review'
    await lifecycle.aclose()


@pytest.mark.asyncio
async def test_recovery_does_not_replay_human_continuation(monkeypatch):
    import recovery
    db = await setup()
    source = await db.conversations.find_one({'conversation_id': 'source'})
    source['metadata']['human_task_resume_id'] = 'task'
    monkeypatch.setattr(recovery, 'get_db', lambda: db)
    stream = AsyncMock()
    monkeypatch.setattr(recovery, 'stream_agent', stream)
    await recovery._resume_conversation(source)
    stream.assert_not_called()
    assert (await db.conversations.find_one({'conversation_id': 'source'}))['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_board_patch_cannot_change_human_task(monkeypatch):
    from api import task_routes
    db = await setup(); doc = await new(db)
    monkeypatch.setattr(task_routes, 'get_db', lambda: db)
    request = SimpleNamespace(match_info={'conversation_id': doc['conversation_id']},
                              get=lambda key, default=None: ASSIGNEE if key == 'user_email' else 'operator',
                              json=AsyncMock(return_value={'task_status': 'done'}))
    result = await task_routes.handle_update_task(request)
    assert result.status == 409
    assert (await h.get(db, OWNER, doc['conversation_id']))['human_task']['decision'] is None


@pytest.mark.asyncio
async def test_cli_rejects_bad_token_before_db_access(monkeypatch):
    from tools import human_tasks
    monkeypatch.setattr(human_tasks, 'verify_user_auth_token', lambda *a: False)
    with pytest.raises(ValueError, match='auth token'):
        await human_tasks.execute(SimpleNamespace(auth_token='bad', user_email=OWNER))
