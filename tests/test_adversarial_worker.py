"""Adversarial synthetic tests: malicious code inside a worker.

Each test runs REAL subprocesses that behave like compromised agent code
(env dumps, backend-file reads, cross-user reads, credential harvesting)
and asserts every attempt fails. All secrets are synthetic.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from broker.service import Broker, Denied
from broker.worker import (
    FORBIDDEN_ENV_NAMES,
    build_worker_env,
    create_workspace,
    spawn_worker,
)

FAKE_SECRETS = {
    "OBSERVABILITY_MONGODB_URI": "mongodb://synthetic-backend/loma",
    "OAUTH_ENCRYPTION_KEY": "synthetic-master-key-1234567890abcdef",
    "ANTHROPIC_API_KEY": "synthetic-anthropic-value",
    "OPENAI_API_KEY": "synthetic-openai-value",
    "SLACK_BOT_TOKEN": "xoxb-synthetic-bot-token",
}

IS_ROOT = os.geteuid() == 0
NOBODY_UID = 65534


@pytest.fixture
def hostile_backend(monkeypatch, tmp_path):
    """A backend with planted synthetic secrets and a per-test worker root."""
    for key, value in FAKE_SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LOMA_WORKER_ROOT", str(tmp_path / "workers"))
    monkeypatch.delenv("LOMA_WORKER_UID", raising=False)
    monkeypatch.delenv("LOMA_WORKER_GID", raising=False)
    return tmp_path


async def run_in_worker(workspace, code: str, extra_env: dict | None = None):
    env = build_worker_env(workspace, extra=extra_env)
    process = await spawn_worker(
        ["python3", "-c", code], workspace=workspace, env=env,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    return process.returncode, stdout.decode(), stderr.decode()


@pytest.mark.asyncio
async def test_malicious_env_dump_finds_no_secrets(hostile_backend):
    workspace = create_workspace()
    code = (
        "import os, json\n"
        "loot = {k: v for k, v in os.environ.items()}\n"
        "print(json.dumps(loot))\n"
    )
    rc, out, err = await run_in_worker(workspace, code)
    assert rc == 0, err
    loot = json.loads(out)
    for name in FORBIDDEN_ENV_NAMES:
        assert name not in loot
    for value in FAKE_SECRETS.values():
        assert value not in out


@pytest.mark.asyncio
async def test_malicious_proc_environ_scrape_finds_no_secrets(hostile_backend):
    """Scraping the worker's own /proc for inherited secrets yields nothing."""
    workspace = create_workspace()
    code = (
        "data = open('/proc/self/environ','rb').read().decode(errors='replace')\n"
        "print(data)\n"
    )
    rc, out, err = await run_in_worker(workspace, code)
    assert rc == 0, err
    for value in FAKE_SECRETS.values():
        assert value not in out


@pytest.mark.asyncio
async def test_malicious_provider_credential_harvest_fails(hostile_backend):
    """Direct provider-credential access: no key in env, no key material in
    the workspace, and nothing at the conventional key variables."""
    workspace = create_workspace()
    code = (
        "import os, glob, json\n"
        "keys = [os.environ.get(k) for k in ('ANTHROPIC_API_KEY','OPENAI_API_KEY',"
        "'OPENROUTER_API_KEY','OPENCODE_API_KEY')]\n"
        "files = glob.glob(os.path.expanduser('~/**/*key*'), recursive=True)\n"
        "files += glob.glob(os.path.expanduser('~/**/auth.json'), recursive=True)\n"
        "print(json.dumps({'keys': keys, 'files': files}))\n"
    )
    rc, out, err = await run_in_worker(workspace, code)
    assert rc == 0, err
    result = json.loads(out)
    assert result["keys"] == [None, None, None, None]
    assert result["files"] == []


@pytest.mark.skipif(not IS_ROOT, reason="privilege-drop checks require root")
@pytest.mark.asyncio
async def test_malicious_backend_config_read_fails_for_nonroot_worker(
    hostile_backend, monkeypatch,
):
    """A worker running as the dedicated non-root uid cannot read backend
    config files (simulated .env with synthetic secrets, mode 0600 root)."""
    backend_env_file = hostile_backend / "backend" / ".env"
    backend_env_file.parent.mkdir()
    backend_env_file.write_text("OAUTH_ENCRYPTION_KEY=" + FAKE_SECRETS["OAUTH_ENCRYPTION_KEY"])
    os.chmod(backend_env_file, 0o600)
    os.chmod(backend_env_file.parent, 0o700)

    monkeypatch.setenv("LOMA_WORKER_UID", str(NOBODY_UID))
    workspace = create_workspace()
    code = (
        f"import os\n"
        f"assert os.geteuid() == {NOBODY_UID}, os.geteuid()\n"
        f"try:\n"
        f"    open({str(backend_env_file)!r}).read()\n"
        f"    print('LEAKED')\n"
        f"except PermissionError:\n"
        f"    print('DENIED')\n"
    )
    rc, out, err = await run_in_worker(workspace, code)
    assert rc == 0, err
    assert out.strip() == "DENIED"


@pytest.mark.skipif(not IS_ROOT, reason="privilege-drop checks require root")
@pytest.mark.asyncio
async def test_malicious_cross_user_artifact_read_fails(hostile_backend, monkeypatch):
    """Worker B (non-root) cannot read another user's workspace/artifacts."""
    # Victim workspace: created for a different run, owned by root (backend).
    monkeypatch.delenv("LOMA_WORKER_UID", raising=False)
    victim = create_workspace(prefix="victim")
    (victim / "artifact.pdf").write_bytes(b"victim-report-contents")
    os.chmod(victim / "artifact.pdf", 0o600)

    # Attacker worker: dropped to the dedicated non-root uid.
    monkeypatch.setenv("LOMA_WORKER_UID", str(NOBODY_UID))
    attacker = create_workspace(prefix="attacker")
    code = (
        f"import os\n"
        f"try:\n"
        f"    data = open({str(victim / 'artifact.pdf')!r}, 'rb').read()\n"
        f"    print('LEAKED:' + data.decode())\n"
        f"except (PermissionError, FileNotFoundError):\n"
        f"    print('DENIED')\n"
        f"try:\n"
        f"    os.listdir({str(victim)!r})\n"
        f"    print('LISTED')\n"
        f"except PermissionError:\n"
        f"    print('LIST-DENIED')\n"
    )
    rc, out, err = await run_in_worker(attacker, code)
    assert rc == 0, err
    assert "LEAKED" not in out
    assert "DENIED" in out and "LIST-DENIED" in out


# ── Forged/expired/replayed capabilities against the broker HTTP surface ──


class _Caps:
    def __init__(self):
        self.docs = {}
        self.create_index = None

    async def insert_one(self, doc):
        import copy
        self.docs[doc["_id"]] = copy.deepcopy(doc)

    async def find_one_and_update(self, query, update):
        import copy
        doc = self.docs.get(query["_id"])
        if not doc or doc["revoked"] or doc["remaining_calls"] <= 0:
            return None
        if doc["expires_at"] <= query["expires_at"]["$gt"]:
            return None
        if query["scopes"]["$elemMatch"] not in doc["scopes"]:
            return None
        old = copy.deepcopy(doc)
        doc["remaining_calls"] -= 1
        return old

    async def update_many(self, query, update):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(update["$set"])


@pytest.mark.asyncio
async def test_forged_and_stolen_capabilities_rejected_over_http():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiohttp.test_utils import TestClient, TestServer

    from broker.http import create_app

    operation = SimpleNamespace(
        valid_resource=lambda r: r == "notify",
        execute=AsyncMock(return_value={"exit_code": 0, "stdout": "", "stderr": ""}),
    )
    db = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={"status": "active"})),
        execution_capabilities=_Caps(),
        execution_audit=SimpleNamespace(insert_one=AsyncMock()),
    )
    broker = Broker(db, "dep-adv", {"tool.invoke": operation})
    run_id, real = await broker.issue(user_email="owner@example.test",
                                      grants={"tool.invoke": ["notify"]})

    async with TestClient(TestServer(create_app(broker))) as client:
        # Forged tokens (right shape, wrong secret) and malformed tokens.
        for forged in ("loma_run_v1_" + "z" * 43, "Bearer x", real[:-1], ""):
            response = await client.post(
                "/v1/invoke",
                headers={"Authorization": f"Bearer {forged}"},
                json={"operation": "tool.invoke", "resource": "notify"},
            )
            assert response.status == 403

        # A revoked (stolen-then-killed) capability is dead everywhere.
        await broker.revoke(run_id)
        response = await client.post(
            "/v1/invoke",
            headers={"Authorization": f"Bearer {real}"},
            json={"operation": "tool.invoke", "resource": "notify"},
        )
        assert response.status == 403
    operation.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_capability_rejected():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    operation = SimpleNamespace(valid_resource=lambda r: True, execute=AsyncMock())
    db = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={"status": "active"})),
        execution_capabilities=_Caps(),
        execution_audit=SimpleNamespace(insert_one=AsyncMock()),
    )
    broker = Broker(db, "dep-adv", {"tool.invoke": operation})
    _, token = await broker.issue(user_email="owner@example.test",
                                  grants={"tool.invoke": ["notify"]}, ttl_seconds=1)
    doc = list(db.execution_capabilities.docs.values())[0]
    doc["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(Denied):
        await broker.execute(token, "tool.invoke", "notify")
    operation.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_broker_fails_closed_when_authz_lookup_errors():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    operation = SimpleNamespace(valid_resource=lambda r: True, execute=AsyncMock())
    caps = _Caps()
    db = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(return_value={"status": "active"})),
        execution_capabilities=caps,
        execution_audit=SimpleNamespace(insert_one=AsyncMock()),
    )
    broker = Broker(db, "dep-adv", {"tool.invoke": operation})
    _, token = await broker.issue(user_email="owner@example.test",
                                  grants={"tool.invoke": ["notify"]})

    async def boom(*args, **kwargs):
        raise RuntimeError("db unavailable")

    caps.find_one_and_update = boom
    with pytest.raises(RuntimeError):
        await broker.execute(token, "tool.invoke", "notify")
    operation.execute.assert_not_awaited()

    # User-status lookup failure after admission also blocks execution.
    caps2 = _Caps()
    db2 = SimpleNamespace(
        users=SimpleNamespace(find_one=AsyncMock(side_effect=RuntimeError("db down"))),
        execution_capabilities=caps2,
        execution_audit=SimpleNamespace(insert_one=AsyncMock()),
    )
    broker2 = Broker(db2, "dep-adv", {"tool.invoke": operation})
    db2.users.find_one.side_effect = None
    db2.users.find_one.return_value = {"status": "active"}
    _, token2 = await broker2.issue(user_email="owner@example.test",
                                    grants={"tool.invoke": ["notify"]})
    db2.users.find_one.side_effect = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        await broker2.execute(token2, "tool.invoke", "notify")
    operation.execute.assert_not_awaited()
