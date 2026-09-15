"""Synthetic-provider relay tests. No real model calls or production accounts."""
import asyncio
import base64
from dataclasses import replace
import json
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from isolation.models import ModelGrant, ModelRelay, ModelDenied, request_body, SCHEMAS
from isolation.protocol import RunAuthority

AUTH = RunAuthority('run-1', 'owner@example.test', frozenset(SCHEMAS))
CANARY = 'test-only-provider-secret'


def grant(protocol='responses'):
    return ModelGrant(protocol, 'https://provider.example.test/v1/responses',
                      'test-model', {'Authorization': 'Bearer ' + CANARY}, max_output_tokens=100)


def body(protocol='responses'):
    return {'model': 'test-model', 'stream': True,
            **({'input': 'hello'} if protocol == 'responses' else
               {'messages': [{'role': 'user', 'content': 'hello'}]})}


@pytest.mark.parametrize('protocol', ['responses', 'messages', 'chat'])
def test_fixed_model_limits_and_input_copy(protocol):
    original = body(protocol)
    result = request_body(grant(protocol), original)
    key = {'responses': 'max_output_tokens', 'messages': 'max_tokens', 'chat': 'max_completion_tokens'}[protocol]
    assert result[key] == 100
    assert key not in original
    if protocol == 'responses':
        assert result['store'] is False


@pytest.mark.parametrize('updates', [
    {'model': 'expensive-model'}, {'stream': False}, {'max_output_tokens': 101},
    {'max_output_tokens': True}, {'max_output_tokens': -1},
    {'previous_response_id': 'other-owner-response'}, {'conversation': 'other-owner'},
    {'background': True}, {'headers': {'Authorization': 'attacker'}},
    {'endpoint': 'http://169.254.169.254'}, {'input': [{'type': 'item_reference', 'id': 'other'}]},
    {'input': [{'type': 'message', 'content': [{'type': 'input_text', 'file_id': 'other'}]}]},
    {'tools': [{'type': 'mcp', 'server_url': 'https://private-service'}]},
    {'tools': [{'type': 'web_search'}]}, {'tools': [{'type': 'code_interpreter'}]},
    {'tools': [{'type': 'function', 'name': 'ok', 'server_url': 'https://private'}]},
])
def test_worker_cannot_expand_upstream_authority(updates):
    with pytest.raises(ModelDenied):
        request_body(grant(), {**body(), **updates})


@pytest.mark.parametrize('protocol,tools', [
    ('messages', [{'type': 'web_search_20250305', 'name': 'search'}]),
    ('messages', [{'name': 'x', 'cache_control': {'type': 'ephemeral'}}]),
    ('chat', [{'type': 'mcp', 'function': {'name': 'x'}}]),
    ('chat', [{'type': 'function', 'function': {'name': 'x', 'url': 'https://other'}}]),
])
def test_hosted_tools_denied_for_every_protocol(protocol, tools):
    with pytest.raises(ModelDenied):
        request_body(grant(protocol), {**body(protocol), 'tools': tools})


@pytest.mark.parametrize('protocol,tool', [
    ('responses', {'type': 'function', 'name': 'local_tool', 'parameters': {'type': 'object'}}),
    ('messages', {'name': 'local_tool', 'input_schema': {'type': 'object'}}),
    ('chat', {'type': 'function', 'function': {'name': 'local_tool', 'parameters': {'type': 'object'}}}),
])
def test_local_tool_descriptions_are_not_hosted_execution(protocol, tool):
    assert request_body(grant(protocol), {**body(protocol), 'tools': [tool]})['tools'] == [tool]


@pytest.mark.parametrize('endpoint', ['http://provider/v1', 'https://user:secret@provider/v1',
                                      'https://provider/v1?token=secret', 'https://provider/v1#x'])
def test_invalid_provider_configuration(endpoint):
    with pytest.raises(ValueError):
        replace(grant(), endpoint=endpoint)


def test_provider_secret_not_in_grant_repr():
    assert CANARY not in repr(grant())


class SyntheticSession:
    """Production grant remains HTTPS; route it only to the test server."""
    def __init__(self, session, url):
        self.session, self.url = session, url
        self.trust_env = session.trust_env
        self.cookie_jar = session.cookie_jar
        self.requests = []

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return await self.session.post(self.url, **kwargs)


async def fixture(handler, **overrides):
    app = web.Application()
    app.router.add_post('/model', handler)
    server = TestServer(app)
    await server.start_server()
    session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False)
    transport = SyntheticSession(session, server.make_url('/model'))
    callbacks = {key: AsyncMock(return_value=True) for key in ('authorize', 'audit', 'reserve', 'settle')}
    callbacks.update(overrides)
    relay = ModelRelay(AUTH, grant(), session=transport, **callbacks)
    return relay, server, session, callbacks


async def close(relay, server, session):
    try:
        await relay.close()
    finally:
        await session.close()
        await server.close()


@pytest.mark.asyncio
async def test_real_http_stream_keeps_credentials_headers_and_budget_on_backend():
    async def provider(request):
        assert request.headers['Authorization'] == 'Bearer ' + CANARY
        value = await request.json()
        assert value['store'] is False and value['max_output_tokens'] == 100
        return web.Response(body=b'data: {"text":"hello"}\n\ndata: [DONE]\n\n',
                            headers={'Content-Type': 'text/event-stream', 'X-Secret': CANARY,
                                     'Set-Cookie': 'secret=' + CANARY})
    relay, server, session, callbacks = await fixture(provider)
    try:
        started = await relay(AUTH, 'model.start', {'body': body()})
        args = {'stream_id': started['stream_id']}
        result = b''
        while True:
            chunk = await relay(AUTH, 'model.read', args)
            assert CANARY not in json.dumps(chunk)
            result += base64.b64decode(chunk['data'])
            if chunk['eof']:
                break
        assert b'hello' in result
        assert CANARY not in json.dumps(started)
        assert callbacks['reserve'].await_count == 1
        assert callbacks['settle'].call_args.args[-1] == 'stream_ended'
        assert not list(session.cookie_jar)
        assert relay.session.requests[0][1]['allow_redirects'] is False
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [302, 401, 429, 500])
async def test_no_redirect_retry_error_body_or_credential_leak(status):
    calls = []
    async def provider(request):
        calls.append(1)
        return web.Response(status=status, text=CANARY, headers={'Location': 'http://private.test'})
    relay, server, session, callbacks = await fixture(provider)
    try:
        with pytest.raises(ModelDenied) as error:
            await relay(AUTH, 'model.start', {'body': body()})
        assert CANARY not in str(error.value)
        assert len(calls) == 1
        assert callbacks['settle'].call_args.args[-1] == 'unknown'
        assert relay.response is None
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_revocation_during_read_never_returns_new_bytes():
    release = asyncio.Event()
    async def provider(request):
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        await release.wait()
        await response.write(b'private bytes')
        return response
    relay, server, session, callbacks = await fixture(provider)
    try:
        started = await relay(AUTH, 'model.start', {'body': body()})
        read = asyncio.create_task(relay(AUTH, 'model.read', {'stream_id': started['stream_id']}))
        await asyncio.sleep(.01)
        callbacks['authorize'].return_value = False
        release.set()
        with pytest.raises(ModelDenied):
            await read
        assert relay.response is None
        assert callbacks['settle'].call_args.args[-1] == 'unknown'
    finally:
        release.set()
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_foreign_owner_and_budget_failure_never_dispatch():
    provider = AsyncMock(side_effect=AssertionError('must not call provider'))
    relay, server, session, callbacks = await fixture(provider)
    try:
        with pytest.raises(ModelDenied):
            await relay(replace(AUTH, user_email='other@example.test'), 'model.start', {'body': body()})
        callbacks['reserve'].side_effect = ValueError('budget exhausted')
        with pytest.raises(ValueError, match='budget exhausted'):
            await relay(AUTH, 'model.start', {'body': body()})
        assert not relay.session.requests
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_active_stream_call_limit_cancel_and_no_replay():
    async def provider(request):
        return web.Response(body=b'data: hello\n\n', content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = replace(grant(), max_calls=1)
    try:
        started = await relay(AUTH, 'model.start', {'body': body()})
        assert await relay(AUTH, 'model.close', {'stream_id': started['stream_id']}) == {'closed': True}
        with pytest.raises(ModelDenied):
            await relay(AUTH, 'model.start', {'body': body()})
        assert len(relay.session.requests) == 1
        assert callbacks['settle'].await_count == 1
        assert callbacks['settle'].call_args.args[-1] == 'interrupted'
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_accounting_failure_blocks_further_calls():
    async def provider(request):
        return web.Response(body=b'', content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    try:
        started = await relay(AUTH, 'model.start', {'body': body()})
        callbacks['settle'].side_effect = RuntimeError('ledger unavailable')
        with pytest.raises(RuntimeError, match='ledger unavailable'):
            await relay(AUTH, 'model.read', {'stream_id': started['stream_id']})
        assert relay.closed
        with pytest.raises(ModelDenied):
            await relay(AUTH, 'model.start', {'body': body()})
        assert len(relay.session.requests) == 1
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['responses', 'messages', 'chat'])
async def test_worker_loopback_bridge_real_http_end_to_end(protocol):
    from isolation.model_bridge import ModelBridge, PATHS
    received = []
    async def provider(request):
        received.append(dict(request.headers))
        return web.Response(body=b'data: {"text":"hello"}\n\n', content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = grant(protocol)
    bridge = ModelBridge(protocol, lambda tool, args: relay(AUTH, tool, args))
    try:
        origin = await bridge.serve()
        assert origin.startswith('http://127.0.0.1:')
        async with session.post(origin + PATHS[protocol], json=body(protocol),
                headers={'Authorization': 'worker-forged-key', 'X-User-Email': 'other@example.test'}) as response:
            assert response.status == 200
            assert await response.read() == b'data: {"text":"hello"}\n\n'
        assert received[0]['Authorization'] == 'Bearer ' + CANARY
        assert 'X-User-Email' not in received[0]
        assert callbacks['settle'].call_args.args[-1] == 'stream_ended'
        assert relay.response is None
    finally:
        await bridge.close()
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_worker_http_bridge_denies_proxy_paths_queries_and_hides_errors():
    from isolation.model_bridge import ModelBridge
    rpc = AsyncMock(side_effect=RuntimeError(CANARY))
    bridge = ModelBridge('responses', rpc)
    try:
        origin = await bridge.serve()
        async with aiohttp.ClientSession() as session:
            for path in ['/v1/files', '/v1/responses/other-owner', '/v1/responses?url=http://private']:
                async with session.post(origin + path, json=body()) as response:
                    assert response.status in (400, 404)
            assert rpc.await_count == 0
            async with session.post(origin + '/v1/responses', json=body()) as response:
                assert response.status == 400
                assert CANARY not in await response.text()
            assert rpc.await_count == 1
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_worker_bridge_shutdown_closes_provider_stream():
    from isolation.model_bridge import ModelBridge
    reading, held = asyncio.Event(), asyncio.Event()
    async def rpc(tool, args):
        if tool == 'model.start':
            return {'stream_id': 'test-stream', 'content_type': 'text/event-stream'}
        if tool == 'model.read':
            reading.set()
            await held.wait()
        if tool == 'model.close':
            held.set()
            return {'closed': True}
    tracked = AsyncMock(side_effect=rpc)
    bridge = ModelBridge('responses', tracked)
    try:
        origin = await bridge.serve()
        async with aiohttp.ClientSession() as session:
            response = await session.post(origin + '/v1/responses', json=body())
            await asyncio.wait_for(reading.wait(), 2)
            await asyncio.wait_for(bridge.close(), 2)
            assert any(c.args == ('model.close', {'stream_id': 'test-stream'}) for c in tracked.call_args_list)
            response.close()
    finally:
        held.set()
        await bridge.close()


@pytest.mark.asyncio
async def test_real_worker_process_model_bridge_over_supervisor(monkeypatch, tmp_path):
    """Real child, HTTP, WS and model gateway. Docker is replaced, not certified."""
    import shutil
    import ssl
    import sys
    from pathlib import Path
    from aiohttp.test_utils import TestClient
    from isolation import supervisor
    from isolation.artifacts import ArtifactScope
    from isolation.gateway import ToolGateway
    from isolation.client import stream_worker
    from tests.test_worker_boundary import settings, TOKEN, Transport

    async def provider(request):
        assert request.headers['Authorization'] == 'Bearer ' + CANARY
        return web.Response(body=b'data: {"text":"isolated hello"}\n\n', content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    scope = ArtifactScope(tmp_path / 'backend-files', AUTH, 'conversation-1')
    gateway = ToolGateway(AUTH, authorize=callbacks['authorize'], audit=callbacks['audit'],
                          artifacts=scope, models=relay)
    package = tmp_path / 'worker-image' / 'isolation'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    for module in ('model_bridge.py', 'protocol.py'):
        shutil.copyfile(Path(__file__).parents[1] / 'isolation' / module, package / module)
    script = package.parent / 'worker.py'
    script.write_text('''import asyncio,json,sys,os
import aiohttp
from isolation.model_bridge import ModelBridge
counter=0
async def rpc(tool,args):
    global counter
    counter+=1
    request_id=str(counter)
    print(json.dumps({'type':'tool_request','id':request_id,'tool':tool,'arguments':args}),flush=True)
    value=json.loads(await asyncio.to_thread(sys.stdin.readline))
    assert value['id']==request_id
    return value['result']
async def main():
    start=json.loads(sys.stdin.readline())['input']
    assert 'ANTHROPIC_API_KEY' not in os.environ
    assert 'OBSERVABILITY_MONGODB_URI' not in os.environ
    assert 'OAUTH_ENCRYPTION_KEY' not in os.environ
    bridge=ModelBridge('responses',rpc)
    try:
        origin=await bridge.serve()
        async with aiohttp.ClientSession() as client:
            async with client.post(origin+'/v1/responses',json=start['body']) as response:
                assert response.status==200
                print(json.dumps({'type':'text','text':await response.text()}),flush=True)
    finally:
        await bridge.close()
    print(json.dumps({'type':'done'}),flush=True)
asyncio.run(main())
''')
    monkeypatch.setattr(supervisor, 'docker_command', lambda *_: [sys.executable, str(script)])
    monkeypatch.setattr(supervisor, 'command', AsyncMock(return_value=b''))
    host = supervisor.Supervisor(settings())
    app = web.Application()
    app.router.add_get('/v1/run', host.run)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        result = [chunk async for chunk in stream_worker(session=Transport(client),
            url='https://worker.example.test', token=TOKEN, tls=ssl.create_default_context(),
            authority=AUTH, input={'body': body()}, authorize=callbacks['authorize'],
            execute_tool=gateway, max_seconds=5)]
        assert result == ['data: {"text":"isolated hello"}\n\n']
        assert not host.active
        assert callbacks['reserve'].await_count == 1
        assert callbacks['settle'].call_args.args[-1] == 'stream_ended'
    finally:
        await client.close()
        scope.close()
        await close(relay, server, session)
