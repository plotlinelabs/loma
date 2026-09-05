"""Reclaim legacy worker-readable account directories for the trusted backend."""
import os
import stat

from broker.service import Denied


def protect_account_directory(path):
    """Remove traversal by old worker UIDs before starting any new worker.

    Open without following a final symlink; tighten ownership/mode through the
    descriptor. A non-root backend may only secure directories it already owns.
    This cannot revoke file descriptors held by processes from an older deploy;
    those processes must be stopped during upgrade.
    """
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise Denied()
        uid = os.geteuid()
        if info.st_uid != uid:
            if uid != 0:
                raise Denied()
            os.fchown(fd, uid, os.getegid())
        os.fchmod(fd, 0o700)
    except Exception:
        raise Denied() from None
    finally:
        if fd is not None:
            os.close(fd)
