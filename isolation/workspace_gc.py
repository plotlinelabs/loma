"""Daily cleanup of the persistent agent workspace ($LOMA_WORKSPACE_DIR).

Rules, applied to git clones (top level or under conversations/<id>/):
- idle >= 7 days: delete rebuildable, git-untracked dirs (.venv, node_modules, .next, ...).
- idle >= 30 days: delete the whole clone, only if its work is clean and pushed.
Conversations idle >= 60 days lose large media files outside any clone.
Never touched: dot-dirs (shared caches, toolchains), non-git top-level dirs, anything
changed in the last 24h, conversations with a running run, and the cwd of any live process.

LOMA_WORKSPACE_GC_MODE: "dry-run" (default, report only), "delete", or "off".
Every run writes a JSON report to <workspace>/.gc/last-run.json.
"""
import asyncio
import fcntl
import json
import logging
import os
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DAY = 86400
REBUILDABLE = frozenset({
    '.venv', 'venv', 'node_modules', '.next', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.turbo', '.gradle', '.dart_tool', '.nx',
})
# Only removed when git ignores them: some repos commit a build/ or dist/ dir.
REBUILDABLE_IF_IGNORED = frozenset({'dist', 'build', '.build', 'out'})
MEDIA_EXTENSIONS = frozenset({'.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v', '.wav', '.aiff'})
MEDIA_MIN_BYTES = 5 * 1024 * 1024
IGNORED_DATA_LIMIT = 50 * 1024 * 1024
CONVERSATION_SCAN_DEPTH = 3
GIT_TIMEOUT = 60


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Policy:
    deps_idle_days: int = 7
    clone_idle_days: int = 30
    media_idle_days: int = 60
    grace_hours: int = 24

    @classmethod
    def from_env(cls):
        return cls(
            deps_idle_days=_env_int('LOMA_WORKSPACE_GC_DEPS_DAYS', 7),
            clone_idle_days=_env_int('LOMA_WORKSPACE_GC_CLONE_DAYS', 30),
            media_idle_days=_env_int('LOMA_WORKSPACE_GC_MEDIA_DAYS', 60),
            grace_hours=_env_int('LOMA_WORKSPACE_GC_GRACE_HOURS', 24),
        )


@dataclass
class Report:
    mode: str
    started_at: float
    actions: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    needs_owner_review: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def act(self, kind, path, size, reason):
        self.actions.append({'kind': kind, 'path': path, 'bytes': size, 'reason': reason})

    def skip(self, path, reason):
        self.skipped.append({'path': path, 'reason': reason})

    def summary(self):
        by_kind = {}
        for a in self.actions:
            by_kind[a['kind']] = by_kind.get(a['kind'], 0) + a['bytes']
        return {
            'mode': self.mode,
            'started_at': self.started_at,
            'duration_s': round(time.time() - self.started_at, 1),
            'actions': len(self.actions),
            'bytes': sum(by_kind.values()),
            'bytes_by_kind': by_kind,
            'skipped': len(self.skipped),
            'needs_owner_review': len(self.needs_owner_review),
            'errors': len(self.errors),
        }

    def as_dict(self):
        return {'summary': self.summary(), 'actions': self.actions, 'skipped': self.skipped,
                'needs_owner_review': self.needs_owner_review, 'errors': self.errors}


def workspace_root():
    return os.path.realpath(os.environ.get('LOMA_WORKSPACE_DIR') or '/opt/loma-workspace')


def gc_mode():
    mode = (os.environ.get('LOMA_WORKSPACE_GC_MODE') or 'dry-run').strip().lower()
    return mode if mode in ('dry-run', 'delete', 'off') else 'dry-run'


def _lmtime(path):
    try:
        return os.lstat(path).st_mtime
    except OSError:
        return 0.0


def _is_real_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _is_git_repo(path):
    dot_git = os.path.join(path, '.git')
    return os.path.lexists(dot_git) and not os.path.islink(dot_git)


def _disk_usage(path):
    """Allocated bytes under path, no symlinks followed, hardlinks counted once."""
    total, seen = 0, set()
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        return info.st_blocks * 512
    total = info.st_blocks * 512
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in dirnames + filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if st.st_nlink > 1 and not stat.S_ISDIR(st.st_mode):
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            total += st.st_blocks * 512
    return total


def _clone_activity(path):
    """Newest mtime of the dir, its direct children and its git metadata."""
    newest = _lmtime(path)
    try:
        with os.scandir(path) as it:
            for entry in it:
                newest = max(newest, _lmtime(entry.path))
    except OSError:
        pass
    for meta in ('index', 'HEAD', 'FETCH_HEAD', 'ORIG_HEAD', 'logs/HEAD'):
        newest = max(newest, _lmtime(os.path.join(path, '.git', meta)))
    return newest


def _git_env():
    return {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}


def _git(path, *args):
    proc = subprocess.run(
        ['git', '-c', 'safe.directory=*', '-C', path, *args],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, env=_git_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f'git {args[0]} failed: {proc.stderr.strip()[:200]}')
    return proc.stdout


def _is_tracked(repo, rel):
    try:
        return bool(_git(repo, 'ls-files', '--', rel).strip())
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return True  # unknown: treat as tracked, keep it


def _is_ignored(repo, rel):
    try:
        proc = subprocess.run(
            ['git', '-c', 'safe.directory=*', '-C', repo, 'check-ignore', '-q', '--', rel],
            capture_output=True, timeout=GIT_TIMEOUT, env=_git_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _unsafe_reason(repo):
    """Why deleting this clone could lose work, or None when it is clean and pushed."""
    try:
        if _git(repo, 'status', '--porcelain', '--untracked-files=normal').strip():
            return 'uncommitted changes'
        if _git(repo, 'stash', 'list').strip():
            return 'stashed changes'
        if not _git(repo, 'for-each-ref', '--format=%(refname)', 'refs/remotes').strip():
            return 'no remote-tracking refs'
        if _git(repo, 'log', '--branches', '--not', '--remotes', '--oneline', '-1').strip():
            return 'unpushed commits'
        if _git(repo, 'log', 'HEAD', '--not', '--remotes', '--oneline', '-1').strip():
            return 'unpushed commits on HEAD'
        ignored = _git(repo, 'status', '--porcelain', '--ignored=matching', '-z')
    except subprocess.TimeoutExpired:
        return 'git timed out'
    except (RuntimeError, OSError) as e:
        return f'git check failed: {e}'
    ignored_bytes = 0
    for record in ignored.split('\0'):
        if not record.startswith('!! '):
            continue
        rel = record[3:].rstrip('/')
        parts = set(rel.split('/'))
        if parts & REBUILDABLE or parts & REBUILDABLE_IF_IGNORED:
            continue
        ignored_bytes += _disk_usage(os.path.join(repo, rel))
        if ignored_bytes > IGNORED_DATA_LIMIT:
            return 'large git-ignored data (renders, dumps, local DBs)'
    return None


def _rebuildable_dirs(repo):
    """Top-most rebuildable dirs inside repo; never descends into .git, nested repos or a match."""
    found = []
    for dirpath, dirnames, _ in os.walk(repo, followlinks=False):
        keep = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if name == '.git' or os.path.islink(full):
                continue
            if name in REBUILDABLE or name in REBUILDABLE_IF_IGNORED:
                found.append(full)
            elif not _is_git_repo(full):
                keep.append(name)
        dirnames[:] = keep
    result = []
    for full in found:
        rel = os.path.relpath(full, repo)
        if _is_tracked(repo, rel):
            continue
        if os.path.basename(full) in REBUILDABLE_IF_IGNORED and not _is_ignored(repo, rel):
            continue
        result.append(full)
    return result


def _live_cwds():
    cwds = set()
    try:
        pids = [p for p in os.listdir('/proc') if p.isdigit()]
    except OSError:
        return cwds
    for pid in pids:
        try:
            cwds.add(os.path.realpath(os.readlink(f'/proc/{pid}/cwd')))
        except OSError:
            continue
    return cwds


class WorkspaceGC:
    def __init__(self, root, *, mode='dry-run', policy=None, active_conversations=(),
                 now=None, live_cwds=None):
        self.root = os.path.realpath(root)
        self.mode = mode
        self.policy = policy or Policy()
        self.active = set(active_conversations)
        self.now = time.time() if now is None else now
        self.live_cwds = _live_cwds() if live_cwds is None else {os.path.realpath(c) for c in live_cwds}
        self.report = Report(mode=mode, started_at=time.time())

    def _idle_days(self, mtime):
        return (self.now - mtime) / DAY

    def _in_grace(self, mtime):
        return self.now - mtime < self.policy.grace_hours * 3600

    def _in_use(self, path):
        real = os.path.realpath(path)
        return any(c == real or c.startswith(real + os.sep) for c in self.live_cwds)

    def _remove(self, kind, path, reason):
        real = os.path.realpath(path)
        if os.path.islink(path) or not real.startswith(self.root + os.sep):
            self.report.skip(path, 'outside workspace or symlink')
            return
        size = _disk_usage(path)
        if self.mode == 'delete':
            parent = os.path.dirname(path)
            try:
                before = os.lstat(parent)
                if _is_real_dir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
                # Keep the parent's mtime so our own delete does not reset the idle clock.
                os.utime(parent, ns=(before.st_atime_ns, before.st_mtime_ns), follow_symlinks=False)
            except OSError as e:
                self.report.errors.append({'path': path, 'error': str(e)})
                return
        self.report.act(kind, path, size, reason)

    def _conversation_ids(self):
        conv_root = os.path.join(self.root, 'conversations')
        if not _is_real_dir(conv_root):
            return []
        return [c for c in sorted(os.listdir(conv_root))
                if c not in self.active and _is_real_dir(os.path.join(conv_root, c))]

    def _clones(self):
        """Yield (clone path, owning conversation id or None)."""
        for name in sorted(os.listdir(self.root)):
            path = os.path.join(self.root, name)
            if name.startswith('.') or name == 'conversations' or not _is_real_dir(path):
                continue
            if _is_git_repo(path):
                yield path, None
        for conv_id in self._conversation_ids():
            conv = os.path.join(self.root, 'conversations', conv_id)
            for dirpath, dirnames, _ in os.walk(conv, followlinks=False):
                depth = 0 if dirpath == conv else os.path.relpath(dirpath, conv).count(os.sep) + 1
                keep = []
                for d in dirnames:
                    full = os.path.join(dirpath, d)
                    if os.path.islink(full) or d in REBUILDABLE or d.startswith('.'):
                        continue
                    if _is_git_repo(full):
                        yield full, conv_id
                    elif depth + 1 < CONVERSATION_SCAN_DEPTH:
                        keep.append(d)
                dirnames[:] = keep

    def _sweep_clone(self, repo, conv_id):
        activity = _clone_activity(repo)
        if conv_id:
            state = os.path.join(self.root, 'conversations', conv_id, 'state.json')
            activity = max(activity, _lmtime(state))
        if self._in_grace(activity):
            return
        if self._in_use(repo):
            self.report.skip(repo, 'a live process is running in it')
            return
        idle = self._idle_days(activity)
        if idle >= self.policy.clone_idle_days:
            reason = _unsafe_reason(repo)
            if reason is None:
                self._remove('clone', repo, f'idle {idle:.0f}d, clean and pushed')
                return
            self.report.skip(repo, f'idle {idle:.0f}d, clone kept: {reason}')
        if idle >= self.policy.deps_idle_days:
            for dep in _rebuildable_dirs(repo):
                self._remove('rebuildable', dep, f'clone idle {idle:.0f}d')

    def _sweep_media(self, conv_id):
        conv = os.path.join(self.root, 'conversations', conv_id)
        newest, media = _lmtime(conv), []
        for dirpath, dirnames, filenames in os.walk(conv, followlinks=False):
            dirnames[:] = [d for d in dirnames
                           if d not in REBUILDABLE and d != '.git'
                           and not os.path.islink(os.path.join(dirpath, d))
                           and not _is_git_repo(os.path.join(dirpath, d))]
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                newest = max(newest, st.st_mtime)
                if (stat.S_ISREG(st.st_mode) and st.st_size >= MEDIA_MIN_BYTES
                        and os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS):
                    media.append(full)
        idle = self._idle_days(newest)
        if not media or idle < self.policy.media_idle_days or self._in_use(conv):
            return
        for path in media:
            self._remove('media', path, f'conversation idle {idle:.0f}d')

    def _flag_unowned_dirs(self):
        """Large idle non-git top-level dirs (video projects, evidence): report only."""
        for name in sorted(os.listdir(self.root)):
            path = os.path.join(self.root, name)
            if name.startswith('.') or name == 'conversations' or not _is_real_dir(path):
                continue
            if _is_git_repo(path):
                continue
            idle = self._idle_days(_clone_activity(path))
            if idle < self.policy.clone_idle_days:
                continue
            size = _disk_usage(path)
            if size >= 1024 ** 3:
                self.report.needs_owner_review.append(
                    {'path': path, 'bytes': size, 'idle_days': round(idle)})

    def run(self):
        for repo, conv_id in list(self._clones()):
            try:
                self._sweep_clone(repo, conv_id)
            except Exception as e:  # one bad clone must not stop the sweep
                self.report.errors.append({'path': repo, 'error': repr(e)})
        for conv_id in self._conversation_ids():
            try:
                self._sweep_media(conv_id)
            except Exception as e:
                self.report.errors.append({'path': conv_id, 'error': repr(e)})
        try:
            self._flag_unowned_dirs()
        except OSError as e:
            self.report.errors.append({'path': self.root, 'error': repr(e)})
        return self.report


def _write_report(root, report):
    out_dir = os.path.join(root, '.gc')
    os.makedirs(out_dir, exist_ok=True)
    data = json.dumps(report.as_dict(), indent=1)
    day = time.strftime('%Y-%m-%d', time.gmtime(report.started_at))
    for name in ('last-run.json', f'run-{day}.json'):
        tmp = os.path.join(out_dir, name + '.tmp')
        with open(tmp, 'w') as f:
            f.write(data)
        os.replace(tmp, os.path.join(out_dir, name))


def run_once(*, mode=None, active_conversations=(), root=None, policy=None):
    """One locked sweep. Returns the report summary, or None when skipped."""
    mode = mode or gc_mode()
    root = os.path.realpath(root or workspace_root())
    if mode == 'off' or not os.path.isdir(root):
        return None
    os.makedirs(os.path.join(root, '.gc'), exist_ok=True)
    with open(os.path.join(root, '.gc', 'lock'), 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info('[WORKSPACE_GC] another sweep is running; skipping')
            return None
        gc = WorkspaceGC(root, mode=mode, policy=policy or Policy.from_env(),
                         active_conversations=active_conversations)
        report = gc.run()
        _write_report(root, report)
    return report.summary()


async def _active_conversation_ids():
    from observability.db import get_db
    db = get_db()
    if db is None:
        return None
    docs = await db.conversations.find(
        {'status': 'running'}, {'conversation_id': 1}).to_list(length=None)
    return {d.get('conversation_id') for d in docs if d.get('conversation_id')}


async def _gc_loop(initial_delay, interval):
    await asyncio.sleep(initial_delay)
    while True:
        try:
            active = await _active_conversation_ids()
            if active is None:
                logger.warning('[WORKSPACE_GC] no database, cannot see running conversations; skipping')
            else:
                summary = await asyncio.to_thread(run_once, active_conversations=active)
                if summary:
                    logger.info('[WORKSPACE_GC] %s', json.dumps(summary))
        except Exception:
            logger.exception('[WORKSPACE_GC] sweep failed')
        await asyncio.sleep(interval)


def start_workspace_gc_loop():
    """Daily sweep in the backend. No-op when the mode is "off"."""
    if gc_mode() == 'off':
        logger.info('[WORKSPACE_GC] disabled (LOMA_WORKSPACE_GC_MODE=off)')
        return None
    initial = _env_int('LOMA_WORKSPACE_GC_INITIAL_DELAY_S', 900)
    interval = _env_int('LOMA_WORKSPACE_GC_INTERVAL_S', DAY)
    logger.info('[WORKSPACE_GC] mode=%s, first sweep in %ds', gc_mode(), initial)
    return asyncio.create_task(_gc_loop(initial, interval))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Sweep the Loma workspace once.')
    parser.add_argument('--mode', choices=('dry-run', 'delete'), default='dry-run')
    parser.add_argument('--root', default=None)
    parser.add_argument('--skip-conversation', action='append', default=[],
                        help='conversation id to leave alone (repeatable)')
    args = parser.parse_args()
    print(json.dumps(run_once(mode=args.mode, root=args.root,
                              active_conversations=args.skip_conversation), indent=1))
