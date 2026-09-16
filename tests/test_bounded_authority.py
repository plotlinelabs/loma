"""Current grants remain authoritative across human waits and delegation."""
from unittest.mock import AsyncMock

import pytest
from autonomy import core, worker
from tests.test_bounded_work import db, setup, OWNER, SEND


@pytest.mark.asyncio
@pytest.mark.parametrize('restriction', ['deny', 'recipient'])
async def test_tightened_policy_blocks_existing_human_approval(db, restriction):
    work, run = await setup(db)
    await worker.tick(db, AsyncMock(return_value=SEND))
    p = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, p['approval_id'], 1, 'approve')
    change = {'policy.actions': {**work['policy']['actions'], 'gmail.send': 'deny'}} if restriction == 'deny' else {'policy.recipients': []}
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': change})
    broker = AsyncMock()
    await worker.tick(db, broker=broker)
    broker.assert_not_awaited()
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'


@pytest.mark.asyncio
async def test_policy_is_checked_when_human_approves(db):
    work, run = await setup(db)
    await worker.tick(db, AsyncMock(return_value=SEND))
    p = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'policy.recipients': []}})
    with pytest.raises(ValueError, match='Recipient'):
        await core.decide(db, OWNER, p['approval_id'], 1, 'approve')
    assert (await db.agent_approvals.find_one({'approval_id': p['approval_id']}))['status'] == 'pending'


@pytest.mark.asyncio
async def test_child_cannot_keep_revoked_root_grant(db):
    work, run = await setup(db, delegates=['agent-b'])
    await worker.tick(db, AsyncMock(return_value={'op': 'delegate', 'agent_id': 'agent-b',
        'instructions': 'Draft reminder', 'success': 'Exact reminder'}))
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'policy.actions': {**work['policy']['actions'], 'gmail.send': 'deny'}}})
    child = await db.agent_runs.find_one({'parent_id': run['run_id']})
    broker = AsyncMock()
    await worker.tick(db, AsyncMock(return_value=SEND), broker)
    broker.assert_not_awaited()
    assert await db.agent_approvals.count_documents({'run_id': child['run_id']}) == 0


@pytest.mark.asyncio
async def test_saved_policy_approval_cannot_bypass_new_ask(db):
    work, run = await setup(db)
    # An automatic approval from an earlier policy cannot satisfy a later ask.
    await worker.tick(db, AsyncMock(return_value=SEND))
    p = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await db.agent_approvals.update_one({'approval_id': p['approval_id']}, {'$set': {
        'status': 'approved', 'approved_digest': p['digest'], 'decided_by': 'saved-policy'}})
    broker = AsyncMock()
    await worker.tick(db, broker=broker)
    broker.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_boundary_refuses_dry_run_even_when_called_directly(db):
    _, run = await setup(db, dry=True)
    broker = AsyncMock()
    with pytest.raises(ValueError, match='Safe tests'):
        await core.execute_proposal(db, {}, run, broker)
    broker.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_work_cannot_enqueue_preview(db):
    work, _ = await setup(db)
    work['revoked'] = True
    with pytest.raises(ValueError, match='revoked'):
        await core.enqueue(db, work, 'another', dry_run=True)


@pytest.mark.asyncio
async def test_root_revocation_stops_child_before_model_or_private_notes(db):
    work, run = await setup(db, delegates=['agent-b'])
    await worker.tick(db, AsyncMock(return_value={'op': 'delegate', 'agent_id': 'agent-b',
        'instructions': 'Research', 'success': 'Summary'}))
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'revoked': True}})
    planner, broker = AsyncMock(), AsyncMock()
    await worker.tick(db, planner, broker)
    planner.assert_not_awaited()
    broker.assert_not_awaited()
