"""Agent-schedule contract and revocation tests; no network or model calls."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from api import flow_routes as routes
from scheduler.agent_work import prepare_agent_work, validate_agent_work
from scheduler.engine import _fix_crontab_dow
from apscheduler.triggers.cron import CronTrigger
from tests.test_flow_identity import database, Request


def fixture():
    db = database()
    db.agent_identities.find_one = AsyncMock(return_value={
        "agent_id": "a1", "name": "Briefing", "description": "Meeting notes",
        "identity_prompt": "Pinned original instructions", "created_by": "owner@example.com",
        "default_model": "anthropic/test",
    })
    data = {"name": "Morning brief", "prompt": "Prepare notes", "agent_id": "a1",
            "status": "paused", "schedule_type": "recurring", "cron": "0 9 * * 1-5",
            "timezone": "Asia/Kolkata", "run_as": "owner@example.com", "visibility": "private",
            "created_by": {"source": "owner@example.com"}}
    return db, data


@pytest.mark.asyncio
async def test_create_pins_server_configuration_and_account():
    db, data = fixture()
    data['agent_snapshot'] = {'context': 'forged'}
    with patch.object(routes, 'get_db', return_value=db):
        response = await routes.handle_create_flow(Request(data))
    assert response.status == 201
    saved = json.loads(response.text)['flow']
    assert 'Pinned original instructions' in saved['agent_snapshot']['context']
    assert 'forged' not in saved['agent_snapshot']['context']
    assert saved['model'] == 'anthropic/test'
    assert saved['agent_id'] == 'a1'
    assert saved['status'] == 'paused'


@pytest.mark.asyncio
@pytest.mark.parametrize('updates', [
    {'agent_id': 42}, {'agent_id': ''}, {'status': 'active'}, {'prompt': ' '},
    {'visibility': 'shared'}, {'run_as': 'victim@example.com'}, {'trigger_type': 'webhook'},
    {'schedule_type': 'once'}, {'cron': 'invalid'}, {'cron': None}, {'timezone': 'Invalid/Zone'},
])
async def test_invalid_or_unsafe_schedule_rejected(updates):
    db, data = fixture()
    with pytest.raises(ValueError):
        await prepare_agent_work(db, {**data, **updates}, 'owner@example.com')


@pytest.mark.asyncio
async def test_revoked_agent_or_account_blocks_existing_schedule():
    db, data = fixture()
    saved = await prepare_agent_work(db, data, 'owner@example.com')
    db.agent_identities.find_one.return_value = None
    with pytest.raises(ValueError, match='unavailable'):
        await validate_agent_work(db, saved)
    db.agent_identities.find_one.return_value = {'name': 'Exists'}
    db.users.find_one.return_value = {'status': 'inactive'}
    with pytest.raises(ValueError, match='blocked'):
        await validate_agent_work(db, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize('patch_body', [
    {'agent_id': None}, {'agent_snapshot': {'context': 'override'}},
    {'visibility': 'shared'}, {'run_as': 'victim@example.com'}, {'status': 'active'},
    {'trigger_type': 'webhook'}, {'schedule_type': 'once'}, {'timezone': 'bad'},
])
async def test_existing_link_cannot_bypass_invariants(patch_body):
    db, data = fixture()
    saved = await prepare_agent_work(db, data, 'owner@example.com')
    db.flows.find_one = AsyncMock(return_value=saved)
    with patch.object(routes, 'get_db', return_value=db):
        response = await routes.handle_update_flow(Request(patch_body))
    assert response.status == 400
    db.flows.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_resume_revalidates_agent_and_pause_remains_available():
    db, data = fixture()
    saved = await prepare_agent_work(db, data, 'owner@example.com')
    db.flows.find_one = AsyncMock(return_value=saved)
    db.agent_identities.find_one.return_value = None
    with patch.object(routes, 'get_db', return_value=db):
        response = await routes.handle_resume_flow(Request())
        assert response.status == 400
        with patch.object(routes, 'update_flow', AsyncMock(return_value=saved)), patch.object(routes, 'remove_flow_from_scheduler', AsyncMock()):
            assert (await routes.handle_pause_flow(Request())).status == 200


@pytest.mark.asyncio
async def test_runtime_blocks_missing_agent_before_model_call():
    import scheduler.executor as executor
    db, data = fixture()
    saved = await prepare_agent_work(db, data, 'owner@example.com')
    saved.update(status='active', flow_id='f1')
    db.flows.find_one = AsyncMock(return_value=saved)
    db.agent_identities.find_one.return_value = None
    with patch.object(executor, 'get_db', return_value=db), patch.object(executor, 'stream_agent') as stream:
        await executor.execute_flow('f1')
        stream.assert_not_called()
    assert 'unavailable' in db.flows.update_one.call_args.args[1]['$set']['last_error']


def test_weekday_cron_is_monday_not_tuesday():
    trigger = CronTrigger.from_crontab(_fix_crontab_dow('0 9 * * 1-5'), timezone='UTC')
    # Friday after the wake-up should next fire on Monday, never Saturday.
    next_run = trigger.get_next_fire_time(None, datetime(2026, 9, 18, 10, tzinfo=timezone.utc))
    assert next_run == datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
