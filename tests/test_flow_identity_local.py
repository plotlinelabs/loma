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
async def test_execute_and_revoke(kind):
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
