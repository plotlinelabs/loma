"""Pinned pricing and real isolated-Mongo accounting; no paid/provider calls."""
import asyncio
from dataclasses import replace

import pytest
from isolation.accounting import BudgetSpec, ModelBudget
from isolation.models import ModelDenied
from isolation.usage import UsageReceipt
from tests.test_bounded_work import db  # Fresh random DB, dropped by fixture.
from tests.test_worker_models import AUTH, grant, body, fixture, close

SPEC = BudgetSpec('responses', 'test-model', 'account-ref', 1000, 100, 3, 10, 1, 4, 8000, 32)
RECEIPT = UsageReceipt('responses', 'response-1', 'test-model', 100, 20, 30)


@pytest.mark.parametrize('changes', [dict(input_ceiling=True), dict(output_ceiling=0),
    dict(input_rate=0), dict(output_rate=-1), dict(cache_read_rate=1.2), dict(cache_write_rate=True),
    dict(budget_nusd=100_000_000_001), dict(max_calls=129), dict(model=''), dict(protocol='x')])
def test_invalid_contract(changes):
    with pytest.raises(ValueError):
        replace(SPEC, **changes)


def test_protocol_cache_semantics():
    assert SPEC.charge(RECEIPT) == 440
    anthropic = replace(SPEC, protocol='messages')
    assert anthropic.charge(replace(RECEIPT, protocol='messages', cache_write_tokens=10)) == 570
    assert SPEC.reservation == 5000


@pytest.mark.parametrize('changes', [dict(protocol='messages'), dict(model='other'),
    dict(input_tokens=1001), dict(output_tokens=101), dict(cache_read_tokens=101),
    dict(cache_write_tokens=1), dict(input_tokens=True), dict(response_id='')])
def test_invalid_receipts(changes):
    with pytest.raises(ValueError):
        SPEC.charge(replace(RECEIPT, **changes))


async def budget(db, spec=SPEC):
    result = ModelBudget(db, AUTH, spec)
    await result.initialize()
    return result


async def saved(db):
    return await db.isolated_model_budgets.find_one({'_id': AUTH.run_id})


@pytest.mark.asyncio
async def test_concurrent_reserve_and_settle_once(db):
    ledger = await budget(db)
    results = await asyncio.gather(*[ledger.reserve(AUTH, str(i), grant(), body()) for i in range(10)], return_exceptions=True)
    assert sum(r is None for r in results) == 1
    row = await saved(db)
    call_id = row['calls'][0]['call_id']
    await asyncio.gather(*[ledger.record_usage(AUTH, call_id, RECEIPT) for _ in range(10)])
    row = await saved(db)
    assert row['committed_nusd'] == row['recorded_nusd'] == 440
    assert row['call_count'] == len(row['calls']) == 1
    await ledger.reserve(AUTH, 'next', grant(), body())
    assert (await saved(db))['committed_nusd'] == 5440


@pytest.mark.asyncio
async def test_unknown_and_stop_never_refund_late_receipt_is_idempotent(db):
    ledger = await budget(db)
    await ledger.reserve(AUTH, 'call', grant(), body())
    await ledger.record_usage(AUTH, 'call', None)
    await ledger.settle(AUTH, 'call', 'stream_ended')
    assert (await saved(db))['committed_nusd'] == 5000
    await ledger.stop()
    await ledger.record_usage(AUTH, 'call', RECEIPT)
    reopened = await budget(db)
    await reopened.record_usage(AUTH, 'call', RECEIPT)
    assert (await saved(db))['committed_nusd'] == 440
    with pytest.raises(ModelDenied):
        await reopened.reserve(AUTH, 'new', grant(), body())


@pytest.mark.asyncio
async def test_wrong_owner_contract_grant_and_duplicate_reserve(db):
    ledger = await budget(db)
    other = replace(AUTH, user_email='other@example.test')
    with pytest.raises(ModelDenied):
        await ModelBudget(db, other, SPEC).initialize()
    with pytest.raises(ModelDenied):
        await budget(db, replace(SPEC, budget_nusd=16000))
    with pytest.raises(ModelDenied):
        await ledger.reserve(other, 'call', grant(), body())
    with pytest.raises(ModelDenied):
        await ledger.reserve(AUTH, 'call', replace(grant(), max_output_tokens=101), body())
    await ledger.reserve(AUTH, 'call', grant(), body())
    with pytest.raises(ModelDenied):
        await ledger.reserve(AUTH, 'call', grant(), body())
    assert (await saved(db))['call_count'] == 1
    with pytest.raises(ModelDenied):
        await ledger.record_usage(other, 'call', RECEIPT)
    with pytest.raises(ModelDenied):
        await ledger.record_usage(AUTH, 'unknown', RECEIPT)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(model='other'), dict(input_tokens=1001)])
async def test_unpriced_or_excessive_usage_blocks_future_calls(db, changes):
    ledger = await budget(db)
    await ledger.reserve(AUTH, 'call', grant(), body())
    with pytest.raises(ModelDenied):
        await ledger.record_usage(AUTH, 'call', replace(RECEIPT, **changes))
    row = await saved(db)
    assert row['blocked'] and row['committed_nusd'] == 5000 and row['recorded_nusd'] == 0
    with pytest.raises(ModelDenied):
        await ledger.reserve(AUTH, 'new', grant(), body())


@pytest.mark.asyncio
async def test_conflicting_and_duplicate_receipts_do_not_refund(db):
    ledger = await budget(db)
    await ledger.reserve(AUTH, 'a', grant(), body())
    await ledger.record_usage(AUTH, 'a', RECEIPT)
    await ledger.reserve(AUTH, 'b', grant(), body())
    with pytest.raises(ModelDenied):
        await ledger.record_usage(AUTH, 'b', RECEIPT)
    assert (await saved(db))['committed_nusd'] == 5440
    with pytest.raises(ModelDenied):
        await ledger.record_usage(AUTH, 'a', replace(RECEIPT, output_tokens=21))
    assert (await saved(db))['recorded_nusd'] == 440


@pytest.mark.asyncio
async def test_call_limit_survives_backend_recreation(db):
    ledger = await budget(db, replace(SPEC, max_calls=1))
    g = replace(grant(), max_calls=1)
    await ledger.reserve(AUTH, 'a', g, body())
    await ledger.record_usage(AUTH, 'a', RECEIPT)
    ledger = await budget(db, replace(SPEC, max_calls=1))
    with pytest.raises(ModelDenied):
        await ledger.reserve(AUTH, 'b', g, body())


@pytest.mark.asyncio
async def test_relay_http_receipt_settles_real_ledger(db):
    from aiohttp import web
    import json
    ledger = await budget(db)
    async def provider(request):
        row = await saved(db)
        assert row['committed_nusd'] == 5000 and row['calls'][0]['status'] == 'held'
        response = {'type': 'response.completed', 'response': {'id': RECEIPT.response_id,
            'model': RECEIPT.model, 'status': 'completed', 'usage': {'input_tokens': 100,
            'output_tokens': 20, 'total_tokens': 120, 'input_tokens_details': {'cached_tokens': 30}}}}
        return web.Response(text='data: ' + json.dumps(response) + '\n\n', content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider, reserve=ledger.reserve,
        settle=ledger.settle, record_usage=ledger.record_usage)
    try:
        args = await relay(AUTH, 'model.start', {'body': body()})
        while not (await relay(AUTH, 'model.read', {'stream_id': args['stream_id']}))['eof']:
            pass
        row = await saved(db)
        assert row['recorded_nusd'] == row['committed_nusd'] == 440
        assert row['calls'][0]['outcome'] == 'stream_ended'
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_budget_failure_prevents_provider_dispatch(db):
    from aiohttp import web
    ledger = await budget(db, replace(SPEC, budget_nusd=1))
    requests = []
    async def provider(request):
        requests.append(True)
        return web.Response(text='', content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider, reserve=ledger.reserve,
        settle=ledger.settle, record_usage=ledger.record_usage)
    try:
        with pytest.raises(ModelDenied):
            await relay(AUTH, 'model.start', {'body': body()})
        assert not requests
        assert (await saved(db))['committed_nusd'] == 0
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_truncated_http_stream_retains_hold(db):
    from aiohttp import web
    ledger = await budget(db)
    async def provider(request):
        return web.Response(text='data: {"type":"response.created"}\n\n', content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider, reserve=ledger.reserve,
        settle=ledger.settle, record_usage=ledger.record_usage)
    try:
        args = await relay(AUTH, 'model.start', {'body': body()})
        while not (await relay(AUTH, 'model.read', {'stream_id': args['stream_id']}))['eof']:
            pass
        row = await saved(db)
        assert row['committed_nusd'] == 5000 and row['recorded_nusd'] == 0
        assert row['calls'][0]['status'] == 'held'
        assert row['calls'][0]['outcome'] == 'stream_ended'
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_accounting_outage_closes_relay_without_replay(db, monkeypatch):
    from unittest.mock import AsyncMock
    from aiohttp import web
    ledger = await budget(db)
    async def provider(request):
        return web.Response(text='data: {"type":"response.created"}\n\n', content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider, reserve=ledger.reserve,
        settle=ledger.settle, record_usage=ledger.record_usage)
    try:
        args = await relay(AUTH, 'model.start', {'body': body()})
        monkeypatch.setattr(ledger, '_entry', AsyncMock(side_effect=RuntimeError('Synthetic DB outage')))
        with pytest.raises(RuntimeError, match='Synthetic DB outage'):
            while not (await relay(AUTH, 'model.read', {'stream_id': args['stream_id']}))['eof']:
                pass
        assert relay.closed
        assert len(relay.session.requests) == 1
        assert (await saved(db))['committed_nusd'] == 5000
        with pytest.raises(ModelDenied):
            await relay(AUTH, 'model.start', {'body': body()})
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_refresh_uses_durable_account_and_rejects_foreign_authority(db):
    from unittest.mock import AsyncMock
    from dataclasses import replace
    from tests.test_worker_models import grant
    budget = ModelBudget(db, AUTH, SPEC)
    resolver = AsyncMock(return_value={'Authorization': 'Bearer synthetic'})
    relay = budget.relay(grant(), session=None, authorize=AsyncMock(), audit=AsyncMock(),
                         resolve_account_headers=resolver)
    assert await relay.resolve_headers(AUTH) == {'Authorization': 'Bearer synthetic'}
    resolver.assert_awaited_once_with(AUTH, SPEC.account_id)
    with pytest.raises(ModelDenied, match='Invalid account scope'):
        await relay.resolve_headers(replace(AUTH, user_email='foreign@example.test'))
    assert resolver.await_count == 1
