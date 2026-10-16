"""Workspace sweep: frees rebuildable space, never loses unsaved work."""
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheduler import workspace_sweep as ws

NOW = time.time()
DAY = 86400


def _age(path: Path, days: float):
    """Backdate path and everything under it."""
    stamp = NOW - days * DAY
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        for name in filenames:
            os.utime(os.path.join(dirpath, name), (stamp, stamp), follow_symlinks=False)
        os.utime(dirpath, (stamp, stamp))


def _write(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _git(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


def _clone(tmp: Path, dest: Path) -> Path:
    """A clone of a local bare remote, fully pushed and clean."""
    remote = tmp / f"remote-{dest.name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    _write(dest / "README.md", "hi")
    _git(dest, "add", ".")
    _git(dest, "commit", "-qm", "init")
    _git(dest, "push", "-q", "origin", "HEAD:main")
    _write(dest / ".gitignore", "node_modules/\n.next/\n")
    _git(dest, "add", ".")
    _git(dest, "commit", "-qm", "ignore")
    _git(dest, "push", "-q", "origin", "HEAD:main")
    return dest


@pytest.fixture
def root(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "conversations").mkdir(parents=True)
    return workspace


def _sweep(root, busy=(), gone=()):
    return ws.sweep(root, set(busy), set(gone), now=NOW)


def test_idle_folder_loses_only_dependency_folders(root):
    chat = root / "conversations" / "c1"
    _write(chat / "report.md")
    _write(chat / "app" / "node_modules" / "left-pad" / "index.js")
    _write(chat / "app" / ".next" / "cache" / "x")
    _write(chat / "app" / "src" / "main.ts")
    _age(chat, 4)
    result = _sweep(root)
    assert result.deps_purged == 2
    assert not (chat / "app" / "node_modules").exists()
    assert not (chat / "app" / ".next").exists()
    assert (chat / "report.md").exists() and (chat / "app" / "src" / "main.ts").exists()


def test_recent_activity_anywhere_keeps_dependencies(root):
    chat = root / "conversations" / "c1"
    _write(chat / "app" / "node_modules" / "a.js")
    _write(chat / "app" / "src" / "deep" / "edited.ts")
    _age(chat, 10)
    os.utime(chat / "app" / "src" / "deep" / "edited.ts", (NOW - 3600, NOW - 3600))
    assert _sweep(root).deps_purged == 0
    assert (chat / "app" / "node_modules" / "a.js").exists()


def test_changes_inside_node_modules_do_not_count_as_activity(root):
    chat = root / "conversations" / "c1"
    _write(chat / "node_modules" / "a.js")
    _age(chat, 5)
    os.utime(chat / "node_modules" / "a.js", (NOW, NOW))
    assert _sweep(root).deps_purged == 1


def test_running_chat_is_never_touched(root):
    chat = root / "conversations" / "busy"
    _write(chat / "node_modules" / "a.js")
    _age(chat, 60)
    result = _sweep(root, busy={"busy"}, gone={"busy"})
    assert result.deps_purged == 0 and result.removed == []
    assert (chat / "node_modules" / "a.js").exists()


def test_chat_idle_past_limit_is_removed(root):
    chat = root / "conversations" / "old"
    _write(chat / "notes.md")
    _age(chat, ws.CHAT_IDLE_DAYS + 1)
    assert _sweep(root).removed == [str(chat)]
    assert not chat.exists()


def test_deleted_chat_goes_once_idle_for_a_few_days(root):
    fresh, settled = root / "conversations" / "fresh", root / "conversations" / "settled"
    _write(fresh / "a.md")
    _write(settled / "a.md")
    _age(fresh, 1)
    _age(settled, 4)
    result = _sweep(root, gone={"fresh", "settled"})
    assert result.removed == [str(settled)]
    assert fresh.exists()


def test_chat_with_unsaved_git_work_keeps_its_files(root, tmp_path):
    chat = root / "conversations" / "old"
    repo = _clone(tmp_path, chat / "repo")
    _write(repo / "wip.py", "unsaved")
    _write(repo / "node_modules" / "a.js")
    _age(chat, ws.CHAT_IDLE_DAYS + 1)
    result = _sweep(root)
    assert result.kept_unsaved == [str(chat)]
    assert (repo / "wip.py").exists()
    assert not (repo / "node_modules").exists()


def test_pushed_clean_clone_idle_past_limit_is_removed(root, tmp_path):
    repo = _clone(tmp_path, root / "plsvc-old")
    _write(repo / "node_modules" / "a.js")
    _age(repo, ws.CLONE_IDLE_DAYS + 1)
    assert _sweep(root).removed == [str(repo)]
    assert not repo.exists()


def test_clone_idle_under_limit_only_loses_dependencies(root, tmp_path):
    repo = _clone(tmp_path, root / "plsvc-recent")
    _write(repo / "node_modules" / "a.js")
    _age(repo, ws.DEPS_IDLE_DAYS + 1)
    result = _sweep(root)
    assert result.removed == [] and result.deps_purged == 1
    assert (repo / "README.md").exists()


@pytest.mark.parametrize("make_unsaved", [
    lambda repo: _write(repo / "wip.py"),
    lambda repo: (_write(repo / "c.py"), _git(repo, "add", "."), _git(repo, "commit", "-qm", "local")),
    lambda repo: (_git(repo, "checkout", "-q", "--detach"), _write(repo / "d.py"),
                  _git(repo, "add", "."), _git(repo, "commit", "-qm", "detached")),
    lambda repo: _git(repo, "remote", "remove", "origin"),
], ids=["uncommitted", "unpushed", "detached-commit", "no-remote"])
def test_clone_with_unsaved_work_is_kept(root, tmp_path, make_unsaved):
    repo = _clone(tmp_path, root / "plsvc-wip")
    make_unsaved(repo)
    _age(repo, ws.CLONE_IDLE_DAYS + 1)
    result = _sweep(root)
    assert result.removed == [] and result.kept_unsaved == [str(repo)]
    assert repo.exists()


def test_top_level_folder_without_git_is_never_removed_whole(root):
    folder = root / "pr-shots"
    _write(folder / "01.png")
    _write(folder / "node_modules" / "a.js")
    _age(folder, 90)
    result = _sweep(root)
    assert result.removed == [] and result.deps_purged == 1
    assert (folder / "01.png").exists()


def test_reserved_and_hidden_top_level_folders_are_skipped(root):
    for name in ["go", "gopath", "toolchain", "ms-playwright", ".cache"]:
        _write(root / name / "node_modules" / "a.js")
    _age(root, 90)
    assert _sweep(root).deps_purged == 0
    assert (root / "toolchain" / "node_modules" / "a.js").exists()


def test_symlinks_are_not_followed(root, tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "node_modules" / "keep.js")
    chat = root / "conversations" / "c1"
    chat.mkdir()
    (chat / "link").symlink_to(outside)
    _age(chat, 4)
    _sweep(root)
    assert (outside / "node_modules" / "keep.js").exists()


def test_missing_root_is_a_no_op(tmp_path):
    assert ws.sweep(tmp_path / "nope", set(), set()).removed == []


class _Cursor:
    def __init__(self, docs):
        self._docs = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._docs)
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
async def test_chat_sets_from_conversations(root):
    for cid in ["run", "queue", "del", "live", "unknown"]:
        (root / "conversations" / cid).mkdir()
    db = MagicMock()
    db.conversations.find.return_value = _Cursor([
        {"conversation_id": "run", "status": "running"},
        {"conversation_id": "queue", "status": "queued"},
        {"conversation_id": "del", "status": "completed", "deleted": True},
        {"conversation_id": "live", "status": "completed"},
    ])
    busy, gone = await ws._chat_sets(db, root)
    assert busy == {"run", "queue"}
    # A folder with no conversation may belong to another scope, so it only ages out.
    assert gone == {"del"}


@pytest.mark.asyncio
async def test_alert_posts_to_the_alerts_channel():
    client = MagicMock()
    client.chat_postMessage = AsyncMock()
    with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_ALERTS_CHANNEL_ID": "C1"}), \
            patch("slack_sdk.web.async_client.AsyncWebClient", return_value=client):
        await ws._post_alert("disk 91% full")
    client.chat_postMessage.assert_awaited_once_with(channel="C1", text="disk 91% full")


def test_disabled_by_env(caplog):
    with patch.dict(os.environ, {"WORKSPACE_SWEEP_ENABLED": "false"}), \
            patch("asyncio.create_task") as create_task:
        ws.start_workspace_sweep()
    create_task.assert_not_called()
