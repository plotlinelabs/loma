"""Backend-only age-based cleanup of the explicitly configured artifact volume.

Committed bytes expire after 30 days, matching download metadata. Incomplete
uploads get seven days, well above the supervisor's four-hour run ceiling.
No symlinks, directories, unknown names or arbitrary caller paths are followed.
"""
import os
import re
import stat
import time

from isolation.downloads import store_root


def cleanup(*, now=None):
    now = time.time() if now is None else now
    removed = 0
    root = os.open(store_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for namespace in os.listdir(root):
            if not re.fullmatch(r'[a-f0-9]{64}', namespace):
                continue
            try:
                directory = os.open(namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            except OSError:
                continue
            try:
                for filename in os.listdir(directory):
                    if not re.fullmatch(r'[a-f0-9]{32}\.(?:blob|json|part|json\.part)', filename):
                        continue
                    try:
                        info = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                        age = 7 * 86400 if filename.endswith('.part') else 30 * 86400
                        if stat.S_ISREG(info.st_mode) and info.st_mtime <= now - age:
                            os.unlink(filename, dir_fd=directory)
                            removed += 1
                    except FileNotFoundError:
                        pass
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        os.close(root)
    return removed


if __name__ == '__main__':
    print({'removed': cleanup()})
