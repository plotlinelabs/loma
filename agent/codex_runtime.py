"""Codex (ChatGPT subscription) runtime support for dashboard chat and flows.

Mirrors the Claude account pool architecture: each team member connects their
own ChatGPT/Codex subscription from the Integrations page, credentials live in
per-user ``CODEX_HOME`` dirs (``$CODEX_USERS_DIR/<email>/auth.json``), and the
pool (agent/codex_pool.py) load-balances warm ``codex app-server`` workers
across accounts in round-robin.

This module keeps the Codex integration behind the same dashboard event
contract used by the Claude SDK and OpenCode paths (text deltas, tool_call /
tool_result, turn, account_info), so agent/client.py and the dashboard need no
new event handling.

Protocol: ``codex app-server`` speaks newline-delimited JSON-RPC over stdio
using the **v2 (thread/turn) API** — verified against the pinned CLI
(0.144.1):

    initialize -> thread/start -> turn/start

and streams server notifications:

    item/agentMessage/delta        assistant text deltas
    item/started / item/completed  items: agentMessage, commandExecution,
                                   mcpToolCall, webSearch, reasoning, ...
    thread/tokenUsage/updated      token usage
    account/rateLimits/updated     usage-window rate limits
    turn/started / turn/completed  turn lifecycle (completed carries status)
    error                          fatal or transient (willRetry) errors

The older v1 surface (``newConversation`` / ``sendUserTurn`` / ``codex/event``)
was removed from the CLI and is NOT supported here. The CLI version must stay
pinned in the image — the app-server protocol is not stability-guaranteed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CODEX_PROVIDER_PREFIX = "codex"
DEFAULT_CODEX_MODEL = "gpt-5.6-sol"
# Fallback Codex model family exposed by ChatGPT-plan auth (subscription
# models only — arbitrary OpenAI API models stay on the OpenCode/API-billing
# path). The live list is fetched from ``model/list`` at worker warm time and
# preferred over this tuple; override order: $CODEX_MODELS > model/list > this.
DEFAULT_CODEX_MODEL_IDS = (
    "gpt-6-astra",  # requires codex CLI >= 0.153 (older CLIs get a 400 from the server)
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4-mini",
)

CODEX_CONNECT_TIMEOUT_SECONDS = int(os.environ.get("CODEX_CONNECT_TIMEOUT", "90"))
CODEX_TURN_TIMEOUT_SECONDS = int(os.environ.get("CODEX_TURN_TIMEOUT", "1800"))
CODEX_EVENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("CODEX_EVENT_IDLE_TIMEOUT", "300"))

# codex app-server embeds full command/tool output in single JSON lines
# (a 100 KB command output arrives as one ~130 KB ``item/completed`` line),
# so the read loop splits lines manually instead of using readline() with
# asyncio's 64 KiB default limit. This is only a safety valve for truly
# pathological lines.
CODEX_MAX_LINE_BYTES = int(os.environ.get("CODEX_MAX_LINE_BYTES", str(64 * 1024 * 1024)))
_READ_CHUNK_BYTES = 65536
_STDERR_TAIL_BYTES = 16384


def default_codex_model() -> str:
    return os.environ.get("CODEX_MODEL", DEFAULT_CODEX_MODEL)


def supported_codex_model_ids(dynamic: list[str] | tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Model ids for the dashboard catalog.

    Precedence: $CODEX_MODELS env override > ``dynamic`` (the live
    ``model/list`` result cached by the pool) > the static fallback tuple.
    """
    raw = os.environ.get("CODEX_MODELS", "")
    if raw.strip():
        return tuple(m.strip() for m in raw.split(",") if m.strip())
    if dynamic:
        return tuple(dynamic)
    return DEFAULT_CODEX_MODEL_IDS


def selected_model_is_codex(selected_model: str | None) -> bool:
    """True for dashboard model ids in the ``codex/<model>`` provider format."""
    if not selected_model or "/" not in selected_model:
        return False
    return selected_model.split("/", 1)[0].lower() == CODEX_PROVIDER_PREFIX


def normalize_codex_model(selected_model: str | None) -> str | None:
    if not selected_model or not selected_model_is_codex(selected_model):
        return None
    return selected_model.split("/", 1)[1]


class CodexError(RuntimeError):
    """Raised when Codex cannot serve a request."""


class CodexAuthError(CodexError):
    """Raised when a Codex account's credentials are expired/revoked."""


class CodexRateLimitError(CodexError):
    """Raised when a Codex account hits its usage window limit."""

    def __init__(self, message: str, resets_in_seconds: int | None = None):
        super().__init__(message)
        self.resets_in_seconds = resets_in_seconds


# ── Credentials (auth.json) helpers ─────────────────────────────────────


def _decode_jwt_claims(token: str) -> dict:
    """Best-effort decode of a JWT payload without signature verification."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def read_codex_auth(config_dir: str | Path) -> dict | None:
    """Parse ``$CODEX_HOME/auth.json``. Returns {email, auth_method} or None.

    The Codex CLI rewrites auth.json on token refresh, so file presence +
    mtime is the same re-admit signal the Claude pool uses for
    .credentials.json.
    """
    auth_path = Path(config_dir) / "auth.json"
    if not auth_path.exists():
        return None
    try:
        data = json.loads(auth_path.read_text())
    except Exception:
        return None

    tokens = data.get("tokens") or {}
    id_token = tokens.get("id_token")
    if id_token:
        claims = _decode_jwt_claims(id_token)
        email = claims.get("email") or claims.get("preferred_username")
        plan = ((claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type"))
        return {
            "email": email,
            "auth_method": "chatgpt",
            "plan": plan,
            "last_refresh": data.get("last_refresh"),
        }
    if data.get("OPENAI_API_KEY"):
        return {"email": None, "auth_method": "api_key", "plan": None}
    return None


# ── Per-account managed config.toml ─────────────────────────────────────


def _toml_str(value: str) -> str:
    """Escape a string as a TOML basic string (JSON escaping is compatible)."""
    return json.dumps(str(value))


def claude_mcp_to_codex_toml(mcp_servers: dict) -> str:
    """Translate the app's MCP source of truth into Codex ``config.toml`` blocks.

    Stdio servers map directly to ``[mcp_servers.<name>]``. HTTP/SSE servers
    use Codex's streamable-HTTP MCP support (``url`` key); entries that don't
    translate are skipped with a log line (no silent drops).
    """
    lines: list[str] = []
    for name, conf in sorted((mcp_servers or {}).items()):
        if not isinstance(conf, dict):
            continue
        server_type = conf.get("type")
        if server_type in ("stdio", "local", None) and conf.get("command"):
            command = conf.get("command")
            args = conf.get("args") or []
            if isinstance(command, list):
                command, args = command[0], [*command[1:], *args]
            lines.append(f"[mcp_servers.{name}]")
            lines.append(f"command = {_toml_str(command)}")
            lines.append("args = [" + ", ".join(_toml_str(a) for a in args) + "]")
            env = conf.get("env") or conf.get("environment") or {}
            if env:
                lines.append(f"[mcp_servers.{name}.env]")
                for key, value in sorted(env.items()):
                    lines.append(f"{key} = {_toml_str(value)}")
            lines.append("")
        elif server_type in ("http", "sse", "remote", "streamable-http") and conf.get("url"):
            lines.append(f"[mcp_servers.{name}]")
            lines.append(f"url = {_toml_str(conf['url'])}")
            headers = conf.get("headers") or {}
            if headers:
                lines.append(f"[mcp_servers.{name}.http_headers]")
                for key, value in sorted(headers.items()):
                    lines.append(f"{key} = {_toml_str(value)}")
            lines.append("")
        else:
            logger.info("Codex config: skipping MCP server %s (untranslatable type=%s)", name, server_type)
    return "\n".join(lines)


def write_managed_codex_config(config_dir: str | Path, mcp_servers: dict) -> None:
    """Write a managed ``config.toml`` into an account's CODEX_HOME.

    Never touches ``auth.json``. Called at worker warm time so config changes
    (integration connect/disconnect) propagate on the next warm cycle.
    """
    config_path = Path(config_dir) / "config.toml"
    body = "\n".join([
        "# Managed by Loma — regenerated at worker warm time. Do not edit.",
        'approval_policy = "never"',
        'sandbox_mode = "danger-full-access"',
        "",
        claude_mcp_to_codex_toml(mcp_servers),
    ])
    config_path.write_text(body)
    config_path.chmod(0o600)


# ── Codex app-server worker ──────────────────────────────────────────────


class CodexWorker:
    """One warm ``codex app-server`` subprocess pinned to a single account.

    Workers are single-use (one conversation), mirroring the Claude pool's
    context-leakage hygiene: the pool discards them after release() and warms
    a replacement in the background.
    """

    def __init__(self, account: dict, model: str | None = None):
        self.account = account
        self.model = model or default_codex_model()
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_tail = b""
        self.thread_id: str | None = None
        self.available_models: list[dict] = []
        self.last_rate_limits: dict | None = None
        self._workspace = None
        self._mcp_proxy_tokens: list[str] = []
        # Pool bookkeeping (mirrors client._pool_account on ClaudeSDKClient)
        self._pool_account = account
        self._pool_model = self.model
        self._pool_ephemeral = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def conversation_id(self) -> str | None:
        """Back-compat alias for the v2 thread id."""
        return self.thread_id

    # ── Wire protocol ────────────────────────────────────────────────

    async def _send(self, payload: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise CodexError("Codex worker process is not running")
        self._proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict | None = None, timeout: int = 60) -> dict:
        self._next_id += 1
        req_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def _respond(self, req_id, result: dict) -> None:
        await self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _handle_line(self, raw: bytes) -> None:
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Codex worker non-JSON line: %.200s", line)
            return

        if "id" in msg and ("result" in msg or "error" in msg):
            future = self._pending.get(msg["id"])
            if future is not None and not future.done():
                if "error" in msg:
                    future.set_exception(_classify_rpc_error(msg["error"]))
                else:
                    future.set_result(msg.get("result") or {})
            return

        method = msg.get("method") or ""
        if "id" in msg:
            # Server -> client request. approval_policy is "never", but
            # auto-approve defensively if an approval request arrives.
            # v2 decisions use accept/decline; legacy uses approved.
            if method.endswith("requestApproval"):
                await self._respond(msg["id"], {"decision": "accept"})
            elif method in ("execCommandApproval", "applyPatchApproval"):
                await self._respond(msg["id"], {"decision": "approved"})
            else:
                await self._respond(msg["id"], {})
            return

        await self._events.put(msg)

    async def _read_loop(self) -> None:
        """Route stdout lines to pending request futures or the event queue.

        Reads in chunks and splits lines manually: ``readline()`` enforces
        asyncio's 64 KiB stream limit and raises on longer lines, but codex
        routinely emits ``item/completed`` notifications far larger than that
        (the full command/tool output is embedded in one JSON line). A crashed
        read loop here is indistinguishable from a dead worker to run_turn(),
        so it must only exit on real EOF.
        """
        assert self._proc is not None and self._proc.stdout is not None
        try:
            buf = b""
            drop_until_newline = False
            while True:
                chunk = await self._proc.stdout.read(_READ_CHUNK_BYTES)
                if not chunk:
                    if not drop_until_newline and buf.strip():
                        await self._handle_line(buf)
                    break
                buf += chunk
                while True:
                    newline = buf.find(b"\n")
                    if newline == -1:
                        break
                    line, buf = buf[:newline], buf[newline + 1:]
                    if drop_until_newline:
                        drop_until_newline = False
                        continue
                    await self._handle_line(line)
                if len(buf) > CODEX_MAX_LINE_BYTES:
                    logger.error(
                        "Codex worker line exceeded %d bytes; dropping it (account=%s)",
                        CODEX_MAX_LINE_BYTES, self.account.get("email"),
                    )
                    buf = b""
                    drop_until_newline = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex worker read loop crashed (account=%s)", self.account.get("email"))
        finally:
            # Unblock any waiter
            await self._events.put({"method": "__closed"})

    async def _stderr_loop(self) -> None:
        """Drain stderr into a bounded tail buffer for crash diagnostics.

        stderr used to go to DEVNULL, which made worker deaths undiagnosable
        in production (the app-server's panic/error output was discarded).
        """
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                chunk = await self._proc.stderr.read(8192)
                if not chunk:
                    break
                self._stderr_tail = (self._stderr_tail + chunk)[-_STDERR_TAIL_BYTES:]
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - diagnostics only
            pass

    def stderr_tail_text(self) -> str:
        return self._stderr_tail.decode("utf-8", errors="ignore").strip()

    def _closed_error(self) -> CodexError:
        """Build an accurate error for the read loop ending mid-turn."""
        returncode = self._proc.returncode if self._proc else None
        if returncode is None:
            message = (
                "Codex worker stream reader failed mid-turn "
                "(app-server process is still running — client-side read error)"
            )
        else:
            message = f"Codex worker process exited mid-turn (exit code {returncode})"
        tail = self.stderr_tail_text()
        if tail:
            message = f"{message}; stderr tail: {tail[-500:]}"
        return _classify_rpc_error({"message": message})

    # ── Lifecycle ────────────────────────────────────────────────────

    async def connect(self, mcp_servers: dict | None = None, system_prompt: str | None = None) -> None:
        """Spawn app-server, initialize, and pre-create the thread.

        The thread (which boots MCP servers) is created at warm time so
        acquire -> first token is near-instant, mirroring the Claude pool's
        pre-warmed MCP handshake.
        """
        # Isolated worker boundary: MCP servers are rewritten through the
        # credential gateway (stdio servers whose env would carry secrets
        # into the worker are disabled — fail closed), the worker gets a
        # fresh private workspace, and the backend environment is never
        # inherited. Workers are single-use, so per-worker == per-run.
        from broker import worker as worker_mod
        from broker.controller import broker_url, proxy_mcp_servers_for_worker
        from broker.gateway import gateway_base_url
        from broker.operations import ALL_TOOLS

        try:
            proxied_servers, proxy_tokens, disabled = proxy_mcp_servers_for_worker(
                mcp_servers or {},
            )
        except Exception:
            proxied_servers, proxy_tokens, disabled = {}, [], sorted(mcp_servers or {})
            logger.error(
                "Execution gateway unavailable — all MCP servers disabled for "
                "this Codex worker (fail closed): %s", disabled,
            )
        self._mcp_proxy_tokens = proxy_tokens
        from broker.controller import current_run, get_subscription_registry, run_worker_env_extra
        ctx = current_run.get()
        if ctx is None or not ctx.capability:
            raise CodexError("Subscription execution requires an authenticated run")
        self._workspace = worker_mod.create_workspace(prefix="codex")
        worker_mod.populate_tool_shims(self._workspace, sorted(ALL_TOOLS))
        # The real account home stays backend-only. The worker gets fresh
        # configuration and a revocable proxy reference, never auth.json.
        config_home = self._workspace / "codex-config"
        config_home.mkdir(mode=0o700)
        token = get_subscription_registry().register(
            "codex", Path(self.account["config_dir"]) / "auth.json", capability=ctx.capability)
        ctx.sub_proxy_tokens.append(token)
        self._sub_proxy_tokens = [token]
        write_managed_codex_config(config_home, proxied_servers)
        path = config_home / "config.toml"
        provider = '\n'.join([
            'model_provider = "loma_subscription"',
            '[model_providers.loma_subscription]',
            'name = "Loma subscription gateway"',
            'base_url = ' + _toml_str(f"{gateway_base_url()}/sub/{token}"),
            'env_key = "LOMA_RUN_CAPABILITY"',
            'wire_api = "responses"',
            'requires_openai_auth = false',
            'supports_websockets = false',
            '',
        ])
        # Top-level provider selection must precede any TOML table.
        path.write_text('model_provider = "loma_subscription"\n' + path.read_text()
                        + '\n' + provider.split('\n', 1)[1])
        worker_mod.grant_worker_access(config_home, workspace=self._workspace)
        env = worker_mod.build_worker_env(self._workspace, extra={
            "CODEX_HOME": str(config_home), **run_worker_env_extra(),
        })
        # Subscription auth only — the scrubbed env can never fall back to
        # API billing, and no provider API keys exist in the worker at all.
        self._proc = await worker_mod.spawn_worker(
            ["codex", "app-server"],
            workspace=self._workspace,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

        await self._request(
            "initialize",
            {"clientInfo": {"name": "loma", "title": "Loma agent", "version": "1.0"}},
            timeout=30,
        )

        params: dict = {
            "model": self.model,
            "cwd": str(self._workspace),
            "approvalPolicy": "never",
            "sandboxPolicy": {"mode": "danger-full-access"},
        }
        if system_prompt:
            params["baseInstructions"] = system_prompt
        result = await self._request("thread/start", params, timeout=CODEX_CONNECT_TIMEOUT_SECONDS)
        self.thread_id = ((result or {}).get("thread") or {}).get("id")
        if not self.thread_id:
            raise CodexError(f"Codex thread/start returned no thread id: {result}")

        # Best-effort live model catalog (surfaced in the dashboard picker).
        try:
            models = await self._request("model/list", {}, timeout=15)
            self.available_models = [
                {"id": m.get("id") or m.get("model"), "label": m.get("displayName") or m.get("id")}
                for m in (models.get("data") or [])
                if not m.get("hidden") and (m.get("id") or m.get("model"))
            ]
        except (CodexError, asyncio.TimeoutError) as e:
            logger.debug("Codex model/list unavailable (using fallback ids): %s", e)

        logger.info(
            "Codex worker connected (account=%s, model=%s, thread=%s)",
            self.account.get("email"), self.model, self.thread_id,
        )

    async def disconnect(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None
        # Revoke gateway proxy tokens and remove the private workspace.
        from broker import worker as worker_mod
        for token in getattr(self, "_sub_proxy_tokens", None) or []:
            from broker.controller import get_subscription_registry
            get_subscription_registry().revoke(token)
        for proxy_token in getattr(self, "_mcp_proxy_tokens", None) or []:
            try:
                from broker.controller import get_proxy_registry
                get_proxy_registry().revoke(proxy_token)
            except Exception:
                pass
        self._mcp_proxy_tokens = []
        workspace = getattr(self, "_workspace", None)
        if workspace is not None:
            worker_mod.cleanup_workspace(workspace)
            self._workspace = None

    # ── Turn streaming ───────────────────────────────────────────────

    async def run_turn(self, prompt: str) -> AsyncGenerator[dict, None]:
        """Send one user turn and yield normalized event dicts until completion."""
        if not self.thread_id:
            raise CodexError("Codex worker has no thread")

        await self._request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt}],
            },
            timeout=60,
        )

        deadline = time.monotonic() + CODEX_TURN_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexError(f"Codex turn timed out after {CODEX_TURN_TIMEOUT_SECONDS}s")
            try:
                msg = await asyncio.wait_for(
                    self._events.get(), timeout=min(remaining, CODEX_EVENT_IDLE_TIMEOUT_SECONDS)
                )
            except asyncio.TimeoutError:
                raise CodexError(
                    f"Timed out waiting for Codex events after {CODEX_EVENT_IDLE_TIMEOUT_SECONDS}s"
                )

            if msg.get("method") == "__closed":
                raise self._closed_error()

            for event in _normalize_v2_event(msg, self.thread_id):
                yield event
                if event.get("type") in ("task_complete", "error", "turn_aborted"):
                    return


def _classify_rpc_error(error: dict) -> CodexError:
    message = str((error or {}).get("message") or error)
    lowered = message.lower()
    if "401" in lowered or "unauthorized" in lowered or "token expired" in lowered or "refresh" in lowered:
        return CodexAuthError(message)
    if "429" in lowered or "rate limit" in lowered or "usage limit" in lowered or "quota" in lowered:
        return CodexRateLimitError(message)
    return CodexError(message)


# ── v2 notification normalization ────────────────────────────────────────

_SNAKE_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "cachedInputTokens": "cached_input_tokens",
    "outputTokens": "output_tokens",
    "reasoningOutputTokens": "reasoning_output_tokens",
    "totalTokens": "total_tokens",
}

_SNAKE_WINDOW_KEYS = {
    "usedPercent": "used_percent",
    "resetsInSeconds": "resets_in_seconds",
    "windowMinutes": "window_minutes",
}


def _snake_usage(usage: dict) -> dict:
    out = {}
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            out[_SNAKE_USAGE_KEYS.get(key, key)] = value
    return out


def _snake_rate_limits(rate_limits: dict) -> dict:
    out = {}
    for window in ("primary", "secondary"):
        info = (rate_limits or {}).get(window)
        if isinstance(info, dict):
            out[window] = {_SNAKE_WINDOW_KEYS.get(k, k): v for k, v in info.items()}
    return out


def _item_text(item: dict) -> str:
    """Extract text from a v2 item (``text`` field or ``content`` parts)."""
    if item.get("text"):
        return str(item["text"])
    parts = item.get("content") or []
    if isinstance(parts, list):
        return "".join(
            str(p.get("text") or "") for p in parts if isinstance(p, dict)
        )
    return ""


def _normalize_v2_event(msg: dict, thread_id: str | None) -> list[dict]:
    """Translate app-server v2 notifications into the internal event contract.

    Internal vocabulary (consumed by run_codex_agent): agent_message_delta,
    agent_message, exec_command_begin/end, mcp_tool_call_begin/end,
    web_search_begin, token_count, task_complete, turn_aborted, error.
    """
    method = msg.get("method") or ""
    params = msg.get("params") or {}

    # Events for other threads (shouldn't happen — one thread per worker).
    event_thread = params.get("threadId")
    if thread_id and event_thread and event_thread != thread_id:
        return []

    if method == "item/agentMessage/delta":
        delta = params.get("delta") or params.get("text") or ""
        return [{"type": "agent_message_delta", "delta": delta}] if delta else []

    if method in ("item/started", "item/completed"):
        item = params.get("item") or {}
        itype = item.get("type") or ""
        call_id = item.get("id") or ""
        if method == "item/started":
            if itype == "commandExecution":
                return [{"type": "exec_command_begin", "call_id": call_id,
                         "command": item.get("command")}]
            if itype == "mcpToolCall":
                return [{"type": "mcp_tool_call_begin", "call_id": call_id,
                         "invocation": {"server": item.get("server"),
                                        "tool": item.get("tool"),
                                        "arguments": item.get("arguments")}}]
            if itype == "webSearch":
                return [{"type": "web_search_begin", "call_id": call_id,
                         "query": item.get("query")}]
            return []
        # item/completed
        if itype == "agentMessage":
            text = _item_text(item)
            return [{"type": "agent_message", "message": text}] if text else []
        if itype == "commandExecution":
            exit_code = item.get("exitCode")
            return [{"type": "exec_command_end", "call_id": call_id,
                     "exit_code": exit_code if exit_code is not None else 0,
                     "aggregated_output": item.get("aggregatedOutput") or ""}]
        if itype == "mcpToolCall":
            status = str(item.get("status") or "").lower()
            return [{"type": "mcp_tool_call_end", "call_id": call_id,
                     "is_error": status in ("failed", "error"),
                     "result": item.get("result") or ""}]
        return []

    if method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage") or params.get("usage") or {}
        total = usage.get("total") or usage.get("totalTokenUsage") or usage
        if not isinstance(total, dict):
            return []
        return [{"type": "token_count",
                 "info": {"total_token_usage": _snake_usage(total)}}]

    if method == "account/rateLimits/updated":
        rate_limits = params.get("rateLimits") or params
        normalized = _snake_rate_limits(rate_limits)
        if not normalized:
            return []
        return [{"type": "token_count", "info": {}, "rate_limits": normalized}]

    if method == "turn/completed":
        turn = params.get("turn") or {}
        events: list[dict] = []
        usage = turn.get("usage") or params.get("usage")
        if isinstance(usage, dict):
            events.append({"type": "token_count",
                           "info": {"total_token_usage": _snake_usage(usage)}})
        status = str(turn.get("status") or "").lower()
        if status == "failed":
            error = turn.get("error") or {}
            message = (error.get("message") if isinstance(error, dict) else str(error)) or "Codex turn failed"
            events.append({"type": "error", "message": message})
        elif status in ("interrupted", "aborted", "cancelled"):
            events.append({"type": "turn_aborted"})
        else:
            events.append({"type": "task_complete"})
        return events

    if method == "error":
        error = params.get("error") if isinstance(params.get("error"), dict) else {}
        message = error.get("message") or params.get("message") or "Codex error"
        if params.get("willRetry"):
            # Transient (e.g. stream reconnect) — codex retries internally.
            logger.debug("Codex transient error (retrying): %s", message)
            return []
        # Surface the HTTP status so _classify_rpc_error can route
        # auth (401) vs rate-limit (429) handling from the message text.
        info = error.get("codexErrorInfo")
        if isinstance(info, dict):
            for detail in info.values():
                if isinstance(detail, dict) and detail.get("httpStatusCode"):
                    message = f"{message} (HTTP {detail['httpStatusCode']})"
                    break
        details = error.get("additionalDetails")
        if details:
            message = f"{message}: {details}"
        return [{"type": "error", "message": message}]

    return []


def _rate_limit_cooldown_seconds(event: dict) -> int | None:
    """Adaptive cooldown: honor the reported usage-window reset when present."""
    rate_limits = event.get("rate_limits") or {}
    resets: list[int] = []
    for window in ("primary", "secondary"):
        info = rate_limits.get(window) or {}
        used = info.get("used_percent")
        reset = info.get("resets_in_seconds")
        if used is not None and used >= 100 and reset:
            resets.append(int(reset))
    return min(resets) if resets else None


# ── Dashboard-facing turn runner ─────────────────────────────────────────


async def run_codex_agent(
    *,
    full_prompt: str,
    selected_model: str,
    observer=None,
    include_steps: bool = False,
    source: str = "dashboard",
    user_email: str | None = None,
    run_ctx=None,
) -> AsyncGenerator[str | dict, None]:
    """Run one turn through the Codex account pool, yielding dashboard events.

    Same event contract as run_opencode_agent / the Claude SDK path. Workers
    are single-use sandboxed subprocesses with private workspaces and a
    scrubbed environment (see CodexWorker.connect); the run capability from
    ``run_ctx`` reaches tools via the prompt, like the Claude runtime.
    """
    del run_ctx  # capability is prompt-delivered; nothing else is needed here
    from agent.codex_pool import get_codex_pool

    model_id = normalize_codex_model(selected_model) or default_codex_model()
    pool = get_codex_pool()

    worker = await pool.acquire(model=model_id)
    account_email = worker.account.get("email")

    if observer and account_email:
        await observer.record_account(account_email)

    pool_status = pool.status()
    if include_steps:
        yield {
            "type": "account_info",
            "runtime": "codex",
            "provider": "openai-chatgpt",
            "model": model_id,
            "account_type": "round_robin",
            "account_email": account_email,
            "pool_available": pool_status["available"],
            "pool_size": pool_status["pool_size"],
        }

    turn_count = 1
    if observer:
        observer.turn_count = turn_count
    if include_steps:
        yield {"type": "turn", "turn_number": turn_count}

    last_text = ""
    streamed_text = ""
    total_usage = {"input_tokens": 0, "output_tokens": 0,
                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    emitted_tool_results: set[str] = set()
    failed: Exception | None = None

    try:
        async for event in worker.run_turn(full_prompt):
            etype = event.get("type")

            if etype == "agent_message_delta":
                delta = event.get("delta") or ""
                if delta:
                    streamed_text += delta
                    yield {"type": "text", "text": delta, "append": True}

            elif etype == "agent_message":
                message = event.get("message") or ""
                if message:
                    last_text = message
                    if observer:
                        await observer.record_text(turn_count, message)
                    if not streamed_text:
                        yield {"type": "text", "text": message, "append": True}
                    streamed_text = ""

            elif etype in ("exec_command_begin", "mcp_tool_call_begin", "web_search_begin"):
                call_id = event.get("call_id") or ""
                if etype == "exec_command_begin":
                    command = event.get("command")
                    if isinstance(command, list):
                        command = " ".join(str(c) for c in command)
                    tool_name, tool_input = "Bash", str(command or "")[:200]
                elif etype == "mcp_tool_call_begin":
                    invocation = event.get("invocation") or {}
                    tool_name = f"mcp__{invocation.get('server', '?')}__{invocation.get('tool', '?')}"
                    tool_input = str(invocation.get("arguments") or "")[:200]
                else:
                    tool_name, tool_input = "WebSearch", str(event.get("query") or "")[:200]
                if observer:
                    await observer.record_tool_call(turn_count, tool_name, call_id, tool_input)
                if include_steps:
                    yield {"type": "tool_call", "name": tool_name, "tool_use_id": call_id, "input": tool_input}
                    yield {"type": "status", "message": f"Running {tool_name}"}

            elif etype in ("exec_command_end", "mcp_tool_call_end"):
                call_id = event.get("call_id") or ""
                if call_id in emitted_tool_results:
                    continue
                emitted_tool_results.add(call_id)
                is_error = bool(event.get("exit_code")) or bool(event.get("is_error"))
                output = event.get("aggregated_output") or event.get("stdout") or event.get("result") or ""
                if observer:
                    await observer.record_tool_result(call_id, is_error, str(output))
                if include_steps:
                    yield {"type": "tool_result", "tool_use_id": call_id, "is_error": is_error}

            elif etype == "token_count":
                info = event.get("info") or {}
                usage = info.get("total_token_usage") or info.get("last_token_usage") or {}
                if usage:
                    total_usage["input_tokens"] = int(usage.get("input_tokens") or 0)
                    total_usage["output_tokens"] = int(usage.get("output_tokens") or 0)
                    total_usage["cache_read_input_tokens"] = int(usage.get("cached_input_tokens") or 0)
                worker.last_rate_limits = event.get("rate_limits") or worker.last_rate_limits
                cooldown = _rate_limit_cooldown_seconds(event)
                if cooldown:
                    pool.mark_account_exhausted(account_email, cooldown_override=cooldown)

            elif etype == "task_complete":
                final = event.get("last_agent_message") or ""
                if final:
                    last_text = final
                break

            elif etype == "error":
                raise _classify_rpc_error({"message": event.get("message") or "Codex error"})

    except CodexAuthError as e:
        failed = e
        pool.mark_account_exhausted(account_email, auth_error=True)
        raise
    except CodexRateLimitError as e:
        failed = e
        pool.mark_account_exhausted(account_email, cooldown_override=e.resets_in_seconds)
        raise
    except Exception as e:
        failed = e
        raise
    finally:
        await pool.release(worker)
        if observer:
            if failed is not None:
                await observer.record_error(str(failed))
            else:
                usage_payload = total_usage if any(total_usage.values()) else None
                # ChatGPT-plan usage has no per-token cost — report tokens only.
                await observer.record_usage(usage_payload, None)
                await observer.finish(final_response=last_text)

    if not last_text and not streamed_text:
        yield "I didn't generate a response. Please try again."
