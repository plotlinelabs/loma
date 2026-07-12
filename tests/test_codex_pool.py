import base64
import json
import time

import pytest


def _fake_jwt(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}."


def _write_auth(config_dir, email="dev@example.com", plan="pro"):
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

    _write_auth(tmp_path / "dev@example.com", email="dev@example.com", plan="pro")
    auth = read_codex_auth(tmp_path / "dev@example.com")
    assert auth is not None
    assert auth["email"] == "dev@example.com"
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

    assert selected_model_is_codex("codex/gpt-5.6-sol")
    assert normalize_codex_model("codex/gpt-5.6-sol") == "gpt-5.6-sol"
    assert not selected_model_is_codex("anthropic/claude-opus-4-8")
    assert not selected_model_is_codex("openai/gpt-5.5")
    assert not selected_model_is_codex("gpt-5.6-sol")  # no provider prefix
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


def test_normalize_v2_agent_message_events():
    from agent.codex_runtime import _normalize_v2_event

    tid = "t-1"
    assert _normalize_v2_event(
        {"method": "item/agentMessage/delta", "params": {"threadId": tid, "delta": "hi"}}, tid
    ) == [{"type": "agent_message_delta", "delta": "hi"}]

    done = _normalize_v2_event(
        {"method": "item/completed", "params": {"threadId": tid, "item": {
            "type": "agentMessage", "id": "i1",
            "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}],
        }}}, tid
    )
    assert done == [{"type": "agent_message", "message": "hello world"}]

    # events for another thread are dropped
    assert _normalize_v2_event(
        {"method": "item/agentMessage/delta", "params": {"threadId": "other", "delta": "x"}}, tid
    ) == []
    assert _normalize_v2_event({"method": "somethingElse", "params": {}}, tid) == []


def test_normalize_v2_tool_events():
    from agent.codex_runtime import _normalize_v2_event

    tid = "t-1"
    begin = _normalize_v2_event(
        {"method": "item/started", "params": {"threadId": tid, "item": {
            "type": "commandExecution", "id": "c1", "command": "ls -la"}}}, tid
    )
    assert begin == [{"type": "exec_command_begin", "call_id": "c1", "command": "ls -la"}]

    end = _normalize_v2_event(
        {"method": "item/completed", "params": {"threadId": tid, "item": {
            "type": "commandExecution", "id": "c1", "exitCode": 1,
            "aggregatedOutput": "boom"}}}, tid
    )
    assert end == [{"type": "exec_command_end", "call_id": "c1",
                    "exit_code": 1, "aggregated_output": "boom"}]

    mcp = _normalize_v2_event(
        {"method": "item/started", "params": {"threadId": tid, "item": {
            "type": "mcpToolCall", "id": "m1", "server": "mongodb", "tool": "find"}}}, tid
    )
    assert mcp[0]["type"] == "mcp_tool_call_begin"
    assert mcp[0]["invocation"]["server"] == "mongodb"

    mcp_end = _normalize_v2_event(
        {"method": "item/completed", "params": {"threadId": tid, "item": {
            "type": "mcpToolCall", "id": "m1", "status": "failed"}}}, tid
    )
    assert mcp_end == [{"type": "mcp_tool_call_end", "call_id": "m1",
                        "is_error": True, "result": ""}]


def test_normalize_v2_usage_rate_limits_and_completion():
    from agent.codex_runtime import _normalize_v2_event

    tid = "t-1"
    usage = _normalize_v2_event(
        {"method": "thread/tokenUsage/updated", "params": {"threadId": tid, "tokenUsage": {
            "total": {"inputTokens": 100, "cachedInputTokens": 40, "outputTokens": 7}}}}, tid
    )
    assert usage == [{"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7}}}]

    limits = _normalize_v2_event(
        {"method": "account/rateLimits/updated", "params": {"rateLimits": {
            "primary": {"usedPercent": 100, "resetsInSeconds": 900},
            "secondary": {"usedPercent": 10, "resetsInSeconds": 500000}}}}, tid
    )
    assert limits[0]["rate_limits"]["primary"] == {"used_percent": 100, "resets_in_seconds": 900}

    ok = _normalize_v2_event(
        {"method": "turn/completed", "params": {"threadId": tid,
         "turn": {"id": "u1", "status": "completed"}}}, tid
    )
    assert ok == [{"type": "task_complete"}]

    failed = _normalize_v2_event(
        {"method": "turn/completed", "params": {"threadId": tid,
         "turn": {"id": "u1", "status": "failed", "error": {"message": "nope"}}}}, tid
    )
    assert failed == [{"type": "error", "message": "nope"}]


def test_normalize_v2_error_events():
    from agent.codex_runtime import _normalize_v2_event

    tid = "t-1"
    # transient reconnects are swallowed — codex retries internally
    assert _normalize_v2_event(
        {"method": "error", "params": {"threadId": tid, "willRetry": True,
         "error": {"message": "Reconnecting... 2/5"}}}, tid
    ) == []

    fatal = _normalize_v2_event(
        {"method": "error", "params": {"threadId": tid, "willRetry": False, "error": {
            "message": "stream disconnected",
            "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 401}}}}}, tid
    )
    assert fatal[0]["type"] == "error"
    assert "HTTP 401" in fatal[0]["message"]

    # a 401-tagged message classifies as an auth error downstream
    from agent.codex_runtime import CodexAuthError, _classify_rpc_error
    assert isinstance(_classify_rpc_error({"message": fatal[0]["message"]}), CodexAuthError)


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


def _make_pool(tmp_path, monkeypatch, emails=("a@example.com", "b@example.com")):
    from agent.codex_pool import CodexClientPool

    monkeypatch.setenv("CODEX_USERS_DIR", str(tmp_path))
    for email in emails:
        _write_auth(tmp_path / email, email=email)
    pool = CodexClientPool(pool_size=3)
    pool._scan_accounts()
    return pool


def test_scan_accounts_finds_valid_dirs(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    (tmp_path / "empty@example.com").mkdir()  # no auth.json — excluded
    pool._scan_accounts()
    assert sorted(a["email"] for a in pool._accounts) == ["a@example.com", "b@example.com"]


def test_scan_accounts_respects_disabled(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    pool._scan_accounts(disabled_emails={"a@example.com"})
    assert [a["email"] for a in pool._accounts] == ["b@example.com"]


def test_round_robin_skips_cooldown(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    first = pool._next_account()["email"]
    second = pool._next_account()["email"]
    assert {first, second} == {"a@example.com", "b@example.com"}

    pool.mark_account_exhausted("a@example.com")
    for _ in range(4):
        assert pool._next_account()["email"] == "b@example.com"

    pool.mark_account_exhausted("b@example.com")
    assert pool._next_account() is None


def test_cooldown_expires(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch, emails=("a@example.com",))
    pool._account_cooldowns["a@example.com"] = time.time() - 1  # already expired
    assert pool._next_account()["email"] == "a@example.com"


def test_adaptive_cooldown_override(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch, emails=("a@example.com",))
    pool.mark_account_exhausted("a@example.com", cooldown_override=42)
    remaining = pool._account_cooldowns["a@example.com"] - time.time()
    assert 40 < remaining <= 42


def test_auth_failure_readmit_on_auth_json_change(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch, emails=("a@example.com",))
    pool.mark_account_exhausted("a@example.com", auth_error=True)
    assert pool._next_account() is None  # 1h cooldown

    # Simulate token refresh: auth.json rewritten with a new mtime
    auth_path = tmp_path / "a@example.com" / "auth.json"
    import os
    os.utime(auth_path, (time.time() + 10, time.time() + 10))

    account = pool._next_account()
    assert account is not None and account["email"] == "a@example.com"
    assert "a@example.com" not in pool._auth_failed_mtimes


def test_status_shape(tmp_path, monkeypatch):
    pool = _make_pool(tmp_path, monkeypatch)
    pool.mark_account_exhausted("a@example.com")
    status = pool.status()
    assert status["enabled"] is True
    assert status["pool_size"] == 3
    assert status["available"] == 0
    assert sorted(status["accounts"]) == ["a@example.com", "b@example.com"]
    assert status["accounts_on_cooldown"] == ["a@example.com"]
