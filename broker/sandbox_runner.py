"""Trusted launcher. Only backend-owned OCI bundles are accepted.

Invoked with Python isolated mode; no imports from worker-controlled directories.
Runtime failure never executes the command outside gVisor.
"""
import json
import os
from urllib.parse import urlsplit
from pathlib import Path
import signal
import stat
import subprocess
import sys


def main():
    bundle = Path(sys.argv[1])
    info = bundle.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError('Invalid sandbox control directory')
    control = json.loads((bundle / 'supervisor.json').read_text())
    spec = json.loads((bundle / 'config.json').read_text())
    spec['process']['args'].extend(sys.argv[2:])
    for name in control['passthrough']:
        if name in os.environ:
            value = os.environ[name]
            if '\0' in value or '\n' in value or '\r' in value:
                raise RuntimeError('Invalid SDK environment')
            if name == 'CLAUDE_CONFIG_DIR':
                Path(value).resolve().relative_to(Path(control['workspace']).resolve())
            if name == 'ANTHROPIC_AUTH_TOKEN' and not value.startswith('loma_subproxy_'):
                raise RuntimeError('Only subscription proxy references may enter a worker')
            if name == 'ANTHROPIC_BASE_URL':
                parsed = urlsplit(value)
                if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.path.startswith('/sub/loma_subproxy_') or parsed.query or parsed.fragment or parsed.username or parsed.password:
                    raise RuntimeError('Only the local subscription proxy is supported')
            spec['process']['env'].append(f'{name}={value}')
    (bundle / 'config.json').write_text(json.dumps(spec))
    base = [control['runtime'], f'--root={bundle.parent / "runtime"}']
    command = [*base, '--platform=systrap', '--network=none', '--host-uds=all',
               '--directfs=false', '--gvisor-marker-file=true', '--file-access=shared', 'run', f'--bundle={bundle}', bundle.name]
    child = subprocess.Popen(command, env={'PATH': '/usr/local/bin:/usr/bin:/bin'})
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        def forward(sig, frame):
            if child.poll() is None:
                child.send_signal(sig)
        signal.signal(sig, forward)
    try:
        return child.wait()
    finally:
        result = subprocess.run([*base, 'delete', '--force', bundle.name], capture_output=True, timeout=30,
                                env={'PATH': '/usr/local/bin:/usr/bin:/bin'})
        if result.returncode != 0:
            raise RuntimeError('Sandbox teardown failed; workspace retained')
        (bundle / 'stopped').touch(mode=0o600)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('Isolated worker could not start or stop safely.', file=sys.stderr)
        sys.exit(125)
