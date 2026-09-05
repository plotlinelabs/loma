"""Trusted OCI supervisor configuration for independent gVisor workers.

No container socket is needed or exposed. The OCI bundle stays backend-owned;
only a private workspace and two credential-broker sockets cross the boundary.
The runtime, image, namespace and cgroup requirements are fail-closed.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys

from broker.worker import WorkerIsolationError


# Development mode exists only for local unit tests / single-user development.
# The shipping image selects runsc explicitly; it must never fall back at runtime.
def enabled():
    mode = os.environ.get('LOMA_WORKER_SANDBOX', 'runsc')
    if mode not in {'runsc', 'development'}:
        raise WorkerIsolationError('Unknown worker sandbox mode')
    if mode == 'development' and os.environ.get('LOMA_ENV') != 'development':
        raise WorkerIsolationError('Unisolated workers require explicit development mode')
    return mode == 'runsc'


def _trusted_directory(path, *, create=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise WorkerIsolationError('Sandbox paths must be absolute')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    for candidate in [path, *path.parents]:
        info = candidate.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}:
            raise WorkerIsolationError('Sandbox control directories must be backend-owned')
        # /tmp may be sticky; the private leaf must never be writable by others.
        if info.st_mode & 0o022 and not (candidate != path and info.st_mode & stat.S_ISVTX):
            raise WorkerIsolationError('Sandbox control directories must not be shared-writable')
    return path


def state_root():
    return _trusted_directory(os.environ.get('LOMA_SANDBOX_STATE', '/var/lib/loma/sandboxes'), create=True)


def socket_root():
    return _trusted_directory(os.environ.get('LOMA_SANDBOX_SOCKETS', '/run/loma-worker-transports'), create=True)


async def serve_transports(broker_runner, gateway_runner):
    from aiohttp import web
    root = socket_root()
    # runsc's trusted gofer traverses this private directory. Workers see only
    # the mounted socket files, not this directory or its host siblings.
    sites = []
    for name, runner in [('broker', broker_runner), ('gateway', gateway_runner)]:
        path = root / f'{name}.sock'
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                raise WorkerIsolationError('Invalid sandbox transport socket')
            # Do not unlink another live server's socket at startup.
            import socket
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise WorkerIsolationError('Sandbox transport is already active')
            finally:
                probe.close()
        site = web.UnixSite(runner, str(path))
        await site.start()
        os.chmod(path, 0o666)  # authentication is the per-run broker capability
        sites.append(site)
    return sites


def _runtime():
    binary = shutil.which('runsc')
    if not binary:
        raise WorkerIsolationError('gVisor runsc is required; unisolated fallback is disabled')
    info = Path(binary).stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise WorkerIsolationError('gVisor must be installed in backend-owned storage')
    return binary


def prepare(argv, workspace, env, *, passthrough=()):
    from broker import worker
    worker.assert_worker_env_clean(env)
    worker.verify_workspace_ownership(workspace)
    workspace = Path(workspace).resolve()
    rootfs = _trusted_directory(os.environ.get('LOMA_WORKER_ROOTFS', '/opt/loma-worker-rootfs'))
    if rootfs == Path('/') or rootfs == workspace or rootfs in workspace.parents or workspace in rootfs.parents:
        raise WorkerIsolationError('A separate secrets-free worker image is required')
    if (rootfs / '.loma-worker-image').read_text().strip() != '1':
        raise WorkerIsolationError('Unrecognized worker image')
    runtime = _runtime()
    identity = worker.workspace_identity(workspace)
    if identity.uid is None or identity.uid == 0 or not identity.per_run:
        raise WorkerIsolationError('Independent workers require per-run non-root identities')
    for name in passthrough:
        if name not in {'CLAUDE_CONFIG_DIR', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING'}:
            raise WorkerIsolationError('Unsupported sandbox SDK passthrough')
    if not argv or not all(isinstance(arg, str) and '\0' not in arg for arg in argv):
        raise WorkerIsolationError('Invalid sandbox command')
    executable = argv[0]
    if not executable.startswith('/'):
        executable = next((f'{directory}/{executable}' for directory in ('/usr/local/bin', '/usr/bin', '/bin')
                           if (rootfs / directory.lstrip('/') / executable).is_file()), '')
    if not executable or not (rootfs / executable.lstrip('/')).is_file():
        raise WorkerIsolationError('Requested executable is missing from worker image')
    mounts = [
        {'destination': '/proc', 'type': 'proc', 'source': 'proc'},
        {'destination': '/dev', 'type': 'tmpfs', 'source': 'tmpfs', 'options': ['nosuid', 'strictatime', 'mode=755', 'size=65536k']},
        {'destination': '/tmp', 'type': 'tmpfs', 'source': 'tmpfs', 'options': ['nosuid', 'nodev', 'mode=1777', 'size=268435456']},
        {'destination': '/run', 'type': 'tmpfs', 'source': 'tmpfs', 'options': ['nosuid', 'nodev', 'mode=755', 'size=1048576']},
        {'destination': str(workspace), 'type': 'bind', 'source': str(workspace), 'options': ['rbind', 'rw', 'nosuid', 'nodev']},
    ]
    for name in ('broker', 'gateway'):
        source = socket_root() / f'{name}.sock'
        info = source.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise WorkerIsolationError('Sandbox broker transport unavailable')
        mounts.append({'destination': f'/run/loma/{name}.sock', 'type': 'bind', 'source': str(source), 'options': ['bind', 'ro', 'nosuid', 'nodev', 'noexec']})
    # Configuration is immutable to worker identities, unlike the workspace.
    root = state_root()
    bundle = root / ('run-' + secrets.token_hex(16))
    bundle.mkdir(mode=0o700)
    spec = {
        'ociVersion': '1.0.2', 'root': {'path': str(rootfs), 'readonly': True},
        'hostname': 'loma-worker', 'mounts': mounts,
        'process': {
            'terminal': False, 'user': {'uid': identity.uid, 'gid': identity.gid},
            'args': ['/usr/local/bin/python3', '/usr/local/lib/loma_worker_entry.py', executable, *argv[1:]],
            'cwd': str(workspace), 'env': [f'{k}={v}' for k, v in env.items()],
            'noNewPrivileges': True,
            'capabilities': {k: [] for k in ('bounding', 'effective', 'inheritable', 'permitted', 'ambient')},
            'rlimits': [{'type': 'RLIMIT_CORE', 'hard': 0, 'soft': 0},
                        {'type': 'RLIMIT_NOFILE', 'hard': 1024, 'soft': 1024},
                        {'type': 'RLIMIT_CPU', 'hard': 600, 'soft': 600},
                        {'type': 'RLIMIT_FSIZE', 'hard': 268435456, 'soft': 268435456}],
        },
        'linux': {
            'cgroupsPath': f'/loma-workers/{bundle.name}',
            'namespaces': [{'type': t} for t in ('pid', 'mount', 'network', 'ipc', 'uts')],
            'resources': {'memory': {'limit': 2147483648}, 'pids': {'limit': 256},
                          'cpu': {'quota': 200000, 'period': 100000}},
        },
    }
    (bundle / 'config.json').write_text(json.dumps(spec))
    (bundle / 'supervisor.json').write_text(json.dumps({'runtime': runtime, 'workspace': str(workspace), 'passthrough': list(passthrough)}))
    runner = Path(__file__).with_name('sandbox_runner.py')
    return [sys.executable, '-I', str(runner), str(bundle)]


def launcher(workspace, real_cli, env, passthrough):
    import shlex
    command = prepare([real_cli], workspace, env, passthrough=passthrough)
    bundle = Path(command[-1])
    script = bundle / 'launch.sh'
    script.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
    script.chmod(0o700)
    return script


def cleanup(workspace):
    root = state_root()
    for bundle in root.glob('run-*'):
        try:
            control = json.loads((bundle / 'supervisor.json').read_text())
            if control['workspace'] != str(Path(workspace).resolve()):
                continue
            if (bundle / 'stopped').exists():
                shutil.rmtree(bundle)
                continue
            result = subprocess.run([control['runtime'], f'--root={root / "runtime"}', 'delete', '--force', bundle.name],
                                    capture_output=True, timeout=30, env={'PATH': '/usr/local/bin:/usr/bin:/bin'})
            if result.returncode:
                # Retain identity/workspace on teardown failures; never reassign
                # its uid while a detached sandbox might still hold open files.
                raise WorkerIsolationError('Sandbox teardown failed; workspace retained')
            shutil.rmtree(bundle)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise WorkerIsolationError('Sandbox teardown failed; workspace retained') from exc


async def ingress(port, workspace):
    """Backend-only local endpoint for the dedicated OpenCode server."""
    from broker.sandbox_entry import bridge
    async def forward(reader, writer):
        fd = None
        try:
            # Pin the socket inode before connecting. A worker-controlled symlink
            # must never redirect this trusted relay to a backend/host socket.
            path = Path(workspace) / '.loma-ingress.sock'
            fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1 or info.st_uid != Path(workspace).stat().st_uid:
                raise WorkerIsolationError('Invalid sandbox ingress socket')
            await bridge(reader, writer, unix=f'/proc/self/fd/{fd}')
        except (OSError, WorkerIsolationError):
            writer.close()
        finally:
            if fd is not None:
                os.close(fd)
    return await asyncio.start_server(forward, '127.0.0.1', port, limit=65536)
