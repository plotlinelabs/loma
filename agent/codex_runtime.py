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

Protocol: ``codex app-server`` speaks newline-delimited JSON-RPC over stdio.
The client sends ``initialize`` -> ``newConversation`` -> ``sendUserTurn`` and
consumes ``codex/event`` notifications (agent_message_delta, exec_command_*,
mcp_tool_call_*, token_count, task_complete, error). The CLI version should be
pinned in the image — the app-server protocol is not yet stability-guaranteed.
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
DEFAULT_CODEX_MODEL = "gpt-5.6-codex"
# Codex model family exposed by ChatGPT-plan auth (subscription models only —
# arbitrary OpenAI API models stay on the OpenCode/API-billing path).
DEFAULT_CODEX_MODEL_IDS = (
    "gpt-5.6-codex",
    "gpt-5.6",
    "gpt-5.5-codex-mini",
)

CODEX_CONNECT_TIMEOUT_SECONDS = int(os.environ.get("CODEX_CONNECT_TIMEOUT", "90"))
CODEX_TURN_TIMEOUT_SECONDS = int(os.environ.get("CODEX_TURN_TIMEOUT", "1800"))
CODEX_EVENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("CODEX_EVENT_IDLE_TIMEOUT", "300"))


def default_codex_model() -> str:
    return os.environ.get("CODEX_MODEL", DEFAULT_CODEX_MODEL)


def supported_codex_model_ids() -> tuple[str, ...]:
    raw = os.environ.get("CODEX_MODELS", "")
    if raw.strip():
        return tuple(m.strip() for m in raw.split(",") if m.strip())
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
        self.conversation_id: str | None = None
        self.last_rate_limits: dict | None = None
        # Pool bookkeeping (mirrors client._pool_account on ClaudeSDKClient)
        self._pool_account = account
        self._pool_model = self.model
        self._pool_ephemeral = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

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

    async def _read_loop(self) -> None:
        """Route stdout lines to pending request futures or the event queue."""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Codex worker non-JSON line: %.200s", line)
                    continue

                if "id" in msg and ("result" in msg or "error" in msg):
                    future = self._pending.get(msg["id"])
                    if future is not None and not future.done():
                        if "error" in msg:
                            future.set_exception(_classify_rpc_error(msg["error"]))
                        else:
                            future.set_result(msg.get("result") or {})
                    continue

                method = msg.get("method") or ""
                if "id" in msg:
                    # Server -> client request (approval prompts). approval_policy
                    # is "never", but auto-approve defensively if one arrives.
                    if method in ("execCommandApproval", "applyPatchApproval"):
                        await self._respond(msg["id"], {"decision": "approved"})
                    else:
                        await self._respond(msg["id"], {})
                    continue

                await self._events.put(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex worker read loop crashed (account=%s)", self.account.get("email"))
        finally:
            # Unblock any waiter
            await self._events.put({"method": "__closed"})

    # ── Lifecycle ────────────────────────────────────────────────────

    async def connect(self, mcp_servers: dict | None = None, system_prompt: str | None = None) -> None:
        """Spawn app-server, initialize, and pre-create the conversation.

        The conversation (which boots MCP servers) is created at warm time so
        acquire -> first token is near-instant, mirroring the Claude pool's
        pre-warmed MCP handshake.
        """
        write_managed_codex_config(self.account["config_dir"], mcp_servers or {})

        env = {**os.environ, "CODEX_HOME": self.account["config_dir"]}
        env.pop("OPENAI_API_KEY", None)  # subscription auth only — never fall back to API billing
        self._proc = await asyncio.create_subprocess_exec(
            "codex", "app-server",
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        await self._request(
            "initialize",
            {"clientInfo": {"name": "loma", "title": "Loma agent", "version": "1.0"}},
            timeout=30,
        )
        await self._send({"jsonrpc": "2.0", "method": "initialized"})

        params: dict = {
            "model": self.model,
            "cwd": str(PROJECT_ROOT),
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        if system_prompt:
            params["baseInstructions"] = system_prompt
        result = await self._request("newConversation", params, timeout=CODEX_CONNECT_TIMEOUT_SECONDS)
        self.conversation_id = result.get("conversationId") or result.get("conversation_id")
        if not self.conversation_id:
            raise CodexError(f"Codex newConversation returned no conversationId: {result}")

        # Subscribe to this conversation's event stream (newer app-server
        # protocol versions require an explicit listener; older ones don't).
        try:
            await self._request(
                "addConversationListener",
                {"conversationId": self.conversation_id},
                timeout=15,
            )
        except CodexError as e:
            logger.debug("addConversationListener unsupported/failed (ok on older CLIs): %s", e)

        logger.info(
            "Codex worker connected (account=%s, model=%s, conversation=%s)",
            self.account.get("email"), self.model, self.conversation_id,
        )

    async def disconnect(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    # ── Turn streaming ───────────────────────────────────────────────

    async def run_turn(self, prompt: str) -> AsyncGenerator[dict, None]:
        """Send one user turn and yield raw codex event msgs until completion."""
        if not self.conversation_id:
            raise CodexError("Codex worker has no conversation")

        items = [{"type": "text", "text": prompt, "data": {"text": prompt}}]
        try:
            await self._request(
                "sendUserTurn",
                {
                    "conversationId": self.conversation_id,
                    "items": items,
                    "cwd": str(PROJECT_ROOT),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"mode": "danger-full-access"},
                    "model": self.model,
                    "summary": "auto",
                },
                timeout=60,
            )
        except CodexError as e:
            if "method" in str(e).lower() and "not found" in str(e).lower():
                await self._request(
                    "sendUserMessage",
                    {"conversationId": self.conversation_id, "items": items},
                    timeout=60,
                )
            else:
                raise

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
                raise CodexError("Codex worker process exited mid-turn")

            event = _extract_codex_event(msg)
            if event is None:
                continue
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


def _extract_codex_event(msg: dict) -> dict | None:
    """Normalize a codex/event notification to its inner msg dict."""
    method = msg.get("method") or ""
    params = msg.get("params") or {}
    if method == "codex/event":
        inner = params.get("msg") or {}
        if isinstance(inner, dict) and inner.get("type"):
            return inner
        return None
    if method.startswith("codex/event/"):
        event = dict(params.get("msg") or params)
        event.setdefault("type", method.rsplit("/", 1)[1])
        return event
    return None


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
) -> AsyncGenerator[str | dict, None]:
    """Run one turn through the Codex account pool, yielding dashboard events.

    Same event contract as run_opencode_agent / the Claude SDK path.
    """
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

            elif etype == "agent_reasoning_delta":
                continue  # reasoning stays hidden behind status updates

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
