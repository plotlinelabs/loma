"""Opt-in real Mongo + running full-backend tests; see docs/history-recall.md."""
import base64
import json
import os
import time
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from bson import ObjectId
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from motor.motor_asyncio import AsyncIOMotorClient

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.environ.get('LOMA_RECALL_E2E') != '1', reason='requires isolated running stack',
)]


@pytest_asyncio.fixture
async def real_stack():
    from dotenv import dotenv_values
    config = dotenv_values(Path(__file__).parents[1] / '.env')
    assert config['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    assert config['WEBHOOK_PORT'] == '13000'
    assert config['LOMA_ENABLE_SLACK'] == config['LOMA_ENABLE_SCHEDULER'] == 'false'
    client = AsyncIOMotorClient(config['OBSERVABILITY_MONGODB_URI'])
    db = client[config['OBSERVABILITY_DB_NAME']]
    private = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(os.environ['LOMA_RECALL_E2E_PRIVATE_KEY']))
    uid = ObjectId()
    email = 'recall-e2e-' + str(uid) + '@example.com'
    await db.users.insert_one({'_id': uid, 'email': email, 'status': 'active', 'system_role': 'admin'})
    cid = 'recall-e2e-' + str(uid)
    await db.conversations.insert_one({
        'conversation_id': cid, 'source': 'dashboard', 'status': 'completed',
        'metadata': {'user_name': email}, 'title': 'Synthetic recall fixture',
        'messages': [{'role': 'user', 'content': 'Remember the synthetic decision.'},
                     {'role': 'assistant', 'content': 'Use read-only recall.\npassword=CANARY_SECRET\nDone.'}],
    })
    async with aiohttp.ClientSession() as session:
        async def fetch(**body):
            claims = {'aud': 'loma:recall:fetch:v1', 'sub': str(uid), 'email': email,
                      'execution_id': 'synthetic-current', 'project_id': None, 'agent_id': None,
                      'iat': int(time.time()), 'exp': int(time.time()) + 300}
            payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
            token = payload + '.' + base64.urlsafe_b64encode(private.sign(payload.encode())).decode()
            async with session.post('http://localhost:13000/api/recall/fetch',
                    json={'conversation_id': cid, **body}, headers={'Authorization': 'Bearer ' + token}) as response:
                return response.status, await response.json()
        try:
            yield db, cid, uid, fetch, session
        finally:
            await db.conversations.delete_many({'conversation_id': cid})
            await db.users.delete_one({'_id': uid})
            client.close()


async def test_real_mongo_full_backend_policy(real_stack):
    db, cid, uid, fetch, session = real_stack
    status, data = await fetch()
    assert status == 200
    assert data['messages'][1]['content'] == 'Use read-only recall.\n[REDACTED]\nDone.'
    assert 'CANARY_SECRET' not in json.dumps(data)
    async with session.post('http://localhost:13000/api/recall/fetch', json={'conversation_id': cid},
                            headers={'X-User-Email': 'admin@example.com'}) as response:
        assert response.status == 401
    await db.conversations.update_one({'conversation_id': cid}, {'$set': {'metadata.user_name': 'other@example.com', 'metadata.visibility': 'shared'}})
    assert (await fetch())[0] == 404


async def test_real_mongo_bson_size_guard(real_stack):
    db, cid, _, fetch, _ = real_stack
    await db.conversations.update_one({'conversation_id': cid}, {'$set': {'messages': [{'role': 'user', 'content': 'x' * (2 * 1024 * 1024)}]}})
    assert (await fetch())[0] == 404


async def test_real_mongo_pagination_edit_and_deletion(real_stack):
    db, cid, _, fetch, _ = real_stack
    await db.conversations.update_one({'conversation_id': cid}, {'$set': {'messages': [{'role': 'assistant', 'content': 'abc' * 1000}]}})
    status, data = await fetch(max_chars=256)
    assert status == 200 and data['next_cursor']
    status, next_page = await fetch(cursor=data['next_cursor'])
    assert status == 200 and next_page['messages'][0]['content_offset'] == 256
    await db.conversations.update_one({'conversation_id': cid}, {'$set': {'messages.0.content': 'edited'}})
    assert (await fetch(cursor=data['next_cursor']))[0] == 409
    await db.conversations.update_one({'conversation_id': cid}, {'$set': {'deleted': True}})
    assert (await fetch(cursor=data['next_cursor']))[0] == 404


async def test_real_mongo_exclusion_and_user_revocation(real_stack):
    db, cid, uid, fetch, _ = real_stack
    await db.conversations.update_one({'conversation_id': cid}, {'$set': {'recall_excluded': True}})
    assert (await fetch())[0] == 404
    await db.conversations.update_one({'conversation_id': cid}, {'$unset': {'recall_excluded': ''}})
    await db.users.update_one({'_id': uid}, {'$set': {'status': 'disabled'}})
    assert (await fetch())[0] == 401
