"""No provider calls: real Mongo reservations and a stubbed Anthropic client."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from autonomy import core, costs, worker
from tests.test_bounded_work import db, setup, OWNER

RATES = {'model': 'qa-model', 'input_nusd_per_token': 3000, 'output_nusd_per_token': 15000}
USAGE = SimpleNamespace(input_tokens=100, output_tokens=30, cache_creation_input_tokens=0, cache_read_input_tokens=0)


@pytest.mark.parametrize('value', [None, 0, -1, True, 1.5, '1000000', 9999, 100000001])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        costs.validate_budget(value)


@pytest.mark.parametrize('raw', ['{}', 'null', '[]', '{', '{"qa-model":{"input_usd_per_million":"NaN","output_usd_per_million":3}}', '{"qa-model":{"input_usd_per_million":0,"output_usd_per_million":3}}'])
def test_pricing_fails_closed(monkeypatch, raw):
    monkeypatch.setenv('LOMA_WORK_PRICING_JSON', raw)
    with pytest.raises(ValueError):
        costs.pricing('qa-model')


def test_fractional_price_exact_model(monkeypatch):
    monkeypatch.setenv('LOMA_WORK_PRICING_JSON', json.dumps({'qa-model': {'input_usd_per_million': '0.8', 'output_usd_per_million': '4'}}))
    assert costs.pricing('qa-model')['input_nusd_per_token'] == 800
    with pytest.raises(ValueError):
        costs.pricing('other-model')


@pytest.mark.asyncio
async def test_concurrent_reservations_and_idempotent_settlement(db):
    _, run = await setup(db)
    results = await asyncio.gather(*[costs.reserve(db, run, RATES) for _ in range(10)], return_exceptions=True)
    entries = [r for r in results if isinstance(r, dict)]
    assert len(entries) == 2  # Two conservative reservations fit in $1, not three.
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['cost_committed_nusd'] <= 1000000000
    await asyncio.gather(*[costs.settle(db, run, entries[0], USAGE) for _ in range(8)])
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['cost_recorded_nusd'] == 750000
    assert saved['input_tokens_used'] == 100
    assert saved['cost_committed_nusd'] == entries[1]['reserved_nusd'] + 750000


@pytest.mark.asyncio
async def test_child_cannot_expand_root_budget(db):
    _, run = await setup(db, delegates=['agent-b'])
    await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {'snapshot.max_cost_microusd': 10000}})
    child = {**run, 'run_id': 'child', 'snapshot': {**run['snapshot'], 'max_cost_microusd': 100000000}}
    with pytest.raises(ValueError, match='budget exhausted'):
        await costs.reserve(db, child, RATES)
    assert 'cost_ledger' not in await db.agent_runs.find_one({'run_id': run['run_id']})


@pytest.mark.asyncio
async def test_unknown_usage_retains_reservation_and_overrun_blocks(db):
    _, run = await setup(db)
    entry = await costs.reserve(db, run, RATES)
    for usage in [SimpleNamespace(), SimpleNamespace(input_tokens=True, output_tokens=0),
                  SimpleNamespace(input_tokens=20, output_tokens=1, cache_read_input_tokens=2)]:
        with pytest.raises(ValueError):
            await costs.settle(db, run, entry, usage)
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['cost_committed_nusd'] == entry['reserved_nusd']
    with pytest.raises(ValueError, match='exceeded'):
        await costs.settle(db, run, entry, SimpleNamespace(input_tokens=costs.INPUT_CEILING + 1, output_tokens=1))
    with pytest.raises(ValueError):
        await costs.reserve(db, run, RATES)


@pytest.mark.asyncio
async def test_cancel_does_not_refund_unknown_charge(db):
    _, run = await setup(db)
    entry = await costs.reserve(db, run, RATES)
    await core.cancel(db, OWNER, run['run_id'])
    with pytest.raises(ValueError):
        await costs.reserve(db, run, RATES)
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['cost_committed_nusd'] == entry['reserved_nusd']
    # A late trustworthy response may settle once, but cannot revive cancelled work.
    await costs.settle(db, run, entry, USAGE)
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'cancelled'


def provider(monkeypatch, side_effect=None, content='{"op":"done","result":"ok"}'):
    import anthropic
    monkeypatch.setenv('LOMA_WORK_MODEL', 'qa-model')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'qa-not-a-real-key')
    monkeypatch.setenv('LOMA_WORK_PRICING_JSON', json.dumps({'qa-model': {'input_usd_per_million': '3', 'output_usd_per_million': '15'}}))
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=side_effect, return_value=SimpleNamespace(
        content=[SimpleNamespace(type='text', text=content)], usage=USAGE))
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(anthropic, 'AsyncAnthropic', MagicMock(return_value=manager))
    return client


@pytest.mark.asyncio
async def test_production_planner_meters_before_dispatch_and_counts_invalid_output(db, monkeypatch):
    _, run = await setup(db)
    client = provider(monkeypatch, content='not json')
    await worker.tick(db)  # actual planner, fake provider
    client.messages.create.assert_awaited_once()
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['status'] == 'failed'
    assert saved['cost_recorded_nusd'] == 750000  # Bad JSON is still billable.


@pytest.mark.asyncio
async def test_production_planner_stops_before_provider_when_budget_missing_or_small(db, monkeypatch):
    _, run = await setup(db)
    client = provider(monkeypatch)
    await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {'snapshot.max_cost_microusd': 10000}})
    await worker.tick(db)
    client.messages.create.assert_not_awaited()
    assert 'budget exhausted' in (await db.agent_runs.find_one({'run_id': run['run_id']}))['result']


@pytest.mark.asyncio
async def test_timeout_keeps_cost_reserved(db, monkeypatch):
    _, run = await setup(db)
    client = provider(monkeypatch, side_effect=TimeoutError())
    await worker.tick(db)
    client.messages.create.assert_awaited_once()
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['status'] == 'queued'  # retry is eligible, but must reserve again
    assert saved['cost_ledger'][0]['status'] == 'held'
    assert saved['cost_committed_nusd'] > 0


@pytest.mark.asyncio
async def test_missing_prices_and_revoked_account_do_not_call_provider(db, monkeypatch):
    _, run = await setup(db)
    client = provider(monkeypatch)
    monkeypatch.delenv('LOMA_WORK_PRICING_JSON')
    await worker.tick(db)
    client.messages.create.assert_not_awaited()
    assert 'pricing' in (await db.agent_runs.find_one({'run_id': run['run_id']}))['result']


@pytest.mark.asyncio
async def test_wrong_owner_cannot_reserve_from_root(db):
    _, run = await setup(db)
    with pytest.raises(ValueError):
        await costs.reserve(db, {**run, 'owner': 'wrong@example.com'}, RATES)
    assert 'cost_ledger' not in await db.agent_runs.find_one({'run_id': run['run_id']})


@pytest.mark.asyncio
async def test_retry_and_worker_restart_do_not_reuse_uncertain_funds(db, monkeypatch):
    _, run = await setup(db)
    client = provider(monkeypatch, side_effect=TimeoutError())
    for _ in range(3):
        await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {'wake_at': core.now()}})
        await worker.tick(db)
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert client.messages.create.await_count == 2
    assert saved['status'] == 'failed'
    assert 'budget exhausted' in saved['result']
    assert len(saved['cost_ledger']) == 2
    assert saved['cost_committed_nusd'] <= 1000000000
    assert all(e['status'] == 'held' for e in saved['cost_ledger'])
