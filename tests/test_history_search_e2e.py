"""Opt-in real Mongo and full app.py search tests, never production."""
import base64
import json
import os
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.recall_index import ensure_indexes, refresh_owner
from tests.test_history_recall_e2e import real_stack

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.environ.get('LOMA_RECALL_E2E') != '1', reason='requires isolated running stack')]


async def test_real_search_backfill_revision_and_deletion(real_stack):
    db, cid, uid, fetch, session = real_stack
    user = await db.users.find_one({'_id': uid})
    private = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(os.environ['LOMA_RECALL_E2E_PRIVATE_KEY']))
    claims = {'aud': 'loma:recall:fetch:v1', 'sub': str(uid), 'email': user['email'],
        'execution_id': 'current', 'project_id': None, 'agent_id': None,
        'iat': int(time.time()), 'exp': int(time.time()) + 300}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    token = payload + '.' + base64.urlsafe_b64encode(private.sign(payload.encode())).decode()
    async def search(query='recall', **kwargs):
        async with session.post('http://localhost:13000/api/recall/search',
                json={'query': query, **kwargs}, headers={'Authorization': 'Bearer ' + token}) as response:
            return response.status, await response.json()
    try:
        await ensure_indexes(db)
        await refresh_owner(db, str(uid))
        status, result = await search()
        assert status == 200, result
        assert len(result['results']) == 2
        assert 'CANARY_SECRET' not in json.dumps(result)
        assert (await search('CANARY_SECRET'))[1]['results'] == []
        hit = result['results'][0]
        status, fetched = await fetch(anchor_message_id=hit['message_id'])
        assert status == 200
        assert hit['content_revision'] == fetched['content_revision']
        await db.conversations.update_one({'conversation_id': cid}, {'$set': {'title': 'Updated'}})
        assert (await search())[1]['coverage']['status'] == 'index_delayed'
        await refresh_owner(db, str(uid))
        assert len((await search())[1]['results']) == 1
        await db.conversations.update_one({'conversation_id': cid}, {'$set': {'deleted': True}})
        assert (await search())[1]['results'] == []
        await refresh_owner(db, str(uid))
        assert await db.recall_index.count_documents({'owner_user_id': str(uid)}) == 0
    finally:
        await db.recall_index.delete_many({'owner_user_id': str(uid)})
        await db.recall_coverage.delete_one({'_id': str(uid)})
