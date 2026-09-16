"""Only temporary local files and synthetic child processes, not isolation proof."""
import asyncio
import hashlib
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from isolation.workspace_tools import WorkspaceTools, TOOLS
from isolation.artifacts import ArtifactScope
from isolation.protocol import RunAuthority


@pytest.fixture
def workspace(tmp_path):
    value = WorkspaceTools(tmp_path / 'files', AsyncMock(), TOOLS)
    yield value
    value.close()


@pytest.mark.asyncio
async def test_read_write_list_and_validation(workspace):
    assert await workspace('workspace.write', {'path': 'hello.txt', 'content': 'hello'}) == {'path': 'hello.txt', 'size': 5}
    assert (await workspace('workspace.read', {'path': 'hello.txt'}))['content'] == 'hello'
    assert await workspace('workspace.list', {}) == {'files': ['hello.txt'], 'truncated': False}
    await workspace('workspace.write', {'path': 'hello.txt', 'content': 'x'})
    assert (workspace.root / 'hello.txt').read_text() == 'x'
    for args in ({'path': '../secret'}, {'path': '/etc/passwd'}, {'path': 'hello.txt', 'owner': 'other'}):
        with pytest.raises(ValueError):
            await workspace('workspace.read', args)


@pytest.mark.asyncio
async def test_symlinks_fifo_and_ungranted_tools(workspace, tmp_path):
    (workspace.root / 'link').symlink_to(tmp_path)
    (workspace.root / 'linked.txt').symlink_to(tmp_path / 'outside')
    os.mkfifo(workspace.root / 'fifo')
    for path in ['link/outside', 'linked.txt', 'fifo']:
        with pytest.raises((ValueError, OSError)):
            await workspace('workspace.write', {'path': path, 'content': 'bad'})
    assert not (tmp_path / 'outside').exists()
    workspace.allowed = frozenset()
    with pytest.raises(ValueError):
        await workspace('workspace.exec', {'command': 'echo bad'})
    workspace.rpc.assert_not_awaited()


@pytest.mark.asyncio
async def test_exec_only_in_private_workspace_and_no_environment(workspace, monkeypatch):
    monkeypatch.setenv('WORKSPACE_TEST_SECRET', 'synthetic-canary')
    result = await workspace('workspace.exec', {'command': 'printf test > out.txt; printf "%s" "$WORKSPACE_TEST_SECRET"'})
    assert result == {'exit_code': 0, 'output': '', 'truncated': False, 'timed_out': False}
    assert (workspace.root / 'out.txt').read_text() == 'test'
    result = await workspace('workspace.exec', {'command': "python -c 'print(\"x\"*100000)'"})
    assert result['truncated'] and len(result['output']) == 65536


@pytest.mark.asyncio
async def test_cancel_kills_process_group(workspace):
    task = asyncio.create_task(workspace('workspace.exec', {'command': 'echo $$ > pid; sleep 60'}))
    for _ in range(100):
        if (workspace.root / 'pid').exists():
            break
        await asyncio.sleep(.01)
    pid = int((workspace.root / 'pid').read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_output_publication_is_bytes_not_backend_path(workspace, tmp_path):
    scope = ArtifactScope(tmp_path / 'broker', RunAuthority('r', 'owner@example.test', frozenset()), 'c')
    calls = []
    async def rpc(tool, args):
        calls.append((tool, args))
        if tool == 'artifacts.begin': return scope.begin(args['name'], args['size'])
        if tool == 'artifacts.write': return scope.write(args['artifact_id'], args['offset'], args['data'])
        if tool == 'artifacts.commit': return scope.commit(args['artifact_id'], args['sha256'])
        raise AssertionError(tool)
    workspace.workspace.rpc = rpc
    try:
        await workspace('workspace.exec', {'command': 'printf test > report.txt'})
        receipt = await workspace('workspace.publish', {'path': 'report.txt'})
        assert receipt['sha256'] == hashlib.sha256(b'test').hexdigest()
        assert receipt['name'] == 'report.txt'
        assert str(workspace.root) not in str(calls)
        assert all('path' not in args for _, args in calls)
    finally:
        scope.close()


def test_catalog_is_scoped_and_never_exposes_model_or_artifact_plumbing():
    from isolation.catalog import CATALOG, catalog
    from isolation.codex_worker import tool_definitions
    authority = RunAuthority('run', 'owner@example.test', frozenset({'skills.get', 'workspace.exec', 'model.start'}))
    tools = catalog(authority)
    assert {tool['name'] for tool in tools} == {'skills.get', 'workspace.exec'}
    tool_definitions(CATALOG)
    tools[0]['input_schema']['properties'].clear()
    assert catalog(authority)[0]['input_schema']['properties']


def test_retention_only_removes_aged_broker_files(tmp_path, monkeypatch):
    from isolation.retention import cleanup
    root = tmp_path / 'store'
    root.mkdir()
    namespace = root / ('a' * 64)
    namespace.mkdir()
    monkeypatch.setenv('LOMA_WORKER_ARTIFACT_DIR', str(root))
    old = namespace / ('b' * 32 + '.blob')
    pending = namespace / ('c' * 32 + '.part')
    fresh = namespace / ('d' * 32 + '.blob')
    unknown = namespace / 'keep-me'
    outside = tmp_path / 'outside'
    for path in (old, pending, fresh, unknown, outside):
        path.write_text('test')
        os.utime(path, (0, 0))
    os.utime(fresh, (40 * 86400, 40 * 86400))
    (namespace / ('e' * 32 + '.json')).symlink_to(outside)
    (root / ('f' * 64)).symlink_to(tmp_path, target_is_directory=True)
    assert cleanup(now=40 * 86400) == 2
    assert fresh.exists() and unknown.exists() and outside.exists()
    assert not old.exists() and not pending.exists()


@pytest.mark.asyncio
async def test_import_only_reads_backend_granted_artifact(workspace, tmp_path):
    import base64
    scope = ArtifactScope(tmp_path / 'broker', RunAuthority('r', 'owner@example.test', frozenset()), 'c')
    meta = scope.ingest('input.txt', b'granted bytes')
    async def rpc(tool, args):
        if tool == 'artifacts.describe': return scope.metadata(args['artifact_id'])
        if tool == 'artifacts.read': return scope.read(args['artifact_id'], args['offset'])
        raise AssertionError(tool)
    workspace.rpc = workspace.workspace.rpc = rpc
    try:
        result = await workspace('workspace.import', {'artifact_id': meta['artifact_id']})
        assert (workspace.root / result['paths'][0]).read_bytes() == b'granted bytes'
        with pytest.raises(ValueError):
            await workspace('workspace.import', {'artifact_id': 'a' * 32})
    finally:
        scope.close()
