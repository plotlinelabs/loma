"""Local-only fixture/step driver. Cannot call a model or personal tool."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).parents[2]))
from autonomy.worker import tick
from autonomy.core import indexes

async def main():
    root = Path(__file__).parents[2]
    env = dotenv_values(root / '.env'); front = dotenv_values(root / 'dashboard/.env')
    assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_bounded_')
    assert env['LOMA_ENABLE_SCHEDULER'] == 'false'
    client = AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'], tz_aware=True)
    db = client[env['OBSERVABILITY_DB_NAME']]
    owner = front['USER_NAME'].lower()
    await indexes(db)
    if sys.argv[1] == 'seed':
        for collection in ('agent_work', 'agent_runs', 'agent_approvals', 'agent_notes'):
            await db[collection].delete_many({'owner': owner})
        await db.flows.delete_many({'bounded_work_id': {'$exists': True}})

        await db.agent_identities.update_one({'agent_id': 'bounded-qa'}, {'$set': {
            'agent_id': 'bounded-qa', 'name': 'Invoice assistant QA', 'description': 'A local test agent',
            'created_by': owner, 'visibility': 'private', 'status': 'active', 'avatar': {'seed': 1, 'motif': 'round'},
        }}, upsert=True)
    elif sys.argv[1] == 'schedule':
        from unittest.mock import patch
        import scheduler.executor as executor
        flow = await db.flows.find_one({'bounded_work_id': {'$exists': True}, 'status': 'active'})
        assert flow is not None
        with patch.object(executor, 'get_db', return_value=db), patch('api.bounded_work_routes.enabled', return_value=True):
            await executor.execute_flow(flow['flow_id'])
            await executor.execute_flow(flow['flow_id'])  # same-minute duplicate coalesces
        assert await db.agent_runs.count_documents({'dry_run': False}) == 1
    elif sys.argv[1] == 'step':
        async def planner(context):
            if context['history']:
                return {'op': 'done', 'result': 'QA complete. External action was simulated, not sent.'}
            return {'op': 'action', 'action': 'gmail.send', 'args': {'to': 'recipient@example.com', 'subject': 'Invoice follow-up QA', 'body': 'Please review invoice INV-QA. This is a local test proposal.'}, 'reason': 'Follow up on the test invoice'}
        broker = AsyncMock(return_value={'message_id': 'SIMULATED-RECEIPT-NOT-SENT'})
        for _ in range(4):
            await tick(db, planner, broker)
        if broker.call_count:
            assert broker.call_args.args[2] == owner
        print('Simulated adapter calls:', broker.call_count)
    elif sys.argv[1] == 'expire':
        from datetime import timedelta
        from autonomy.core import now
        run = await db.agent_runs.find_one({'owner': owner, 'status': 'queued', 'dry_run': True})
        assert run is not None
        await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {'deadline_at': now() - timedelta(seconds=1)}})
        planner, broker = AsyncMock(), AsyncMock()
        await tick(db, planner, broker)
        planner.assert_not_called(); broker.assert_not_called()
        assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'
    elif sys.argv[1] == 'verify':
        assert await db.agent_runs.count_documents({'owner': owner, 'status': 'done', 'dry_run': True}) == 1
        assert await db.agent_runs.count_documents({'owner': owner, 'status': 'done', 'dry_run': False}) == 1
        assert await db.agent_approvals.count_documents({'owner': owner, 'status': 'executed'}) == 1
        assert await db.agent_notes.count_documents({'owner': owner}) == 0
        assert await db.flows.count_documents({'bounded_work_id': {'$type': 'string'}}) == 1
        assert (await db.flows.find_one({'bounded_work_id': {'$type': 'string'}}))['status'] == 'paused'
        print('Database assertions passed')
    client.close()

asyncio.run(main())
