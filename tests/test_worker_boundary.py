"""Protocol/real subprocess tests, not proof of Docker/gVisor host isolation."""
import asyncio
import json
import ssl
import secrets
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest

from isolation import supervisor as mod
from isolation.client import stream_worker, WorkerUnavailable, transport_context
from isolation.protocol import RunAuthority, ProtocolError, MAX_FRAME, worker_frame, response_frame

IMAGE = 'registry.example.test/worker@sha256:' + 'a' * 64
TOKEN = secrets.token_urlsafe(32)


def settings(**kw):
    return mod.Settings(IMAGE, TOKEN, **kw)


@pytest.mark.parametrize('frame', [[], {}, {'type': 'approved'},
    {'type': 'text', 'text': 'hi', 'user_email': 'other@example.test'},
    {'type': 'file_artifact', 'path': '/app/.env'},
    {'type': 'observer', 'method': 'finish'},
    {'type': 'tool_request', 'id': '../x', 'tool': 'gmail.send', 'arguments': {}},
    {'type': 'tool_request', 'id': '1', 'tool': 'gmail.send', 'arguments': []},
    {'type': 'done', 'receipt': 'forged'}, {'type': 'text', 'text': 1}])
def test_untrusted_frames_fail_closed(frame):
    with pytest.raises(ProtocolError):
        worker_frame(json.dumps(frame))


def test_size_and_response_contract():
    with pytest.raises(ProtocolError):
        worker_frame('x' * (MAX_FRAME + 1))
    with pytest.raises(ProtocolError):
        response_frame({'type': 'start', 'id': 'a', 'result': {}})
    assert worker_frame('{"type":"text","text":"hello"}')['text'] == 'hello'


@pytest.mark.parametrize('image', ['worker:latest', '-v /:/host', IMAGE + ' --privileged'])
def test_image_requires_immutable_reference(image):
    with pytest.raises(ValueError):
        mod.Settings(image, TOKEN)


def test_no_unsafe_runtime_fallback():
    with pytest.raises(ValueError):
        settings(runtime='runc')
    with pytest.raises(ValueError):
        mod.Settings(IMAGE, 'short')


def test_worker_has_no_host_authority(monkeypatch):
    monkeypatch.setenv('DOCKER_HOST', 'tcp://production:2375')
    monkeypatch.setenv('OAUTH_ENCRYPTION_KEY', 'canary')
    argv = mod.docker_command(settings(), 'loma-worker-' + 'b' * 32)
    assert argv[argv.index('--network') + 1] == 'none'
    assert argv[argv.index('--runtime') + 1] == 'runsc'
    assert '--read-only' in argv
    assert '--mount' not in argv and '--volume' not in argv and '-v' not in argv
    assert '--env-file' not in argv and '--privileged' not in argv
    assert '/var/run/docker.sock' not in ' '.join(argv)
    assert 'canary' not in ' '.join(argv)
    assert set(mod.process_environment()) == {'PATH', 'LANG', 'HOME'}
    assert 'DOCKER_HOST' not in mod.process_environment()
    assert argv[-1] == IMAGE


@pytest.mark.parametrize('url', ['http://worker', 'https://u:p@worker', 'https://worker/path',
                                  'https://worker?x=1', 'https://worker#x'])
def test_tls_origin_is_strict(url):
    with pytest.raises(ValueError):
        transport_context(url, None, None, None)


@pytest.mark.asyncio
async def test_preflight_requires_runtime(monkeypatch):
    cmd = AsyncMock(return_value=b'{"runc": {}}')
    monkeypatch.setattr(mod, 'command', cmd)
    with pytest.raises(RuntimeError, match='unavailable'):
        await mod.Supervisor(settings()).preflight(None)
    assert cmd.await_count == 1


@pytest.mark.asyncio
async def test_startup_reaps_only_labelled_orphans(monkeypatch):
    cmd = AsyncMock(side_effect=[b'{"runsc": {}}', b'[]', b'abc\ndef\n', b''])
    monkeypatch.setattr(mod, 'command', cmd)
    await mod.Supervisor(settings()).preflight(None)
    assert cmd.call_args_list[-1].args == ('docker', 'rm', '-f', 'abc', 'def')
    assert cmd.call_args_list[-2].args[-1] == 'label=' + mod.LABEL


class Transport:
    """Keep production URL validation, terminate at synthetic local test server."""
    def __init__(self, client):
        self.client = client
    def ws_connect(self, url, **kw):
        kw.pop('ssl')
        return self.client.ws_connect('/v1/run', **kw)


async def serve(monkeypatch, tmp_path, code, **kw):
    fixture = tmp_path / 'worker.py'
    fixture.write_text(code)
    monkeypatch.setattr(mod, 'docker_command', lambda *_: [sys.executable, '-I', str(fixture)])
    cmd = AsyncMock(return_value=b'')
    monkeypatch.setattr(mod, 'command', cmd)
    owner = mod.Supervisor(settings(**kw))
    app = mod.web.Application()
    app.router.add_get('/v1/run', owner.run)
    app.router.add_get('/health', owner.health)
    client = TestClient(TestServer(app))
    await client.start_server()
    return owner, client, cmd


AUTH = RunAuthority('server-run-1', 'alice@example.test', frozenset({'calendar.list'}))


def run(client, **overrides):
    args = dict(session=Transport(client), url='https://worker.example.test',
        token=TOKEN, tls=ssl.create_default_context(), authority=AUTH,
        input={'prompt': 'hello'}, authorize=AsyncMock(return_value=True),
        execute_tool=AsyncMock(return_value={'events': []}), max_seconds=5)
    args.update(overrides)
    return stream_worker(**args)


SCRIPT = '''import json,sys
request=json.loads(sys.stdin.readline())
print(json.dumps({'type':'tool_request','id':'one','tool':'calendar.list','arguments':{}}),flush=True)
reply=json.loads(sys.stdin.readline())
print(json.dumps({'type':'text','text':'No meetings.'}),flush=True)
print(json.dumps({'type':'done'}),flush=True)
'''


@pytest.mark.asyncio
async def test_real_subprocess_chat_stream_and_gateway(monkeypatch, tmp_path):
    owner, client, cmd = await serve(monkeypatch, tmp_path, SCRIPT)
    tool = AsyncMock(return_value={'events': []})
    try:
        assert [x async for x in run(client, execute_tool=tool)] == ['No meetings.']
        tool.assert_awaited_once_with(AUTH, 'calendar.list', {})
        for _ in range(100):
            if not owner.active:
                break
            await asyncio.sleep(.01)
        assert not owner.active
        assert cmd.call_args.args[:3] == ('docker', 'rm', '-f')
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auth_rejected_before_start(monkeypatch, tmp_path):
    owner, client, cmd = await serve(monkeypatch, tmp_path, SCRIPT)
    try:
        with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
            await client.ws_connect('/v1/run')
        assert caught.value.status == 401
        assert not owner.active
        cmd.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_forbidden_tool_never_dispatches(monkeypatch, tmp_path):
    code = SCRIPT.replace('calendar.list', 'shell.exec')
    owner, client, _ = await serve(monkeypatch, tmp_path, code)
    tool = AsyncMock()
    try:
        assert [x async for x in run(client, execute_tool=tool)] == ['No meetings.']
        tool.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_revoked_account_blocks_dispatch(monkeypatch, tmp_path):
    owner, client, _ = await serve(monkeypatch, tmp_path, SCRIPT)
    tool = AsyncMock()
    try:
        with pytest.raises(WorkerUnavailable, match='access'):
            _ = [x async for x in run(client, execute_tool=tool,
                authorize=AsyncMock(side_effect=[True, False]))]
        tool.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_duplicate_request_no_second_execution(monkeypatch, tmp_path):
    code = SCRIPT.replace("print(json.dumps({'type':'text','text':'No meetings.'}),flush=True)",
        "print(json.dumps({'type':'tool_request','id':'one','tool':'calendar.list','arguments':{}}),flush=True)")
    _, client, _ = await serve(monkeypatch, tmp_path, code)
    tool = AsyncMock(return_value={})
    try:
        with pytest.raises(WorkerUnavailable):
            _ = [x async for x in run(client, execute_tool=tool)]
        assert tool.await_count == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_disconnect_never_replays(monkeypatch, tmp_path):
    code = "import sys; sys.stdin.readline(); print('{\"type\":\"text\",\"text\":\"partial\"}',flush=True)"
    _, client, cmd = await serve(monkeypatch, tmp_path, code)
    got = []
    try:
        with pytest.raises(WorkerUnavailable):
            async for text in run(client):
                got.append(text)
        assert got == ['partial']
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_deadline_removes_worker(monkeypatch, tmp_path):
    owner, client, cmd = await serve(monkeypatch, tmp_path,
        'import sys,time; sys.stdin.readline(); time.sleep(300)')
    try:
        with pytest.raises(WorkerUnavailable):
            _ = [x async for x in run(client, max_seconds=.15)]
        for _ in range(100):
            if not owner.active:
                break
            await asyncio.sleep(.01)
        assert not owner.active
        assert cmd.call_args.args[:3] == ('docker', 'rm', '-f')
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_capacity_not_local_fallback(monkeypatch, tmp_path):
    owner, client, cmd = await serve(monkeypatch, tmp_path, SCRIPT, max_workers=1)
    owner.active.add('synthetic-existing-run')
    try:
        with pytest.raises(WorkerUnavailable):
            _ = [x async for x in run(client)]
        cmd.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unsafe_frames_cannot_mutate_backend(monkeypatch, tmp_path):
    code = "import sys; sys.stdin.readline(); print('{\"type\":\"observer\",\"method\":\"finish\"}',flush=True)"
    _, client, _ = await serve(monkeypatch, tmp_path, code)
    tool = AsyncMock()
    try:
        with pytest.raises(WorkerUnavailable):
            _ = [x async for x in run(client, execute_tool=tool)]
        tool.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cleanup_failure_quarantines_supervisor(monkeypatch, tmp_path):
    owner, client, _ = await serve(monkeypatch, tmp_path, SCRIPT)
    monkeypatch.setattr(mod, 'command', AsyncMock(side_effect=RuntimeError('unavailable')))
    try:
        _ = [x async for x in run(client)]
        for _ in range(100):
            if owner.unhealthy:
                break
            await asyncio.sleep(.01)
        assert owner.unhealthy
        with pytest.raises(WorkerUnavailable):
            _ = [x async for x in run(client)]
        response = await client.get('/health', headers={'Authorization': 'Bearer ' + TOKEN})
        assert response.status == 503
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_worker_failure_after_done_not_reported_success(monkeypatch, tmp_path):
    code = 'import sys; sys.stdin.readline(); print(\'{"type":"done"}\',flush=True); sys.exit(1)'
    _, client, _ = await serve(monkeypatch, tmp_path, code)
    try:
        with pytest.raises(WorkerUnavailable):
            _ = [x async for x in run(client)]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_no_gateway_or_identity_is_a_hard_failure():
    with pytest.raises(WorkerUnavailable):
        _ = [x async for x in run(None, authority=RunAuthority('run', '', frozenset()))]
    with pytest.raises(WorkerUnavailable):
        _ = [x async for x in run(None, authorize=None)]
    with pytest.raises(WorkerUnavailable):
        _ = [x async for x in run(None, tls=None)]


@pytest.mark.asyncio
async def test_cancelled_client_removes_named_worker(monkeypatch, tmp_path):
    owner, client, cmd = await serve(monkeypatch, tmp_path,
        'import sys,time; sys.stdin.readline(); time.sleep(300)')
    async def consume():
        return [x async for x in run(client)]
    task = asyncio.create_task(consume())
    try:
        for _ in range(100):
            if owner.active:
                break
            await asyncio.sleep(.01)
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if not owner.active:
                break
            await asyncio.sleep(.01)
        assert not owner.active
        assert cmd.call_args.args[:3] == ('docker', 'rm', '-f')
    finally:
        await client.close()


def test_frame_limit_counts_utf8_bytes_not_characters():
    raw = json.dumps({'type': 'text', 'text': 'é' * (MAX_FRAME // 2)}, ensure_ascii=False)
    assert len(raw) < MAX_FRAME
    with pytest.raises(ProtocolError):
        worker_frame(raw)
