"""Real runtime smoke checks on a dedicated, secrets-free CI host.

No vendor credentials or deployment are involved. Normal unit runs skip this
file; the Worker Isolation workflow supplies the built image and runsc.
"""
import json
import os
from pathlib import Path

import pytest
import pytest_asyncio
import asyncio
from aiohttp import web

from broker import sandbox, worker

pytestmark = pytest.mark.skipif(os.environ.get('LOMA_TEST_GVISOR') != '1', reason='requires a gVisor-capable host and worker image')


@pytest_asyncio.fixture
async def running_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv('LOMA_WORKER_SANDBOX', 'runsc')
    monkeypatch.setenv('LOMA_WORKER_UID_RANGE', '240000-240999')
    monkeypatch.setenv('LOMA_WORKER_ROOT', str(tmp_path / 'workers'))
    monkeypatch.setenv('LOMA_SANDBOX_STATE', str(tmp_path / 'control'))
    monkeypatch.setenv('LOMA_SANDBOX_SOCKETS', str(tmp_path / 'transports'))
    private = tmp_path / 'synthetic-host-only.txt'
    private.write_text('synthetic-host-only')
    private.chmod(0o644)  # must be hidden by isolation, not just Unix permissions
    app = web.Application()
    async def ok(request):
        return web.json_response({'synthetic': True})
    app.router.add_get('/test', ok)
    broker = web.AppRunner(app)
    gateway = web.AppRunner(web.Application())
    await broker.setup()
    await gateway.setup()
    await sandbox.serve_transports(broker, gateway)
    workspace = worker.create_workspace(prefix='ci')
    sibling = worker.create_workspace(prefix='sibling')
    (sibling / 'synthetic.txt').write_text('synthetic-sibling')
    try:
        yield workspace, sibling, private
    finally:
        worker.cleanup_workspace(workspace)
        worker.cleanup_workspace(sibling)
        await broker.cleanup()
        await gateway.cleanup()
    assert not workspace.exists()
    assert not list(sandbox.state_root().glob('run-*'))


@pytest.mark.asyncio
async def test_actual_independent_sandbox(running_sandbox):
    workspace, sibling, private = running_sandbox
    code = '''import json,os,socket,urllib.request
from pathlib import Path
assert Path('/proc/gvisor/kernel_is_gvisor').exists()
assert not Path(%r).exists()
assert not Path(%r).exists()
assert 'OAUTH_ENCRYPTION_KEY' not in os.environ
assert os.getuid() != 0
for address in [('198.51.100.1',443),('127.0.0.1',3000)]:
    try:
        socket.create_connection(address,timeout=1)
    except OSError:
        pass
    else:
        raise AssertionError('unexpected network access')
print(json.dumps({'broker': json.load(urllib.request.urlopen('http://127.0.0.1:3100/test'))}))
''' % (str(private), str(sibling))
    process = await worker.spawn_worker(['python3', '-c', code], workspace=workspace,
                                        env=worker.build_worker_env(workspace), wall_time_seconds=60)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 90)
    assert process.returncode == 0, stderr.decode()
    assert json.loads(stdout)['broker'] == {'synthetic': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('cli', ['claude', 'codex', 'opencode'])
async def test_installed_cli_boots_in_real_sandbox(running_sandbox, cli):
    workspace, _, _ = running_sandbox
    process = await worker.spawn_worker([cli, '--version'], workspace=workspace,
                                        env=worker.build_worker_env(workspace), wall_time_seconds=60)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 90)
    assert process.returncode == 0, stderr.decode()
    assert stdout.strip() or stderr.strip()


@pytest.mark.asyncio
async def test_cancelled_worker_cannot_leave_detached_processes(running_sandbox):
    workspace, _, _ = running_sandbox
    process = await worker.spawn_worker(['python3', '-u', '-c',
        "import subprocess,time; subprocess.Popen(['sleep','300'], start_new_session=True); print('ready',flush=True); time.sleep(300)"],
        workspace=workspace, env=worker.build_worker_env(workspace), wall_time_seconds=60)
    assert await asyncio.wait_for(process.stdout.readline(), 45) == b'ready\n'
    worker.terminate_worker(process)
    # Teardown must force-delete the sandbox, including children that detached
    # from the runtime's original process group, before releasing its workspace.
    await asyncio.to_thread(worker.cleanup_workspace, workspace)
    await asyncio.wait_for(process.communicate(), 15)
    assert not workspace.exists()
    assert not list(sandbox.state_root().glob('run-*'))
