"""Scheduled reconciliation tests use only synthetic owner records."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from mongomock_motor import AsyncMongoMockClient

from scripts import recall_reconcile
from tests.test_history_recall import capability
from tests.test_history_search import search_rig


@pytest.mark.asyncio
async def test_automatic_pass_edit_deletion_and_excluded_user(search_rig):
    db, _, _, claims, _ = search_rig
    db.recall_index_locks = AsyncMongoMockClient().db.recall_index_locks
    await db.conversations.update_one({}, {'$set': {'messages': [
        {'role': 'assistant', 'content': 'new recall decision\npassword=RECONCILE_CANARY'}]}})
    assert (await recall_reconcile.reconcile_owner(db, claims['sub']))['status'] == 'stored_messages_only'
    row = await db.recall_index.find_one({})
    assert 'new recall decision' in row['sanitized_text']
    assert 'RECONCILE_CANARY' not in row['sanitized_text']
    await db.conversations.delete_many({})
    await recall_reconcile.reconcile_owner(db, claims['sub'])
    assert await db.recall_index.count_documents({}) == 0
    await db.users.delete_many({})
    assert (await recall_reconcile.reconcile_owner(db, claims['sub']))['status'] == 'excluded'
    assert await db.recall_index_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_concurrent_worker_fails_closed_and_cancel_releases(monkeypatch):
    db = AsyncMongoMockClient().db
    uid = str(ObjectId())
    entered, block = asyncio.Event(), asyncio.Event()
    async def refresh(*args):
        entered.set()
        await block.wait()
    monkeypatch.setattr(recall_reconcile, 'refresh_owner', refresh)
    task = asyncio.create_task(recall_reconcile.reconcile_owner(db, uid))
    await entered.wait()
    assert await recall_reconcile.reconcile_owner(db, uid) == {'status': 'worker_locked'}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await db.recall_index_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_multiple_batches_and_failure_cleanup(monkeypatch):
    db = AsyncMongoMockClient().db
    uid = str(ObjectId())
    refresh = AsyncMock(side_effect=[
        {'next_cursor': str(ObjectId()), 'indexed': 100, 'status': 'partial'},
        {'next_cursor': None, 'indexed': 1, 'status': 'stored_messages_only'},
    ])
    monkeypatch.setattr(recall_reconcile, 'refresh_owner', refresh)
    result = await recall_reconcile.reconcile_owner(db, uid)
    assert result == {'status': 'stored_messages_only', 'indexed': 101}
    assert refresh.await_count == 2
    refresh.side_effect = RuntimeError('synthetic failure')
    with pytest.raises(RuntimeError):
        await recall_reconcile.reconcile_owner(db, uid)
    assert await db.recall_index_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_watch_repeats_without_busy_loop(monkeypatch):
    reconcile = AsyncMock(return_value={'status': 'stored_messages_only'})
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(recall_reconcile, 'reconcile_owner', reconcile)
    monkeypatch.setattr(recall_reconcile.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        await recall_reconcile.watch_owner(None, 'user', 60)
    assert reconcile.await_count == sleep.await_count == 2
    assert sleep.call_args.args == (60,)
    with pytest.raises(ValueError):
        await recall_reconcile.watch_owner(None, 'user', 0)
