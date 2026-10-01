"""Opt-in concurrency checks on a disposable Mongo database, no provider writes."""
import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

from api import human_tasks as service, human_task_worker as worker


@pytest.mark.asyncio
async def test_real_mongo_concurrent_create_notification_and_decision():
    if os.getenv('LOMA_LOCAL_E2E') != '1':
        pytest.skip('Requires opt-in isolated Mongo')
    env = dotenv_values(Path(__file__).parents[1] / '.env')
    assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    name = 'loma_local_handoff_test_' + uuid.uuid4().hex
    client = AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'], tz_aware=True)
    db = client[name]
    try:
        owner, reviewer = 'requester@example.test', 'reviewer@example.test'
        await db.users.insert_many([{'email': email, 'status': 'active'} for email in (owner, reviewer)])
        await db.conversations.insert_one({'conversation_id': 'source', 'metadata': {'user_name': owner}})
        data = {'source_conversation_id': 'source', 'assignee': reviewer, 'kind': 'approval',
                'title': 'Fixture approval', 'details': 'Approve fixture invoice TEST-123 only', 'request_key': 'test-123'}
        results = await asyncio.gather(*(service.create(db, owner, data) for _ in range(20)))
        assert sum(created for _, created in results) == 1
        assert len({doc['conversation_id'] for doc, _ in results}) == 1
        doc = results[0][0]
        with patch('observability.notifications.fire_user_push', lambda *a, **kw: None):
            await asyncio.gather(*(worker.notify_assignment(db, doc) for _ in range(10)))
        assert await db.notifications.count_documents({}) == 1
        payload = {'decision': 'approve', 'response': 'Approved fixture only', 'version': 1}
        await service.respond(db, reviewer, doc['conversation_id'], payload)
        await service.respond(db, reviewer, doc['conversation_id'], payload)
        stored = await service.get(db, owner, doc['conversation_id'])
        assert stored['human_task']['version'] == 2
        assert stored['human_task']['resume_state'] == 'queued'
    finally:
        assert name.startswith('loma_local_handoff_test_')
        await client.drop_database(name)
        client.close()
