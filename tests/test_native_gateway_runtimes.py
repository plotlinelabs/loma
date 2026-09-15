"""Native binaries talk only to local synthetic providers and gateway tools."""
import asyncio
from dataclasses import replace
import json
import shutil
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from isolation.claude_worker import ClaudeRuntime, runtime_environment as claude_environment
from isolation.opencode_worker import OpenCodeRuntime, runtime_environment as opencode_environment
from isolation.codex_worker import CodexRuntime, RuntimeFailed
from tests.test_claude_worker import message_events
from tests.test_codex_worker import TOOL, sse
from tests.test_worker_models import AUTH, CANARY, fixture, grant, close

RUNTIMES = [('claude', ClaudeRuntime, 'messages', 'claude-sonnet-4-6'),
            ('opencode', OpenCodeRuntime, 'chat', 'test-model')]


def provider_reply(protocol, tool=False, tool_name=None):
    if protocol == 'messages':
        if not tool:
            return sse(message_events('Verified synthetic result'))
        events = message_events('')
        return sse([events[0],
            {'type': 'content_block_start', 'index': 0, 'content_block': {
                'type': 'tool_use', 'id': 'call_test', 'name': tool_name, 'input': {}}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': '{}'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use', 'stop_sequence': None},
             'usage': {'output_tokens': 5}}, {'type': 'message_stop'}])
    delta = ({'tool_calls': [{'index': 0, 'id': 'call_test', 'type': 'function',
                             'function': {'name': tool_name, 'arguments': '{}'}}]} if tool
             else {'role': 'assistant', 'content': 'Verified synthetic result'})
    data = [{'id': 'chat_test', 'object': 'chat.completion.chunk', 'model': 'test-model',
             'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {'id': 'chat_test', 'object': 'chat.completion.chunk', 'model': 'test-model',
             'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if tool else 'stop'}],
             'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}]
    return (''.join('data: ' + json.dumps(x) + '\n\n' for x in data) + 'data: [DONE]\n\n').encode()


@pytest.mark.parametrize('binary,runtime_class,protocol,model', RUNTIMES)
@pytest.mark.asyncio
async def test_native_gateway_tool_dispatch(tmp_path, binary, runtime_class, protocol, model):
    executable = shutil.which(binary)
    if not executable:
        pytest.skip('Pinned native binary required')
    requests, called = [], []
    async def provider(request):
        assert request.headers['Authorization'] == 'Bearer ' + CANARY
        data = await request.json()
        requests.append(data)
        tools = data.get('tools', [])
        names = [t['name'] if protocol == 'messages' else t['function']['name'] for t in tools]
        assert len(names) == 1 and 'gateway_0' in names[0]
        assert 'metadata' not in data
        return web.Response(body=provider_reply(protocol, len(requests) == 1, names[0]),
                            content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider)
    relay.grant = replace(grant(protocol), model=model, max_output_tokens=8192, native_claude=binary == 'claude')
    async def rpc(tool, args):
        if tool.startswith('model.'):
            return await relay(AUTH, tool, args)
        called.append((tool, args))
        return {'events': ['Synthetic calendar event']}
    output = []
    async def emit(text): output.append(text)
    runtime = runtime_class(tmp_path / 'runtime', rpc, emit, executable=executable)
    try:
        await runtime.start(model, 'Use the calendar tool then answer.', [TOOL])
        await runtime.turn('Show my calendar', timeout=30)
        assert called == [('calendar.list', {})]
        assert len(requests) == 2
        assert 'Synthetic calendar event' in json.dumps(requests[1]['messages'])
        assert ''.join(output) == 'Verified synthetic result'
        assert CANARY not in json.dumps(requests)
    finally:
        await runtime.close()
        await close(relay, server, session)


@pytest.mark.parametrize('binary,runtime_class,protocol,model', RUNTIMES)
@pytest.mark.asyncio
async def test_native_failure_does_not_retry_provider(tmp_path, binary, runtime_class, protocol, model):
    executable = shutil.which(binary)
    if not executable:
        pytest.skip('Pinned native binary required')
    async def provider(request):
        return web.Response(status=503, text='private diagnostic must not escape')
    relay, server, session, _ = await fixture(provider)
    relay.grant = replace(grant(protocol), model=model, max_output_tokens=8192, native_claude=binary == 'claude')
    async def rpc(tool, args): return await relay(AUTH, tool, args)
    emit = AsyncMock()
    runtime = runtime_class(tmp_path / 'runtime', rpc, emit, executable=executable)
    try:
        await runtime.start(model, 'Answer briefly.', [])
        with pytest.raises(RuntimeFailed, match='not replayed'):
            await runtime.turn('hello', timeout=30)
        assert len(relay.session.requests) == 1
        emit.assert_not_awaited()
        assert runtime.proc.returncode is not None
    finally:
        await runtime.close()
        await close(relay, server, session)


@pytest.mark.parametrize('binary,runtime_class,protocol,model', RUNTIMES)
@pytest.mark.asyncio
async def test_native_cancel_kills_process_and_closes_stream(tmp_path, binary, runtime_class, protocol, model):
    executable = shutil.which(binary)
    if not executable:
        pytest.skip('Pinned native binary required')
    requested = asyncio.Event()
    release = asyncio.Event()
    async def provider(request):
        requested.set()
        await release.wait()
        return web.Response(body=provider_reply(protocol), content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider)
    relay.grant = replace(grant(protocol), model=model, max_output_tokens=8192, native_claude=binary == 'claude')
    async def rpc(tool, args): return await relay(AUTH, tool, args)
    runtime = runtime_class(tmp_path / 'runtime', rpc, AsyncMock(), executable=executable)
    task = None
    try:
        await runtime.start(model, 'Answer briefly.', [])
        task = asyncio.create_task(runtime.turn('hello', timeout=30))
        await asyncio.wait_for(requested.wait(), 20)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.proc.returncode is not None
        await runtime.close()
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        release.set()
        await runtime.close()
        await close(relay, server, session)


def test_native_environments_do_not_copy_backend_credentials(tmp_path, monkeypatch):
    for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'OPENCODE_CONFIG_CONTENT',
                'CLAUDE_CONFIG_DIR', 'HTTP_PROXY', 'NODE_OPTIONS', 'PYTHONPATH', 'GH_TOKEN'):
        monkeypatch.setenv(key, 'private-canary')
    for env in (claude_environment(tmp_path, 'http://127.0.0.1:1'), opencode_environment(tmp_path, {})):
        assert 'private-canary' not in json.dumps(env)
        assert 'HTTP_PROXY' not in env and 'NODE_OPTIONS' not in env and 'PYTHONPATH' not in env


@pytest.mark.parametrize('binary,runtime_class,protocol,model', RUNTIMES)
@pytest.mark.asyncio
async def test_native_cli_through_real_worker_process(tmp_path, monkeypatch, binary, runtime_class, protocol, model):
    import ssl
    import sys
    from pathlib import Path
    from aiohttp.test_utils import TestClient, TestServer
    from isolation import supervisor
    from isolation.client import stream_worker
    from tests.test_worker_boundary import TOKEN, Transport, settings
    executable = shutil.which(binary)
    if not executable:
        pytest.skip('Pinned native binary required')
    package = tmp_path / 'image' / 'isolation'
    package.mkdir(parents=True)
    modules = ('__init__.py', 'protocol.py', 'model_bridge.py', 'codex_worker.py',
               'claude_worker.py', 'opencode_worker.py', 'mcp_bridge.py', 'worker_entry.py', 'workspace_tools.py', 'workspace.py', 'artifacts.py')
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
    monkeypatch.setattr(supervisor, 'docker_command', lambda *_: [sys.executable, str(script), str(root), executable])
    monkeypatch.setattr(supervisor, 'command', AsyncMock(return_value=b''))
    host = supervisor.Supervisor(settings())
    app = web.Application()
    app.router.add_get('/v1/run', host.run)
    client = TestClient(TestServer(app))
    await client.start_server()
    async def provider(request):
        return web.Response(body=provider_reply(protocol), content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider)
    relay.grant = replace(grant(protocol), model=model, max_output_tokens=8192, native_claude=binary == 'claude')
    try:
        output = [chunk async for chunk in stream_worker(session=Transport(client),
            url='https://worker.example.test', token=TOKEN, tls=ssl.create_default_context(),
            authority=AUTH, input={'runtime': binary, 'model': model,
                                   'instructions': 'Answer briefly.', 'prompt': 'hello', 'tools': []},
            authorize=AsyncMock(return_value=True), execute_tool=relay, max_seconds=30)]
        assert ''.join(output) == 'Verified synthetic result'
        assert len(relay.session.requests) == 1
        assert list(root.iterdir()) == []
    finally:
        await client.close()
        await close(relay, server, session)
    assert not host.active


@pytest.mark.parametrize('binary,runtime_class,protocol,model', RUNTIMES)
@pytest.mark.asyncio
async def test_native_invented_tool_cannot_dispatch(tmp_path, binary, runtime_class, protocol, model):
    executable = shutil.which(binary)
    if not executable:
        pytest.skip('Pinned native binary required')
    requests = []
    async def provider(request):
        requests.append(await request.json())
        return web.Response(body=provider_reply(protocol, len(requests) == 1, 'not_a_registered_tool'),
                            content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider)
    relay.grant = replace(grant(protocol), model=model, max_output_tokens=8192, native_claude=binary == 'claude')
    called = []
    async def rpc(tool, args):
        if tool.startswith('model.'):
            return await relay(AUTH, tool, args)
        called.append(tool)
        raise AssertionError('Invented tool reached backend')
    runtime = runtime_class(tmp_path / 'runtime', rpc, AsyncMock(), executable=executable)
    try:
        await runtime.start(model, 'Answer briefly.', [TOOL])
        try:
            await runtime.turn('hello', timeout=30)
        except RuntimeFailed:
            pass  # Native clients may abort rather than return a tool error.
        assert not called
        assert len(relay.session.requests) <= 2
    finally:
        await runtime.close()
        await close(relay, server, session)


@pytest.mark.parametrize('binary,runtime_class,protocol,model', RUNTIMES)
@pytest.mark.asyncio
async def test_native_adapter_never_silently_drops_followup_history(tmp_path, binary, runtime_class, protocol, model):
    runtime = runtime_class(tmp_path / 'runtime', AsyncMock(), AsyncMock())
    runtime.used = True
    with pytest.raises(RuntimeFailed, match='Invalid runtime turn'):
        await runtime.turn('Follow up on the previous request')
    assert runtime.proc is None


@pytest.mark.parametrize('binary,runtime_class,protocol,model', RUNTIMES + [
    ('codex', CodexRuntime, 'responses', 'gpt-5.4')])
@pytest.mark.asyncio
async def test_fresh_native_worker_receives_backend_selected_history(tmp_path, binary, runtime_class, protocol, model):
    from tests.test_codex_worker import reply_events
    executable = shutil.which(binary)
    if not executable:
        pytest.skip('Pinned native binary required')
    requests = []
    async def provider(request):
        data = await request.json()
        requests.append(data)
        wire = sse(reply_events('Verified synthetic result')) if protocol == 'responses' else provider_reply(protocol)
        return web.Response(body=wire, content_type='text/event-stream')
    relay, server, session, _ = await fixture(provider)
    relay.grant = replace(grant(protocol), model=model, max_output_tokens=8192,
                          native_claude=binary == 'claude', native_codex=binary == 'codex')
    async def rpc(tool, args): return await relay(AUTH, tool, args)
    first = runtime_class(tmp_path / 'first-worker', rpc, AsyncMock(), executable=executable)
    second = runtime_class(tmp_path / 'second-worker', rpc, AsyncMock(), executable=executable)
    try:
        await first.start(model, 'Answer briefly.', [])
        await first.turn('The project is Cedar', timeout=30)
        await first.close()
        # In production this transcript must be reloaded by the authenticated
        # backend with current conversation access. Not read from worker files.
        relay.grant = replace(relay.grant, history=(('user', 'The project is Cedar'),
                              ('assistant', 'Verified synthetic result')))
        await second.start(model, 'Answer briefly.', [])
        await second.turn('What is the project name?', timeout=30)
        messages = requests[1]['input' if protocol == 'responses' else 'messages']
        assert sum('Cedar' in json.dumps(m) for m in messages) == 1
        assert 'Verified synthetic result' in json.dumps(messages)
        assert 'What is the project name?' in json.dumps(messages)
        assert len(requests) == 2
    finally:
        await first.close()
        await second.close()
        await close(relay, server, session)
