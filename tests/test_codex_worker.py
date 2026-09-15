"""Native CLI tests use a local synthetic provider, never an account or real model."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tomllib
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from isolation.codex_worker import CodexRuntime, RuntimeFailed, runtime_environment, tool_definitions, write_config
from isolation.models import ModelDenied, request_body
from isolation.worker_entry import Broker, validate_input
from isolation.protocol import ProtocolError
from tests.test_worker_models import AUTH, CANARY, body, close, fixture, grant


def test_runtime_does_not_inherit_secrets_or_config(tmp_path, monkeypatch):
    for key in ('OPENAI_API_KEY', 'CODEX_HOME', 'HTTP_PROXY', 'NODE_OPTIONS', 'PYTHONPATH', 'GH_TOKEN'):
        monkeypatch.setenv(key, 'private-canary')
    env = runtime_environment(tmp_path)
    assert 'private-canary' not in json.dumps(env)
    assert set(env) == {'PATH', 'HOME', 'CODEX_HOME', 'LANG', 'TMPDIR'}
    write_config(tmp_path, 'http://127.0.0.1:1234', 'gpt-5.4')
    config = tomllib.loads((tmp_path / '.codex/config.toml').read_text())
    assert config['model_providers']['broker']['requires_openai_auth'] is False
    assert config['model_providers']['broker']['request_max_retries'] == 0
    assert config['model_providers']['broker']['stream_max_retries'] == 0
    assert not config['features']['shell_tool']
    assert not (tmp_path / '.codex/auth.json').exists()
    with pytest.raises(FileExistsError):
        write_config(tmp_path, 'http://127.0.0.1:1234', 'gpt-5.4')


@pytest.mark.parametrize('origin', ['http://evil.test', 'http://127.0.0.1:1234/path',
                                  'https://127.0.0.1:1234', 'http://user@127.0.0.1:1234'])
def test_runtime_cannot_select_external_provider(tmp_path, origin):
    with pytest.raises(ValueError):
        write_config(tmp_path, origin, 'gpt-5.4')


TOOL = {'name': 'calendar.list', 'description': 'Read events',
        'input_schema': {'type': 'object', 'properties': {}}}


@pytest.mark.parametrize('tools', [None, [TOOL, TOOL], [{**TOOL, 'name': 'model.start'}],
    [{**TOOL, 'name': 'artifacts.read'}], [{**TOOL, 'name': '../x'}],
    [{**TOOL, 'input_schema': 'string'}], [{**TOOL, 'user_email': 'other'}]])
def test_invalid_tool_catalog(tools):
    with pytest.raises(ValueError):
        tool_definitions(tools)


def test_native_request_adaptation_is_opt_in():
    native = {**body(), 'client_metadata': {'thread_id': 'untrusted'},
              'prompt_cache_key': 'other-owner', 'include': ['reasoning.encrypted_content'],
              'tools': [{'type': 'custom', 'name': 'apply_patch'},
                        {'type': 'function', 'name': 'view_image'},
                        {'type': 'function', 'name': 'gateway_0'}]}
    with pytest.raises(ModelDenied):
        request_body(grant(), native)
    adapted = request_body(replace(grant(), native_codex=True), native)
    assert adapted['tools'] == [{'type': 'function', 'name': 'gateway_0'}]
    assert not {'client_metadata', 'prompt_cache_key', 'include'} & adapted.keys()
    assert 'client_metadata' in native


@pytest.mark.parametrize('update', [{'include': ['message.input_image.image_url']},
    {'tools': [{'type': 'web_search'}]}, {'tools': [{'type': 'mcp', 'server_url': 'https://evil'}]},
    {'headers': {'Authorization': 'other'}}, {'previous_response_id': 'other-owner'}])
def test_native_mode_does_not_allow_hosted_or_cross_user_access(update):
    with pytest.raises(ModelDenied):
        request_body(replace(grant(), native_codex=True), {**body(), **update})


@pytest.mark.asyncio
async def test_runtime_never_approves_native_privileged_requests(tmp_path):
    rpc = AsyncMock()
    runtime = CodexRuntime(tmp_path, rpc, AsyncMock())
    runtime.thread = 'ours'
    runtime.tools = {'gateway_0': 'calendar.list'}
    assert await runtime.handle_call('item/commandExecution/requestApproval', {}) == {'decision': 'decline'}
    assert await runtime.handle_call('execCommandApproval', {}) == {'decision': 'denied'}
    for params in ({'tool': 'shell'}, {'tool': 'gateway_0', 'threadId': 'other', 'arguments': {}},
                   {'tool': 'gateway_0', 'threadId': 'ours', 'arguments': {}, 'namespace': 'other'}):
        assert not (await runtime.handle_call('item/tool/call', params))['success']
    rpc.assert_not_awaited()
    rpc.return_value = {'events': []}
    result = await runtime.handle_call('item/tool/call', {'tool': 'gateway_0', 'threadId': 'ours', 'arguments': {}})
    assert result['success']
    rpc.assert_awaited_once_with('calendar.list', {})


@pytest.mark.asyncio
async def test_broker_bad_response_poisoned_not_retried():
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"type":"tool_response","id":"wrong","result":{}}\n')
    writes = []
    broker = Broker(reader, writes.append)
    with pytest.raises(ProtocolError):
        await broker.rpc('calendar.list', {})
    with pytest.raises(ProtocolError):
        await broker.rpc('calendar.list', {})
    assert len(writes) == 1


@pytest.mark.parametrize('update', [{'runtime': 'unknown'}, {'argv': ['sh']}, {'auth_token': 'secret'},
                                    {'resume_path': '/app/accounts'}, {'prompt': None}])
def test_no_identity_executable_or_backend_resume_path_in_input(update):
    with pytest.raises(ProtocolError):
        validate_input({'runtime': 'codex', 'model': 'gpt-5.4', 'instructions': '',
                        'prompt': 'Hi', 'tools': [], **update})


def sse(events):
    return ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()


def reply_events(text):
    item = {'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
    return [
        {'type': 'response.created', 'response': {'id': 'resp_test', 'status': 'in_progress', 'output': []}},
        {'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'content': []}},
        {'type': 'response.output_text.delta', 'item_id': 'msg_test', 'output_index': 0, 'content_index': 0, 'delta': text},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
        {'type': 'response.completed', 'response': {'id': 'resp_test', 'status': 'completed', 'output': [item],
                                                 'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}}]


NATIVE = shutil.which('codex')


@pytest.mark.asyncio
@pytest.mark.skipif(not NATIVE, reason='Native Codex binary is required (synthetic provider only)')
async def test_actual_native_codex_model_gateway_tool_and_followup(tmp_path):
    requests = []
    async def provider(request):
        assert request.headers['Authorization'] == 'Bearer ' + CANARY
        data = await request.json()
        requests.append(data)
        assert data['model'] == 'gpt-5.4'
        assert 'client_metadata' not in data and 'prompt_cache_key' not in data
        assert [t['name'] for t in data['tools']] == ['gateway_0']
        if len(requests) == 1:
            item = {'id': 'fc_test', 'type': 'function_call', 'call_id': 'call_test',
                    'name': 'gateway_0', 'arguments': '{}'}
            events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                      {'type': 'response.completed', 'response': {'id': 'resp_tool', 'status': 'completed', 'output': [item]}}]
        else:
            events = reply_events('Verified synthetic calendar result')
        return web.Response(body=sse(events), content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = replace(grant(), model='gpt-5.4', native_codex=True)
    called = []
    async def rpc(tool, arguments):
        if tool.startswith('model.'):
            return await relay(AUTH, tool, arguments)
        called.append((tool, arguments))
        assert tool == 'calendar.list' and arguments == {}
        return {'events': [{'title': 'synthetic event'}]}
    output = []
    async def emit(text): output.append(text)
    runtime = CodexRuntime(tmp_path / 'runtime', rpc, emit, executable=NATIVE)
    try:
        await runtime.start('gpt-5.4', 'Use the calendar tool then answer.', [TOOL])
        await runtime.turn('List events', timeout=30)
        assert called == [('calendar.list', {})]
        assert ''.join(output) == 'Verified synthetic calendar result'
        assert any(i.get('type') == 'function_call_output' for i in requests[1]['input'])
        await runtime.turn('Summarize that result', timeout=30)
        assert len(requests) == 3
        assert any('synthetic event' in json.dumps(i) for i in requests[2]['input'])
        assert CANARY not in json.dumps(requests)
        assert callbacks['reserve'].await_count == 3
    finally:
        await runtime.close()
        await close(relay, server, session)
    assert runtime.proc.returncode is not None


@pytest.mark.asyncio
@pytest.mark.skipif(not NATIVE, reason='Native Codex binary is required (synthetic provider only)')
async def test_native_provider_failure_not_retried(tmp_path):
    async def provider(request): return web.Response(status=503, text='private provider diagnostics')
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = replace(grant(), model='gpt-5.4', native_codex=True)
    async def rpc(tool, args): return await relay(AUTH, tool, args)
    runtime = CodexRuntime(tmp_path / 'runtime', rpc, AsyncMock(), executable=NATIVE)
    try:
        await runtime.start('gpt-5.4', 'Answer briefly.', [])
        with pytest.raises(RuntimeFailed):
            await runtime.turn('hello', timeout=30)
        assert len(relay.session.requests) == 1
    finally:
        await runtime.close()
        await close(relay, server, session)


@pytest.mark.asyncio
@pytest.mark.skipif(not NATIVE, reason='Native Codex binary is required (synthetic provider only)')
@pytest.mark.parametrize("with_files", [False, True])
async def test_native_cli_through_real_worker_process_and_supervisor(tmp_path, monkeypatch, with_files):
    import ssl
    import sys
    from aiohttp.test_utils import TestClient, TestServer
    from isolation import supervisor
    from isolation.client import stream_worker
    from tests.test_worker_boundary import TOKEN, Transport, settings
    # Build an explicit worker package allowlist, not the backend source tree.
    package = tmp_path / 'image' / 'isolation'
    package.mkdir(parents=True)
    modules = ('__init__.py', 'protocol.py', 'model_bridge.py', 'codex_worker.py', 'worker_entry.py', 'workspace_tools.py', 'workspace.py', 'artifacts.py')
    for module in modules:
        shutil.copyfile(Path(__file__).parents[1] / 'isolation' / module, package / module)
    script = tmp_path / 'image' / 'launch.py'
    root = tmp_path / 'workspace'
    root.mkdir()
    script.write_text('''import asyncio,sys
from pathlib import Path
from isolation.worker_entry import run
async def main():
    reader=asyncio.StreamReader(limit=9*1024*1024)
    protocol=asyncio.StreamReaderProtocol(reader)
    transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin.buffer)
    def write(raw):
        sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    try: await run(reader,write,root=Path(sys.argv[1]),executable=sys.argv[2])
    finally: transport.close()
asyncio.run(main())
''')
    monkeypatch.setattr(supervisor, 'docker_command', lambda *_: [sys.executable, str(script), str(root), NATIVE])
    monkeypatch.setattr(supervisor, 'command', AsyncMock(return_value=b''))
    host = supervisor.Supervisor(settings())
    app = web.Application()
    app.router.add_get('/v1/run', host.run)
    client = TestClient(TestServer(app))
    await client.start_server()
    requests = []
    async def provider(request):
        data = await request.json()
        requests.append(data)
        if with_files and len(requests) <= 2:
            args = ({'command': 'printf "native-generated" > report.txt'} if len(requests) == 1 else {'path': 'report.txt'})
            item = {'id': 'fc_' + str(len(requests)), 'type': 'function_call', 'call_id': 'call_' + str(len(requests)),
                    'name': 'gateway_' + str(len(requests) - 1), 'arguments': json.dumps(args)}
            events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                      {'type': 'response.completed', 'response': {'id': 'resp_' + str(len(requests)), 'status': 'completed', 'output': [item]}}]
        else:
            events = reply_events('Real native worker reply')
        return web.Response(body=sse(events), content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = replace(grant(), model='gpt-5.4', native_codex=True)
    from isolation.artifacts import ArtifactScope
    from isolation.gateway import ToolGateway, FILE_SCHEMAS
    from isolation.catalog import CATALOG
    authority = replace(AUTH, allowed_tools=AUTH.allowed_tools | frozenset(FILE_SCHEMAS))
    relay.authority = authority
    scope = ArtifactScope(tmp_path / 'artifacts', authority, 'conversation')
    file_events = []
    async def committed(meta): file_events.append(meta)
    gateway = ToolGateway(authority, authorize=AsyncMock(return_value=True), audit=AsyncMock(),
                          artifacts=scope, models=relay, on_artifact=committed)
    tools = [t for t in CATALOG if t['name'] in ('workspace.exec', 'workspace.publish')] if with_files else []
    try:
        output = [chunk async for chunk in stream_worker(session=Transport(client),
            url='https://worker.example.test', token=TOKEN, tls=ssl.create_default_context(),
            authority=authority, input={'runtime': 'codex', 'model': 'gpt-5.4',
                                   'instructions': 'Answer briefly.', 'prompt': 'hello', 'tools': tools},
            authorize=AsyncMock(return_value=True), execute_tool=gateway, max_seconds=30)]
        assert ''.join(output) == 'Real native worker reply'
        assert len(relay.session.requests) == (3 if with_files else 1)
        if with_files:
            import base64
            assert len(file_events) == 1
            assert base64.b64decode(scope.read(file_events[0]['artifact_id'], 0)['data']) == b'native-generated'
            assert file_events[0]['name'] == 'report.txt'
        assert list(root.iterdir()) == []  # ephemeral home removed on success
    finally:
        scope.close()
        await client.close()
        await close(relay, server, session)
    assert not host.active


@pytest.mark.asyncio
@pytest.mark.skipif(not NATIVE, reason='Native Codex binary is required (synthetic provider only)')
async def test_native_cancellation_closes_provider_and_process(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    async def provider(request):
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        started.set()
        await release.wait()
        return response
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = replace(grant(), model='gpt-5.4', native_codex=True)
    async def rpc(tool, args): return await relay(AUTH, tool, args)
    runtime = CodexRuntime(tmp_path / 'runtime', rpc, AsyncMock(), executable=NATIVE)
    turn = None
    try:
        await runtime.start('gpt-5.4', 'Answer briefly.', [])
        turn = asyncio.create_task(runtime.turn('hello', timeout=30))
        await asyncio.wait_for(started.wait(), 10)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError): await turn
        await asyncio.wait_for(runtime.close(), 10)
        assert runtime.proc.returncode is not None
        # Backend independently closes active model streams on full-run teardown.
        await relay.close()
        assert relay.response is None
        assert callbacks['settle'].await_count == 1
        assert len(relay.session.requests) == 1
    finally:
        release.set()
        if turn is not None and not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        await runtime.close()
        await close(relay, server, session)
