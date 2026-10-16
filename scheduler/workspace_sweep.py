"""Keeps the agent workspace volume ($LOMA_WORKSPACE_DIR) from filling the disk.

Agents get a folder per chat (conversations/<id>) and clone repos at the top
level; nothing removed either, so the volume grew ~7 GB a day until deploys
failed with "no space left on device". The sweep only deletes what can be
rebuilt or re-cloned:

- dependency/build folders (node_modules, .next, .nx, .turbo) in any folder
  idle for DEPS_IDLE_DAYS;
- chat folders whose chat was deleted, or idle for CHAT_IDLE_DAYS;
- top-level clones idle for CLONE_IDLE_DAYS.

A folder holding a git repo with uncommitted changes, unpushed commits or no
remote is never removed whole (only its dependency folders go), and a top-level
folder that isn't a git repo is never removed whole. Running and queued chats
are skipped entirely.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from observability.db import get_db

logger = logging.getLogger(__name__)

DEPENDENCY_DIRS = frozenset({"node_modules", ".next", ".nx", ".turbo"})
# Shared toolchains and caches every agent reuses; never swept.
RESERVED_TOP_LEVEL = frozenset({"conversations", "go", "gopath", "toolchain", "ms-playwright"})

DEPS_IDLE_DAYS = float(os.environ.get("WORKSPACE_DEPS_IDLE_DAYS", "3"))
CHAT_IDLE_DAYS = float(os.environ.get("WORKSPACE_CHAT_IDLE_DAYS", "30"))
CLONE_IDLE_DAYS = float(os.environ.get("WORKSPACE_CLONE_IDLE_DAYS", "14"))

CHECK_INTERVAL_SECONDS = 3600
SWEEP_INTERVAL_SECONDS = 24 * 3600
# Above this the sweep runs early (at most once per check) instead of waiting a day.
EARLY_SWEEP_PERCENT = 85
ALERT_PERCENT = 80
ALERT_COOLDOWN_SECONDS = 6 * 3600

_DAY = 86400


@dataclass
class SweepResult:
    deps_purged: int = 0
    removed: list[str] = field(default_factory=list)
    kept_unsaved: list[str] = field(default_factory=list)
    freed_bytes: int = 0


def workspace_root() -> Path:
    return Path(os.environ.get("LOMA_WORKSPACE_DIR") or "/opt/loma-workspace")


def active_since(path: Path, cutoff: float) -> bool:
    """True if anything under path (dependency folders and git objects aside)
    changed after cutoff. Stops at the first hit, so busy folders are cheap."""
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            if current.lstat().st_mtime > cutoff:
                return True
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in DEPENDENCY_DIRS or (entry.name == "objects" and current.name == ".git"):
                        continue
                    stack.append(Path(entry.path))
                elif entry.stat(follow_symlinks=False).st_mtime > cutoff:
                    return True
            except OSError:
                continue
    return False


def _git(repo: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _git_repos(path: Path, max_depth: int = 3) -> list[Path]:
    repos, stack = [], [(path, 0)]
    while stack:
        current, depth = stack.pop()
        if (current / ".git").exists():
            repos.append(current)
            continue
        if depth >= max_depth:
            continue
        try:
            for entry in os.scandir(current):
                if (entry.is_dir(follow_symlinks=False)
                        and entry.name not in DEPENDENCY_DIRS and not entry.name.startswith(".")):
                    stack.append((Path(entry.path), depth + 1))
        except OSError:
            continue
    return repos


def has_unsaved_work(repo: Path) -> bool:
    """Uncommitted changes, commits on no remote, or no remote at all. Anything
    git can't answer counts as unsaved, so a broken repo is never deleted."""
    if not (_git(repo, "remote") or "").strip():
        return True
    status = _git(repo, "status", "--porcelain")
    if status is None or status.strip():
        return True
    unpushed = _git(repo, "rev-list", "--count", "HEAD", "--branches", "--not", "--remotes")
    return unpushed is None or unpushed.strip() != "0"


def purge_dependencies(path: Path) -> int:
    count, stack = 0, [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in DEPENDENCY_DIRS:
                shutil.rmtree(entry.path, ignore_errors=True)
                count += 1
            elif entry.name != ".git":
                stack.append(Path(entry.path))
    return count


def _remove_if_saved(path: Path, result: SweepResult, require_repo: bool) -> None:
    repos = _git_repos(path)
    if require_repo and not repos:
        return
    unsaved = [r for r in repos if has_unsaved_work(r)]
    if unsaved:
        result.kept_unsaved.append(str(path))
        return
    shutil.rmtree(path, ignore_errors=True)
    result.removed.append(str(path))


def sweep(root: Path, busy_chats: set[str], gone_chats: set[str], now: float | None = None) -> SweepResult:
    """One pass over the workspace. busy_chats are running/queued conversation
    ids (never touched); gone_chats are deleted ones."""
    now = now or time.time()
    result = SweepResult()
    if not root.is_dir():
        return result
    try:
        before = shutil.disk_usage(root).used
    except OSError:
        before = None

    chats = root / "conversations"
    units: list[tuple[Path, str]] = []
    if chats.is_dir():
        units += [(Path(e.path), "chat") for e in os.scandir(chats)
                  if e.is_dir(follow_symlinks=False) and e.name not in busy_chats]
    units += [(Path(e.path), "clone") for e in os.scandir(root)
              if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
              and e.name not in RESERVED_TOP_LEVEL]

    for path, kind in units:
        if active_since(path, now - DEPS_IDLE_DAYS * _DAY):
            continue
        if kind == "chat" and (path.name in gone_chats
                               or not active_since(path, now - CHAT_IDLE_DAYS * _DAY)):
            _remove_if_saved(path, result, require_repo=False)
        elif kind == "clone" and not active_since(path, now - CLONE_IDLE_DAYS * _DAY):
            _remove_if_saved(path, result, require_repo=True)
        if path.exists():
            result.deps_purged += purge_dependencies(path)

    if before is not None:
        try:
            result.freed_bytes = max(0, before - shutil.disk_usage(root).used)
        except OSError:
            pass
    return result


def disk_percent(root: Path) -> float | None:
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return None
    return 100 * usage.used / usage.total if usage.total else None


async def _chat_sets(db, root: Path) -> tuple[set[str], set[str]]:
    """(running or queued ids, deleted ids) among the chat folders. A folder with
    no conversation at all may be another scope's, so it only ages out."""
    chats = root / "conversations"
    ids = [e.name for e in os.scandir(chats) if e.is_dir(follow_symlinks=False)] if chats.is_dir() else []
    busy, deleted = set(), set()
    for start in range(0, len(ids), 1000):
        batch = ids[start:start + 1000]
        async for doc in db.conversations.find({"conversation_id": {"$in": batch}},
                                               {"conversation_id": 1, "status": 1, "deleted": 1}):
            cid = doc["conversation_id"]
            if doc.get("status") in ("running", "queued"):
                busy.add(cid)
            elif doc.get("deleted"):
                deleted.add(cid)
    return busy, deleted


async def _post_alert(text: str) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_ALERTS_CHANNEL_ID", "")
    if not (token and channel):
        logger.warning("[WORKSPACE_SWEEP] %s (SLACK_ALERTS_CHANNEL_ID unset, not posted)", text)
        return
    from slack_sdk.web.async_client import AsyncWebClient
    try:
        await AsyncWebClient(token=token).chat_postMessage(channel=channel, text=text)
    except Exception:
        logger.exception("[WORKSPACE_SWEEP] Failed to post disk alert")


async def run_sweep(root: Path | None = None) -> SweepResult | None:
    root = root or workspace_root()
    db = get_db()
    if db is None or not root.is_dir():
        return None
    busy, gone = await _chat_sets(db, root)
    result = await asyncio.to_thread(sweep, root, busy, gone)
    logger.info("[WORKSPACE_SWEEP] freed %.1f GB: %d dependency folders, %d folders removed, "
                "%d kept for unsaved git work", result.freed_bytes / 1e9, result.deps_purged,
                len(result.removed), len(result.kept_unsaved))
    return result


async def _sweep_loop():
    root = workspace_root()
    last_sweep = 0.0
    last_alert = 0.0
    while True:
        try:
            percent = disk_percent(root)
            due = time.time() - last_sweep >= SWEEP_INTERVAL_SECONDS
            if percent is not None and (due or percent >= EARLY_SWEEP_PERCENT):
                last_sweep = time.time()
                await run_sweep(root)
                percent = disk_percent(root)
            if (percent is not None and percent >= ALERT_PERCENT
                    and time.time() - last_alert >= ALERT_COOLDOWN_SECONDS):
                last_alert = time.time()
                await _post_alert(f":warning: Loma disk is {percent:.0f}% full after the workspace "
                                  "sweep. Deploys fail when it reaches 100%.")
        except Exception:
            logger.exception("[WORKSPACE_SWEEP] Unexpected error")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def start_workspace_sweep() -> None:
    """Hourly disk check, a sweep once a day (sooner when the disk passes 85%)."""
    if os.environ.get("WORKSPACE_SWEEP_ENABLED", "true").lower() in ("0", "false", "no"):
        logger.info("Workspace sweep disabled (WORKSPACE_SWEEP_ENABLED=false)")
        return
    asyncio.create_task(_sweep_loop())
