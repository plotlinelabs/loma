"""Personal webhook ownership, revocation and isolation, without provider traffic."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from webhooks import grain_personal as personal
from tools import grain

OWNER = 'owner@example.test'
SECRET = 'x' * 43


@pytest_asyncio.fixture
async def webhook(monkeypatch):
    db = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={'status': 'active'})),
        grain_webhook_subscriptions=SimpleNamespace(
            find_one=AsyncMock(return_value={'_id': OWNER}),
            replace_one=AsyncMock(), delete_one=AsyncMock()),
        personal_grain_events=SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock(), find=MagicMock()),
        changestreams=SimpleNamespace(update_one=AsyncMock()),
    )
    monkeypatch.setattr(personal, 'get_db', lambda: db)
    token = AsyncMock(return_value='synthetic-personal-access')
    monkeypatch.setattr(personal, 'get_valid_provider_token', token)
    async def transcript(*args, **kwargs):
        assert grain._access_token.get() == 'synthetic-personal-access'
        return {'transcript': 'Private test transcript'}
    fetch = AsyncMock(side_effect=transcript)
    monkeypatch.setattr(grain, 'get_transcript', fetch)
    monkeypatch.setattr(grain, 'find_recording_by_id', AsyncMock(return_value={'id': 'recording-1'}))
    @web.middleware
    async def identity(request, handler):
        request['user_email'] = OWNER
        return await handler(request)
    app = web.Application(middlewares=[identity])
    personal.setup_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client, db, token, fetch


@pytest.mark.asyncio
async def test_owner_comes_from_subscription_not_payload_and_storage_is_private(webhook):
    client, db, token, fetch = webhook
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET},
                                 json={'recording_id': 'recording-1', 'owner': 'other', 'token': 'do-not-persist'})
    assert response.status == 200
    token.assert_awaited_once_with(OWNER, 'grain', db=db)
    query, update = db.personal_grain_events.update_one.call_args.args
    assert query == {'_id': hashlib.sha256((OWNER + '\0recording-1').encode()).hexdigest()}
    event = update['$setOnInsert']
    assert event['owner'] == OWNER and event['visibility'] == 'private'
    assert event['raw_event']['webhook_body'] == {'recording_id': 'recording-1'}
    db.changestreams.update_one.assert_not_awaited()
    assert grain._access_token.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('header', ['', 'Bearer wrong', 'Basic ' + SECRET])
async def test_invalid_header_cannot_lookup_credentials(webhook, header):
    client, db, token, fetch = webhook
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': header}, json={'recording_id': 'recording-1'})
    assert response.status == 401
    db.grain_webhook_subscriptions.find_one.assert_not_awaited()
    token.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_or_expired_subscription_denied(webhook):
    client, db, token, fetch = webhook
    db.grain_webhook_subscriptions.find_one.return_value = None
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, json={'recording_id': 'recording-1'})
    assert response.status == 401
    assert '$gt' in db.grain_webhook_subscriptions.find_one.call_args.args[0]['expires_at']
    token.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_during_fetch_prevents_storage(webhook):
    client, db, token, fetch = webhook
    db.grain_webhook_subscriptions.find_one.side_effect = [{'_id': OWNER}, None]
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, json={'recording_id': 'recording-1'})
    assert response.status == 401
    db.personal_grain_events.update_one.assert_not_awaited()
    assert grain._access_token.get() is None


@pytest.mark.asyncio
async def test_duplicate_does_not_fetch_again(webhook):
    client, db, token, fetch = webhook
    db.personal_grain_events.find_one.return_value = {'_id': 'existing'}
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, json={'recording_id': 'recording-1'})
    assert response.status == 200 and (await response.json())['duplicate']
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'recording_id': '../../private'}, {'recording_id': 'a?x=y'}, {}, []])
async def test_invalid_recording_not_sent_to_provider(webhook, body):
    client, db, token, fetch = webhook
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, json=body)
    assert response.status == 400
    token.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnected_account_no_org_fallback(webhook):
    client, db, token, fetch = webhook
    token.return_value = None
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, json={'recording_id': 'recording-1'})
    assert response.status == 409
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_configuration_rotates_only_current_owner_and_stores_digest(webhook):
    client, db, token, fetch = webhook
    response = await client.put('/api/integrations/grain/webhook', json={'owner': 'other'})
    assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
    issued = (await response.json())['authorization'][7:]
    query, doc = db.grain_webhook_subscriptions.replace_one.call_args.args
    assert query == {'_id': OWNER} and doc['_id'] == OWNER
    assert doc['token_hash'] == hashlib.sha256(issued.encode()).hexdigest()
    assert issued not in str(doc)
    response = await client.delete('/api/integrations/grain/webhook')
    assert response.status == 200
    db.grain_webhook_subscriptions.delete_one.assert_awaited_once_with({'_id': OWNER})


@pytest.mark.asyncio
async def test_read_endpoint_always_filters_owner(webhook):
    client, db, token, fetch = webhook
    db.personal_grain_events.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=[])
    response = await client.get('/api/integrations/grain/recordings?owner=other')
    assert response.status == 200
    db.personal_grain_events.find.assert_called_once_with({'owner': OWNER}, {'_id': 0})


@pytest.mark.asyncio
async def test_oversize_body_rejected_before_provider(webhook):
    client, db, token, fetch = webhook
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, data=b'x' * 16_385)
    assert response.status == 413
    token.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_owner_denied_before_credentials(webhook):
    client, db, token, fetch = webhook
    db.users.find_one.return_value = None
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, json={'recording_id': 'recording-1'})
    assert response.status == 403
    token.assert_not_awaited()


@pytest.mark.asyncio
async def test_upstream_failure_resets_context_without_logging_provider_secret(webhook):
    client, db, token, fetch = webhook
    fetch.side_effect = RuntimeError('synthetic-private-upstream-error')
    response = await client.post('/webhooks/grain/personal', headers={'Authorization': 'Bearer ' + SECRET}, json={'recording_id': 'recording-1'})
    assert response.status == 502
    assert 'synthetic-private' not in await response.text()
    db.personal_grain_events.update_one.assert_not_awaited()
    assert grain._access_token.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining', [None, -1, 3600])
async def test_status_is_owner_scoped_and_never_returns_secret(webhook, remaining):
    from datetime import datetime, timedelta, timezone
    client, db, token, fetch = webhook
    expiry = datetime.now(timezone.utc) + timedelta(seconds=remaining) if remaining is not None else None
    db.grain_webhook_subscriptions.find_one.return_value = ({'_id': OWNER, 'expires_at': expiry,
        'token_hash': 'must-not-be-returned'} if expiry else None)
    response = await client.get('/api/integrations/grain/webhook?owner=other')
    assert response.status == 200
    assert response.headers['Cache-Control'] == 'no-store'
    body = await response.json()
    assert body['enabled'] is (remaining is not None and remaining > 0)
    assert set(body) == {'enabled', 'expires_at', 'path'}
    db.grain_webhook_subscriptions.find_one.assert_awaited_once_with({'_id': OWNER}, {'expires_at': 1})
    token.assert_not_awaited()
    db.grain_webhook_subscriptions.replace_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_accepts_mongo_naive_utc_date(webhook):
    from datetime import datetime, timedelta
    client, db, *_ = webhook
    db.grain_webhook_subscriptions.find_one.return_value = {'expires_at': datetime.now() + timedelta(days=1)}
    response = await client.get('/api/integrations/grain/webhook')
    assert (await response.json())['enabled']


@pytest.mark.asyncio
async def test_inactive_user_cannot_read_webhook_status(webhook):
    client, db, *_ = webhook
    db.users.find_one.return_value = None
    response = await client.get('/api/integrations/grain/webhook')
    assert response.status == 403
    db.grain_webhook_subscriptions.find_one.assert_not_awaited()
