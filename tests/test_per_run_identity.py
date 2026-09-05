"""Per-run worker identity separation.

Two concurrent runs of DIFFERENT users must not be able to read each other's
workspaces, temp files, or launcher/shim state. With LOMA_WORKER_UID_RANGE
configured (backend as root) every workspace gets its own uid; the shared-uid
fallback keeps strict 0700 ownership checks. Privilege-drop tests run only as
root (they auto-skip in non-root CI). All values are plainly synthetic.
"""

import asyncio
import os
from pathlib import Path

import pytest

import broker.worker as worker_mod
from broker.worker import (
    WorkerIsolationError,
    build_worker_env,
    cleanup_workspace,
    create_workspace,
    spawn_worker,
    verify_workspace_ownership,
    workspace_identity,
)

IS_ROOT = os.geteuid() == 0
RANGE = "210000-210015"


@pytest.fixture
def per_run_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    monkeypatch.setenv("LOMA_WORKER_UID_RANGE", RANGE)
    monkeypatch.delenv("LOMA_WORKER_UID", raising=False)
    monkeypatch.delenv("LOMA_WORKER_GID", raising=False)
    monkeypatch.delenv("LOMA_KEEP_WORKSPACES", raising=False)
    return tmp_path


@pytest.fixture
def shared_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    monkeypatch.delenv("LOMA_WORKER_UID_RANGE", raising=False)
    monkeypatch.delenv("LOMA_WORKER_UID", raising=False)
    monkeypatch.delenv("LOMA_WORKER_GID", raising=False)
    return tmp_path


async def _run(workspace, code):
    process = await spawn_worker(
        ["python3", "-c", code], workspace=workspace, env=build_worker_env(workspace),
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    return process.returncode, stdout.decode(), stderr.decode()


# ── Allocator behavior (no privileges needed beyond what applies) ────────


@pytest.mark.skipif(not IS_ROOT, reason="uid allocation requires root")
def test_each_workspace_gets_a_distinct_uid_from_the_range(per_run_backend):
    first = create_workspace(prefix="usera")
    second = create_workspace(prefix="userb")
    ida, idb = workspace_identity(first), workspace_identity(second)
    assert ida.per_run and idb.per_run
    assert ida.uid != idb.uid
    assert 210000 <= ida.uid <= 210015 and 210000 <= idb.uid <= 210015
    assert first.stat().st_uid == ida.uid
    assert (first / "tmp").stat().st_uid == ida.uid
    assert second.stat().st_uid == idb.uid


@pytest.mark.skipif(not IS_ROOT, reason="uid allocation requires root")
def test_uid_released_only_after_cleanup_and_reused(per_run_backend, monkeypatch):
    monkeypatch.setenv("LOMA_WORKER_UID_RANGE", "210020-210021")
    a = create_workspace()
    b = create_workspace()
    with pytest.raises(WorkerIsolationError):
        create_workspace()  # range exhausted while both runs are live
    uid_a = workspace_identity(a).uid
    cleanup_workspace(a)
    c = create_workspace()
    assert workspace_identity(c).uid == uid_a  # released uid is reusable
    cleanup_workspace(b)
    cleanup_workspace(c)


@pytest.mark.skipif(not IS_ROOT, reason="uid allocation requires root")
def test_on_disk_owners_count_as_in_use_after_restart(per_run_backend, monkeypatch):
    monkeypatch.setenv("LOMA_WORKER_UID_RANGE", "210030-210031")
    survivor = create_workspace()
    survivor_uid = workspace_identity(survivor).uid
    # Simulate a backend restart: registry gone, workspace still on disk.
    worker_mod._workspace_identities.clear()
    replacement = create_workspace()
    assert workspace_identity(replacement).uid != survivor_uid
    cleanup_workspace(replacement)


def test_range_without_root_fails_closed(per_run_backend, monkeypatch):
    if IS_ROOT:
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(WorkerIsolationError):
        create_workspace()


def test_malformed_range_fails_closed(shared_backend, monkeypatch):
    for bad in ("banana", "100-90", "0-99", "1000-999999999999"):
        monkeypatch.setenv("LOMA_WORKER_UID_RANGE", bad)
        with pytest.raises(WorkerIsolationError):
            create_workspace()


# ── Shared-uid fallback: strict ownership verification ───────────────────


def test_spawn_refuses_group_readable_workspace(shared_backend):
    workspace = create_workspace()
    os.chmod(workspace, 0o755)
    with pytest.raises(WorkerIsolationError):
        verify_workspace_ownership(workspace)


@pytest.mark.skipif(not IS_ROOT, reason="chown requires root")
def test_spawn_refuses_foreign_owned_workspace(shared_backend):
    workspace = create_workspace()
    os.chown(workspace, 65534, 65534)  # hijacked by another identity
    with pytest.raises(WorkerIsolationError):
        verify_workspace_ownership(workspace)


@pytest.mark.asyncio
async def test_fallback_workspace_still_spawns(shared_backend):
    workspace = create_workspace()
    rc, out, _ = await _run(workspace, "print('ok')")
    assert rc == 0 and out.strip() == "ok"


# ── Adversarial: concurrent runs of two different users ──────────────────


@pytest.mark.skipif(not IS_ROOT, reason="privilege-drop checks require root")
@pytest.mark.asyncio
async def test_concurrent_runs_cannot_read_each_other(per_run_backend):
    """Alice's live run cannot read Bob's live run: workspace, tmp files,
    tool shims, or planted credential-shaped material."""
    alice = create_workspace(prefix="alice")
    bob = create_workspace(prefix="bob")
    assert workspace_identity(alice).uid != workspace_identity(bob).uid

    secret_path = bob / "tmp" / "session-material.json"
    secret_path.write_text('{"token": "synthetic-bob-session-value"}')
    os.chmod(secret_path, 0o600)
    os.chown(secret_path, workspace_identity(bob).uid, workspace_identity(bob).gid)

    probe = (
        "import os, json\n"
        f"targets = [{str(bob)!r}, {str(secret_path)!r}, {str(bob / 'tools')!r}]\n"
        "results = []\n"
        "for target in targets:\n"
        "    try:\n"
        "        if os.path.isdir(target):\n"
        "            os.listdir(target)\n"
        "        else:\n"
        "            open(target).read()\n"
        "        results.append('LEAKED:' + target)\n"
        "    except (PermissionError, FileNotFoundError):\n"
        "        results.append('DENIED')\n"
        "print(json.dumps(results))\n"
    )
    # Both runs execute CONCURRENTLY, each probing the other.
    (rc_a, out_a, err_a), (rc_b, out_b, err_b) = await asyncio.gather(
        _run(alice, probe),
        _run(bob, "import os; print(os.geteuid())"),
    )
    assert rc_a == 0, err_a
    assert rc_b == 0, err_b
    assert "LEAKED" not in out_a
    assert out_a.count("DENIED") == 3
    assert int(out_b.strip()) == workspace_identity(bob).uid
    assert "synthetic-bob-session-value" not in out_a


@pytest.mark.skipif(not IS_ROOT, reason="privilege-drop checks require root")
@pytest.mark.asyncio
async def test_worker_cannot_write_into_sibling_workspace(per_run_backend):
    alice = create_workspace(prefix="alice")
    bob = create_workspace(prefix="bob")
    code = (
        "import os\n"
        f"try:\n"
        f"    open(os.path.join({str(bob)!r}, 'implant.py'), 'w').write('x')\n"
        f"    print('WROTE')\n"
        f"except (PermissionError, FileNotFoundError):\n"
        f"    print('DENIED')\n"
    )
    rc, out, err = await _run(alice, code)
    assert rc == 0, err
    assert out.strip() == "DENIED"
    assert not (bob / "implant.py").exists()


@pytest.mark.skipif(not IS_ROOT, reason="privilege-drop checks require root")
@pytest.mark.asyncio
async def test_worker_runs_as_its_allocated_uid_not_root(per_run_backend):
    workspace = create_workspace()
    identity = workspace_identity(workspace)
    rc, out, err = await _run(
        workspace,
        "import os; print(os.geteuid(), os.getegid(), os.getgroups())",
    )
    assert rc == 0, err
    euid, egid, groups = out.strip().split(" ", 2)
    assert int(euid) == identity.uid
    assert int(egid) == identity.gid
    assert groups in ("[]", f"[{identity.gid}]")
