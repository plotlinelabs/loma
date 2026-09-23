"""Compose-bundled login broker. Local Unix socket, no Docker socket/host shell.

One root-owned chroot with immutable runtime files; each login has a distinct,
never-reused UID and 0700 temporary home. CLI networking is restricted to the
bundled CONNECT proxy. This is same-kernel isolation, not a separate VM.
"""
import asyncio
import ipaddress
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import sys
from types import SimpleNamespace

from aiohttp import web
from isolation.supervisor import Supervisor, command

ROOT = Path('/sandbox/sessions')
SOCKET = Path('/run/loma-login/login.sock')


def firewall_commands(address):
    ip = ipaddress.IPv4Address(address)
    # Resolve proxy once before denying ALL DNS and direct network egress.
    return [
        ['iptables', '-P', 'OUTPUT', 'DROP'],
        ['iptables', '-F', 'OUTPUT'],
        ['iptables', '-A', 'OUTPUT', '-p', 'tcp', '-d', str(ip), '--dport', '3128', '-j', 'ACCEPT'],
        ['ip6tables', '-P', 'OUTPUT', 'DROP'],
    ]


def owned_processes(uid):
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                status = (entry / 'status').read_text()
                line = next(line for line in status.splitlines() if line.startswith('Uid:'))
                if uid in [int(value) for value in line.split()[1:]]:
                    yield int(entry.name)
            except FileNotFoundError:
                pass


class BundledLogin(Supervisor):
    def __init__(self):
        super().__init__(SimpleNamespace(max_seconds=630, max_workers=4, runtime='bundled-chroot'))
        self.uids = {}
        self.next_uid = 10000
        self.proxy = None

    async def preflight(self, app):
        if os.geteuid() != 0 or not Path('/sandbox/usr/local/bin/claude').exists():
            raise RuntimeError('Bundled login image or privileges are unavailable')
        address = socket.gethostbyname('loma-login-proxy')
        for argv in firewall_commands(address):
            await command(*argv)  # fail closed, including hosts without firewall support
        self.proxy = 'http://' + address + ':3128'
        for name, minor in (('null', 3), ('random', 8), ('urandom', 9)):
            path = Path('/sandbox/dev') / name
            if not path.exists():
                os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(1, minor))
                os.chmod(path, 0o666)
        ROOT.mkdir(parents=True, exist_ok=True)
        # The tmpfs is ephemeral; never recover unknown sessions after restart.
        for child in ROOT.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        SOCKET.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(SOCKET.parent, 0o700)
        SOCKET.unlink(missing_ok=True)

    def authenticate(self, request):
        # No TCP listener. Only backend/broker have the root-only socket volume.
        return

    def container_command(self, name):
        if not re.fullmatch(r'loma-worker-[a-f0-9]{32}', name):
            raise ValueError('Invalid login name')
        if self.next_uid >= 2**31:
            raise RuntimeError('Login service needs restart')
        uid = self.next_uid
        self.next_uid += 1
        self.uids[name] = uid
        home = ROOT / name
        home.mkdir(mode=0o700)
        for path in (home / '.claude', home / 'tmp'):
            path.mkdir(mode=0o700)
            os.chown(path, uid, uid)
        os.chown(home, uid, uid)
        return [sys.executable, '-m', 'isolation.bundled_login', 'worker', name, str(uid), self.proxy]

    async def remove_worker(self, name, proc):
        if proc is not None and proc.returncode is None:
            proc.kill()  # trusted wrapper first; cannot spawn another child after sweep
            await proc.wait()
        uid = self.uids.pop(name, None)
        if uid is not None:
            for _ in range(20):
                pids = list(owned_processes(uid))
                if not pids:
                    break
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await asyncio.sleep(.1)  # Compose init reaps orphaned children
            else:
                self.unhealthy = True
        if (ROOT / name).exists():
            shutil.rmtree(ROOT / name)


def make_app():
    broker = BundledLogin()
    app = web.Application(client_max_size=8192)
    app.router.add_get('/health', broker.health)
    app.router.add_get('/v1/run', broker.run)
    app.on_startup.append(broker.preflight)
    return app


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        from isolation.claude_login_worker import main
        name, uid, proxy = sys.argv[2:]
        main(ROOT / name, command=[sys.executable, '/opt/login/isolation/bundled_login_child.py', name, uid], proxy=proxy)
    else:
        os.umask(0o077)
        web.run_app(make_app(), path=str(SOCKET), access_log=None)
