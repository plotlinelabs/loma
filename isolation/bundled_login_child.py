"""Trusted launcher: chroot, remove privileges, then exec only Claude auth login.

Executed in a fresh interpreter, never as preexec_fn in the threaded backend.
Only the login PID namespace is visible through its private, read-only /proc.
No backend code, socket, persisted accounts or secrets exist in this root.
"""
import ctypes
import os
import re
import resource
import select
import signal
import sys


# Linux namespace/mount flags. Setup is fail-closed and runs before chroot/UID drop.
CLONE_NEWNS, CLONE_NEWPID = 0x00020000, 0x20000000
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REC, MS_PRIVATE = 16384, 1 << 18


def checked(result):
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def drop_privileges(uid):
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)  # clears effective/permitted capabilities
    checked(ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0))  # NO_NEW_PRIVS


def guard_parent(parent_fd=None):
    parent = os.getppid()
    checked(ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0))
    # Within NEWPID, getppid is zero. The inherited pipe closes on parent death
    # and covers death before PDEATHSIG was armed (also after setuid clears it).
    if parent_fd is not None:
        if select.select([parent_fd], [], [], 0)[0]:
            raise RuntimeError('Login parent exited')
    elif parent == 1 or os.getppid() != parent:
        raise RuntimeError('Login parent exited')


def isolate_processes():
    libc = ctypes.CDLL(None, use_errno=True)
    checked(libc.unshare(CLONE_NEWNS | CLONE_NEWPID))
    # Mount changes must never propagate to the broker or another login.
    checked(libc.mount(None, b'/', None, MS_REC | MS_PRIVATE, None))
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    child = os.fork()  # unshare(NEWPID) only applies to subsequently born children
    if child:
        os.close(read_fd)
        # Trusted waiter retains no CLI execution path; parent-death signals
        # tear down the whole namespace even during privileged setup.
        _, status = os.waitpid(child, 0)
        code = os.waitstatus_to_exitcode(status)
        os._exit(code if code >= 0 else 128 - code)
    os.close(write_fd)
    guard_parent(read_fd)
    # PID 1 in this namespace; killing it also kills all its descendants.
    # Never bind-mount the broker/host procfs: it would expose their processes.
    checked(libc.mount(b'proc', b'/sandbox/proc', b'proc',
                       MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC, None))
    return read_fd


def main(name, uid):
    if not re.fullmatch(r'loma-worker-[a-f0-9]{32}', name) or not 10000 <= uid < 2**31:
        raise ValueError('Invalid login identity')
    guard_parent()
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
    parent_fd = isolate_processes()
    os.chroot('/sandbox')
    os.chdir(home)
    drop_privileges(uid)
    guard_parent(parent_fd)
    os.close(parent_fd)
    os.umask(0o077)
    os.execve('/usr/local/bin/claude', ['claude', 'auth', 'login'], env)


if __name__ == '__main__':
    try:
        main(sys.argv[1], int(sys.argv[2]))
    except Exception as error:
        # Static classification only; never reflect paths, environment or tokens.
        print(f'Login sandbox setup failed: {type(error).__name__} (errno={getattr(error, "errno", None)})', file=sys.stderr)
        sys.exit(1)
