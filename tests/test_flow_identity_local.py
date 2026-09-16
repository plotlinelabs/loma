"""Opt-in isolated Mongo integration; never calls a model or connected tool.

LOMA_LOCAL_E2E=1 .venv/bin/python -m pytest tests/test_flow_identity_local.py -q
Reads the clone's .env; refuses any database not named loma_local_*.
"""
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("LOMA_LOCAL_E2E") != "1", reason="Requires isolated local stack")]


@pytest.mark.parametrize("kind", ["scheduled", "webhook"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_execute_and_revoke(kind, legacy):
    from scheduler.models import create_flow
    from observability.observer import ConversationObserver
    import scheduler.executor as scheduled
    import scheduler.webhook_executor as webhook
    env = dotenv_values(Path(__file__).parents[1] / '.env')
    assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    client = AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'])
    db = client[env['OBSERVABILITY_DB_NAME']]
    email = f'qa-execution-{uuid.uuid4().hex}@example.com'
    await db.users.insert_one({'email': email, 'status': 'active'})
    flow = await create_flow(db, {'name': 'Local execution QA', 'prompt': 'No external actions', 'prompt_template': 'Draft {{payload}}', 'trigger_type': kind, 'schedule_type': 'recurring', 'run_as': email, 'model': 'anthropic/test', 'created_by': {'source': email}})
    if legacy:
        from datetime import datetime, timezone
        from scheduler.legacy_identity import backfill_legacy_identities
        await db.flows.update_one({'flow_id': flow['flow_id']}, {
            '$unset': {'run_as': '', 'identity_version': ''},
            '$set': {'created_at': datetime(2026, 1, 1, tzinfo=timezone.utc)},
        })
        assert (await backfill_legacy_identities(db))['assigned'] == 1
        assert (await backfill_legacy_identities(db))['assigned'] == 0
        flow = await db.flows.find_one({'flow_id': flow['flow_id']})
        assert flow['run_as_backfill']['account'] == email
    log_id = str(uuid.uuid4())
    await db.webhook_logs.insert_one({'log_id': log_id})
    calls = []

    async def fake_stream(**kwargs):
        calls.append(kwargs['user_email'])
        yield 'QA execution result. No external action performed.'

    module = scheduled if kind == 'scheduled' else webhook
    async def run():
        if kind == 'scheduled':
            await module.execute_flow(flow['flow_id'])
        else:
            await module.execute_webhook_flow(flow, b'{}', {}, log_id)

    try:
        with patch.object(module, 'get_db', return_value=db), patch.object(module, 'stream_agent', fake_stream), \
             patch.object(ConversationObserver, '_run_title_topic_enrichment', new_callable=AsyncMock), \
             patch.object(ConversationObserver, '_run_savings_estimation', new_callable=AsyncMock), \
             patch.object(webhook, 'ingest_dashboard_chat', new_callable=AsyncMock):
            await run()
            saved = await db.flows.find_one({'flow_id': flow['flow_id']})
            assert saved['run_count'] == 1
            assert calls == [email]
            conversation = await db.conversations.find_one({'metadata.flow_id': flow['flow_id']})
            assert conversation['metadata']['run_as'] == email
            await db.users.update_one({'email': email}, {'$set': {'status': 'inactive'}})
            # Webhook receives the same old snapshot, but must recheck persisted identity.
            await run()
            saved = await db.flows.find_one({'flow_id': flow['flow_id']})
            assert saved['run_count'] == 1
            assert 'blocked' in saved['last_error']
            assert calls == [email]
    finally:
        await db.users.delete_one({'email': email})
        client.close()


async def test_legacy_migration_filters_and_admin_race():
    from datetime import datetime, timezone
    from scheduler.legacy_identity import backfill_legacy_identities
    from types import SimpleNamespace
    env = dotenv_values(Path(__file__).parents[1] / '.env')
    assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    client = AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'])
    db = client[env['OBSERVABILITY_DB_NAME']]
    email = f'qa-migration-{uuid.uuid4().hex}@example.com'
    await db.users.insert_one({'email': email, 'status': 'active'})
    prefix = uuid.uuid4().hex
    baseline = {'created_at': datetime(2026, 1, 1, tzinfo=timezone.utc), 'created_by': {'source': email}, 'status': 'paused'}
    cases = {
        'eligible': {}, 'null': {'run_as': None}, 'empty': {'run_as': ''},
        'explicit': {'run_as': 'different@example.com'},
        'invalid_explicit': {'run_as': {}},
        'new': {'created_at': datetime(2027, 1, 1, tzinfo=timezone.utc)},
        'marked': {'identity_version': 1}, 'agent': {'agent_id': 'a1'},
        'ambiguous': {'created_by': {'source': email, 'user_name': 'different@example.com'}},
        'unknown': {'created_by': {'source': 'unknown@example.com'}},
        'missing_date': {'created_at': None},
        'already_migrated': {'run_as_backfill': {'migration': 'legacy_creator_identity_v1'}},
    }
    documents = [{**baseline, **values, 'flow_id': prefix + name} for name, values in cases.items()]
    await db.flows.insert_many(documents)
    try:
        result = await backfill_legacy_identities(db, dry_run=True)
        assert result['eligible'] == 3
        assert result['assigned'] == 0
        # Another instance or admin can save an explicit account between read and write.
        collection = db.flows
        async def racing_write(operations, **kwargs):
            await collection.update_one({'flow_id': prefix + 'eligible'}, {'$set': {'run_as': 'admin-selected@example.com'}})
            return await collection.bulk_write(operations, **kwargs)
        proxy = SimpleNamespace(users=db.users, flows=SimpleNamespace(find=collection.find, bulk_write=racing_write))
        result = await backfill_legacy_identities(proxy)
        assert result['assigned'] == 2
        assert (await backfill_legacy_identities(db))['assigned'] == 0
        saved = {doc['flow_id'][len(prefix):]: doc async for doc in db.flows.find({'flow_id': {'$in': [d['flow_id'] for d in documents]}})}
        assert saved['eligible']['run_as'] == 'admin-selected@example.com'
        for name in ('null', 'empty'):
            assert saved[name]['run_as'] == email
            assert saved[name]['run_as_backfill']['account'] == email
            assert saved[name]['status'] == 'paused'
        for name in cases.keys() - {'eligible', 'null', 'empty'}:
            assert saved[name].get('run_as') == cases[name].get('run_as')
        # Deactivated creators are not backfilled.
        await db.users.update_one({'email': email}, {'$set': {'status': 'inactive'}})
        await db.flows.insert_one({**baseline, 'flow_id': prefix + 'inactive'})
        assert (await backfill_legacy_identities(db))['assigned'] == 0
    finally:
        await db.flows.delete_many({'flow_id': {'$regex': '^' + prefix}})
        await db.users.delete_one({'email': email})
        client.close()
