import json
import os
import subprocess
import time

import pytest

from isolation import workspace_gc
from isolation.workspace_gc import DAY, WorkspaceGC, run_once


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for key, value in {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
                       'GIT_COMMITTER_EMAIL': 't@t', 'GIT_CONFIG_GLOBAL': os.devnull}.items():
        monkeypatch.setenv(key, value)


def git(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


def make_clone(tmp_path, path, *, push=True):
    """A clone of a bare remote with one pushed commit, an ignored node_modules and a .venv."""
    remote = tmp_path / f'remote-{path.name}.git'
    git(tmp_path, 'init', '-q', '--bare', str(remote))
    path.mkdir(parents=True)
    git(path, 'init', '-q', '-b', 'main')
    git(path, 'remote', 'add', 'origin', str(remote))
    (path / '.gitignore').write_text('node_modules/\n.venv/\n')
    (path / 'app.py').write_text('print(1)\n')
    (path / 'build').mkdir()
    (path / 'build' / 'tracked.txt').write_text('committed build dir\n')
    git(path, 'add', '.')
    git(path, 'commit', '-q', '-m', 'init')
    if push:
        git(path, 'push', '-q', 'origin', 'main')
    (path / 'node_modules' / 'pkg').mkdir(parents=True)
    (path / 'node_modules' / 'pkg' / 'index.js').write_text('x' * 1000)
    (path / '.venv' / 'lib').mkdir(parents=True)
    (path / '.venv' / 'lib' / 'site.py').write_text('y' * 1000)
    return path


def sweep(root, days, mode='delete', **kw):
    kw.setdefault('live_cwds', ())
    return WorkspaceGC(str(root), mode=mode, now=time.time() + days * DAY, **kw).run()


def kinds(report):
    return sorted((a['kind'], os.path.basename(a['path'])) for a in report.actions)


def test_fresh_clone_is_left_alone(tmp_path):
    root = tmp_path / 'ws'
    make_clone(tmp_path, root / 'loma-run-abc')
    assert sweep(root, 0).actions == []


def test_idle_week_drops_only_untracked_rebuildable_dirs(tmp_path):
    root = tmp_path / 'ws'
    clone = make_clone(tmp_path, root / 'loma-run-abc', push=False)
    report = sweep(root, 8)
    assert kinds(report) == [('rebuildable', '.venv'), ('rebuildable', 'node_modules')]
    assert not (clone / 'node_modules').exists() and not (clone / '.venv').exists()
    assert (clone / 'app.py').exists() and (clone / 'build' / 'tracked.txt').exists()


def test_dry_run_reports_without_deleting(tmp_path):
    root = tmp_path / 'ws'
    clone = make_clone(tmp_path, root / 'loma-run-abc')
    report = sweep(root, 40, mode='dry-run')
    assert kinds(report) == [('clone', 'loma-run-abc')]
    assert report.actions[0]['bytes'] > 0
    assert (clone / 'node_modules').exists()


def test_idle_month_deletes_only_clean_pushed_clones(tmp_path):
    root = tmp_path / 'ws'
    pushed = make_clone(tmp_path, root / 'pushed')
    unpushed = make_clone(tmp_path, root / 'unpushed', push=False)
    dirty = make_clone(tmp_path, root / 'dirty')
    (dirty / 'app.py').write_text('changed\n')
    report = sweep(root, 40)
    assert not pushed.exists()
    assert (unpushed / 'app.py').exists() and (dirty / 'app.py').read_text() == 'changed\n'
    assert not (unpushed / 'node_modules').exists() and not (dirty / '.venv').exists()
    reasons = {os.path.basename(s['path']): s['reason'] for s in report.skipped}
    assert 'no remote-tracking refs' in reasons['unpushed']
    assert 'uncommitted changes' in reasons['dirty']


def test_unpushed_commit_keeps_clone(tmp_path):
    root = tmp_path / 'ws'
    clone = make_clone(tmp_path, root / 'c')
    (clone / 'new.py').write_text('1\n')
    git(clone, 'add', 'new.py')
    git(clone, 'commit', '-q', '-m', 'local only')
    sweep(root, 40)
    assert (clone / 'new.py').exists()


def test_large_ignored_data_keeps_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_gc, 'IGNORED_DATA_LIMIT', 100)
    root = tmp_path / 'ws'
    clone = make_clone(tmp_path, root / 'c')
    (clone / '.gitignore').write_text('node_modules/\n.venv/\nrenders/\n')
    git(clone, 'commit', '-qam', 'ignore renders')
    git(clone, 'push', '-q', 'origin', 'main')
    (clone / 'renders').mkdir()
    (clone / 'renders' / 'final.mp4').write_bytes(b'v' * 10000)
    report = sweep(root, 40)
    assert (clone / 'renders' / 'final.mp4').exists()
    assert 'ignored data' in report.skipped[0]['reason']


def test_active_conversation_and_live_cwd_are_skipped(tmp_path):
    root = tmp_path / 'ws'
    active = make_clone(tmp_path, root / 'conversations' / 'conv-a' / 'repos' / 'loma')
    busy = make_clone(tmp_path, root / 'conversations' / 'conv-b' / 'repos' / 'loma')
    idle = make_clone(tmp_path, root / 'conversations' / 'conv-c' / 'repos' / 'loma')
    sweep(root, 40, active_conversations={'conv-a'}, live_cwds={str(busy / 'node_modules')})
    assert active.exists() and busy.exists() and not idle.exists()


def test_symlinks_are_never_followed(tmp_path):
    root = tmp_path / 'ws'
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep')
    clone = make_clone(tmp_path, root / 'c', push=False)
    (clone / 'apps').mkdir()
    (clone / 'apps' / 'node_modules').symlink_to(outside, target_is_directory=True)
    (root / 'conversations' / 'x').mkdir(parents=True)
    (root / 'conversations' / 'x' / 'link').symlink_to(outside, target_is_directory=True)
    sweep(root, 8)
    assert (outside / 'keep.txt').exists()


def test_media_in_idle_conversation_and_owner_review(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_gc, 'MEDIA_MIN_BYTES', 10)
    root = tmp_path / 'ws'
    conv = root / 'conversations' / 'conv-v'
    (conv / 'render').mkdir(parents=True)
    (conv / 'render' / 'cut.mp4').write_bytes(b'v' * 100)
    (conv / 'notes.md').write_text('keep')
    project = root / 'pmv-film'
    project.mkdir()
    (project / 'source.mov').write_bytes(b'v' * 100)
    monkeypatch.setattr(workspace_gc, '_disk_usage', lambda p: 2 * 1024 ** 3)
    report = sweep(root, 61)
    assert not (conv / 'render' / 'cut.mp4').exists() and (conv / 'notes.md').exists()
    assert (project / 'source.mov').exists()
    assert [os.path.basename(r['path']) for r in report.needs_owner_review] == ['pmv-film']


def test_dot_dirs_are_never_swept(tmp_path):
    root = tmp_path / 'ws'
    make_clone(tmp_path, root / '.toolchains' / 'flutter', push=False)
    assert sweep(root, 400).actions == []


def test_run_once_writes_report_and_respects_off(tmp_path):
    root = tmp_path / 'ws'
    make_clone(tmp_path, root / 'c')
    assert run_once(mode='off', root=str(root)) is None
    summary = run_once(mode='dry-run', root=str(root))
    assert summary['mode'] == 'dry-run'
    data = json.loads((root / '.gc' / 'last-run.json').read_text())
    assert data['summary']['actions'] == summary['actions']


def test_own_deletes_do_not_reset_the_idle_clock(tmp_path):
    root = tmp_path / 'ws'
    clone = make_clone(tmp_path, root / 'c', push=False)
    (clone / 'apps' / 'node_modules').mkdir(parents=True)
    old = time.time() - DAY
    for path in (clone, clone / 'apps'):
        os.utime(path, (old, old))
    sweep(root, 10)
    assert not (clone / 'apps' / 'node_modules').exists() and not (clone / '.venv').exists()
    assert abs(os.lstat(clone).st_mtime - old) < 1 and abs(os.lstat(clone / 'apps').st_mtime - old) < 1
