"""Real local subprocess tests of request-owned input and output channels."""
import base64
import io
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from broker.operations import ToolInvoke
from broker.service import Denied
from broker.worker import populate_tool_shims


@pytest.fixture
def sandbox_tool(tmp_path, monkeypatch):
    from broker import operations
    tools = tmp_path / 'tools'
    tools.mkdir()
    monkeypatch.setattr(operations, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr('tools._auth_token.create_user_auth_token', lambda email: 'synthetic')
    db = SimpleNamespace(integrations=SimpleNamespace(find_one=AsyncMock(return_value={'status': 'active'})))
    return tools, db


@pytest.mark.asyncio
async def test_binary_artifact_roundtrip_uses_only_request_owned_paths(sandbox_tool):
    tools, db = sandbox_tool
    (tools / 'zoho_books.py').write_text('''import sys,json
from pathlib import Path
out = sys.argv[sys.argv.index('--output') + 1]
Path(out).write_bytes(bytes(range(256)))
print(json.dumps({'output':out}))
''')
    target = '/workspace/report.pdf'
    result = await ToolInvoke().execute(db, 'owner@example.test', 'zoho_books', {
        'argv': ['download-invoice-pdf', 'synthetic', '--region', 'in', '--output', target]})
    assert result['exit_code'] == 0
    assert base64.b64decode(result['artifacts'][target]['data']) == bytes(range(256))
    assert target in result['stdout']
    assert 'loma-broker-files-' not in result['stdout']
    assert not Path(target).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
async def test_output_channel_does_not_read_special_files(sandbox_tool, kind):
    tools, db = sandbox_tool
    (tools / 'zoho_books.py').write_text('''import sys,os
out = sys.argv[sys.argv.index('--output') + 1]
''' + ("os.symlink('/example/nonexistent', out)" if kind == 'symlink' else 'os.mkfifo(out)'))
    with pytest.raises((Denied, OSError)):
        await ToolInvoke().execute(db, 'owner@example.test', 'zoho_books', {
            'argv': ['download-invoice-pdf', 'synthetic', '--region', 'in', '--output', '/workspace/output']})


@pytest.mark.asyncio
async def test_stdin_roundtrip_and_identity_stripping(sandbox_tool):
    tools, db = sandbox_tool
    (tools / 'pylon.py').write_text('import sys\nprint(sys.stdin.read())')
    result = await ToolInvoke().execute(db, 'owner@example.test', 'pylon', {
        'argv': ['note', 'synthetic', '--user-email', 'other@example.test', '--auth-token', 'forged'],
        'stdin': '<p>example note</p>'})
    assert result['exit_code'] == 0
    assert result['stdout'].strip() == '<p>example note</p>'


@pytest.mark.asyncio
async def test_stdin_is_denied_on_commands_without_explicit_support(monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr('asyncio.create_subprocess_exec', spawn)
    with pytest.raises(Denied):
        await ToolInvoke().execute(None, 'owner@example.test', 'notify', {
            'argv': ['list'], 'stdin': 'unexpected'})
    spawn.assert_not_called()


def test_worker_saves_artifact_without_modifying_outside_paths(tmp_path, monkeypatch):
    populate_tool_shims(tmp_path, ['zoho_books'])
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('LOMA_RUN_CAPABILITY', 'synthetic')
    monkeypatch.setattr(sys, 'argv', ['zoho_books.py', 'download-invoice-pdf', 'synthetic'])
    data = bytes(range(256))
    target = tmp_path / 'output.pdf'
    payload = {'exit_code': 0, 'stdout': '', 'artifacts': {str(target): {
        'encoding': 'base64', 'data': base64.b64encode(data).decode()}}}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: Response())
    shim = runpy.run_path(str(tmp_path / 'tools' / 'zoho_books.py'))
    assert shim['main']() == 0
    assert target.read_bytes() == data
    outside = tmp_path.parent / (tmp_path.name + '-outside.pdf')
    payload['artifacts'] = {str(outside): payload['artifacts'][str(target)]}
    assert shim['main']() == 1
    assert not outside.exists()


def test_worker_only_reads_stdin_for_declared_commands(tmp_path, monkeypatch):
    populate_tool_shims(tmp_path, ['pylon'])
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('LOMA_RUN_CAPABILITY', 'synthetic')
    monkeypatch.setattr(sys, 'argv', ['pylon.py', 'note', 'synthetic'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('example note'))
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"stdout":"","exit_code":0}'
    def urlopen(request, **kw):
        assert json.loads(request.data)['params']['stdin'] == 'example note'
        return Response()
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    shim = runpy.run_path(str(tmp_path / 'tools' / 'pylon.py'))
    assert shim['main']() == 0
