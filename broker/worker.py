"""Per-run worker isolation: private workspaces and scrubbed environments.

Every agent runtime subprocess (Claude CLI, OpenCode server, Codex app-server)
and every agent-accessible terminal must be spawned through this module. The
boundary implemented here is a hardened subprocess:

- a fresh private workspace directory per run (0700, optionally chowned to a
  dedicated non-root worker uid/gid when the backend runs as root),
- an explicitly constructed minimal environment. The backend environment is
  never inherited: workers receive only the allowlist built by
  ``build_worker_env`` (no DB connection strings, no encryption/signing keys,
  no provider API keys, no messaging tokens),
- ``setsid`` + resource limits (CPU time, address space, file size, open
  files, process count, no core dumps) via ``preexec_fn`` where the platform
  supports it, and optional bubblewrap wrapping when available,
- a wall-clock watchdog that kills the whole process group.

This is an in-process boundary, not a substitute for kernel-level isolation.
Production deployments must additionally run workers under a hardened
container runtime (gVisor/Kata/Firecracker), with no host mounts, no
docker socket, no cloud metadata access, and network policy that only allows
the broker/gateway and approved model endpoints. See
``docs/execution-security.md``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import resource
import secrets
import shutil
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkerIsolationError(RuntimeError):
    """A worker could not be prepared or spawned within the isolation rules."""


# Environment variable names that must NEVER appear in a worker environment.
# This is a defense-in-depth check on top of allowlist-only construction.
FORBIDDEN_ENV_NAMES = frozenset({
    "OBSERVABILITY_MONGODB_URI",
    "MONGODB_URI",
    "OAUTH_ENCRYPTION_KEY",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_SIGNING_SECRET",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_AUTH_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "SENTRY_ACCESS_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "LINEAR_WEBHOOK_SECRET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
})

# Any extra env key matching this pattern is rejected unless explicitly named
# in _EXTRA_ALLOWED_SENSITIVE (worker-safe, worker-scoped values).
_SENSITIVE_NAME_RE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE|API_KEY|APIKEY|CREDENTIAL|"
    r"ACCESS_KEY|SIGNING|MONGODB|DATABASE_URL|_DSN)", re.IGNORECASE,
)

# Worker-scoped values that intentionally look sensitive but are safe:
# the run capability is *designed* to be handed to the worker.
_EXTRA_ALLOWED_SENSITIVE = frozenset({
    "LOMA_RUN_CAPABILITY",
})

# Baseline vars copied from the backend env when present (never secret).
_PASSTHROUGH_BASE = ("LANG", "LC_ALL", "TZ")


def worker_root() -> Path:
    """Root directory holding all per-run workspaces.

    Defaults under the system temp dir so generated-file detection (which
    only recognizes temp paths) keeps working for worker outputs.
    """
    root = os.environ.get("LOMA_WORKER_ROOT", "").strip()
    if root:
        return Path(root)
    return Path(tempfile.gettempdir()) / "loma-workers"


def _worker_uid_gid() -> tuple[int | None, int | None]:
    """Dedicated shared non-root uid/gid for workers, when configured."""
    try:
        uid = int(os.environ.get("LOMA_WORKER_UID", "") or -1)
        gid = int(os.environ.get("LOMA_WORKER_GID", "") or -1)
    except ValueError:
        raise WorkerIsolationError("LOMA_WORKER_UID/LOMA_WORKER_GID must be integers")
    if uid <= 0:
        return None, None
    if os.geteuid() != 0:
        if os.geteuid() != uid or os.getegid() != (gid if gid > 0 else uid):
            raise WorkerIsolationError("Cannot enforce the configured worker identity")
        return None, None
    return uid, (gid if gid > 0 else uid)


# ── Per-run worker identity ──────────────────────────────────────────────
#
# With only the single shared worker uid, concurrent runs of different users
# are the SAME Unix identity: 0700 directory modes do not stop one run's
# process from reading another run's workspace. When the backend runs as
# root and LOMA_WORKER_UID_RANGE (e.g. "200000-200999") is set, every
# workspace instead gets its own uid from the range; two concurrent runs can
# then never read each other's files. Without a range (or without root) we
# fall back to the shared uid and enforce strict 0700 ownership — a weaker,
# documented boundary (see docs/execution-security.md).


@dataclass(frozen=True)
class WorkerIdentity:
    uid: int | None
    gid: int | None
    per_run: bool = False


_identity_lock = threading.Lock()
_workspace_identities: dict[str, WorkerIdentity] = {}


def _uid_range() -> tuple[int, int] | None:
    spec = os.environ.get("LOMA_WORKER_UID_RANGE", "").strip()
    if not spec:
        return None
    match = re.fullmatch(r"(\d{4,10})-(\d{4,10})", spec)
    if not match:
        raise WorkerIsolationError(
            "LOMA_WORKER_UID_RANGE must look like '200000-200999'")
    low, high = int(match.group(1)), int(match.group(2))
    if low < 1000 or high < low or high - low > 65535:
        raise WorkerIsolationError("LOMA_WORKER_UID_RANGE is not a sane uid range")
    return low, high


def _uids_in_use(low: int, high: int) -> set[int]:
    """Uids from the range currently allocated (registry + on-disk owners).

    Scanning existing workspace owners guards against reusing a uid still
    attached to a live workspace after a backend restart.
    """
    used = {
        identity.uid for identity in _workspace_identities.values()
        if identity.uid is not None
    }
    root = worker_root()
    if root.is_dir():
        for entry in root.iterdir():
            try:
                owner = entry.stat().st_uid
            except OSError:
                continue
            if low <= owner <= high:
                used.add(owner)
    return used


def _allocate_identity() -> WorkerIdentity:
    """Pick the identity for a new workspace (call under _identity_lock)."""
    uid_range = _uid_range()
    if uid_range is not None:
        if os.geteuid() != 0:
            # An operator explicitly asked for per-run identities; running
            # without the privilege to enforce them must not silently
            # degrade to the shared-uid boundary.
            raise WorkerIsolationError(
                "LOMA_WORKER_UID_RANGE requires the backend to run as root")
        low, high = uid_range
        used = _uids_in_use(low, high)
        for candidate in range(low, high + 1):
            if candidate not in used:
                return WorkerIdentity(uid=candidate, gid=candidate, per_run=True)
        raise WorkerIsolationError("No free worker uid: all range uids are in use")
    uid, gid = _worker_uid_gid()
    return WorkerIdentity(uid=uid, gid=gid, per_run=False)


def workspace_identity(workspace: Path | str) -> WorkerIdentity:
    """The Unix identity every process of this workspace must run as."""
    key = str(Path(workspace).resolve())
    with _identity_lock:
        identity = _workspace_identities.get(key)
    if identity is not None:
        return identity
    # Not a registered workspace (legacy/manual path): shared configuration.
    uid, gid = _worker_uid_gid()
    return WorkerIdentity(uid=uid, gid=gid, per_run=False)


def verify_workspace_ownership(workspace: Path | str) -> None:
    """Fail closed if a workspace is not private to its assigned identity.

    This is the load-bearing check for the shared-uid fallback: with one
    worker uid the only boundary between runs is that each workspace stays
    0700 and owned by exactly the expected identity.
    """
    identity = workspace_identity(workspace)
    expected_uid = identity.uid if identity.uid is not None else os.geteuid()
    for path in (Path(workspace), Path(workspace) / "tmp"):
        try:
            info = os.stat(path)
        except OSError as exc:
            raise WorkerIsolationError(f"Workspace path missing: {path}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise WorkerIsolationError(f"Workspace path is not a directory: {path}")
        if info.st_uid != expected_uid:
            raise WorkerIsolationError(
                f"Workspace {path} is owned by uid {info.st_uid}, expected {expected_uid}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise WorkerIsolationError(
                f"Workspace {path} is group/world accessible")


def create_workspace(prefix: str = "run") -> Path:
    """Create a fresh private workspace for one run.

    Layout: ``<workspace>/`` is the worker cwd and HOME, ``<workspace>/tmp``
    is its TMPDIR, ``<workspace>/tools`` holds broker-backed tool shims.
    Each workspace is bound to a worker identity: a per-run uid when
    LOMA_WORKER_UID_RANGE is configured (backend running as root), else the
    shared LOMA_WORKER_UID.
    """
    root = worker_root()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o711)  # traversable, not listable, for non-root workers
    workspace = root / f"{prefix}-{secrets.token_hex(8)}"

    with _identity_lock:
        identity = _allocate_identity()
        workspace.mkdir(mode=0o700)
        (workspace / "tmp").mkdir(mode=0o700)
        (workspace / "tools").mkdir(mode=0o700)
        if identity.uid is not None:
            for p in (workspace, workspace / "tmp", workspace / "tools"):
                os.chown(p, identity.uid, identity.gid)
        _workspace_identities[str(workspace.resolve())] = identity
    return workspace


def grant_worker_access(path: Path | str, recursive: bool = True,
                        workspace: Path | str | None = None) -> None:
    """Make a path owned by a worker identity (when privilege drop is active).

    Needed for state the runtime CLIs themselves must read/write inside the
    sandbox. Pass the run's ``workspace`` so ownership goes to that run's
    identity; without it the shared configured uid is used. No-op when
    privilege drop is not active.
    """
    if workspace is not None:
        identity = workspace_identity(workspace)
        uid, gid = identity.uid, identity.gid
    else:
        uid, gid = _worker_uid_gid()
    if uid is None:
        return
    path = Path(path)
    try:
        os.chown(path, uid, gid)
        if recursive and path.is_dir():
            for child in path.rglob("*"):
                os.chown(child, uid, gid, follow_symlinks=False)
    except OSError:
        logger.warning("Could not grant worker access to %s", path)


def cleanup_workspace(workspace: Path | str) -> None:
    """Best-effort removal of a run workspace after the run ends."""
    workspace = Path(workspace)
    if os.environ.get("LOMA_KEEP_WORKSPACES", "").lower() in {"1", "true", "yes"}:
        return
    try:
        # Only ever delete inside the worker root.
        workspace.resolve().relative_to(worker_root().resolve())
    except ValueError:
        logger.error("Refusing to delete non-workspace path %s", workspace)
        return
    shutil.rmtree(workspace, ignore_errors=True)
    # Release the workspace's per-run uid only after its files are gone.
    with _identity_lock:
        _workspace_identities.pop(str(workspace.resolve()), None)


def _tool_path_dirs() -> list[str]:
    """PATH entries for the worker: standard bins plus runtime CLI locations."""
    dirs: list[str] = []
    for exe in ("python3", "node", "claude", "opencode", "codex", "npx"):
        found = shutil.which(exe)
        if found:
            parent = str(Path(found).parent)
            if parent not in dirs:
                dirs.append(parent)
    for standard in ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if standard not in dirs:
            dirs.append(standard)
    return dirs


def build_worker_env(
    workspace: Path | str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Construct the ONLY environment a worker process may receive.

    Allowlist-only. ``extra`` is for runtime-specific, non-secret settings
    (e.g. CLAUDE_CONFIG_DIR, XDG_CONFIG_HOME, LOMA_BROKER_URL,
    LOMA_RUN_CAPABILITY). Keys with secret-looking names are rejected unless
    explicitly worker-scoped, and known backend secret values are rejected
    outright wherever they appear.
    """
    workspace = Path(workspace)
    env: dict[str, str] = {
        "PATH": ":".join(_tool_path_dirs()),
        "HOME": str(workspace),
        "PWD": str(workspace),
        "TMPDIR": str(workspace / "tmp"),
        "TERM": "xterm-256color",
        "LOMA_ISOLATED_WORKER": "1",
    }
    for name in _PASSTHROUGH_BASE:
        value = os.environ.get(name)
        if value:
            env[name] = value

    for key, value in (extra or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise WorkerIsolationError("Worker env entries must be strings")
        if key in FORBIDDEN_ENV_NAMES:
            raise WorkerIsolationError(f"Refusing to pass backend secret env var to worker: {key}")
        if _SENSITIVE_NAME_RE.search(key) and key not in _EXTRA_ALLOWED_SENSITIVE:
            raise WorkerIsolationError(f"Refusing sensitive-looking env var in worker env: {key}")
        env[key] = value

    assert_worker_env_clean(env)
    return env


def assert_worker_env_clean(env: dict[str, str]) -> None:
    """Fail closed if a worker env contains backend secret names or values."""
    for name in FORBIDDEN_ENV_NAMES:
        if name in env:
            raise WorkerIsolationError(f"Worker env contains forbidden variable {name}")
    # Value-level check: no backend secret value may be smuggled under any name.
    backend_secret_values = {
        value for name in FORBIDDEN_ENV_NAMES
        if (value := os.environ.get(name, "").strip())
    }
    if not backend_secret_values:
        return
    for key, value in env.items():
        if key in _EXTRA_ALLOWED_SENSITIVE:
            continue
        for secret_value in backend_secret_values:
            if secret_value and secret_value in value:
                raise WorkerIsolationError(
                    f"Worker env variable {key} contains a backend secret value"
                )


# ── Resource limits ───────────────────────────────────────────────────────

_RLIMIT_DEFAULTS = {
    "LOMA_WORKER_CPU_SECONDS": 3600,           # per-process CPU time
    "LOMA_WORKER_AS_BYTES": 8 * 1024 ** 3,     # virtual memory
    "LOMA_WORKER_FSIZE_BYTES": 2 * 1024 ** 3,  # max created file size
    "LOMA_WORKER_NOFILE": 4096,
    "LOMA_WORKER_NPROC": 512,
}


def _limit(name: str) -> int:
    try:
        return int(os.environ.get(name, str(_RLIMIT_DEFAULTS[name])))
    except ValueError:
        return _RLIMIT_DEFAULTS[name]


def worker_preexec_fn(setsid: bool = True,
                      workspace: Path | str | None = None):
    """Build the ``preexec_fn`` applied in the child before exec.

    Applies setsid, umask, rlimits, and (when configured and running as root)
    drops privileges to the workspace's worker identity (per-run uid when
    allocated, else the shared configured uid). Pass ``setsid=False`` for
    children that are already session leaders (e.g. after pty.fork).
    """
    if workspace is not None:
        identity = workspace_identity(workspace)
        uid, gid = identity.uid, identity.gid
    else:
        uid, gid = _worker_uid_gid()
    limits = [
        (resource.RLIMIT_CORE, (0, 0)),
        (resource.RLIMIT_CPU, (_limit("LOMA_WORKER_CPU_SECONDS"),) * 2),
        (resource.RLIMIT_FSIZE, (_limit("LOMA_WORKER_FSIZE_BYTES"),) * 2),
        (resource.RLIMIT_NOFILE, (_limit("LOMA_WORKER_NOFILE"),) * 2),
    ]
    as_bytes = _limit("LOMA_WORKER_AS_BYTES")
    if as_bytes > 0:
        limits.append((resource.RLIMIT_AS, (as_bytes, as_bytes)))
    nproc = _limit("LOMA_WORKER_NPROC")

    def _preexec():  # pragma: no cover - runs in the forked child
        if setsid:
            os.setsid()
        os.umask(0o077)
        for which, limit in limits:
            try:
                resource.setrlimit(which, limit)
            except (ValueError, OSError):
                pass
        if uid is not None:
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
            except (ValueError, OSError):
                pass
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)

    return _preexec


# ── Optional bubblewrap wrapping ─────────────────────────────────────────

def bwrap_available() -> bool:
    requested = os.environ.get("LOMA_WORKER_BWRAP", "").lower() in {"1", "true", "yes"}
    if requested and shutil.which("bwrap") is None:
        raise WorkerIsolationError("Requested worker isolation is unavailable: bubblewrap is missing")
    return requested


def build_bwrap_argv(argv: list[str], workspace: Path | str) -> list[str]:
    """Wrap a worker command with bubblewrap: read-only system, private
    workspace bind, unshared pid/ipc/uts namespaces, no other host paths.

    Network stays shared so the worker can reach the local broker/gateway;
    egress restriction is a network-policy concern (see docs).
    """
    workspace = str(workspace)
    wrapper = [
        "bwrap",
        "--die-with-parent",
        "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]
    for ro in ("/usr", "/bin", "/lib", "/lib64", "/sbin", "/etc/ssl",
               "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf",
               "/etc/ca-certificates"):
        if os.path.exists(ro):
            wrapper += ["--ro-bind", ro, ro]
    wrapper += ["--bind", workspace, workspace, "--chdir", workspace, "--"]
    return wrapper + list(argv)


# ── Spawning ─────────────────────────────────────────────────────────────

async def spawn_worker(
    argv: list[str],
    *,
    workspace: Path | str,
    env: dict[str, str],
    stdin=asyncio.subprocess.DEVNULL,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    wall_time_seconds: int | None = None,
) -> asyncio.subprocess.Process:
    """Spawn a sandboxed worker subprocess.

    The environment is validated (fail closed) and passed verbatim — nothing
    is inherited from the backend process. The child gets its own session and
    resource limits; an optional wall-clock watchdog kills the process group.
    """
    assert_worker_env_clean(env)
    verify_workspace_ownership(workspace)
    if bwrap_available():
        argv = build_bwrap_argv(argv, workspace)
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(workspace),
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        preexec_fn=worker_preexec_fn(workspace=workspace),
    )
    if wall_time_seconds and wall_time_seconds > 0:
        asyncio.get_running_loop().create_task(
            _wall_time_watchdog(process, wall_time_seconds)
        )
    return process


async def _wall_time_watchdog(process: asyncio.subprocess.Process, seconds: int) -> None:
    try:
        await asyncio.wait_for(process.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        logger.warning("Worker pid=%s exceeded wall time %ss; killing", process.pid, seconds)
        terminate_worker(process)


def terminate_worker(process: asyncio.subprocess.Process) -> None:
    """Kill a worker and its whole process group."""
    import signal

    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass


# ── Launcher script for SDK-spawned CLIs ─────────────────────────────────

def write_cli_launcher(
    workspace: Path | str,
    real_cli: str,
    env: dict[str, str],
    passthrough: tuple[str, ...] = (),
) -> Path:
    """Write a launcher the Claude Agent SDK spawns instead of ``claude``.

    The SDK merges ``os.environ`` into its subprocess env, so a plain
    ``options.env`` override cannot prevent backend-secret inheritance.
    The launcher re-execs the real CLI through ``env -i`` with only the
    validated worker env plus explicitly named passthrough variables that
    the SDK itself must set (e.g. CLAUDE_CODE_ENTRYPOINT).
    """
    workspace = Path(workspace)
    assert_worker_env_clean(env)
    verify_workspace_ownership(workspace)
    for name in passthrough:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise WorkerIsolationError(f"Invalid passthrough var name: {name}")
        if name in FORBIDDEN_ENV_NAMES or (
            _SENSITIVE_NAME_RE.search(name) and name not in _EXTRA_ALLOWED_SENSITIVE
        ):
            raise WorkerIsolationError(f"Refusing sensitive passthrough var: {name}")

    def _sh_quote(value: str) -> str:
        return "'" + value.replace("'", "'\\''") + "'"

    # When the backend runs as root with a worker identity configured, the
    # launcher also drops privileges (to the workspace's per-run uid when
    # allocated) before exec'ing the CLI, so even SDK-spawned workers never
    # run as root.
    identity = workspace_identity(workspace)
    uid, gid = identity.uid, identity.gid
    drop = ""
    if uid is not None:
        if not shutil.which("setpriv"):
            raise WorkerIsolationError("Cannot enforce SDK worker identity: setpriv is missing")
        drop = f"setpriv --reuid {uid} --regid {gid} --clear-groups "

    lines = [
        "#!/bin/sh",
        "# Managed by Loma — scrubs the environment before exec'ing the agent CLI.",
        "ulimit -c 0 2>/dev/null",
        f"ulimit -t {_limit('LOMA_WORKER_CPU_SECONDS')} 2>/dev/null",
        f"ulimit -n {_limit('LOMA_WORKER_NOFILE')} 2>/dev/null",
        "exec /usr/bin/env -i \\",
    ]
    for key, value in env.items():
        lines.append(f"  {key}={_sh_quote(value)} \\")
    for name in passthrough:
        # Forward the SDK-set value if present (never a backend secret name).
        lines.append(f"  {name}=\"${{{name}}}\" \\")
    command = build_bwrap_argv([real_cli], workspace) if bwrap_available() else [real_cli]
    lines.append(f"  {drop}{' '.join(_sh_quote(arg) for arg in command)} \"$@\"")

    launcher = workspace / "loma-cli-launcher.sh"
    launcher.write_text("\n".join(lines) + "\n")
    os.chmod(launcher, 0o755)
    if uid is not None:
        os.chown(launcher, uid, gid)
    return launcher


# ── Broker-backed tool shims ─────────────────────────────────────────────

_SHIM_TEMPLATE = '''#!/usr/bin/env python3
"""Broker-backed shim for the {tool!r} Loma tool (generated per run).

Runs inside an isolated worker with no backend credentials. Forwards the
invocation to the execution broker, which authenticates the run capability
and executes the real tool server-side with the run owner's identity.
"""
import json
import os
import sys
import urllib.error
import urllib.request

TOOL = {tool!r}
MAX_UPLOAD_BYTES = 1024 * 1024


def _capability(argv):
    for i, arg in enumerate(argv):
        if arg.startswith("--auth-token="):
            return arg.split("=", 1)[1]
        if arg == "--auth-token" and i + 1 < len(argv):
            return argv[i + 1]
    return os.environ.get("LOMA_RUN_CAPABILITY", "")


def main():
    argv = sys.argv[1:]
    broker_url = os.environ.get("LOMA_BROKER_URL", "http://127.0.0.1:3100").rstrip("/")
    capability = _capability(argv)
    if not capability:
        print(json.dumps({{"error": "No run capability is available in this "
                          "environment; personal tools are disabled for this run."}}))
        return 1

    files = {{}}
    total = 0
    workspace = os.environ.get("HOME", "")
    input_flags = {{"--attachments", "--file", "--file-path", "--content-file",
                   "--skill-md", "--html-body-file", "--files-json-file", "--code-file"}}
    candidates = []
    for index, arg in enumerate(argv):
        flag, sep, value = arg.partition("=")
        if flag not in input_flags:
            continue
        if not sep:
            value = argv[index + 1] if index + 1 < len(argv) else ""
        candidates.extend(value.split(",") if flag == "--attachments" else [value])
    for arg in candidates:
        arg = arg.strip()
        try:
            if (workspace and arg and os.path.isfile(arg)
                    and os.path.commonpath([os.path.realpath(arg), os.path.realpath(workspace)]) == os.path.realpath(workspace)
                    and len(files) < 8):
                with open(arg, "r", encoding="utf-8") as fh:
                    content = fh.read(MAX_UPLOAD_BYTES + 1)
                size = len(content.encode("utf-8"))
                if size <= MAX_UPLOAD_BYTES and total + size <= MAX_UPLOAD_BYTES:
                    files[arg] = content
                    total += size
        except (OSError, UnicodeDecodeError, ValueError):
            continue

    payload = json.dumps({{
        "operation": "tool.invoke",
        "resource": TOOL,
        "params": {{"argv": argv, "files": files}},
    }}).encode()
    request = urllib.request.Request(
        broker_url + "/v1/invoke", data=payload,
        headers={{"Authorization": "Bearer " + capability,
                  "Content-Type": "application/json"}},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode())
        except Exception:
            body = {{"error": "Broker request failed"}}
        print(json.dumps(body))
        return 1
    except Exception:
        print(json.dumps({{"error": "Could not reach the Loma execution broker."}}))
        return 1

    if isinstance(body, dict) and "stdout" in body:
        sys.stdout.write(body.get("stdout") or "")
        stderr_text = body.get("stderr") or ""
        if stderr_text:
            sys.stderr.write(stderr_text)
        return int(body.get("exit_code") or 0)
    print(json.dumps(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def populate_tool_shims(workspace: Path | str, tool_names: list[str]) -> None:
    """Write broker-backed shims into ``<workspace>/tools/<name>.py``.

    Agent prompts reference tools as ``python3 tools/<name>.py``; with the
    worker cwd set to its workspace, these shims replace the real tools,
    which now only run server-side via the broker.
    """
    tools_dir = Path(workspace) / "tools"
    tools_dir.mkdir(mode=0o700, exist_ok=True)
    for tool in tool_names:
        if not re.fullmatch(r"[a-z0-9_]{1,64}", tool):
            raise WorkerIsolationError(f"Invalid tool shim name: {tool}")
        shim = tools_dir / f"{tool}.py"
        shim.write_text(_SHIM_TEMPLATE.format(tool=tool))
        os.chmod(shim, 0o755)
    identity = workspace_identity(workspace)
    if identity.uid is not None:
        os.chown(tools_dir, identity.uid, identity.gid)
        for shim_file in tools_dir.iterdir():
            os.chown(shim_file, identity.uid, identity.gid)
