"""Synthetic subscription files and throwaway Mongo; never real model accounts."""
import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from unittest.mock import AsyncMock

import pytest

from isolation.accounts import SubscriptionAccount, SubscriptionAccounts
from isolation.models import ModelDenied
from tests.test_bounded_work import db, OWNER, OTHER
from tests.test_worker_run import AUTH, args, seed
from isolation import run as mod


def account(tmp_path, runtime='codex', email=OWNER, identity='provider-one', token='synthetic-token'):
    directory = tmp_path / (runtime + '-' + email)
    directory.mkdir(exist_ok=True)
    if runtime == 'codex':
        claims = base64.urlsafe_b64encode(json.dumps({'sub': identity}).encode()).decode().rstrip('=')
        (directory / 'auth.json').write_text(json.dumps({'tokens': {
            'account_id': 'provider-account', 'id_token': 'header.' + claims + '.signature', 'access_token': token}}))
    else:
        (directory / '.claude.json').write_text(json.dumps({'oauthAccount': {'accountUuid': identity, 'emailAddress': email}}))
        (directory / '.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': token}}))
    return SubscriptionAccount(runtime, email, directory)


def selector(db, accounts, **kw):
    return SubscriptionAccounts(db, accounts, check_access=kw.get('check_access', AsyncMock(return_value=True)),
        refresh=kw.get('refresh', AsyncMock(return_value={'Authorization': 'Bearer synthetic-refreshed'})))


@pytest.mark.parametrize('runtime', ['codex', 'claude'])
def test_identity_tracks_relogin_not_rotation(tmp_path, runtime):
    original = account(tmp_path, runtime)
    fingerprint = original.identity()
    assert fingerprint == account(tmp_path, runtime, token='rotated').identity()
    assert fingerprint != account(tmp_path, runtime, identity='other-provider').identity()
    assert str(tmp_path) not in repr(original) and 'synthetic-token' not in repr(original)


@pytest.mark.parametrize('payload', [None, [], {}, {'OPENAI_API_KEY': 'synthetic-api-key'},
    {'tokens': []}, {'tokens': {'id_token': 'broken'}}, {'tokens': {'id_token': 'h.###.s'}}])
def test_invalid_or_api_key_account_is_not_subscription(tmp_path, payload):
    a = account(tmp_path)
    (a.directory / 'auth.json').write_text(json.dumps(payload))
    with pytest.raises(ModelDenied, match='unavailable'):
        a.identity()


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'oversized', 'directory-symlink'])
def test_bounded_nofollow_reads(tmp_path, kind):
    a = account(tmp_path)
    f = a.directory / 'auth.json'
    if kind == 'directory-symlink':
        link = tmp_path / 'linked'; link.symlink_to(a.directory, target_is_directory=True)
        a = replace(a, directory=link)
    elif kind == 'oversized':
        f.write_text(' ' * 65537)
    else:
        f.unlink()
        if kind == 'fifo':
            os.mkfifo(f)
        else:
            target = tmp_path / 'other'; target.write_text('{}'); f.symlink_to(target)
    with pytest.raises(ModelDenied):
        a.identity()


@pytest.mark.asyncio
async def test_selection_round_robin_runtime_and_current_grants(db, tmp_path):
    a, b = account(tmp_path), account(tmp_path, email=OTHER)
    claude = account(tmp_path, 'claude')
    pool = selector(db, [a, b, claude])
    ids = []
    for _ in range(3):
        selected = await pool.select(AUTH, 'codex')
        ids.append(selected.account_id)
        await selected.release()
    assert ids == [a.account_id, b.account_id, a.account_id]
    assert (await pool.select(AUTH, 'claude')).account_id == claude.account_id
    await db.users.update_one({'email': OWNER}, {'$set': {'codex_pool_enabled': False}})
    assert (await pool.select(AUTH, 'codex')).account_id == b.account_id
    pool.check_access = AsyncMock(return_value=False)
    with pytest.raises(ModelDenied):
        await pool.select(AUTH, 'codex')


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disabled', 'deleted', 'inactive', 'disconnected', 'relogin', 'cooldown'])
async def test_pinned_account_never_fails_over(db, tmp_path, change):
    a, b = account(tmp_path), account(tmp_path, email=OTHER)
    pool = selector(db, [a, b]); selected = await pool.select(AUTH, 'codex')
    if change == 'disabled':
        await db.users.update_one({'email': OWNER}, {'$set': {'codex_pool_enabled': False}})
    elif change == 'deleted':
        await db.users.delete_one({'email': OWNER})
    elif change == 'inactive':
        await db.users.update_one({'email': OWNER}, {'$set': {'status': 'disabled'}})
    elif change == 'disconnected':
        (a.directory / 'auth.json').unlink()
    elif change == 'relogin':
        account(tmp_path, identity='new-account')
    else:
        await pool.cooldown(a.account_id, 60)
    with pytest.raises(ModelDenied):
        await selected.resolve_headers(AUTH, selected.account_id)
    pool.refresh.assert_not_called()
    assert (await pool.select(AUTH, 'codex')).account_id == b.account_id


@pytest.mark.asyncio
@pytest.mark.parametrize('during', ['disabled', 'relogin', 'error', 'cancelled'])
async def test_revocation_or_failure_during_refresh(db, tmp_path, during):
    a = account(tmp_path)
    async def refresh(*_):
        if during == 'disabled':
            await db.users.update_one({'email': OWNER}, {'$set': {'codex_pool_enabled': False}})
        elif during == 'relogin':
            account(tmp_path, identity='new-identity')
        elif during == 'cancelled':
            raise asyncio.CancelledError()
        else:
            raise RuntimeError('PRIVATE-TOKEN')
        return {'Authorization': 'PRIVATE-TOKEN'}
    pool = selector(db, [a], refresh=refresh); selected = await pool.select(AUTH, 'codex')
    with pytest.raises(asyncio.CancelledError if during == 'cancelled' else ModelDenied) as err:
        await selected.resolve_headers(AUTH, selected.account_id)
    assert 'PRIVATE-TOKEN' not in str(err.value)


@pytest.mark.asyncio
async def test_scope_and_credential_rotation(db, tmp_path):
    a = account(tmp_path); pool = selector(db, [a]); selected = await pool.select(AUTH, 'codex')
    for auth, account_id in [(replace(AUTH, run_id='foreign-run'), selected.account_id), (AUTH, 'foreign-account')]:
        with pytest.raises(ModelDenied):
            await selected.resolve_headers(auth, account_id)
    pool.refresh.assert_not_called()
    account(tmp_path, token='rotated')
    assert await selected.resolve_headers(AUTH, selected.account_id) == {'Authorization': 'Bearer synthetic-refreshed'}
    pool.refresh.assert_awaited_once_with(AUTH, a)


@pytest.mark.asyncio
async def test_durable_cooldown_never_shortens_and_expires(db, tmp_path):
    a = account(tmp_path); pool = selector(db, [a])
    await pool.cooldown(a.account_id, 3600)
    await asyncio.gather(*[pool.cooldown(a.account_id, 1) for _ in range(5)])
    state = await pool.states.find_one({'_id': a.account_id})
    assert state['cooldown_until'] > datetime.now(timezone.utc) + timedelta(minutes=59)
    another = selector(db, [a])
    with pytest.raises(ModelDenied):
        await another.select(AUTH, 'codex')
    await pool.states.update_one({'_id': a.account_id}, {'$set': {'cooldown_until': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert (await another.select(AUTH, 'codex')).account_id == a.account_id


@pytest.mark.asyncio
async def test_database_failure_and_empty_pool_fail_closed(db, tmp_path):
    a = account(tmp_path); pool = selector(db, [a]); pool.users = AsyncMock()
    pool.users.find_one.side_effect = RuntimeError('DB unavailable')
    with pytest.raises(RuntimeError):
        await pool.select(AUTH, 'codex')
    pool.refresh.assert_not_called()
    with pytest.raises(ModelDenied):
        await selector(db, []).select(AUTH, 'codex')


@pytest.mark.asyncio
async def test_run_selection_pins_budget_and_never_sends_account_to_worker(db, tmp_path, monkeypatch):
    await seed(db)
    a = account(tmp_path); pool = selector(db, [a])
    async def worker(**kw):
        relay = kw['execute_tool'].models
        assert await relay.resolve_headers(kw['authority']) == {'Authorization': 'Bearer synthetic-refreshed'}
        row = await db.isolated_model_budgets.find_one({'_id': kw['authority'].run_id})
        assert row['spec']['account_id'] == a.account_id
        wire = json.dumps(kw['input'])
        assert all(value not in wire for value in [a.account_id, a.email, str(a.directory), 'synthetic-refreshed'])
        yield 'Selected account verified'
    monkeypatch.setattr(mod, 'stream_worker', worker)
    assert [e async for e in mod.stream_run(**args(db, tmp_path / 'artifacts', subscription_accounts=pool))] == ['Selected account verified']
    row = await db.isolated_model_budgets.find_one({})
    assert row['active'] is False


@pytest.mark.asyncio
async def test_run_denial_never_starts_worker_or_creates_budget(db, tmp_path, monkeypatch):
    await seed(db)
    worker = AsyncMock(); monkeypatch.setattr(mod, 'stream_worker', worker)
    with pytest.raises(ModelDenied):
        _ = [e async for e in mod.stream_run(**args(db, tmp_path, subscription_accounts=selector(db, [])))]
    worker.assert_not_called()
    assert await db.isolated_model_budgets.count_documents({}) == 0
    with pytest.raises(ValueError, match='one account'):
        _ = [e async for e in mod.stream_run(**args(db, tmp_path, subscription_accounts=selector(db, []), resolve_account_headers=AsyncMock()))]


@pytest.mark.asyncio
@pytest.mark.parametrize('policy_result', [None, 'yes', 1, False])
async def test_policy_requires_explicit_true(db, tmp_path, policy_result):
    pool = selector(db, [account(tmp_path)], check_access=AsyncMock(return_value=policy_result))
    with pytest.raises(ModelDenied):
        await pool.select(AUTH, 'codex')


@pytest.mark.asyncio
async def test_configuration_and_cooldown_validation(db, tmp_path):
    a = account(tmp_path)
    with pytest.raises(ValueError):
        selector(db, [a, a])
    with pytest.raises(ValueError):
        selector(db, [a], refresh=False)
    pool = selector(db, [a])
    for account_id, seconds in [('unknown', 60), (a.account_id, True), (a.account_id, 0), (a.account_id, 86401)]:
        with pytest.raises(ValueError):
            await pool.cooldown(account_id, seconds)
    assert await pool.states.count_documents({}) == 0


@pytest.mark.asyncio
async def test_naive_mongo_client_cooldowns_work(db, tmp_path):
    from bson.codec_options import CodecOptions
    naive_db = db.with_options(codec_options=CodecOptions(tz_aware=False))
    a = account(tmp_path); pool = selector(naive_db, [a])
    await pool.cooldown(a.account_id, 60)
    with pytest.raises(ModelDenied):
        await pool.select(AUTH, 'codex')


@pytest.mark.asyncio
async def test_stream_revocation_blocks_output_and_closes_budget(db, tmp_path, monkeypatch):
    from isolation.gateway import GatewayDenied
    await seed(db)
    a = account(tmp_path); pool = selector(db, [a])
    async def worker(**kw):
        await db.users.update_one({'email': OWNER}, {'$set': {'codex_pool_enabled': False}})
        assert not await kw['authorize'](kw['authority'])
        yield 'Must not reach the user'
    monkeypatch.setattr(mod, 'stream_worker', worker)
    with pytest.raises(GatewayDenied):
        _ = [e async for e in mod.stream_run(**args(db, tmp_path / 'artifacts', subscription_accounts=pool))]
    row = await db.isolated_model_budgets.find_one({})
    assert row['active'] is False


@pytest.mark.asyncio
async def test_soft_deleted_account_is_unavailable(db, tmp_path):
    a = account(tmp_path); pool = selector(db, [a])
    await db.users.update_one({'email': OWNER}, {'$set': {'deleted': True}})
    with pytest.raises(ModelDenied):
        await pool.select(AUTH, 'codex')


@pytest.mark.asyncio
async def test_provider_feedback_pins_account_and_blocks_other_backend(db,tmp_path):
    a,b=account(tmp_path),account(tmp_path,email=OTHER)
    pool=selector(db,[a,b]); selected=await pool.select(AUTH,'codex')
    with pytest.raises(ModelDenied):
        await selected.report_rate_limit(replace(AUTH,run_id='foreign'),120)
    state = await pool.states.find_one({'_id': a.account_id})
    assert state['cooldown_until'] <= datetime.now(timezone.utc)
    await selected.report_rate_limit(AUTH,120)
    assert not await selected.authorize(AUTH)
    await selected.report_rate_limit(AUTH,3600)
    state=await db.isolated_subscription_accounts.find_one({'_id':a.account_id})
    assert state['cooldown_until'].replace(tzinfo=timezone.utc)>datetime.now(timezone.utc)+timedelta(seconds=3500)
    other_backend=selector(db,[a,b])
    assert (await other_backend.select(AUTH,'codex')).account_id==b.account_id
    assert (await pool.states.find_one({'_id': b.account_id}))['cooldown_until'] <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_run_wires_provider_feedback_to_selected_account(db,tmp_path,monkeypatch):
    await seed(db)
    a=account(tmp_path); pool=selector(db,[a])
    async def worker(**kw):
        relay=kw['execute_tool'].models
        assert callable(relay.on_rate_limit)
        await relay.on_rate_limit(kw['authority'],90)
        yield 'must not escape after account cooldown'
    monkeypatch.setattr(mod,'stream_worker',worker)
    with pytest.raises(Exception,match='access is no longer valid'):
        _=[e async for e in mod.stream_run(**args(db,tmp_path/'artifacts',subscription_accounts=pool))]
    state=await db.isolated_subscription_accounts.find_one({'_id':a.account_id})
    assert state['cooldown_until'] is not None


@pytest.mark.asyncio
async def test_distributed_capacity_is_atomic_and_reusable(db, tmp_path):
    a = replace(account(tmp_path), max_concurrent_runs=2)
    # Independent selectors model separate backend processes, not a shared lock.
    pools = [selector(db, [a]) for _ in range(12)]
    results = await asyncio.gather(*[p.select(replace(AUTH, run_id=f'run-{i}'), 'codex')
        for i, p in enumerate(pools)], return_exceptions=True)
    winners = [r for r in results if not isinstance(r, Exception)]
    assert len(winners) == 2
    assert all(isinstance(r, ModelDenied) for r in results if isinstance(r, Exception))
    assert len((await pools[0].states.find_one({'_id': a.account_id}))['leases']) == 2
    await winners[0].release()
    replacement = await pools[-1].select(AUTH, 'codex')
    await winners[0].release()  # stale cleanup must not release the replacement
    assert await replacement.authorize(AUTH)
    assert not await winners[0].authorize(winners[0].authority)
    with pytest.raises(ModelDenied):
        await pools[0].select(AUTH, 'codex')


@pytest.mark.asyncio
async def test_expired_lease_reclaimed_but_old_holder_cannot_refresh(db, tmp_path):
    a = account(tmp_path); pool = selector(db, [a])
    old = await pool.select(AUTH, 'codex')
    await pool.states.update_one({'_id': a.account_id}, {'$set': {
        'leases.0.expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert not await old.authorize(AUTH)
    with pytest.raises(ModelDenied):
        await old.resolve_headers(AUTH, a.account_id)
    current = await selector(db, [a]).select(AUTH, 'codex')
    await old.release()
    assert await current.authorize(AUTH)
    assert len((await pool.states.find_one({'_id': a.account_id}))['leases']) == 1


@pytest.mark.asyncio
async def test_capacity_mismatch_and_acquisition_outage_fail_closed(db, tmp_path):
    a = account(tmp_path); pool = selector(db, [a])
    selected = await pool.select(AUTH, 'codex'); await selected.release()
    with pytest.raises(ModelDenied):
        await selector(db, [replace(a, max_concurrent_runs=2)]).select(AUTH, 'codex')
    pool._acquire = AsyncMock(side_effect=RuntimeError('database unavailable'))
    with pytest.raises(RuntimeError):
        await pool.select(AUTH, 'codex')
    pool.refresh.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('seconds', [True, 0, 59, 3661, float('nan'), '60'])
async def test_invalid_lease_duration(db, tmp_path, seconds):
    with pytest.raises(ValueError):
        await selector(db, [account(tmp_path)]).select(AUTH, 'codex', lease_seconds=seconds)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['initialize', 'worker', 'cancel'])
async def test_run_releases_lease_on_failure(db, tmp_path, monkeypatch, failure):
    await seed(db)
    a = account(tmp_path); pool = selector(db, [a])
    async def worker(**kw):
        if failure == 'cancel':
            raise asyncio.CancelledError()
        raise RuntimeError('synthetic worker failure')
        yield
    monkeypatch.setattr(mod, 'stream_worker', worker)
    if failure == 'initialize':
        monkeypatch.setattr(mod.ModelBudget, 'initialize', AsyncMock(side_effect=RuntimeError('budget unavailable')))
    with pytest.raises(asyncio.CancelledError if failure == 'cancel' else RuntimeError):
        _ = [e async for e in mod.stream_run(**args(db, tmp_path / 'artifacts', subscription_accounts=pool))]
    assert (await pool.states.find_one({'_id': a.account_id}))['leases'] == []


@pytest.mark.asyncio
async def test_cooldown_race_is_checked_atomically_on_acquire(db, tmp_path):
    a = account(tmp_path); pool = selector(db, [a])
    await pool.cooldown(a.account_id, 60)
    # Simulate the cooldown arriving after _allowed but before acquisition.
    pool._allowed = AsyncMock(return_value=True)
    with pytest.raises(ModelDenied): await pool.select(AUTH, 'codex')
    assert not (await pool.states.find_one({'_id': a.account_id})).get('leases')


@pytest.mark.parametrize('capacity', [0, 65, True, '2', 1.5])
def test_capacity_configuration_is_bounded(tmp_path, capacity):
    with pytest.raises(ValueError): replace(account(tmp_path), max_concurrent_runs=capacity)


@pytest.mark.asyncio
async def test_run_deadline_includes_setup_and_releases_capacity(db, tmp_path, monkeypatch):
    await seed(db)
    a = account(tmp_path); pool = selector(db, [a])
    entered = asyncio.Event()
    async def slow_initialize(self):
        entered.set()
        await asyncio.sleep(10)
    monkeypatch.setattr(mod.ModelBudget, 'initialize', slow_initialize)
    with pytest.raises(TimeoutError):
        _ = [e async for e in mod.stream_run(**args(db, tmp_path / 'artifacts',
            subscription_accounts=pool, max_seconds=2))]
    assert entered.is_set()
    assert (await pool.states.find_one({'_id': a.account_id}))['leases'] == []
