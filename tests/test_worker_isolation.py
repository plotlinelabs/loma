"""Worker isolation boundary tests: scrubbed env, private workspaces, shims.

All synthetic: fake secret values are planted in the backend env and the
tests assert they can never reach a worker process by any route.
"""

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from broker import worker as worker_mod
from broker.worker import (
    FORBIDDEN_ENV_NAMES,
    WorkerIsolationError,
    assert_worker_env_clean,
    build_bwrap_argv,
    build_worker_env,
    cleanup_workspace,
    create_workspace,
    populate_tool_shims,
    spawn_worker,
    worker_root,
    write_cli_launcher,
)

FAKE_SECRETS = {
    "OBSERVABILITY_MONGODB_URI": "mongodb://synthetic-host/db",
    "OAUTH_ENCRYPTION_KEY": "synthetic-fernet-key-000000000000000000000000",
    "ANTHROPIC_API_KEY": "sk-ant-synthetic-000",
    "SLACK_BOT_TOKEN": "xoxb-synthetic-000",
    "OPENAI_API_KEY": "sk-synthetic-000",
}


@pytest.fixture
def polluted_backend_env(monkeypatch):
    for key, value in FAKE_SECRETS.items():
        monkeypatch.setenv(key, value)
    return FAKE_SECRETS


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    return create_workspace(prefix="test")


# ── Environment construction ─────────────────────────────────────────────


def test_worker_env_contains_no_backend_secrets(polluted_backend_env, workspace):
    env = build_worker_env(workspace)
    for name in FORBIDDEN_ENV_NAMES:
        assert name not in env
    joined = json.dumps(env)
    for value in polluted_backend_env.values():
        assert value not in joined
    # Only the expected allowlist shape.
    assert env["HOME"] == str(workspace)
    assert env["TMPDIR"] == str(workspace / "tmp")
    assert env["LOMA_ISOLATED_WORKER"] == "1"


def test_worker_env_rejects_forbidden_extra_keys(workspace):
    for key in ("OAUTH_ENCRYPTION_KEY", "OBSERVABILITY_MONGODB_URI", "ANTHROPIC_API_KEY"):
        with pytest.raises(WorkerIsolationError):
            build_worker_env(workspace, extra={key: "x"})


@pytest.mark.parametrize("key", [
    "MY_SECRET", "SOME_TOKEN", "DB_PASSWORD", "X_API_KEY", "SIGNING_KEY_ID",
    "SERVICE_CREDENTIAL", "FOO_ACCESS_KEY", "MONGODB_HOST", "SENTRY_DSN_X",
])
def test_worker_env_rejects_sensitive_looking_extra_keys(workspace, key):
    with pytest.raises(WorkerIsolationError):
        build_worker_env(workspace, extra={key: "x"})


def test_worker_env_allows_run_capability_and_runtime_settings(workspace):
    env = build_worker_env(workspace, extra={
        "LOMA_RUN_CAPABILITY": "loma_run_v1_" + "a" * 43,
        "LOMA_BROKER_URL": "http://127.0.0.1:3100",
        "CLAUDE_CONFIG_DIR": "/opt/claude-users/u@example.test",
        "XDG_CONFIG_HOME": "/x",
    })
    assert env["LOMA_RUN_CAPABILITY"].startswith("loma_run_v1_")


def test_env_clean_assertion_catches_smuggled_secret_values(polluted_backend_env, workspace):
    env = build_worker_env(workspace)
    env["INNOCENT_LOOKING"] = "prefix " + FAKE_SECRETS["OAUTH_ENCRYPTION_KEY"]
    with pytest.raises(WorkerIsolationError):
        assert_worker_env_clean(env)


def test_env_clean_assertion_catches_forbidden_names(workspace):
    env = build_worker_env(workspace)
    env["ANTHROPIC_API_KEY"] = "anything"
    with pytest.raises(WorkerIsolationError):
        assert_worker_env_clean(env)


# ── Workspaces ───────────────────────────────────────────────────────────


def test_workspaces_are_private_and_unique(tmp_path, monkeypatch):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    a = create_workspace(prefix="run")
    b = create_workspace(prefix="run")
    assert a != b
    for ws in (a, b):
        mode = stat.S_IMODE(os.stat(ws).st_mode)
        assert mode == 0o700
        assert (ws / "tmp").is_dir()
        assert (ws / "tools").is_dir()


def test_cleanup_refuses_paths_outside_worker_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    monkeypatch.delenv("LOMA_KEEP_WORKSPACES", raising=False)
    victim = tmp_path / "not-a-workspace"
    victim.mkdir()
    (victim / "data.txt").write_text("keep me")
    cleanup_workspace(victim)
    assert (victim / "data.txt").exists()

    ws = create_workspace()
    cleanup_workspace(ws)
    assert not ws.exists()


# ── Spawning (adversarial: real subprocess dumps its environment) ────────


@pytest.mark.asyncio
async def test_spawned_worker_cannot_see_backend_secrets(polluted_backend_env, workspace):
    env = build_worker_env(workspace)
    process = await spawn_worker(
        ["/usr/bin/env", "python3", "-c",
         "import os, json; print(json.dumps(dict(os.environ)))"],
        workspace=workspace,
        env=env,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    assert process.returncode == 0, stderr.decode()
    seen = json.loads(stdout.decode())
    for name in FORBIDDEN_ENV_NAMES:
        assert name not in seen
    dumped = json.dumps(seen)
    for value in polluted_backend_env.values():
        assert value not in dumped


@pytest.mark.asyncio
async def test_spawned_worker_runs_in_workspace_with_limits(workspace):
    env = build_worker_env(workspace)
    process = await spawn_worker(
        ["python3", "-c",
         "import os, resource, json;"
         "print(json.dumps({'cwd': os.getcwd(),"
         " 'core': resource.getrlimit(resource.RLIMIT_CORE)[0],"
         " 'sid_is_own': os.getsid(0) == os.getpid()}))"],
        workspace=workspace,
        env=env,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    assert process.returncode == 0, stderr.decode()
    info = json.loads(stdout.decode())
    assert Path(info["cwd"]) == workspace
    assert info["core"] == 0
    assert info["sid_is_own"] is True


@pytest.mark.asyncio
async def test_spawn_refuses_dirty_env(workspace):
    env = build_worker_env(workspace)
    env["OAUTH_ENCRYPTION_KEY"] = "leak"
    with pytest.raises(WorkerIsolationError):
        await spawn_worker(["/bin/true"], workspace=workspace, env=env)


# ── SDK CLI launcher ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_launcher_scrubs_inherited_environment(polluted_backend_env, workspace):
    env = build_worker_env(workspace, extra={"LOMA_BROKER_URL": "http://127.0.0.1:3100"})
    launcher = write_cli_launcher(
        workspace, "/usr/bin/env", env, passthrough=("CLAUDE_CONFIG_DIR",),
    )
    assert os.access(launcher, os.X_OK)
    # Simulate the SDK: full backend env inherited + its own additions.
    sdk_env = {**os.environ, "CLAUDE_CONFIG_DIR": "/opt/claude-users/u@example.test"}
    process = await asyncio.create_subprocess_exec(
        str(launcher), env=sdk_env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    assert process.returncode == 0, stderr.decode()
    printed = stdout.decode()
    for name in FORBIDDEN_ENV_NAMES:
        assert f"{name}=" not in printed
    for value in polluted_backend_env.values():
        assert value not in printed
    assert "CLAUDE_CONFIG_DIR=/opt/claude-users/u@example.test" in printed
    assert "LOMA_BROKER_URL=http://127.0.0.1:3100" in printed


def test_cli_launcher_rejects_sensitive_passthrough(workspace):
    env = build_worker_env(workspace)
    for name in ("ANTHROPIC_API_KEY", "MY_TOKEN", "OAUTH_ENCRYPTION_KEY", "$(evil)"):
        with pytest.raises(WorkerIsolationError):
            write_cli_launcher(workspace, "claude", env, passthrough=(name,))


# ── bubblewrap arg construction ──────────────────────────────────────────


def test_bwrap_argv_confines_to_workspace(workspace):
    argv = build_bwrap_argv(["python3", "x.py"], workspace)
    assert argv[0] == "bwrap"
    assert "--unshare-pid" in argv
    joined = " ".join(argv)
    assert f"--bind {workspace} {workspace}" in joined
    assert f"--chdir {workspace}" in joined
    # No home or arbitrary host binds.
    assert "--bind /root" not in joined and "--bind /home" not in joined
    assert argv[-2:] == ["python3", "x.py"]


# ── Tool shims ───────────────────────────────────────────────────────────


def test_tool_shims_are_generated_and_credential_free(workspace):
    populate_tool_shims(workspace, ["gmail", "notify", "loma_skills"])
    for tool in ("gmail", "notify", "loma_skills"):
        shim = workspace / "tools" / f"{tool}.py"
        assert shim.exists() and os.access(shim, os.X_OK)
        body = shim.read_text()
        assert "urllib.request" in body
        assert "LOMA_BROKER_URL" in body
        # Shims never reference DB URIs or encryption keys.
        assert "OBSERVABILITY_MONGODB_URI" not in body
        assert "OAUTH_ENCRYPTION_KEY" not in body


def test_tool_shims_reject_invalid_names(workspace):
    for bad in ("../evil", "a b", "UPPER", "x" * 65, "tool;rm"):
        with pytest.raises(WorkerIsolationError):
            populate_tool_shims(workspace, [bad])


@pytest.mark.asyncio
async def test_shim_fails_closed_without_capability(workspace):
    populate_tool_shims(workspace, ["notify"])
    env = build_worker_env(workspace)
    process = await spawn_worker(
        ["python3", "tools/notify.py", "send", "--title", "x"],
        workspace=workspace,
        env=env,
    )
    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    assert process.returncode == 1
    assert "Missing --auth-token" in stdout.decode()


@pytest.mark.asyncio
async def test_shim_round_trip_through_broker_http(workspace, monkeypatch):
    """End-to-end: shim in a worker -> broker HTTP -> tool result back."""
    from aiohttp import web

    from broker.http import create_app

    class FakeToolOp:
        accepts_params = True

        @staticmethod
        def valid_resource(value):
            return value == "notify"

        async def execute(self, db, email, resource, params=None):
            assert params["argv"][0] == "list"
            return {"exit_code": 0, "stdout": json.dumps({"ok": True, "email": email}), "stderr": ""}

    class FakeBroker:
        async def execute(self, token, operation, resource, params=None):
            from broker.service import Denied
            if token != CAPABILITY or operation != "tool.invoke":
                raise Denied()
            return await FakeToolOp().execute(None, "owner@example.test", resource, params)

    CAPABILITY = "loma_run_v1_" + "a" * 43
    app = create_app(FakeBroker())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        populate_tool_shims(workspace, ["notify"])
        env = build_worker_env(workspace, extra={
            "LOMA_BROKER_URL": f"http://127.0.0.1:{port}",
            "LOMA_RUN_CAPABILITY": CAPABILITY,
        })
        process = await spawn_worker(
            ["python3", "tools/notify.py", "list"],
            workspace=workspace,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, stderr.decode()
        assert json.loads(stdout.decode()) == {"ok": True, "email": "owner@example.test"}
    finally:
        await runner.cleanup()


def test_worker_root_under_temp_by_default(monkeypatch):
    monkeypatch.delenv("LOMA_WORKER_ROOT", raising=False)
    assert "loma-workers" in str(worker_root())
