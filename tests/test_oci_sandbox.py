"""OCI configuration and transport tests. No claim of deployed isolation."""
import asyncio
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
from aiohttp import web, ClientSession, UnixConnector

from broker import sandbox, worker


@pytest.fixture
def oci(tmp_path, monkeypatch):
    monkeypatch.setenv('LOMA_WORKER_SANDBOX', 'runsc')
    image = tmp_path / 'rootfs'
    (image / 'usr/local/bin').mkdir(parents=True)
    (image / 'usr/local/bin/python3').write_text('synthetic executable')
    (image / '.loma-worker-image').write_text('1')
    monkeypatch.setenv('LOMA_WORKER_ROOTFS', str(image))
    monkeypatch.setenv('LOMA_SANDBOX_STATE', str(tmp_path / 'state'))
    monkeypatch.setenv('LOMA_SANDBOX_SOCKETS', str(tmp_path / 'sockets'))
    workspace = tmp_path / 'workspace'
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(worker, 'workspace_identity', lambda _: SimpleNamespace(uid=200000, gid=200000, per_run=True))
    monkeypatch.setattr(worker, 'verify_workspace_ownership', lambda _: None)
    monkeypatch.setattr(sandbox, '_runtime', lambda: '/usr/local/bin/runsc')
    sockets = []
    for name in ('broker', 'gateway'):
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(sandbox.socket_root() / f'{name}.sock'))
        sockets.append(sock)
    yield workspace, image
    for sock in sockets:
        sock.close()


def test_shipping_default_never_falls_back(monkeypatch):
    monkeypatch.delenv('LOMA_WORKER_SANDBOX')
    assert sandbox.enabled()
    monkeypatch.setenv('LOMA_WORKER_SANDBOX', 'development')
    monkeypatch.delenv('LOMA_ENV')
    with pytest.raises(worker.WorkerIsolationError):
        sandbox.enabled()


def test_oci_has_separate_root_no_network_or_backend_mounts(oci):
    workspace, image = oci
    argv = sandbox.prepare(['python3', '-c', 'print(1)'], workspace, worker.build_worker_env(workspace))
    bundle = Path(argv[-1])
    config = json.loads((bundle / 'config.json').read_text())
    assert config['root'] == {'path': str(image), 'readonly': True}
    assert config['process']['noNewPrivileges']
    assert config['process']['user']['uid'] == 200000
    assert all(not value for value in config['process']['capabilities'].values())
    assert {'type': 'network'} in config['linux']['namespaces']
    sources = [m['source'] for m in config['mounts'] if m['type'] == 'bind']
    assert sources == [str(workspace), str(sandbox.socket_root() / 'broker.sock'), str(sandbox.socket_root() / 'gateway.sock')]
    assert bundle.stat().st_mode & 0o777 == 0o700
    assert not bundle.is_relative_to(workspace)


def test_launch_script_is_not_worker_writable(oci):
    workspace, _ = oci
    path = sandbox.launcher(workspace, 'python3', worker.build_worker_env(workspace), ())
    assert not path.is_relative_to(workspace)
    assert path.stat().st_mode & 0o777 == 0o700
    assert '-I' in path.read_text()


def test_missing_runtime_is_fatal(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, 'which', lambda _: None)
    with pytest.raises(worker.WorkerIsolationError, match='fallback is disabled'):
        sandbox._runtime()


@pytest.mark.parametrize('kind', ['root', 'workspace', 'symlink', 'shared'])
def test_invalid_image_rejected(oci, monkeypatch, kind):
    workspace, image = oci
    if kind == 'root':
        monkeypatch.setenv('LOMA_WORKER_ROOTFS', '/')
    elif kind == 'workspace':
        monkeypatch.setenv('LOMA_WORKER_ROOTFS', str(workspace))
    elif kind == 'symlink':
        alias = image.parent / 'alias'
        alias.symlink_to(image)
        monkeypatch.setenv('LOMA_WORKER_ROOTFS', str(alias))
    else:
        image.chmod(0o777)
    with pytest.raises(worker.WorkerIsolationError):
        sandbox.prepare(['python3'], workspace, worker.build_worker_env(workspace))


def test_shared_worker_uid_not_accepted(oci, monkeypatch):
    workspace, _ = oci
    monkeypatch.setattr(worker, 'workspace_identity', lambda _: SimpleNamespace(uid=990, gid=990, per_run=False))
    with pytest.raises(worker.WorkerIsolationError):
        sandbox.prepare(['python3'], workspace, worker.build_worker_env(workspace))


@pytest.mark.asyncio
async def test_unix_transport_serves_only_registered_app(tmp_path, monkeypatch):
    monkeypatch.setenv('LOMA_SANDBOX_SOCKETS', str(tmp_path / 'sockets'))
    app = web.Application()
    app.router.add_get('/test', lambda request: web.json_response({'ok': True}))
    broker = web.AppRunner(app)
    gateway = web.AppRunner(web.Application())
    await broker.setup()
    await gateway.setup()
    try:
        await sandbox.serve_transports(broker, gateway)
        async with ClientSession(connector=UnixConnector(path=str(sandbox.socket_root() / 'broker.sock'))) as client:
            async with client.get('http://local/test') as response:
                assert await response.json() == {'ok': True}
        with pytest.raises(worker.WorkerIsolationError, match='already active'):
            await sandbox.serve_transports(broker, gateway)
    finally:
        await broker.cleanup()
        await gateway.cleanup()


@pytest.mark.asyncio
async def test_bidirectional_transport_bridge(tmp_path):
    from broker.sandbox_entry import bridge
    path = tmp_path / 'echo.sock'
    async def echo(reader, writer):
        data = await reader.read(1024)
        writer.write(data)
        await writer.drain()
        writer.close()
    unix = await asyncio.start_unix_server(echo, path=str(path))
    async def forward(reader, writer):
        await bridge(reader, writer, unix=str(path))
    tcp = await asyncio.start_server(forward, '127.0.0.1', 0)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', tcp.sockets[0].getsockname()[1])
        writer.write(b'synthetic')
        await writer.drain()
        assert await asyncio.wait_for(reader.read(1024), timeout=2) == b'synthetic'
        writer.close()
        await writer.wait_closed()
    finally:
        tcp.close()
        unix.close()
        await tcp.wait_closed()
        await unix.wait_closed()


@pytest.mark.asyncio
async def test_ingress_rejects_symlink_to_host_socket(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = tmp_path / 'backend.sock'
    called = []
    async def backend(reader, writer):
        called.append(True)
        writer.close()
    server = await asyncio.start_unix_server(backend, path=str(target))
    (workspace / '.loma-ingress.sock').symlink_to(target)
    proxy = await sandbox.ingress(0, workspace)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', proxy.sockets[0].getsockname()[1])
        assert await asyncio.wait_for(reader.read(100), timeout=2) == b''
        assert not called
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        proxy.close()
        await server.wait_closed()
        await proxy.wait_closed()


@pytest.mark.asyncio
async def test_ingress_connects_pinned_socket_inode(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    async def echo(reader, writer):
        writer.write(await reader.read(100))
        await writer.drain()
        writer.close()
    server = await asyncio.start_unix_server(echo, path=str(workspace / '.loma-ingress.sock'))
    proxy = await sandbox.ingress(0, workspace)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', proxy.sockets[0].getsockname()[1])
        writer.write(b'synthetic')
        await writer.drain()
        assert await asyncio.wait_for(reader.read(100), timeout=2) == b'synthetic'
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        proxy.close()
        await server.wait_closed()
        await proxy.wait_closed()


def test_worker_renderer_is_local_not_privileged_broker(oci):
    workspace, _ = oci
    # This fixture uses a synthetic owner; don't chown test files.
    import unittest.mock
    with unittest.mock.patch.object(worker, 'workspace_identity', return_value=SimpleNamespace(uid=None)):
        worker.populate_tool_shims(workspace, ['pptx_creator'])
    source = (workspace / 'tools/pptx_creator.py').read_text()
    assert 'runpy.run_path' in source
    assert 'urlopen' not in source


def test_runner_uses_fixed_isolation_flags_and_keeps_args_in_container(oci, monkeypatch):
    from broker import sandbox_runner
    workspace, _ = oci
    command = sandbox.prepare(['python3'], workspace, worker.build_worker_env(workspace))
    bundle = Path(command[-1])
    seen = []
    class Child:
        def wait(self):
            return 0
        def poll(self):
            return 0
    monkeypatch.setattr(sandbox_runner.sys, 'argv', ['runner', str(bundle), '--network=host'])
    monkeypatch.setattr(sandbox_runner.subprocess, 'Popen', lambda argv, **kwargs: seen.append((argv, kwargs)) or Child())
    monkeypatch.setattr(sandbox_runner.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(sandbox_runner.signal, 'signal', lambda *a: None)
    assert sandbox_runner.main() == 0
    argv, kwargs = seen[0]
    assert '--network=none' in argv
    assert '--network=host' not in argv
    assert '--directfs=false' in argv
    assert kwargs['env'] == {'PATH': '/usr/local/bin:/usr/bin:/bin'}
    config = json.loads((bundle / 'config.json').read_text())
    assert config['process']['args'][-1] == '--network=host'


def test_blank_pptx_composition_has_no_backend_asset_dependency(tmp_path, monkeypatch):
    from tools import pptx_creator
    from pptx import Presentation
    monkeypatch.setattr(pptx_creator, 'SLIDE_INDEX_PATH', tmp_path / 'absent.json')
    monkeypatch.setattr(pptx_creator, 'OUTPUT_DIR', tmp_path / 'artifacts')
    spec = tmp_path / 'deck.json'
    spec.write_text(json.dumps({'output': 'synthetic.pptx', 'slides': [
        {'action': 'create', 'layout': 'section-divider', 'content': {'title': 'Synthetic test'}}]}))
    pptx_creator.cmd_compose(SimpleNamespace(spec=str(spec)))
    output = tmp_path / 'artifacts/synthetic.pptx'
    assert output.exists()
    assert len(Presentation(output).slides) == 1


def test_runner_does_not_report_success_when_teardown_fails(oci, monkeypatch):
    from broker import sandbox_runner
    workspace, _ = oci
    command = sandbox.prepare(['python3'], workspace, worker.build_worker_env(workspace))
    bundle = Path(command[-1])
    class Child:
        def wait(self):
            return 0
        def poll(self):
            return 0
    monkeypatch.setattr(sandbox_runner.sys, 'argv', ['runner', str(bundle)])
    monkeypatch.setattr(sandbox_runner.subprocess, 'Popen', lambda *a, **kw: Child())
    monkeypatch.setattr(sandbox_runner.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1))
    monkeypatch.setattr(sandbox_runner.signal, 'signal', lambda *a: None)
    with pytest.raises(RuntimeError, match='teardown failed'):
        sandbox_runner.main()
    assert not (bundle / 'stopped').exists()
    assert bundle.exists()
