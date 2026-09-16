"""Reporting uses the same settled ledger; no secondary mutable counters."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from isolation.accounting import account_usage, ModelBudget
from tests.test_worker_accounting import SPEC, RECEIPT, AUTH, grant, body
from tests.test_bounded_work import db, OWNER


@pytest.mark.asyncio
async def test_usage_totals_unknown_holds_and_duplicate_settlement(db):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    for i, account in enumerate(['a', 'a', 'b']):
        auth = replace(AUTH, run_id=f'run-{i}')
        budget = ModelBudget(db, auth, replace(SPEC, account_id=account))
        await budget.initialize()
        await budget.reserve(auth, 'call', grant(), body())
        if i != 1:
            await asyncio.gather(*[budget.record_usage(auth, 'call', RECEIPT) for _ in range(3)])
        await budget.stop()
    report = await account_usage(db, start, datetime.now(timezone.utc))
    a, b = report['accounts']
    assert (a['account_id'], a['runs'], a['calls'], a['recorded_nusd'], a['held_nusd']) == ('a', 2, 2, 440, 5000)
    assert (a['input_tokens'], a['output_tokens'], a['cache_read_tokens'], a['unsettled_calls']) == (100, 20, 30, 1)
    assert b['recorded_nusd'] == 440 and b['held_nusd'] == 0
    assert 'response-1' not in json.dumps(report) and AUTH.user_email not in json.dumps(report)
    # A late, identical provider receipt reduces holds exactly once.
    budget = ModelBudget(db, replace(AUTH, run_id='run-1'), replace(SPEC, account_id='a'))
    await budget.record_usage(budget.authority, 'call', RECEIPT)
    a = (await account_usage(db, start, datetime.now(timezone.utc)))['accounts'][0]
    assert a['recorded_nusd'] == 880 and a['held_nusd'] == a['unsettled_calls'] == 0


@pytest.mark.asyncio
async def test_window_is_run_creation_half_open_and_preserves_empty_runs(db):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc); end = start + timedelta(days=1)
    for i, at in enumerate([start - timedelta(seconds=1), start, end]):
        ledger = ModelBudget(db, replace(AUTH, run_id=str(i)), SPEC)
        await ledger.initialize()
        await ledger.collection.update_one(ledger.key, {'$set': {'created_at': at}})
    row = (await account_usage(db, start, end))['accounts'][0]
    assert row['runs'] == 1 and row['calls'] == 0 and row['input_tokens'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['naive', 'reverse', 'long'])
async def test_window_validation(db, kind):
    start = datetime.now(timezone.utc); end = start + timedelta(days=1)
    if kind == 'naive': start = start.replace(tzinfo=None)
    elif kind == 'reverse': end = start
    else: end = start + timedelta(days=32)
    with pytest.raises(ValueError): await account_usage(db, start, end)


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['chatter', 'maintainer', 'admin'])
async def test_reporting_route_requires_current_admin(db, monkeypatch, role):
    from api import routes
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    req = make_mocked_request('GET', '/api/remote-account-usage')
    req['user_email'] = OWNER; req['system_role'] = role
    await db.users.update_one({'email': OWNER}, {'$set': {'system_role': role}})
    if role != 'admin':
        with pytest.raises(web.HTTPForbidden): await routes.handle_remote_account_usage(req)
        return
    response = await routes.handle_remote_account_usage(req)
    assert response.status == 200 and json.loads(response.text)['accounts'] == []
    assert response.headers['Cache-Control'] == 'no-store'
    await db.users.update_one({'email': OWNER}, {'$set': {'status': 'disabled'}})
    with pytest.raises(web.HTTPForbidden): await routes.handle_remote_account_usage(req)


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['?start=bad', '?end=2026-01-01', '?owner=other', '?start=2026-01-01T00:00:00Z&end=2026-03-01T00:00:00Z'])
async def test_reporting_route_rejects_bad_windows(db, monkeypatch, query):
    from api import routes
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    await db.users.update_one({'email': OWNER}, {'$set': {'system_role': 'admin'}})
    req = make_mocked_request('GET', '/api/remote-account-usage' + query)
    req['user_email'] = OWNER; req['system_role'] = 'admin'
    assert (await routes.handle_remote_account_usage(req)).status == 400


@pytest.mark.asyncio
async def test_reporting_denies_missing_identity_deleted_and_demoted_admin(db, monkeypatch):
    from api import routes
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    req = make_mocked_request('GET', '/api/remote-account-usage')
    req['system_role'] = 'admin'
    with pytest.raises(web.HTTPUnauthorized): await routes.handle_remote_account_usage(req)
    req['user_email'] = OWNER
    for changes in [{'system_role': 'chatter'}, {'system_role': 'admin', 'deleted': True}]:
        await db.users.update_one({'email': OWNER}, {'$set': changes})
        with pytest.raises(web.HTTPForbidden): await routes.handle_remote_account_usage(req)
