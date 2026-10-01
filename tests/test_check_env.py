"""Deploy guard for the project .env (scripts/check_env.sh) and the read-only mount."""
import importlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_env.sh"

FULL_ENV = "SLACK_BOT_TOKEN=xoxb-secret-value\nOBSERVABILITY_MONGODB_URI=mongodb://x\n# comment\nexport OAUTH_ENCRYPTION_KEY=k\nEMPTY=\n"


def run(tmp_path, *args, **env):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=tmp_path, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", **env},
    )


def backups(tmp_path):
    return sorted((tmp_path / ".env.backups").glob("env.*"))


def test_first_deploy_passes_and_backs_up(tmp_path):
    (tmp_path / ".env").write_text(FULL_ENV)
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(backups(tmp_path)) == 1
    assert backups(tmp_path)[0].read_text() == FULL_ENV


def test_record_then_unchanged_passes_without_duplicate_backup(tmp_path):
    (tmp_path / ".env").write_text(FULL_ENV)
    assert run(tmp_path, "--record").returncode == 0
    assert (tmp_path / ".env.deployed-keys").read_text().split() == [
        "EMPTY", "OAUTH_ENCRYPTION_KEY", "OBSERVABILITY_MONGODB_URI", "SLACK_BOT_TOKEN"]
    assert run(tmp_path).returncode == 0
    assert run(tmp_path).returncode == 0
    assert len(backups(tmp_path)) == 1


def test_overwritten_env_blocks_deploy_and_keeps_good_backup(tmp_path):
    # The Oct 2026 incident: an agent replaced the 66-key .env with 4 test keys.
    env = tmp_path / ".env"
    env.write_text(FULL_ENV)
    run(tmp_path)
    run(tmp_path, "--record")
    env.write_text("PLOTLINE_BASE_URL=https://example.test\n")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "SLACK_BOT_TOKEN" in r.stdout and "OBSERVABILITY_MONGODB_URI" in r.stdout
    assert "xoxb-secret-value" not in r.stdout + r.stderr
    assert any(b.read_text() == FULL_ENV for b in backups(tmp_path))


def test_removal_allowed_with_override(tmp_path):
    env = tmp_path / ".env"
    env.write_text(FULL_ENV)
    run(tmp_path, "--record")
    env.write_text("SLACK_BOT_TOKEN=x\nOBSERVABILITY_MONGODB_URI=y\nOAUTH_ENCRYPTION_KEY=z\n")
    assert run(tmp_path).returncode == 1
    assert run(tmp_path, ALLOW_ENV_KEY_REMOVAL="1").returncode == 0


def test_added_keys_pass(tmp_path):
    env = tmp_path / ".env"
    env.write_text(FULL_ENV)
    run(tmp_path, "--record")
    env.write_text(FULL_ENV + "NEW_KEY=1\n")
    assert run(tmp_path).returncode == 0


def test_missing_env_file_fails(tmp_path):
    assert run(tmp_path).returncode == 1


def test_backups_are_rotated(tmp_path):
    env = tmp_path / ".env"
    for i in range(5):
        env.write_text(FULL_ENV + f"N={i}\n")
        assert run(tmp_path, ENV_BACKUP_KEEP="3").returncode == 0
    assert len(backups(tmp_path)) == 3


def test_backend_mounts_app_env_read_only():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "- ./.env:/app/.env:ro" in compose
    assert "LOMA_ENV_FILE: /etc/loma/.env" in compose


def test_env_editor_writes_to_loma_env_file(monkeypatch):
    import api.env_routes as env_routes
    monkeypatch.setenv("LOMA_ENV_FILE", "/etc/loma/.env")
    assert importlib.reload(env_routes).DOTENV_PATH == "/etc/loma/.env"
    monkeypatch.delenv("LOMA_ENV_FILE")
    assert importlib.reload(env_routes).DOTENV_PATH == str(ROOT / ".env")
