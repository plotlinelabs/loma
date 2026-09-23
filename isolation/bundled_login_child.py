"""Trusted launcher: chroot, remove privileges, then exec only Claude auth login.

Executed in a fresh interpreter, never as preexec_fn in the threaded backend.
No backend code, socket, persisted accounts, /proc or secrets exist in this root.
"""
import ctypes
import os
from pathlib import Path
import re
import resource
import sys


def main(name, uid):
    if not re.fullmatch(r'loma-worker-[a-f0-9]{32}', name) or not 10000 <= uid < 2**31:
        raise ValueError('Invalid login identity')
    proxy = os.environ['HTTPS_PROXY']
    if not re.fullmatch(r'http://[0-9.]+:3128', proxy):
        raise ValueError('Invalid login proxy')
    home = '/sessions/' + name
    env = {'HOME': home, 'CLAUDE_CONFIG_DIR': home + '/.claude',
           'PATH': '/usr/local/bin:/usr/bin:/bin', 'TERM': 'dumb',
           'BROWSER': '/bin/false', 'DISABLE_AUTOUPDATER': '1',
           'HTTPS_PROXY': proxy, 'HTTP_PROXY': proxy, 'TMPDIR': home + '/tmp'}
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    os.chroot('/sandbox')
    os.chdir(home)
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)  # clears effective/permitted capabilities
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:  # NO_NEW_PRIVS
        raise OSError('Cannot remove privileges')
    os.umask(0o077)
    os.execve('/usr/local/bin/claude', ['claude', 'auth', 'login'], env)


if __name__ == '__main__':
    try:
        main(sys.argv[1], int(sys.argv[2]))
    except Exception as error:
        # Static classification only; never reflect paths, environment or tokens.
        print(f'Login sandbox setup failed: {type(error).__name__} (errno={getattr(error, "errno", None)})', file=sys.stderr)
        sys.exit(1)
