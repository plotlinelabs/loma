import base64
import hashlib
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from isolation.artifacts import ArtifactScope, CHUNK, MAX_FILE, MAX_RUN, MAX_FILES
from isolation.gateway import ToolGateway, GatewayDenied, FILE_SCHEMAS, READ_SCHEMAS, personal_read
from isolation.protocol import RunAuthority
from isolation.workspace import Workspace

ALICE = RunAuthority('run-1', 'alice@example.test', frozenset(FILE_SCHEMAS) | frozenset(READ_SCHEMAS))
BOB = RunAuthority('run-2', 'bob@example.test', ALICE.allowed_tools)


@pytest.fixture
def scope(tmp_path):
    value = ArtifactScope(tmp_path / 'store', ALICE, 'conversation-1')
    yield value
    value.close()


def gateway(scope, **kw):
    options = dict(authorize=AsyncMock(return_value=True), audit=AsyncMock(), artifacts=scope)
    options.update(kw)
    return ToolGateway(ALICE, **options)


@pytest.mark.parametrize('filename', ['../.env', '/app/.env', '', '.', '..', 'x/y', 'x\\y', 'a\n', 'a\x00', 'é' * 110])
def test_no_paths_in_file_protocol(scope, filename):
    with pytest.raises(ValueError):
        scope.begin(filename, 2)


@pytest.mark.parametrize('size', [-1, True, 1.5, '2', MAX_FILE + 1])
def test_size_validation(scope, size):
    with pytest.raises(ValueError):
        scope.begin('out.pdf', size)


def test_committed_bytes_survive_worker_and_require_current_conversation(tmp_path, scope):
    meta = scope.ingest('report.pdf', b'hello')
    artifact_id = meta['artifact_id']
    scope.close()
    contexts = [(ALICE, 'conversation-1', [artifact_id], True),
                (BOB, 'conversation-1', [artifact_id], False),
                (ALICE, 'different-conversation', [artifact_id], False),
                (ALICE, 'conversation-1', [], False)]
    for principal, conversation, ids, allowed in contexts:
        later = ArtifactScope(tmp_path / 'store', principal, conversation, inputs=ids)
        try:
            if allowed:
                assert later.manifest() == [meta]
                assert base64.b64decode(later.read(artifact_id, 0)['data']) == b'hello'
            else:
                with pytest.raises(ValueError, match='Unknown'):
                    later.read(artifact_id, 0)
        finally:
            later.close()


def test_incomplete_uploads_not_readable_or_listed(scope):
    artifact_id = scope.begin('out.txt', 4)['artifact_id']
    with pytest.raises(ValueError):
        scope.metadata(artifact_id)
    with pytest.raises(ValueError):
        scope.commit(artifact_id, hashlib.sha256(b'').hexdigest())
    assert scope.manifest() == []


def test_upload_offsets_checksum_and_duplicate_commit(scope):
    artifact_id = scope.begin('out.txt', 4)['artifact_id']
    with pytest.raises(ValueError):
        scope.write(artifact_id, 1, 'YWJjZA==')
    scope.write(artifact_id, 0, 'YWJjZA==')
    with pytest.raises(ValueError):
        scope.write(artifact_id, 0, 'YWJjZA==')
    with pytest.raises(ValueError):
        scope.commit(artifact_id, '0' * 64)
    scope.commit(artifact_id, hashlib.sha256(b'abcd').hexdigest())
    with pytest.raises(ValueError):
        scope.commit(artifact_id, hashlib.sha256(b'abcd').hexdigest())


def test_run_quotas_reserved_before_writes(scope):
    for _ in range(3):
        scope.begin('large.bin', MAX_FILE)
    with pytest.raises(ValueError, match='quota'):
        scope.begin('large.bin', MAX_FILE)
    assert scope.bytes_reserved <= MAX_RUN


def test_zero_size_files_still_consume_count_quota(scope):
    for _ in range(MAX_FILES):
        scope.ingest('empty.txt', b'')
    with pytest.raises(ValueError, match='quota'):
        scope.begin('empty.txt', 0)


def test_cleanup_removes_incomplete_not_published(tmp_path, scope):
    scope.ingest('complete.txt', b'yes')
    scope.begin('incomplete.txt', 9)
    scope.close()
    assert not list((tmp_path / 'store').glob('*/*.part'))
    assert len(list((tmp_path / 'store').glob('*/*.json'))) == 1


def test_store_rejects_blob_symlink(tmp_path, scope):
    meta = scope.ingest('out.txt', b'good')
    secret = tmp_path / 'canary'
    secret.write_text('do not reveal')
    namespace = next((tmp_path / 'store').iterdir())
    path = namespace / (meta['artifact_id'] + '.blob')
    path.unlink()
    path.symlink_to(secret)
    with pytest.raises(OSError):
        scope.read(meta['artifact_id'], 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,args', [
    ('shell.exec', {'command': 'cat /app/.env'}), ('gmail.send', {'to': 'anywhere'}),
    ('artifacts.read', {'path': '/app/.env', 'offset': 0}),
    ('calendar.list', {'user_email': 'bob@example.test'}),
    ('gmail.search', {'query': 'q', 'auth_token': 'forged'}),
    ('gmail.read', {'message_id': ['id']}),
])
async def test_gateway_denies_unknown_or_forged_authority(scope, tool, args):
    connector = AsyncMock()
    with pytest.raises(GatewayDenied):
        await gateway(scope, connector=connector)(ALICE, tool, args)
    connector.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_rechecks_after_audit_before_dispatch(scope):
    connector = AsyncMock()
    gate = gateway(scope, authorize=AsyncMock(side_effect=[True, False]), connector=connector)
    with pytest.raises(GatewayDenied):
        await gate(ALICE, 'calendar.list', {})
    connector.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_during_read_discards_data(scope):
    connector = AsyncMock(return_value={'secret': 'canary'})
    gate = gateway(scope, authorize=AsyncMock(side_effect=[True, True, False]), connector=connector)
    with pytest.raises(GatewayDenied):
        await gate(ALICE, 'calendar.list', {})
    connector.assert_awaited_once_with('calendar.list', {}, ALICE.user_email)


@pytest.mark.asyncio
async def test_audit_failure_prevents_dispatch(scope):
    connector = AsyncMock()
    with pytest.raises(RuntimeError):
        await gateway(scope, audit=AsyncMock(side_effect=RuntimeError('down')), connector=connector)(ALICE, 'calendar.list', {})
    connector.assert_not_awaited()


@pytest.mark.asyncio
async def test_principal_cannot_be_swapped(scope):
    with pytest.raises(GatewayDenied):
        await gateway(scope)(BOB, 'artifacts.list', {})


@pytest.mark.asyncio
async def test_multichunk_worker_input_output_roundtrip(tmp_path, scope):
    payload = os.urandom(CHUNK * 2 + 15)
    first = scope.ingest('input.bin', payload)
    scope.inputs = frozenset([first['artifact_id']])
    gate = gateway(scope)
    async def rpc(tool, args):
        return await gate(ALICE, tool, args)
    local = tmp_path / 'worker'
    work = Workspace(local, rpc)
    try:
        paths = await work.stage(scope.manifest())
        assert (local / paths[0]).read_bytes() == payload
        (local / 'result.bin').write_bytes(payload[::-1])
        receipt = await work.publish('result.bin')
        assert receipt['sha256'] == hashlib.sha256(payload[::-1]).hexdigest()
        assert 'path' not in receipt and 'owner' not in receipt
    finally:
        work.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/app/.env', '../canary', 'link/canary', 'symlink'])
async def test_worker_exports_only_regular_files_below_workspace(tmp_path, scope, path):
    root = tmp_path / 'worker'
    root.mkdir()
    (tmp_path / 'canary').write_text('secret')
    (root / 'link').symlink_to(tmp_path, target_is_directory=True)
    (root / 'symlink').symlink_to(tmp_path / 'canary')
    rpc = AsyncMock()
    work = Workspace(root, rpc)
    try:
        with pytest.raises((OSError, ValueError)):
            await work.publish(path)
        rpc.assert_not_awaited()
    finally:
        work.close()


@pytest.mark.asyncio
async def test_worker_fifo_does_not_block_or_export(tmp_path):
    rpc = AsyncMock()
    work = Workspace(tmp_path / 'worker', rpc)
    try:
        os.mkfifo(tmp_path / 'worker' / 'pipe')
        with pytest.raises(ValueError):
            await work.publish('pipe')
        rpc.assert_not_awaited()
    finally:
        work.close()


@pytest.mark.asyncio
async def test_connector_uses_backend_owner_and_fixed_commands(monkeypatch):
    personal = AsyncMock(return_value={'events': []})
    monkeypatch.setattr('autonomy.connector.personal', personal)
    await personal_read('calendar.list', {}, ALICE.user_email)
    personal.assert_awaited_once_with('calendar', ['list-events', '--limit', '10'], ALICE.user_email)


@pytest.mark.asyncio
async def test_actual_process_file_transfer_over_supervisor(monkeypatch, tmp_path, scope):
    """Real child + WebSocket + gateway + file IO; not Docker containment."""
    import ssl
    import sys
    from aiohttp.test_utils import TestClient, TestServer
    from isolation import supervisor
    from isolation.client import stream_worker
    from tests.test_worker_boundary import settings, TOKEN, Transport

    source = scope.ingest('input.txt', b'attachment data')
    scope.inputs = frozenset([source['artifact_id']])
    gate = gateway(scope)
    # Only explicit module source is available to the worker. No imports of
    # agent/client, database, model pools, CLI tools or prompt credential minting.
    module_root = tmp_path / 'worker-image'
    package = module_root / 'isolation'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    import shutil
    for module in ('workspace.py', 'artifacts.py'):
        shutil.copyfile(Path(__file__).parents[1] / 'isolation' / module, package / module)
    workspace = tmp_path / 'worker-data'
    script = module_root / 'worker.py'
    script.write_text('''import asyncio,json,sys
from pathlib import Path
from isolation.workspace import Workspace
counter=0
async def rpc(tool,args):
    global counter
    counter+=1
    request_id=str(counter)
    print(json.dumps({'type':'tool_request','id':request_id,'tool':tool,'arguments':args}),flush=True)
    value=json.loads(sys.stdin.readline())
    assert value['id']==request_id
    return value['result']
async def main():
    start=json.loads(sys.stdin.readline())['input']
    root=Path(sys.argv[1])
    work=Workspace(root,rpc)
    try:
        paths=await work.stage(start['files'])
        (root/'result.txt').write_bytes((root/paths[0]).read_bytes().upper())
        receipt=await work.publish('result.txt')
        print(json.dumps({'type':'text','text':receipt['artifact_id']}),flush=True)
    finally:
        work.close()
    print(json.dumps({'type':'done'}),flush=True)
asyncio.run(main())
''')
    monkeypatch.setattr(supervisor, 'docker_command', lambda *_: [sys.executable, str(script), str(workspace)])
    monkeypatch.setattr(supervisor, 'command', AsyncMock(return_value=b''))
    host = supervisor.Supervisor(settings())
    app = supervisor.web.Application()
    app.router.add_get('/v1/run', host.run)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        result = [chunk async for chunk in stream_worker(session=Transport(client),
            url='https://worker.example.test', token=TOKEN, tls=ssl.create_default_context(),
            authority=ALICE, input={'files': scope.manifest()},
            authorize=AsyncMock(return_value=True), execute_tool=gate, max_seconds=5)]
        assert len(result) == 1
        assert base64.b64decode(scope.read(result[0], 0)['data']) == b'ATTACHMENT DATA'
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_chunks_cannot_overwrite(scope):
    import asyncio
    gate = gateway(scope)
    artifact_id = (await gate(ALICE, 'artifacts.begin', {'name': 'x', 'size': 2}))['artifact_id']
    results = await asyncio.gather(*[gate(ALICE, 'artifacts.write', {'artifact_id': artifact_id,
                         'offset': 0, 'data': 'eA=='}) for _ in range(2)], return_exceptions=True)
    assert sum(isinstance(result, GatewayDenied) for result in results) == 1
    assert scope.pending[artifact_id]['offset'] == 1


@pytest.mark.asyncio
async def test_cancelled_read_does_not_retry(scope):
    import asyncio
    called = asyncio.Event()
    async def connector(*args):
        called.set()
        await asyncio.Event().wait()
    gate = gateway(scope, connector=connector)
    task = asyncio.create_task(gate(ALICE, 'calendar.list', {}))
    await called.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.calls == 1


@pytest.mark.asyncio
async def test_revoked_permission_blocks_file_publication(scope):
    meta = scope.begin('x', 0)
    gate = gateway(scope, authorize=AsyncMock(return_value=False))
    with pytest.raises(GatewayDenied):
        await gate(ALICE, 'artifacts.commit', {'artifact_id': meta['artifact_id'],
                                             'sha256': hashlib.sha256(b'').hexdigest()})
    assert not scope.published


@pytest.mark.asyncio
async def test_bad_input_checksum_removes_partial_file(tmp_path):
    payload = b'data'
    rpc = AsyncMock(return_value={'data': base64.b64encode(payload).decode(), 'offset': 0, 'next_offset': 4})
    work = Workspace(tmp_path / 'worker', rpc)
    try:
        with pytest.raises(ValueError, match='checksum'):
            await work.stage([{'artifact_id': 'a' * 32, 'name': 'x', 'size': 4, 'sha256': '0' * 64}])
        assert not (tmp_path / 'worker' / ('a' * 32) / 'x').exists()
    finally:
        work.close()
