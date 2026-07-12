import base64
import json
import time

import pytest


def _fake_jwt(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}."


def _write_auth(config_dir, email="dev@plotline.so", plan="pro"):
    config_dir.mkdir(parents=True, exist_ok=True)
    token = _fake_jwt({
        "email": email,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan},
    })
    (config_dir / "auth.json").write_text(json.dumps({
        "tokens": {"id_token": token, "access_token": "x", "refresh_token": "y"},
        "last_refresh": "2026-07-12T00:00:00Z",
    }))


# ── auth.json parsing ────────────────────────────────────────────────────


def test_read_codex_auth_parses_email_and_plan(tmp_path):
    from agent.codex_runtime import read_codex_auth

    _write_auth(tmp_path / "dev@plotline.so", email="dev@plotline.so", plan="pro")
    auth = read_codex_auth(tmp_path / "dev@plotline.so")
    assert auth is not None
    assert auth["email"] == "dev@plotline.so"
    assert auth["auth_method"] == "chatgpt"
    assert auth["plan"] == "pro"


def test_read_codex_auth_missing_or_invalid(tmp_path):
    from agent.codex_runtime import read_codex_auth

    assert read_codex_auth(tmp_path) is None
    (tmp_path / "auth.json").write_text("not-json")
    assert read_codex_auth(tmp_path) is None


# ── model id helpers ─────────────────────────────────────────────────────


def test_selected_model_is_codex():
    from agent.codex_runtime import normalize_codex_model, selected_model_is_codex

    assert selected_model_is_codex("codex/gpt-5.6-codex")
    assert normalize_codex_model("codex/gpt-5.6-codex") == "gpt-5.6-codex"
    assert not selected_model_is_codex("anthropic/claude-opus-4-8")
    assert not selected_model_is_codex("openai/gpt-5.5")
    assert not selected_model_is_codex("gpt-5.6-codex")  # no provider prefix
    assert not selected_model_is_codex(None)


# ── MCP config translation ───────────────────────────────────────────────


def test_claude_mcp_to_codex_toml_stdio_and_http():
    from agent.codex_runtime import claude_mcp_to_codex_toml

    toml = claude_mcp_to_codex_toml({
        "mongodb": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "mongodb-mcp-server"],
            "env": {"MDB_CONNECTION_STRING": "mongodb://x"},
        },
        "linear": {"type": "http", "url": "https://mcp.linear.app/mcp",
                   "headers": {"Authorization": "Bearer t"}},
        "broken": {"type": "http"},  # no url — skipped
    })
    assert "[mcp_servers.mongodb]" in toml
    assert 'command = "npx"' in toml
    assert '"mongodb-mcp-server"' in toml
    assert "[mcp_servers.mongodb.env]" in toml
    assert "[mcp_servers.linear]" in toml
    assert 'url = "https://mcp.linear.app/mcp"' in toml
    assert "broken" not in toml


# ── event normalization ──────────────────────────────────────────────────


def test_extract_codex_event_shapes():
    from agent.codex_runtime import _extract_codex_event

    inner = {"type": "agent_message_delta", "delta": "hi"}
    assert _extract_codex_event(
        {"method": "codex/event", "params": {"msg": inner}}
    ) == inner
    namespaced = _extract_codex_event(
        {"method": "codex/event/task_complete", "params": {"last_agent_message": "done"}}
    )
    assert namespaced["type"] == "task_complete"
    assert namespaced["last_agent_message"] == "done"
    assert _extract_codex_event({"method": "somethingElse", "params": {}}) is None


def test_rate_limit_cooldown_from_event():
    from agent.codex_runtime import _rate_limit_cooldown_seconds

    assert _rate_limit_cooldown_seconds({"rate_limits": {
        "primary": {"used_percent": 100, "resets_in_seconds": 900},
        "secondary": {"used_percent": 40, "resets_in_seconds": 500000},
    }}) == 900
    assert _rate_limit_cooldown_seconds({"rate_limits": {
        "primary": {"used_percent": 50, "resets_in_seconds": 900},
    }}) is None
    assert _rate_limit_cooldown_seconds({}) is None


# ── pool: account scan / round-robin / cooldowns ─────────────────────────


def _make_pool(tmp_path, monkeypatch, emails=("a@plotline.so", "b@plotline.so")):
    from agent.codex_pool import CodexClientPool

    monkeypatch.setenv("CODEX_USERS_DIR", str(tmp_path))
    for email in emails:
        _write_auth(tmp_path / email, email=email)
    pool = CodexClientPool(pool_size=3)
    pool._scan_accounts()
    return pool


def test_scan_accounts_finds_valid_dirs(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    (tmp_path / "empty@plotline.so").mkdir()  # no auth.json — excluded
    pool._scan_accounts()
    assert sorted(a["email"] for a in pool._accounts) == ["a@plotline.so", "b@plotline.so"]


def test_scan_accounts_respects_disabled(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    pool._scan_accounts(disabled_emails={"a@plotline.so"})
    assert [a["email"] for a in pool._accounts] == ["b@plotline.so"]


def test_round_robin_skips_cooldown(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    first = pool._next_account()["email"]
    second = pool._next_account()["email"]
    assert {first, second} == {"a@plotline.so", "b@plotline.so"}

    pool.mark_account_exhausted("a@plotline.so")
    for _ in range(4):
        assert pool._next_account()["email"] == "b@plotline.so"

    pool.mark_account_exhausted("b@plotline.so")
    assert pool._next_account() is None


def test_cooldown_expires(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch, emails=("a@plotline.so",))
    pool._account_cooldowns["a@plotline.so"] = time.time() - 1  # already expired
    assert pool._next_account()["email"] == "a@plotline.so"


def test_adaptive_cooldown_override(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch, emails=("a@plotline.so",))
    pool.mark_account_exhausted("a@plotline.so", cooldown_override=42)
    remaining = pool._account_cooldowns["a@plotline.so"] - time.time()
    assert 40 < remaining <= 42


def test_auth_failure_readmit_on_auth_json_change(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch, emails=("a@plotline.so",))
    pool.mark_account_exhausted("a@plotline.so", auth_error=True)
    assert pool._next_account() is None  # 1h cooldown

    # Simulate token refresh: auth.json rewritten with a new mtime
    auth_path = tmp_path / "a@plotline.so" / "auth.json"
    import os
    os.utime(auth_path, (time.time() + 10, time.time() + 10))

    account = pool._next_account()
    assert account is not None and account["email"] == "a@plotline.so"
    assert "a@plotline.so" not in pool._auth_failed_mtimes


def test_status_shape(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    pool.mark_account_exhausted("a@plotline.so")
    status = pool.status()
    assert status["pool_size"] == 3
    assert status["available"] == 0
    assert sorted(status["accounts"]) == ["a@plotline.so", "b@plotline.so"]
    assert status["accounts_on_cooldown"] == ["a@plotline.so"]
