"""Local, synthetic regression tests for connection and credential boundaries."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp.test_utils import make_mocked_request

from api import oauth_helpers as helpers, oauth_routes as routes, governance_routes
from tools._auth_token import create_user_auth_token, verify_user_auth_token
from utils.secret_redaction import redact_secrets
from observability.observer import ConversationObserver


class StateStore:
    def __init__(self):
        self.records = {}
        self.create_index = AsyncMock()

    async def insert_one(self, doc):
        self.records[doc['_id']] = doc.copy()

    async def find_one_and_delete(self, query):
        doc = self.records.get(query['_id'])
        if not doc or any(doc[k] != query[k] for k in ('provider', 'browser_hash')):
            return None
        if doc['expires_at'] <= query['expires_at']['$gt']:
            return None
        return self.records.pop(query['_id'])


@pytest.fixture
def db():
    return SimpleNamespace(oauth_states=StateStore(), users=SimpleNamespace(
        find_one=AsyncMock(return_value={'email': 'owner@example.com', 'status': 'active'}),
    ))


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setenv('OAUTH_ENCRYPTION_KEY', 'synthetic-test-key')


@pytest.mark.asyncio
async def test_state_opaque_and_single_use_across_workers(db):
    state, cookie = await helpers.create_oauth_state(db, 'owner@example.com', 'google', 'verifier')
    assert 'owner' not in state and 'verifier' not in state
    assert not verify_user_auth_token(state, 'owner@example.com')
    results = await asyncio.gather(*(helpers.consume_oauth_state(db, state, 'google', cookie) for _ in range(2)))
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result)['code_verifier'] == 'verifier'


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong', ['cookie', 'provider', 'state'])
async def test_state_requires_exact_browser_and_provider_without_consuming(db, wrong):
    state, cookie = await helpers.create_oauth_state(db, 'owner@example.com', 'grain')
    assert await helpers.consume_oauth_state(db, state + 'x' if wrong == 'state' else state,
        'google' if wrong == 'provider' else 'grain', '' if wrong == 'cookie' else cookie) is None
    assert await helpers.consume_oauth_state(db, state, 'grain', cookie)


@pytest.mark.asyncio
async def test_expired_state_denied_before_ttl_cleanup(db):
    state, cookie = await helpers.create_oauth_state(db, 'owner@example.com', 'google')
    next(iter(db.oauth_states.records.values()))['expires_at'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await helpers.consume_oauth_state(db, state, 'google', cookie) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('user', [None, {'status': 'pending'}, {'status': 'rejected'}])
async def test_disabled_user_cannot_finish_linking(db, user):
    state, cookie = await helpers.create_oauth_state(db, 'owner@example.com', 'google')
    db.users.find_one.return_value = user
    assert await helpers.consume_oauth_state(db, state, 'google', cookie) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['google', 'slack', 'grain', 'notion', 'hubspot', 'custom-mcp/demo'])
async def test_every_callback_rejects_missing_browser_before_token_exchange(db, provider):
    state, _ = await helpers.create_oauth_state(db, 'owner@example.com', provider)
    custom = provider.startswith('custom-mcp/')
    handler = routes.handle_custom_mcp_callback if custom else (
        {'google': routes.handle_google_callback, 'slack': routes.handle_slack_callback}.get(provider, routes.handle_provider_callback))
    req = make_mocked_request('GET', f'/api/oauth/{provider}/callback?code=synthetic&state={state}',
                              match_info={'provider': provider.split('/')[-1]})
    with patch.object(routes, 'get_db', return_value=db), patch.object(routes.aiohttp, 'ClientSession') as network:
        response = await handler(req)
    assert response.status == 400
    network.assert_not_called()


@pytest.mark.asyncio
async def test_authorize_cookie_properties_and_parallel_tabs(db, monkeypatch):
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_ID', 'synthetic-client')
    monkeypatch.setenv('APP_BASE_URL', 'https://example.invalid')
    request = make_mocked_request('GET', '/api/oauth/google/authorize')
    request['user_email'] = 'owner@example.com'
    with patch.object(routes, 'get_db', return_value=db):
        first = await routes.handle_google_authorize(request)
        second = await routes.handle_google_authorize(request)
    assert set(first.cookies).isdisjoint(second.cookies)
    state = parse_qs(urlparse(json.loads(first.text)['authorize_url']).query)['state'][0]
    cookie = first.cookies[helpers.oauth_state_cookie(state)]
    assert cookie['httponly'] and cookie['secure'] and cookie['samesite'] == 'Lax'
    assert first.headers['Cache-Control'] == 'no-store'
    assert await helpers.consume_oauth_state(db, state, 'google', cookie.value)


@pytest.mark.asyncio
async def test_google_callback_with_browser_stores_for_initiator(db, monkeypatch):
    for key in ('GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_SECRET'):
        monkeypatch.setenv(key, 'synthetic')
    state, cookie = await helpers.create_oauth_state(db, 'owner@example.com', 'google')
    req = make_mocked_request('GET', f'/api/oauth/google/callback?code=synthetic&state={state}',
        headers={'Cookie': f'{helpers.oauth_state_cookie(state)}={cookie}'})
    post = MagicMock()
    post.__aenter__ = AsyncMock(return_value=SimpleNamespace(status=200, json=AsyncMock(return_value={
        'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh'})))
    get = MagicMock()
    get.__aenter__ = AsyncMock(return_value=SimpleNamespace(status=200, json=AsyncMock(return_value={
        'email': 'chosen-account@example.com'})))
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=SimpleNamespace(post=MagicMock(return_value=post), get=MagicMock(return_value=get)))
    save = AsyncMock()
    with patch.object(routes, 'get_db', return_value=db), patch.object(routes.aiohttp, 'ClientSession', return_value=session), patch.object(routes, 'store_google_tokens', save):
        response = await routes.handle_google_callback(req)
        replay = await routes.handle_google_callback(req)
    assert response.status == 200 and replay.status == 400
    assert save.await_count == 1
    assert save.await_args.kwargs['user_email'] == 'owner@example.com'


def test_personal_token_purpose_identity_and_expiry(monkeypatch):
    token = create_user_auth_token('owner@example.com')
    assert verify_user_auth_token(token, 'owner@example.com')
    assert not verify_user_auth_token(token, 'other@example.com')
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now + 3601)
    assert not verify_user_auth_token(token, 'owner@example.com')
    monkeypatch.setattr(time, 'time', lambda: now - 100)
    assert not verify_user_auth_token(token, 'owner@example.com')


def legacy_token():
    ts = str(int(time.time()))
    sig = hmac.new(b'synthetic-test-key', f'owner@example.com:{ts}'.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(json.dumps({'email': 'owner@example.com', 'ts': ts, 'sig': sig}).encode()).decode()


def test_legacy_tokens_and_oauth_state_no_longer_accepted():
    assert not verify_user_auth_token(legacy_token(), 'owner@example.com')


@pytest.mark.parametrize('payload', [[], None, 'text', 1, {'purpose': 'personal-tool', 'version': 1, 'email': [], 'ts': 1, 'sig': {}}])
def test_malformed_tokens_fail_closed(payload):
    token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    assert not verify_user_auth_token(token, 'owner@example.com')


@pytest.mark.parametrize('template', ['--auth-token {token}', '--auth-token="{token}"', 'Personal Tools Auth Token: {token}', '{{"nested": "{token}"}}', 'raw: {token}'])
def test_redacts_old_and_new_personal_tokens(template):
    for token in (legacy_token(), create_user_auth_token('owner@example.com')):
        assert token not in redact_secrets(template.format(token=token))


@pytest.mark.asyncio
async def test_observer_redacts_before_truncation_and_history_response():
    from api import routes as chat, task_routes
    token = create_user_auth_token('owner@example.com')
    database = MagicMock()
    database.turns.update_one = AsyncMock()
    observer = ConversationObserver(database, {'prompt': token})
    await observer.record_tool_call(1, 'Bash', 'call', {'command': 'x' * 9900 + ' --auth-token ' + token})
    assert token[:40] not in repr(database.turns.update_one.await_args)
    await observer.record_tool_result('call', False, token)
    assert token not in repr(database.turns.update_one.await_args)
    for serialize in (chat._serialize, task_routes._serialize):
        assert token not in json.dumps(serialize({'messages': [{'content': token}], 'tool_calls': [{'input': token}]}))


@pytest.mark.asyncio
async def test_profile_and_admin_serialization_exclude_local_auth():
    database = MagicMock()
    user = {'email': 'owner@example.com', 'local_auth': {'password_hash': 'synthetic-hash', 'salt': 'synthetic-salt'}}
    database.users.find_one = AsyncMock(return_value=user)
    req = make_mocked_request('GET', '/api/governance/me')
    req['user_email'] = user['email']
    with patch.object(governance_routes, 'get_db', return_value=database):
        response = await governance_routes.handle_get_me(req)
    assert 'local_auth' not in response.text
    assert 'local_auth' not in governance_routes._serialize([user])[0]
    assert 'local_auth' in user  # Sanitization never mutates credential storage.


def test_callback_html_escapes_untrusted_provider_text():
    response = routes._callback_error("</script><script>alert('test')</script>")
    assert response.status == 400
    assert response.text.count('<script>') == 1
    assert "}, '*')" not in response.text
