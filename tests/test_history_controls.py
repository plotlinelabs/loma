"""Multi-instance limit/cursor contracts with no model or source datastore access."""
import asyncio
from dataclasses import replace
import time

import pytest
from mongomock_motor import AsyncMongoMockClient

from api.recall_auth import RecallIdentity
from api.recall_controls import (RecallError, admit_request, charge_response,
    create_cursor, ensure_control_indexes, read_cursor)
from tests.test_history_recall import capability, rig
from tests.test_history_search import search_rig

IDENTITY = RecallIdentity('user-a', 'a@example.com', 'execution-a', None, None)


@pytest.mark.asyncio
async def test_opaque_cursor_other_instance_and_binding():
    client = AsyncMongoMockClient()
    db = client.db
    state = {'binding': 'owner-scope-query', 'exp': int(time.time()) + 900, 'offset': 2}
    token = await create_cursor(db, state)
    assert len(token) == 43 and 'owner' not in token
    assert await read_cursor(client.db, token, state['binding']) == state
    row = await db.recall_cursors.find_one({})
    assert row['_id'] != token and token not in str(row)
    for bad_token, binding in [(token + 'x', state['binding']), (token, 'other-scope')]:
        with pytest.raises(RecallError, match='cursor_expired'):
            await read_cursor(db, bad_token, binding)
    await db.recall_cursors.update_one({}, {'$set': {'state.exp': int(time.time()) - 1}})
    with pytest.raises(RecallError, match='cursor_expired'):
        await read_cursor(db, token, state['binding'])


@pytest.mark.asyncio
async def test_concurrent_admission_and_separate_users(monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: 1800000000)
    client = AsyncMongoMockClient()
    results = await asyncio.gather(*(admit_request(client.db, IDENTITY) for _ in range(100)),
                                   return_exceptions=True)
    assert sum(r is None for r in results) == 60
    assert all(r is None or (isinstance(r, RecallError) and r.status == 429) for r in results)
    # New execution/scope cannot bypass the per-user bucket.
    with pytest.raises(RecallError):
        await admit_request(client.db, replace(IDENTITY, execution_id='different'))
    await admit_request(client.db, replace(IDENTITY, user_id='user-b'))
    monkeypatch.setattr(time, 'time', lambda: 1800000060)
    await admit_request(client.db, IDENTITY)


@pytest.mark.asyncio
async def test_output_limit_concurrency_and_content_free_storage():
    db = AsyncMongoMockClient().db
    results = await asyncio.gather(*(charge_response(db, IDENTITY, {'secret-source': 'X' * 10000})
                                    for _ in range(30)), return_exceptions=True)
    assert sum(r is None for r in results) == 23
    rows = await db.recall_limits.find({}).to_list(None)
    assert 'secret-source' not in str(rows) and 'example.com' not in str(rows)
    assert rows[0]['characters'] <= 240000


@pytest.mark.asyncio
async def test_ttl_indexes():
    db = AsyncMongoMockClient().db
    await ensure_control_indexes(db)
    for name in ('recall_cursors', 'recall_limits'):
        indexes = await db[name].index_information()
        assert indexes['expires_at_1']['expireAfterSeconds'] == 0


@pytest.mark.asyncio
async def test_fetch_http_limit_and_no_source_read_after_exhaustion(rig, monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    db, fetch, *_ = rig
    for _ in range(60):
        assert (await fetch()).status == 200
    reads = db.conversations.find_one.await_count
    blocked = await fetch()
    assert blocked.status == 429
    assert await blocked.json() == {'error': 'rate_limited'}
    assert blocked.headers['Cache-Control'] == 'no-store'
    assert db.conversations.find_one.await_count == reads


@pytest.mark.asyncio
async def test_search_and_fetch_share_user_limit(search_rig, monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    db, search, key, claims, client = search_rig
    from tests.test_history_recall import signed
    for _ in range(60):
        assert (await search({'query': 'missing'})).status == 200
    response = await client.post('/api/recall/fetch', json={'conversation_id': 'old'},
        headers={'Authorization': 'Bearer ' + signed(key, claims)})
    assert response.status == 429
