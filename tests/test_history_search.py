"""Search and offline indexing contracts; no production connections."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web
from bson import ObjectId
from mongomock_motor import AsyncMongoMockClient

from api import recall_routes, recall_search
from api.auth_middleware import auth_middleware
from api.recall_index import ensure_indexes, refresh_owner
from tests.test_history_recall import capability, signed


@pytest_asyncio.fixture
async def search_rig(aiohttp_client, monkeypatch, capability):
    key, claims = capability
    mongo = AsyncMongoMockClient().db
    db = SimpleNamespace(**{name: mongo[name] for name in (
        "users", "conversations", "recall_index", "recall_coverage", "recall_cursors", "recall_limits")})
    await db.users.insert_one({'_id': ObjectId(claims['sub']), 'email': claims['email'], 'status': 'active'})
    await db.conversations.insert_one({'conversation_id': 'old', 'source': 'dashboard', 'status': 'completed',
        'title': 'A decision', 'metadata': {'user_name': claims['email']},
        'messages': [{'role': 'user', 'content': 'remember RECALL-42 /cohort/custom/sync', 'timestamp': datetime(2026, 9, 1)},
                     {'role': 'assistant', 'content': 'RECALL-42 uses two tools.\npassword=CANARY_FOR_SEARCH'}]})
    original = db.conversations.find_one
    async def find(query, *args, **kwargs):
        return await original({k: v for k, v in query.items() if k != '$expr'}, *args, **kwargs)
    db.conversations.find_one = AsyncMock(side_effect=find)
    monkeypatch.setattr(recall_routes, 'get_db', lambda: db)
    await ensure_indexes(db)
    await refresh_owner(db, claims['sub'])
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_post('/api/recall/search', recall_search.handle_search_history)
    app.router.add_post('/api/recall/fetch', recall_routes.handle_fetch_history)
    client = await aiohttp_client(app)
    async def search(body=None, token=None, headers=None):
        return await client.post('/api/recall/search', json=body if body is not None else {'query': 'RECALL-42'},
            headers=headers if headers is not None else {'Authorization': 'Bearer ' + (token or signed(key, claims))})
    return db, search, key, claims, client


@pytest.mark.asyncio
async def test_search_fetch_round_trip(search_rig):
    db, search, key, claims, client = search_rig
    response = await search()
    data = await response.json()
    assert response.status == 200, data
    assert response.headers['Cache-Control'] == 'no-store'
    assert len(data['results']) == 2
    assert data['coverage']['status'] == 'stored_messages_only'
    assert 'CANARY_FOR_SEARCH' not in str(data)
    assert 'CANARY_FOR_SEARCH' not in str(await db.recall_index.find_one({}))
    hit = data['results'][0]
    fetched = await client.post('/api/recall/fetch', json={'conversation_id': hit['conversation_id'],
        'anchor_message_id': hit['message_id']}, headers={'Authorization': 'Bearer ' + signed(key, claims)})
    assert fetched.status == 200
    assert hit['content_revision'] == (await fetched.json())['content_revision']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,query,count', [('literal', '/cohort/custom/sync', 1),
    ('literal', 'recall-42', 0), ('keywords', 'recall-42', 2), ('phrase', 'uses   two', 1),
    ('literal', '.*', 0), ('keywords', 'CANARY_FOR_SEARCH', 0)])
async def test_modes(search_rig, mode, query, count):
    _, search, *_ = search_rig
    response = await search({'query': query, 'match_mode': mode})
    assert response.status == 200
    assert len((await response.json())['results']) == count


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'query': {}}, {'query': ' '}, {'query': 'x' * 1001},
    {'query': 'x', 'limit': True}, {'query': 'x', 'limit': 21}, {'query': 'x', 'scope': 'all'},
    {'query': 'x', 'filters': {'owner_id': 'x'}}, {'query': 'x', 'filters': {'after': '2026-09-01'}},
    {'query': 'x', 'match_mode': 'regex'}, {'query': 'x', 'filters': {'project_id': {'$ne': None}}}])
async def test_invalid_input(search_rig, body):
    _, search, *_ = search_rig
    response = await search(body)
    assert response.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize('update', [{'metadata.user_name': 'other@example.com'}, {'deleted': True},
    {'recall_excluded': True}, {'metadata.recall_excluded': True}, {'project_id': 'new'},
    {'messages': [{'role': 'assistant', 'content': 'Changed RECALL-42'}]}])
async def test_stale_index_never_leaks(search_rig, update):
    db, search, *_ = search_rig
    await db.conversations.update_one({}, {'$set': update})
    response = await search()
    data = await response.json()
    assert data['results'] == []
    assert data['coverage']['status'] == 'index_delayed'


@pytest.mark.asyncio
async def test_cross_user_even_admin(search_rig):
    db, search, key, claims, _ = search_rig
    claims.update(sub=str(ObjectId()), email='other@example.com')
    await db.users.insert_one({'_id': ObjectId(claims['sub']), 'email': claims['email'], 'system_role': 'admin'})
    response = await search()
    assert (await response.json())['results'] == []
    response = await search(headers={'X-User-Email': 'owner@example.com'})
    assert response.status == 401


@pytest.mark.asyncio
async def test_pagination_bound_to_query_identity_and_revision(search_rig):
    db, search, key, claims, _ = search_rig
    first = await (await search({'query': 'RECALL-42', 'limit': 1})).json()
    cursor = first['next_cursor']
    assert cursor
    second = await (await search({'query': 'RECALL-42', 'limit': 1, 'cursor': cursor})).json()
    assert first['results'][0]['message_id'] != second['results'][0]['message_id']
    assert second['next_cursor'] is None
    changed_query = await search({'query': 'tools', 'cursor': cursor})
    assert (await changed_query.json())['error'] == 'cursor_expired'
    claims['execution_id'] = 'other-execution'
    assert (await search({'query': 'RECALL-42', 'cursor': cursor})).status == 400
    claims['execution_id'] = 'current'
    await db.conversations.update_one({}, {'$set': {'title': 'changed'}})
    assert (await search({'query': 'RECALL-42', 'cursor': cursor})).status == 409


@pytest.mark.asyncio
async def test_scope_and_date_filters(search_rig):
    db, search, key, claims, _ = search_rig
    claims['project_id'] = 'project-one'
    denied = await search({'query': 'RECALL-42', 'filters': {'project_id': 'project-two'}})
    assert denied.status == 403
    assert (await (await search()).json())['results'] == []
    claims['project_id'] = None
    response = await search({'query': 'RECALL-42', 'filters': {'after': '2026-08-01T00:00:00Z', 'before': '2026-10-01T00:00:00Z'}})
    assert len((await response.json())['results']) == 1
    claims['execution_id'] = 'old'
    assert (await (await search()).json())['results'] == []


@pytest.mark.asyncio
async def test_refresh_purge_and_idempotence(search_rig):
    db, search, _, claims, _ = search_rig
    await refresh_owner(db, claims['sub'])
    assert await db.recall_index.count_documents({}) == 1
    batch = await refresh_owner(db, claims['sub'], batch_size=1)
    assert batch['next_cursor']
    assert (await refresh_owner(db, claims['sub'], after=batch['next_cursor']))['next_cursor'] is None
    await db.conversations.delete_many({})
    await refresh_owner(db, claims['sub'])
    assert await db.recall_index.count_documents({}) == 0


@pytest.mark.asyncio
async def test_disabled_and_revoked(search_rig, monkeypatch):
    db, search, *_ = search_rig
    monkeypatch.setenv('LOMA_RECALL_ENABLED', 'false')
    assert (await search()).status == 403
    monkeypatch.setenv('LOMA_RECALL_ENABLED', 'true')
    await db.users.update_one({}, {'$set': {'recall_excluded': True}})
    assert (await search()).status == 401


@pytest.mark.asyncio
async def test_excluded_envelope_never_indexed(search_rig):
    db, search, _, claims, _ = search_rig
    await db.conversations.update_one({}, {'$push': {'messages': {'role': 'user',
        'content': '[Authenticated User: secret@example.com] ONLY_HIDDEN_CANARY'}}})
    await refresh_owner(db, claims['sub'])
    assert 'ONLY_HIDDEN_CANARY' not in str(await db.recall_index.find_one({}))
    assert (await (await search()).json())['coverage']['status'] == 'partial'


@pytest.mark.asyncio
async def test_processing_cap_is_explicit(search_rig, monkeypatch):
    _, search, *_ = search_rig
    monkeypatch.setattr(recall_search, 'MAX_MATCHES', 1)
    data = await (await search()).json()
    assert len(data['results']) == 1
    assert data['coverage']['processing_limit_reached']
    assert data['coverage']['status'] == 'partial'


@pytest.mark.asyncio
async def test_delete_during_release_never_returns_snippets(search_rig):
    db, search, *_ = search_rig
    original = db.conversations.find_one.side_effect
    calls = 0
    async def racing(query, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            await db.conversations.delete_many({})
        return await original(query, *args, **kwargs)
    db.conversations.find_one.side_effect = racing
    response = await search()
    assert response.status == 409
    assert await response.json() == {'error': 'revision_changed'}
