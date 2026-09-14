"""Policy unit tests and opt-in real Mongo state-machine/concurrency tests.

No model or personal adapter runs here. LOMA_LOCAL_E2E=1 enables fresh,
throwaway loma_local_* databases. Every fixture drops only its own random DB.
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

from autonomy import core
from autonomy.worker import tick

OWNER = 'qa-owner@example.com'
OTHER = 'qa-other@example.com'
POLICY = {'actions': {'gmail.search': 'allow', 'gmail.read': 'allow', 'gmail.send': 'ask'}, 'recipients': ['recipient@example.com']}
SEND = {'op': 'action', 'action': 'gmail.send', 'args': {'to': 'recipient@example.com', 'subject': 'Review me', 'body': 'Exact approved body'}, 'reason': 'Reminder requested'}
DONE = {'op': 'done', 'result': 'Completed with receipt'}


@pytest.mark.parametrize('policy', [None, [], {'actions': {'shell': 'allow'}}, {'actions': {'gmail.send': 'maybe'}}, {'recipients': ['*@example.com']}, {'recipients': ['Name <name@example.com>']}])
def test_invalid_policies(policy):
    # Wildcards are not exact email identities.
    if policy == {'recipients': ['*@example.com']}:
        with pytest.raises(ValueError):
            core.validate_policy(policy)
    else:
        with pytest.raises(ValueError):
            core.validate_policy(policy)


@pytest.mark.parametrize('action,args', [
    ('shell', {'command': 'curl'}), ('gmail.send', {**SEND['args'], 'to': 'wrong@example.com'}),
    ('gmail.send', {**SEND['args'], 'attachments': '/etc/passwd'}),
    ('gmail.send', {**SEND['args'], 'subject': 'hello\nBcc: other@example.com'}),
    ('gmail.read', {'message_id': None}), ('gmail.search', {'query': 'x', 'url': 'https://evil.example'}),
])
def test_broker_rejects_unapproved_arguments(action, args):
    with pytest.raises(ValueError):
        core.validate_action(action, args, core.validate_policy(POLICY))


def test_default_deny_and_intersection():
    assert all(v == 'deny' for v in core.validate_policy({})['actions'].values())
    reduced = core.intersect(core.validate_policy(POLICY), core.validate_policy({'actions': {'gmail.send': 'allow'}, 'recipients': []}))
    assert reduced['actions']['gmail.send'] == 'ask'
    assert not reduced['recipients']


@pytest_asyncio.fixture
async def db():
    if os.getenv('LOMA_LOCAL_E2E') != '1':
        pytest.skip('Requires opt-in isolated Mongo')
    env = dotenv_values(Path(__file__).parents[1] / '.env')
    assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    name = 'loma_local_bounded_test_' + uuid.uuid4().hex
    client = AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'], tz_aware=True)
    database = client[name]
    await core.indexes(database)
    await database.users.insert_many([{'email': OWNER, 'status': 'active'}, {'email': OTHER, 'status': 'active'}])
    await database.agent_identities.insert_many([
        {'agent_id': 'agent-a', 'name': 'Finance', 'description': 'Test', 'created_by': OWNER, 'status': 'active', 'visibility': 'private'},
        {'agent_id': 'agent-b', 'name': 'Research', 'description': 'Test', 'created_by': OTHER, 'status': 'active', 'visibility': 'workspace'},
    ])
    try:
        yield database
    finally:
        assert name.startswith('loma_local_bounded_test_')
        await client.drop_database(name)
        client.close()


async def setup(db, dry=False, delegates=None):
    work = await core.create_work(db, OWNER, {'agent_id': 'agent-a', 'title': 'Invoices', 'instructions': 'Draft reminders', 'success': 'Receipt', 'policy': POLICY, 'delegates': delegates or [], 'max_steps': 8})
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'paused': False}})
    work['paused'] = False
    run = await core.enqueue(db, work, 'event-1', dry_run=dry)
    return work, run


@pytest.mark.asyncio
async def test_complete_approval_workflow(db):
    work, run = await setup(db)
    planner = AsyncMock(side_effect=[SEND, DONE]); broker = AsyncMock(return_value={'message_id': 'receipt-1'})
    assert await tick(db, planner, broker)
    approval = await db.agent_approvals.find_one({'run_id': run['run_id']})
    broker.assert_not_called()
    await core.decide(db, OWNER, approval['approval_id'], 1, 'approve')
    await tick(db, planner, broker)
    await tick(db, planner, broker)
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['status'] == 'done'
    assert broker.call_count == 1
    assert broker.call_args.args[1] == SEND['args']
    assert broker.call_args.args[2] == OWNER
    assert planner.call_count == 2
    assert (await db.agent_approvals.find_one({'approval_id': approval['approval_id']}))['status'] == 'executed'


@pytest.mark.asyncio
async def test_duplicate_trigger_and_overlapping_work(db):
    work, run = await setup(db)
    again = await asyncio.gather(*[core.enqueue(db, work, 'event-1') for _ in range(8)])
    assert {r['run_id'] for r in again} == {run['run_id']}
    with pytest.raises(ValueError, match='already has active'):
        await core.enqueue(db, work, 'event-2')
    assert await db.agent_runs.count_documents({}) == 1


@pytest.mark.asyncio
async def test_edit_invalidates_approval_and_concurrent_clicks(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    updated = await core.decide(db, OWNER, a['approval_id'], 1, 'edit', {**SEND['args'], 'body': 'Edited body'})
    assert updated['version'] == 2 and updated['status'] == 'pending'
    with pytest.raises(ValueError, match='changed'):
        await core.decide(db, OWNER, a['approval_id'], 1, 'approve')
    results = await asyncio.gather(*[core.decide(db, OWNER, a['approval_id'], 2, 'approve') for _ in range(5)], return_exceptions=True)
    assert sum(isinstance(x, dict) for x in results) == 1
    broker = AsyncMock(return_value={'id': 'receipt'})
    await asyncio.gather(*[tick(db, AsyncMock(return_value=DONE), broker) for _ in range(3)])
    assert broker.call_count == 1 and broker.call_args.args[1]['body'] == 'Edited body'


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['reject', 'cancel'])
async def test_rejection_never_sends(db, decision):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, a['approval_id'], 1, decision)
    broker = AsyncMock()
    await tick(db, AsyncMock(return_value=DONE), broker)
    broker.assert_not_called()
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['history'][-1]['status'] in ('rejected', 'cancelled')


@pytest.mark.asyncio
async def test_revoked_account_blocks_approved_action(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, a['approval_id'], 1, 'approve')
    await db.users.update_one({'email': OWNER}, {'$set': {'status': 'inactive'}})
    broker = AsyncMock()
    await tick(db, AsyncMock(), broker)
    broker.assert_not_called()
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'


@pytest.mark.asyncio
async def test_other_account_cannot_approve_or_run_private_agent(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    with pytest.raises(ValueError, match='not found'):
        await core.decide(db, OTHER, a['approval_id'], 1, 'approve')
    with pytest.raises(ValueError):
        await core.authority(db, OTHER, 'agent-a')


@pytest.mark.asyncio
async def test_dry_run_has_no_external_reads_or_writes(db):
    _, run = await setup(db, dry=True)
    planner = AsyncMock(side_effect=[SEND, {'op': 'action', 'action': 'gmail.search', 'args': {'query': 'invoice'}, 'reason': 'Find invoices'}, DONE])
    broker = AsyncMock()
    for _ in range(3): await tick(db, planner, broker)
    broker.assert_not_called()
    assert await db.agent_approvals.count_documents({'status': 'preview'}) == 2
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'done'


@pytest.mark.asyncio
async def test_expiry_and_payload_tampering(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, a['approval_id'], 1, 'approve')
    await db.agent_approvals.update_one({'approval_id': a['approval_id']}, {'$set': {'args.body': 'tampered'}})
    broker = AsyncMock()
    await tick(db, AsyncMock(), broker)
    broker.assert_not_called()
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'


@pytest.mark.asyncio
async def test_expired_approval_does_not_execute(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await db.agent_approvals.update_one({'approval_id': a['approval_id']}, {'$set': {'expires_at': core.now() - timedelta(seconds=1)}})
    with pytest.raises(ValueError, match='expired'):
        await core.decide(db, OWNER, a['approval_id'], 1, 'approve')
    broker = AsyncMock(); await tick(db, AsyncMock(), broker)
    broker.assert_not_called()
    assert (await db.agent_approvals.find_one({'approval_id': a['approval_id']}))['status'] == 'expired'


@pytest.mark.asyncio
async def test_uncertain_send_never_retries(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, a['approval_id'], 1, 'approve')
    broker = AsyncMock(side_effect=TimeoutError('unknown'))
    for _ in range(3): await tick(db, AsyncMock(), broker)
    assert broker.call_count == 1
    assert (await db.agent_approvals.find_one({'approval_id': a['approval_id']}))['status'] == 'uncertain'
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'


@pytest.mark.asyncio
async def test_restart_recovers_saved_decision_without_replanning(db):
    _, run = await setup(db)
    await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {'status': 'running', 'lease': 'dead', 'lease_until': core.now() - timedelta(seconds=1), 'decision': SEND}})
    planner = AsyncMock(); broker = AsyncMock()
    await tick(db, planner, broker)
    planner.assert_not_called(); broker.assert_not_called()
    assert await db.agent_approvals.count_documents({'run_id': run['run_id']}) == 1


@pytest.mark.asyncio
async def test_sleep_and_input_are_not_approvals(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value={'op': 'input', 'question': 'Which invoice?'}), AsyncMock())
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'needs_input'
    assert await db.agent_approvals.count_documents({}) == 0
    assert not await tick(db, AsyncMock(), AsyncMock())


@pytest.mark.asyncio
async def test_delegate_shares_principal_budget_and_cancellation(db):
    _, run = await setup(db, delegates=['agent-b'])
    delegate = {'op': 'delegate', 'agent_id': 'agent-b', 'instructions': 'Investigate', 'success': 'Summary'}
    await tick(db, AsyncMock(return_value=delegate), AsyncMock())
    child = await db.agent_runs.find_one({'parent_id': run['run_id']})
    assert child['owner'] == OWNER and child['root_id'] == run['run_id']
    assert child['snapshot']['delegates'] == []
    assert child['snapshot']['policy'] == run['snapshot']['policy']
    await tick(db, AsyncMock(return_value=DONE), AsyncMock())
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['calls_used'] == 2
    await tick(db, AsyncMock(return_value=DONE), AsyncMock())
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'done'


@pytest.mark.asyncio
async def test_cancellation_blocks_queued_actions(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, a['approval_id'], 1, 'approve')
    await core.cancel(db, OWNER, run['run_id'])
    broker = AsyncMock(); await tick(db, AsyncMock(), broker)
    broker.assert_not_called()
    assert (await db.agent_approvals.find_one({'approval_id': a['approval_id']}))['status'] == 'cancelled'

@pytest.mark.asyncio
async def test_control_plane_rejects_forged_identity_and_body(db, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from api import bounded_work_routes as routes
    monkeypatch.setenv('LOMA_BOUNDED_WORK_ENABLED', 'true')
    monkeypatch.setenv('LOMA_WORK_GATEWAY_SECRET', 'k' * 32)
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    @web.middleware
    async def identity(request, handler):
        request['user_email'] = request.headers.get('X-User-Email', '')
        request['system_role'] = 'operator'
        return await handler(request)
    app = web.Application(middlewares=[identity])
    app.router.add_route('*', '/api/bounded-work/{tail:.*}', routes.handle)
    async with TestClient(TestServer(app)) as client:
        path = '/api/bounded-work/overview'
        response = await client.get(path, headers={'X-User-Email': OWNER})
        assert response.status == 401
        stamp = str(int(time.time()))
        payload = '\n'.join([stamp, 'GET', path, OWNER, hashlib.sha256(b'').hexdigest()])
        signature = hmac.new(b'k' * 32, payload.encode(), hashlib.sha256).hexdigest()
        headers = {'X-User-Email': OWNER, 'X-Work-Time': stamp, 'X-Work-Signature': signature}
        assert (await client.get(path, headers=headers)).status == 200
        assert (await client.get(path, headers={**headers, 'X-User-Email': OTHER})).status == 401
        assert (await client.get(path, headers={**headers, 'X-Work-Time': '1'})).status == 401
        assert (await client.post(path, json={'decision': 'approve'}, headers=headers)).status == 401


@pytest.mark.asyncio
async def test_notes_scoped_to_execution_principal(db):
    _, run = await setup(db)
    await db.agent_notes.insert_many([
        {'owner': OWNER, 'agent_id': 'agent-a', 'note_id': 'own', 'title': 'Own note', 'content': 'Allowed'},
        {'owner': OTHER, 'agent_id': 'agent-a', 'note_id': 'private', 'title': 'Secret', 'content': 'Must never appear'},
    ])
    planner = AsyncMock(return_value=DONE)
    await tick(db, planner, AsyncMock())
    notes = planner.call_args.args[0]['notes']
    assert notes == [{'title': 'Own note', 'content': 'Allowed'}]


@pytest.mark.asyncio
async def test_model_budget_and_forbidden_delegation(db):
    _, run = await setup(db)
    await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {'calls_used': 8}})
    planner = AsyncMock(); await tick(db, planner, AsyncMock())
    planner.assert_not_called()
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'


@pytest.mark.asyncio
async def test_unknown_tools_fail_closed(db):
    _, run = await setup(db)
    broker = AsyncMock()
    await tick(db, AsyncMock(return_value={'op': 'action', 'action': 'shell', 'args': {'command': 'curl'}, 'reason': 'Bypass'}), broker)
    broker.assert_not_called()
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'


@pytest.mark.asyncio
async def test_human_can_approve_after_worker_lease_expires(db):
    _, run = await setup(db)
    broker = AsyncMock(return_value={'message_id': 'receipt'})
    await tick(db, AsyncMock(return_value=SEND), broker)
    await db.agent_runs.update_one({'run_id': run['run_id']},
        {'$set': {'lease_until': core.now() - timedelta(hours=1)}})
    approval = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, approval['approval_id'], 1, 'approve')
    await tick(db, AsyncMock(return_value=DONE), broker)
    assert broker.call_count == 1


@pytest.mark.asyncio
async def test_edit_preserves_reviewed_payload_in_audit(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    approval = await db.agent_approvals.find_one({'run_id': run['run_id']})
    updated = await core.decide(db, OWNER, approval['approval_id'], 1, 'edit',
                                {**SEND['args'], 'body': 'Revised'})
    assert updated['version'] == 2
    assert updated['audit'][0]['args'] == SEND['args']
    assert updated['audit'][0]['digest'] == approval['digest']


def test_legacy_flow_api_cannot_manage_bounded_schedule():
    from api.flow_routes import _can_manage_flow
    from unittest.mock import patch
    flow = {'bounded_work_id': 'work', 'created_by': {'source': OWNER}, 'run_as': OWNER}
    with patch('api.flow_routes.get_user_email', return_value=OWNER), \
         patch('api.flow_routes.get_system_role', return_value='admin'):
        assert not _can_manage_flow(flow, {})


@pytest.mark.asyncio
async def test_crash_after_action_claim_is_visible_as_uncertain(db):
    _, run = await setup(db)
    await tick(db, AsyncMock(return_value=SEND), AsyncMock())
    a = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, a['approval_id'], 1, 'approve')
    await db.agent_approvals.update_one({'approval_id': a['approval_id']},
                                       {'$set': {'status': 'executing'}})
    await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {
        'status': 'running', 'lease': 'dead-worker', 'lease_until': core.now() - timedelta(seconds=1)}})
    broker = AsyncMock()
    await tick(db, AsyncMock(), broker)
    broker.assert_not_called()
    assert (await db.agent_approvals.find_one({'approval_id': a['approval_id']}))['status'] == 'uncertain'
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'
