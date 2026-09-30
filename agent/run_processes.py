"""Kill the background processes an agent run leaves behind.

Agents background long-lived helpers (test proxies, cloudflared tunnels,
``while`` loops). Once the tool shell that started them exits they are
reparented to PID 1, so they are no longer descendants of the runtime and
survive both ``_kill_process_tree`` and a process-group kill (tool shells are
often spawned detached, i.e. in their own group).

Every runtime process we spawn therefore carries a ``LOMA_PROC_TAG`` env var
that all of its descendants inherit, reparented or not. When a run ends we
signal every process carrying that tag: SIGTERM, then SIGKILL after a short
grace. Nothing is meant to outlive a run; state that must survive belongs in
the conversation work dir (see ``agent.client.conversation_work_dir``).

Scope: only local runtime processes (Claude CLI, Codex app-server, OpenCode
server) are tagged. Remote isolation workers clean up their own process groups
(``isolation/*_worker.py``) and the device runner lives on a separate host.
"""

import asyncio
import logging
import os
import signal
import uuid
from time import monotonic

logger = logging.getLogger(__name__)

PROC_TAG_ENV = "LOMA_PROC_TAG"
TERM_GRACE_SECONDS = 3.0
_PROC = "/proc"


def new_proc_tag() -> str:
    return uuid.uuid4().hex


def clock_ticks_now() -> int:
    """Current time in the units of /proc/<pid>/stat starttime (ticks since boot)."""
    try:
        with open(f"{_PROC}/uptime") as f:
            return int(float(f.read().split()[0]) * os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError):
        return 0


def _stat(pid: int) -> tuple[int, str, int] | None:
    """(ppid, state, starttime) for ``pid``; None if it is gone."""
    try:
        with open(f"{_PROC}/{pid}/stat") as f:
            data = f.read()
    except OSError:
        return None
    # comm (field 2) may contain spaces/parens: split after the last ')'.
    fields = data[data.rfind(")") + 2:].split()
    try:
        return int(fields[1]), fields[0], int(fields[19])
    except (IndexError, ValueError):
        return None


def _has_tag(pid: int, tag: str) -> bool:
    try:
        with open(f"{_PROC}/{pid}/environ", "rb") as f:
            env = f.read()
    except OSError:
        return False
    return f"{PROC_TAG_ENV}={tag}".encode() in env.split(b"\0")


def _descends_from(pid: int, root: int, procs: dict[int, tuple[int, str, int]]) -> bool:
    for _ in range(128):
        if pid == root:
            return True
        info = procs.get(pid)
        if info is None or info[0] <= 1:
            return False
        pid = info[0]
    return False


def find_tagged(tag: str, *, started_after: int | None = None,
                detached_from: int | None = None) -> list[int]:
    """Live pids carrying ``tag``.

    ``started_after`` skips processes older than that tick count;
    ``detached_from`` skips processes still attached under that pid (a shared
    runtime server's own children, e.g. its MCP servers).
    """
    if not tag or not os.path.isdir(_PROC):
        return []
    procs: dict[int, tuple[int, str, int]] = {}
    for entry in os.listdir(_PROC):
        if entry.isdigit() and (info := _stat(int(entry))) is not None:
            procs[int(entry)] = info
    own = os.getpid()
    return [
        pid for pid, (_, state, start) in procs.items()
        if pid != own and state != "Z"
        and (started_after is None or start >= started_after)
        and (detached_from is None or not _descends_from(pid, detached_from, procs))
        and _has_tag(pid, tag)
    ]


def _signal_all(pids: list[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _alive(pid: int) -> bool:
    info = _stat(pid)
    return info is not None and info[1] != "Z"


async def kill_tagged(tag: str, *, started_after: int | None = None,
                      detached_from: int | None = None,
                      grace: float = TERM_GRACE_SECONDS) -> int:
    """SIGTERM every process carrying ``tag``, SIGKILL survivors after ``grace``."""
    try:
        pids = await asyncio.to_thread(
            find_tagged, tag, started_after=started_after, detached_from=detached_from,
        )
        if not pids:
            return 0
        _signal_all(pids, signal.SIGTERM)
        deadline = monotonic() + grace
        while monotonic() < deadline and any(_alive(p) for p in pids):
            await asyncio.sleep(0.2)
        survivors = [p for p in pids if _alive(p)]
        _signal_all(survivors, signal.SIGKILL)
        logger.info(
            "Killed %d leftover run process(es) (tag=%s, %d needed SIGKILL)",
            len(pids), tag[:8], len(survivors),
        )
        return len(pids)
    except Exception:
        logger.exception("Run process cleanup failed (tag=%s)", tag[:8])
        return 0
