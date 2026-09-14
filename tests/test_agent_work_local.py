"""Opt-in isolated DB executor test. Model/tool calls are always stubbed.

LOMA_LOCAL_E2E=1 .venv/bin/python -m pytest tests/test_agent_work_local.py -q
Also used by the browser test after creating and enabling a job through the UI.
"""
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('LOMA_LOCAL_E2E') != '1', reason='Isolated local DB required')]


def connect():
    assert os.getenv('LOMA_LOCAL_E2E') == '1'
    env = dotenv_values(Path(__file__).parents[1] / '.env')
    assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    client = AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'])
    return client, client[env['OBSERVABILITY_DB_NAME']]


async def execute_fixture(flow_id):
    import scheduler.executor as executor
    from observability.observer import ConversationObserver
    client, db = connect()
    calls = []
    async def stub(**kwargs):
        calls.append(kwargs)
        yield 'Meeting brief ready (QA). No external tools or model were called.'
    try:
        flow = await db.flows.find_one({'flow_id': flow_id})
        assert flow['name'].endswith('(QA)')
        with patch.object(executor, 'get_db', return_value=db), patch.object(executor, 'stream_agent', stub), \
             patch.object(ConversationObserver, '_run_title_topic_enrichment', new_callable=AsyncMock), \
             patch.object(ConversationObserver, '_run_savings_estimation', new_callable=AsyncMock):
            await executor.execute_flow(flow_id)
        return calls
    finally:
        client.close()


async def test_pin_revoke_and_private_results():
    from scheduler.agent_work import prepare_agent_work
    from scheduler.models import create_flow
    client, db = connect()
    suffix = uuid4().hex
    email = f'qa-{suffix}@example.com'
    agent_id = f'qa-{suffix}'
    try:
        await db.users.insert_one({'email': email, 'status': 'active'})
        await db.agent_identities.insert_one({'agent_id': agent_id, 'name': 'Briefing QA', 'description': 'Meeting brief', 'created_by': email, 'visibility': 'private', 'identity_prompt': 'ORIGINAL PINNED INSTRUCTIONS'})
        data = await prepare_agent_work(db, {'name': 'Pinned brief (QA)', 'prompt': 'Draft my brief', 'agent_id': agent_id, 'run_as': email, 'created_by': {'source': email}, 'visibility': 'private', 'status': 'paused', 'cron': '0 9 * * 1-5', 'timezone': 'UTC', 'schedule_type': 'recurring'}, email)
        flow = await create_flow(db, data)
        assert not await execute_fixture(flow['flow_id'])  # paused never executes
        await db.flows.update_one({'flow_id': flow['flow_id']}, {'$set': {'status': 'active'}})
        await db.agent_identities.update_one({'agent_id': agent_id}, {'$set': {'identity_prompt': 'UNREVIEWED EDIT'}})
        calls = await execute_fixture(flow['flow_id'])
        assert len(calls) == 1
        assert calls[0]['user_email'] == email
        assert 'ORIGINAL PINNED INSTRUCTIONS' in calls[0]['prompt']
        assert 'UNREVIEWED EDIT' not in calls[0]['prompt']
        assert 'Do not send messages' in calls[0]['prompt']
        assert 'use MCP tools to take actions' not in calls[0]['prompt']
        run = await db.conversations.find_one({'metadata.flow_id': flow['flow_id']})
        assert run['metadata']['agent_id'] == agent_id
        assert run['metadata']['visibility'] == 'private'
        assert run['metadata']['user_name'] == email
        assert run['metadata']['agent_snapshot']['context'] == data['agent_snapshot']['context']
        for change in ({'status': 'disabled'}, {'status': 'active', 'deleted': True}):
            await db.agent_identities.update_one({'agent_id': agent_id}, {'$set': change})
            assert not await execute_fixture(flow['flow_id'])
        saved = await db.flows.find_one({'flow_id': flow['flow_id']})
        assert saved['run_count'] == 1
        assert 'unavailable' in saved['last_error']
    finally:
        await db.users.delete_one({'email': email})
        await db.agent_identities.delete_one({'agent_id': agent_id})
        await db.flows.delete_many({'agent_id': agent_id})
        await db.conversations.delete_many({'metadata.agent_id': agent_id})
        client.close()


async def test_timer_wakes_executor_once_then_waits():
    """Use an empty test-only scheduler, never init_scheduler's persisted jobs."""
    import asyncio
    from datetime import datetime, timezone, timedelta
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from scheduler.agent_work import prepare_agent_work
    from scheduler.models import create_flow
    from observability.observer import ConversationObserver
    import scheduler.engine as engine
    import scheduler.executor as executor
    client, db = connect()
    suffix = uuid4().hex
    email = f'qa-{suffix}@example.com'
    agent_id = f'qa-{suffix}'
    scheduler = AsyncIOScheduler(timezone='UTC')
    calls = []
    async def stub(**kwargs):
        calls.append(kwargs['user_email'])
        yield 'Timer-triggered test result. No external tools called.'
    try:
        await db.users.insert_one({'email': email, 'status': 'active'})
        await db.agent_identities.insert_one({'agent_id': agent_id, 'name': 'Timer QA', 'description': 'Timer test', 'created_by': email, 'visibility': 'private'})
        data = await prepare_agent_work(db, {'name': 'Timer wake-up (QA)', 'prompt': 'Return a test result', 'agent_id': agent_id, 'run_as': email, 'created_by': {'source': email}, 'visibility': 'private', 'status': 'paused', 'cron': '0 9 * * 1-5', 'timezone': 'UTC', 'schedule_type': 'recurring'}, email)
        flow = await create_flow(db, {**data, 'status': 'active'})
        with patch.object(engine, '_scheduler', scheduler), patch.object(executor, 'get_db', return_value=db), \
             patch.object(executor, 'stream_agent', stub), \
             patch.object(ConversationObserver, '_run_title_topic_enrichment', new_callable=AsyncMock), \
             patch.object(ConversationObserver, '_run_savings_estimation', new_callable=AsyncMock):
            scheduler.start()
            engine._add_job_for_flow(flow)
            job = scheduler.get_job(flow['flow_id'])
            assert job.max_instances == 1
            assert job.next_run_time.weekday() < 5
            # Accelerate this one test job; preserve its recurring trigger for the next wake.
            job.modify(next_run_time=datetime.now(timezone.utc) + timedelta(milliseconds=100))
            for _ in range(100):
                saved = await db.flows.find_one({'flow_id': flow['flow_id']})
                if saved['run_count'] == 1:
                    break
                await asyncio.sleep(.05)
            assert saved['run_count'] == 1
            assert calls == [email]
            assert scheduler.get_job(flow['flow_id']).next_run_time > datetime.now(timezone.utc)
            await asyncio.sleep(.15)
            assert calls == [email]
            scheduler.shutdown(wait=False)
            await asyncio.sleep(.05)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await db.users.delete_one({'email': email})
        await db.agent_identities.delete_one({'agent_id': agent_id})
        await db.flows.delete_many({'agent_id': agent_id})
        await db.conversations.delete_many({'metadata.agent_id': agent_id})
        client.close()
