"""Synthetic broker tests. No real providers, databases or credentials."""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from broker.service import Broker, Denied
from broker.grain import GrainTranscript
from broker.http import create_app

RESOURCE = '11111111-2222-3333-4444-555555555555'
OTHER = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
EMAIL = 'owner@example.test'


class Capabilities:
    def __init__(self):
        self.docs = {}
        self.create_index = AsyncMock()

    async def insert_one(self, doc):
        self.docs[doc['_id']] = copy.deepcopy(doc)

    async def find_one_and_update(self, query, update):
        doc = self.docs.get(query['_id'])
        if not doc or any(doc[k] != query[k] for k in ('deployment_id', 'revoked')):
            return None
        if doc['expires_at'] <= query['expires_at']['$gt'] or doc['remaining_calls'] <= 0:
            return None
        if query['scopes']['$elemMatch'] not in doc['scopes']:
            return None
        old = copy.deepcopy(doc)
        doc['remaining_calls'] += update['$inc']['remaining_calls']
        return old

    async def update_many(self, query, update):
        for doc in self.docs.values():
            if all(doc[k] == v for k, v in query.items()):
                doc.update(update['$set'])


@pytest.fixture
def setup():
    db = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={'status': 'active'})),
        execution_capabilities=Capabilities(),
        execution_audit=SimpleNamespace(insert_one=AsyncMock()),
        oauth_tokens=SimpleNamespace(find_one=AsyncMock(return_value={
            'access_token': 'encrypted-synthetic',
            'token_expiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        })),
    )
    operation = SimpleNamespace(valid_resource=GrainTranscript.valid_resource,
                                execute=AsyncMock(return_value={'transcript': 'hello'}))
    broker = Broker(db, 'deployment-a', {'grain.transcript': operation})
    return db, broker, operation


async def issue(broker, **kwargs):
    return await broker.issue(user_email=EMAIL, grants={'grain.transcript': [RESOURCE]}, **kwargs)


@pytest.mark.asyncio
async def test_opaque_capability_and_server_derived_identity(setup):
    db, broker, operation = setup
    run, token = await issue(broker)
    assert EMAIL not in token and run not in token
    assert token not in repr(db.execution_capabilities.docs)
    assert await broker.execute(token, 'grain.transcript', RESOURCE) == {'transcript': 'hello'}
    operation.execute.assert_awaited_once_with(db, EMAIL, RESOURCE)
    assert token not in repr(db.execution_audit.insert_one.call_args)
    await broker.initialize()
    db.execution_capabilities.create_index.assert_awaited_once_with('expires_at', expireAfterSeconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['token', 'operation', 'resource', 'expiry', 'revocation', 'tenant'])
async def test_invalid_or_out_of_scope_capabilities_never_call_provider(setup, case):
    db, broker, operation = setup
    run, token = await issue(broker)
    name, resource = 'grain.transcript', RESOURCE
    if case == 'token': token += 'x'
    if case == 'operation': name = 'grain.delete'
    if case == 'resource': resource = OTHER
    if case == 'expiry': next(iter(db.execution_capabilities.docs.values()))['expires_at'] = datetime.now(timezone.utc)
    if case == 'revocation': await broker.revoke(run)
    if case == 'tenant': broker = Broker(db, 'deployment-b', broker.operations)
    with pytest.raises(Denied):
        await broker.execute(token, name, resource)
    operation.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['pending', 'rejected', None, 'deleted'])
async def test_account_rechecked_after_issuance(setup, status):
    db, broker, operation = setup
    _, token = await issue(broker)
    db.users.find_one.return_value = {'status': status} if status != 'deleted' else None
    with pytest.raises(Denied): await broker.execute(token, 'grain.transcript', RESOURCE)
    with pytest.raises(Denied): await issue(broker)
    operation.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_admission_concurrency_and_failures(setup):
    _, broker, operation = setup
    _, token = await issue(broker, max_calls=2)
    operation.execute.side_effect = RuntimeError('synthetic failure')
    results = await asyncio.gather(*(
        broker.execute(token, 'grain.transcript', RESOURCE) for _ in range(10)
    ), return_exceptions=True)
    assert sum(isinstance(r, Denied) for r in results) == 8
    assert operation.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['users', 'execution_audit'])
async def test_dependency_failure_prevents_provider_call(setup, target):
    db, broker, operation = setup
    _, token = await issue(broker)
    getattr(getattr(db, target), 'find_one' if target == 'users' else 'insert_one').side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError): await broker.execute(token, 'grain.transcript', RESOURCE)
    operation.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [{'ttl_seconds': 0}, {'ttl_seconds': 7201}, {'ttl_seconds': True},
                                   {'max_calls': 0}, {'max_calls': 1001}, {'max_calls': False}])
async def test_invalid_issuance_bounds(setup, kwargs):
    with pytest.raises(ValueError): await issue(setup[1], **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('grants', [{}, {'unknown': [RESOURCE]}, {'grain.transcript': ['*']},
                                   {'grain.transcript': []}, {'grain.transcript': RESOURCE}])
async def test_grants_deny_unknown_operations_and_wildcards(setup, grants):
    with pytest.raises(ValueError): await setup[1].issue(user_email=EMAIL, grants=grants)


@pytest.mark.parametrize('resource', ['../../etc/passwd', 'https://evil.test', RESOURCE + '?url=x',
                                     RESOURCE + '/..', '', None, {}, '*'])
def test_resource_validation(resource):
    assert not GrainTranscript.valid_resource(resource)


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [[], {}, {'operation': 'grain.transcript', 'resource': RESOURCE, 'user_email': EMAIL},
                                 {'operation': 'grain.transcript', 'resource': RESOURCE, 'url': 'https://evil.test'}])
async def test_http_rejects_identity_or_destination_override(setup, body):
    _, broker, operation = setup
    _, token = await issue(broker)
    async with TestClient(TestServer(create_app(broker))) as client:
        response = await client.post('/v1/invoke', headers={'Authorization': 'Bearer ' + token}, json=body)
        assert response.status == 400
        assert (await client.post('/v1/issue', json={})).status == 404
    operation.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_errors_hide_secrets_and_headers_do_not_authenticate(setup):
    db, broker, operation = setup
    _, token = await issue(broker)
    operation.execute.side_effect = RuntimeError('SECRET provider key')
    async with TestClient(TestServer(create_app(broker))) as client:
        response = await client.post('/v1/invoke', headers={'X-User-Email': EMAIL},
                                     json={'operation': 'grain.transcript', 'resource': RESOURCE})
        assert response.status == 403
        response = await client.post('/v1/invoke', headers={'Authorization': 'Bearer ' + token},
                                     json={'operation': 'grain.transcript', 'resource': RESOURCE})
        assert response.status == 503 and 'SECRET' not in await response.text()


@pytest.mark.asyncio
async def test_http_success_redacts_and_is_not_cached(setup):
    _, broker, operation = setup
    _, token = await issue(broker)
    operation.execute.return_value = {'text': token, 'access_token': 'synthetic'}
    async with TestClient(TestServer(create_app(broker))) as client:
        response = await client.post('/v1/invoke', headers={'Authorization': 'Bearer ' + token},
                                     json={'operation': 'grain.transcript', 'resource': RESOURCE})
        assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
        text = await response.text()
        assert token not in text and 'synthetic' not in text


class Response:
    def __init__(self, status=200, chunks=None, encoding='identity'):
        self.status = status
        self.headers = {'Content-Encoding': encoding}
        self.chunks = chunks or [b'hello synthetic-provider-token']
        self.content = self

    async def iter_chunked(self, size):
        for chunk in self.chunks: yield chunk

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass


def network(response):
    session = SimpleNamespace(get=Mock(return_value=response))
    context = AsyncMock()
    context.__aenter__.return_value = session
    return context


@pytest.mark.asyncio
async def test_grain_pins_destination_and_only_reads_owners_oauth(setup):
    db = setup[0]
    with patch('broker.grain.decrypt_token', return_value='synthetic-provider-token'), \
            patch('broker.grain.aiohttp.ClientSession', return_value=network(Response())) as factory:
        result = await GrainTranscript().execute(db, EMAIL, RESOURCE)
    db.oauth_tokens.find_one.assert_awaited_once_with({'user_email': EMAIL, 'provider': 'grain'})
    assert 'synthetic-provider-token' not in json.dumps(result)
    assert factory.call_args.kwargs['trust_env'] is False
    assert factory.call_args.kwargs['auto_decompress'] is False
    request = factory.return_value.__aenter__.return_value.get
    assert request.call_args.args == (f'https://api.grain.com/_/public-api/v2/recordings/{RESOURCE}/transcript.txt',)
    assert request.call_args.kwargs['allow_redirects'] is False
    assert request.call_args.kwargs['headers']['Authorization'] == 'Bearer synthetic-provider-token'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['absent', 'expired'])
async def test_grain_no_organization_or_environment_fallback(setup, state, monkeypatch):
    db = setup[0]
    monkeypatch.setenv('GRAIN_API_TOKEN', 'organization-secret')
    db.oauth_tokens.find_one.return_value = None if state == 'absent' else {
        'access_token': 'encrypted', 'token_expiry': datetime.now(timezone.utc) - timedelta(seconds=1)}
    with patch('broker.grain.aiohttp.ClientSession') as factory:
        with pytest.raises(Denied): await GrainTranscript().execute(db, EMAIL, RESOURCE)
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [Response(status=302), Response(status=401),
                                     Response(encoding='gzip'), Response(chunks=[b'x' * (1024 * 1024 + 1)])])
async def test_grain_rejects_redirects_compression_errors_and_oversize(setup, response):
    with patch('broker.grain.decrypt_token', return_value='synthetic-provider-token'), \
            patch('broker.grain.aiohttp.ClientSession', return_value=network(response)):
        with pytest.raises(Denied): await GrainTranscript().execute(setup[0], EMAIL, RESOURCE)


@pytest.mark.asyncio
async def test_restart_and_targeted_revocation(setup):
    db, broker, operation = setup
    first_run, first = await issue(broker)
    _, second = await issue(broker)
    restarted = Broker(db, 'deployment-a', broker.operations)
    await restarted.revoke(first_run)
    with pytest.raises(Denied): await restarted.execute(first, 'grain.transcript', RESOURCE)
    await restarted.execute(second, 'grain.transcript', RESOURCE)
    assert operation.execute.await_count == 1


@pytest.mark.asyncio
async def test_http_oversize_and_invalid_json(setup):
    _, broker, operation = setup
    _, token = await issue(broker)
    async with TestClient(TestServer(create_app(broker))) as client:
        for body, status in [('x' * (3 * 1024 * 1024), 413), ('{bad', 400)]:
            response = await client.post('/v1/invoke', headers={'Authorization': 'Bearer ' + token}, data=body)
            assert response.status == status
    operation.execute.assert_not_awaited()
