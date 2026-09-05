"""OpenCode runtime support for dashboard chat and selected flow runs.

This module intentionally keeps the OpenCode integration behind the same
dashboard event contract used by the Claude SDK path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import socket
import secrets
import tempfile
import time
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import urlparse

import aiohttp

from agent.prompt import build_pooled_system_prompt
from broker import worker as worker_mod

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Neutral scratch workspace for catalog-only servers (never runs agent code).
_catalog_workspace: Path | None = None


def _catalog_dir() -> str:
    global _catalog_workspace
    if _catalog_workspace is None or not _catalog_workspace.exists():
        _catalog_workspace = worker_mod.create_workspace(prefix="opencode-catalog")
    return str(_catalog_workspace)
DEFAULT_OPENCODE_HOST = "127.0.0.1"
DEFAULT_OPENCODE_PORT = 4097
OPENCODE_START_TIMEOUT_SECONDS = 20
# Wall-clock cap for a whole turn. Chat turns default to 0 (no cap) so long
# implementation work (installs, builds, test runs) is only guarded by the idle
# watchdog, matching the Claude/Codex runtimes. Flows keep a ceiling by default.
OPENCODE_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_REQUEST_TIMEOUT", "0"))
OPENCODE_FLOW_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_FLOW_REQUEST_TIMEOUT", "1800"))
OPENCODE_EVENT_BUFFER_LIMIT_BYTES = int(
    os.environ.get("OPENCODE_EVENT_BUFFER_LIMIT_BYTES", str(64 * 1024 * 1024))
)
OPENCODE_EVENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_EVENT_IDLE_TIMEOUT", "480"))
OPENCODE_FLOW_EVENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_FLOW_EVENT_IDLE_TIMEOUT", "900"))
OPENCODE_CONFIG_TTL_SECONDS = int(os.environ.get("OPENCODE_CONFIG_TTL_SECONDS", "30"))
OPENCODE_MODEL_CATALOG_TTL_SECONDS = int(os.environ.get("OPENCODE_MODEL_CATALOG_TTL_SECONDS", "60"))
OPENCODE_PREWARM_POOL_SIZE = int(os.environ.get("OPENCODE_PREWARM_POOL_SIZE", "2"))
OPENCODE_PREWARM_WAIT_SECONDS = float(os.environ.get("OPENCODE_PREWARM_WAIT_SECONDS", "0.5"))
OPENCODE_TURN_MAX_ATTEMPTS = max(1, int(os.environ.get("OPENCODE_TURN_MAX_ATTEMPTS", "2")))
OPENCODE_MAX_SERVERS = max(1, int(os.environ.get("OPENCODE_MAX_SERVERS", "3")))
OPENCODE_SERVER_LOG_DIR = Path(
    os.environ.get(
        "OPENCODE_SERVER_LOG_DIR", str(Path(tempfile.gettempdir()) / "loma-opencode-logs")
    )
)
OPENCODE_SERVER_LOG_TAIL_BYTES = 4096
OPENCODE_DEFAULT_PREWARM_MODELS = "opencode-go/deepseek-v4-flash"
OPENCODE_AUTO_APPROVE_PERMISSIONS = os.environ.get("OPENCODE_AUTO_APPROVE_PERMISSIONS", "1").lower() not in {
    "0",
    "false",
    "no",
}
OPENCODE_WARMUP_PROMPT = (
    "Internal latency warmup for the dashboard agent. Reply exactly READY. "
    "Do not use tools. Ignore this warmup exchange in future user-facing answers."
)

class _OpenCodeServer:
    """One managed `opencode serve` process bound to a specific config hash."""

    def __init__(
        self,
        *,
        config_hash: str,
        config_home: Path,
        host: str,
        port: int,
        process: asyncio.subprocess.Process | None,
        log_path: Path | None = None,
        log_file=None,
    ) -> None:
        self.config_hash = config_hash
        self.config_home = config_home
        self.host = host
        self.port = port
        self.process = process
        self.log_path = log_path
        self.log_file = log_file
        self.active_turns = 0
        self.last_used_at = time.monotonic()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def touch(self) -> None:
        self.last_used_at = time.monotonic()

    def log_tail(self) -> str:
        """Last chunk of the server's captured stdout/stderr for diagnostics."""
        if not self.log_path:
            return ""
        try:
            data = self.log_path.read_bytes()
        except OSError:
            return ""
        return data[-OPENCODE_SERVER_LOG_TAIL_BYTES:].decode("utf-8", errors="ignore").strip()

    async def terminate(self) -> None:
        _managed_server_passwords.pop(self.base_url, None)
        if self.is_alive:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.log_file is not None:
            try:
                self.log_file.close()
            except OSError:
                pass
            self.log_file = None


# Managed OpenCode servers keyed by config hash. Running one server per config
# means a user bringing different MCP connectors gets their own server instead
# of restarting (and killing) everyone else's in-flight runs.
_opencode_servers: dict[str, _OpenCodeServer] = {}
_opencode_server_lock: asyncio.Lock = asyncio.Lock()
_EXTERNAL_SERVER_HASH = "external"
_opencode_mcp_names: set[str] = set()
# Config cache keyed by the sorted override-name key -> (home, hash, checked_at).
_opencode_config_cache: dict[str, tuple[Path, str, float]] = {}
_opencode_model_catalog: dict | None = None
_opencode_model_catalog_checked_at: float = 0.0
# Session reuse cache keyed by (config_hash, conversation_id, model).
_opencode_session_cache: dict[tuple[str, str, str], str] = {}
# Warm session pools keyed by (config_hash, model).
_opencode_warm_sessions: dict[tuple[str, str], list[str]] = {}
_opencode_prewarm_tasks: dict[tuple[str, str], asyncio.Task] = {}
_opencode_prewarm_lock = asyncio.Lock()


async def reset_opencode_runtime(reason: str = "") -> None:
    """Clear OpenCode caches and restart managed servers on next use."""
    global _opencode_model_catalog, _opencode_model_catalog_checked_at

    _opencode_session_cache.clear()
    _opencode_warm_sessions.clear()
    _opencode_config_cache.clear()
    _opencode_model_catalog = None
    _opencode_model_catalog_checked_at = 0.0

    for task in list(_opencode_prewarm_tasks.values()):
        if not task.done():
            task.cancel()
    _opencode_prewarm_tasks.clear()

    servers = list(_opencode_servers.values())
    _opencode_servers.clear()
    for server in servers:
        if server.is_alive:
            logger.info(
                "Stopping OpenCode server %s%s", server.base_url, f" ({reason})" if reason else ""
            )
        await server.terminate()


def _opencode_system_prompt() -> str:
    """Return the shared system prompt used by agent runtimes."""
    return build_pooled_system_prompt()


def _opencode_permission_config() -> dict:
    """Permission config for the isolated dashboard OpenCode runtime.

    Dashboard chat has no permission approval UI yet. The OpenCode server is
    already isolated to this app-managed config, so selected OpenCode runs use
    full tool permissions by default.
    """
    return {
        "*": {"*": "allow"},
        "external_directory": {"*": "allow"},
    }


def _opencode_session_permission_rules() -> list[dict]:
    return [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "external_directory", "pattern": "*", "action": "allow"},
    ]


class OpenCodeError(RuntimeError):
    """Raised when OpenCode cannot serve a dashboard chat request."""


class OpenCodeModelError(OpenCodeError):
    """Raised when the model/provider itself reports an error for a message."""


def _is_retryable_turn_error(exc: BaseException) -> bool:
    """Whether a failed turn is worth one retry on a fresh session.

    Model-reported errors (bad request, provider refusal) are not transient;
    transport-level failures (idle timeout, dropped stream, server restart,
    connection errors) usually are.
    """
    if isinstance(exc, OpenCodeModelError):
        return False
    return isinstance(exc, (OpenCodeError, aiohttp.ClientError, asyncio.TimeoutError))


def _format_opencode_error(error: object) -> str:
    if isinstance(error, dict):
        data = error.get("data")
        if isinstance(data, dict):
            message = data.get("message")
            if message:
                return str(message)

            response_body = data.get("responseBody")
            if isinstance(response_body, str):
                parsed = _format_opencode_error(response_body)
                if parsed != response_body:
                    return parsed

        nested_error = error.get("error")
        if isinstance(nested_error, dict):
            message = nested_error.get("message")
            if message:
                return str(message)

        message = error.get("message")
        if message:
            return str(message)
        name = error.get("name")
        if name:
            return str(name)

    if isinstance(error, str):
        try:
            parsed = json.loads(error)
        except json.JSONDecodeError:
            return error[:1000]
        return _format_opencode_error(parsed)

    return str(error)[:1000]


def _configured_server_url() -> str:
    explicit = os.environ.get("OPENCODE_SERVER_URL")
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("OPENCODE_HOST", DEFAULT_OPENCODE_HOST)
    port = int(os.environ.get("OPENCODE_PORT", str(DEFAULT_OPENCODE_PORT)))
    return f"http://{host}:{port}"


_managed_server_passwords: dict[str, str] = {}


def _auth(base_url: str | None = None) -> aiohttp.BasicAuth | None:
    if base_url in _managed_server_passwords:
        return aiohttp.BasicAuth("opencode", _managed_server_passwords[base_url])
    # Only explicitly configured external servers use operator credentials.
    if base_url is not None and base_url.rstrip("/") != _configured_server_url():
        return None
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if not password:
        return None
    return aiohttp.BasicAuth(os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"), password)


async def _request_json(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: int | None = None,
    base_url: str | None = None,
) -> dict:
    if base_url is None:
        base_url = await ensure_opencode_server()
    request_timeout = aiohttp.ClientTimeout(total=timeout or OPENCODE_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=request_timeout, auth=_auth(base_url)) as session:
        async with session.request(
            method,
            f"{base_url}{path}",
            json=json_body,
            params=params,
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise OpenCodeError(
                    f"OpenCode {method} {path} failed ({resp.status}): {_format_opencode_error(text)}"
                )
            if not text:
                return {}
            try:
                return await resp.json()
            except Exception as exc:
                raise OpenCodeError(f"OpenCode returned non-JSON for {method} {path}: {text[:500]}") from exc


async def _health_check(base_url: str, directory: str | None = None) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout, auth=_auth(base_url)) as session:
            async with session.get(
                f"{base_url}/config/providers",
                params={"directory": directory or _catalog_dir()},
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


def _find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _pick_server_port(host: str) -> int:
    """Prefer the configured port when free; otherwise grab an ephemeral one."""
    preferred = int(os.environ.get("OPENCODE_PORT", str(DEFAULT_OPENCODE_PORT)))
    if any(server.port == preferred for server in _opencode_servers.values()):
        return _find_free_port(host)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, preferred))
        except OSError:
            return _find_free_port(host)
    return preferred


def _open_server_log(port: int) -> tuple[Path | None, object | None]:
    """Open a per-server log file so crashes are no longer silent."""
    try:
        OPENCODE_SERVER_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = OPENCODE_SERVER_LOG_DIR / f"opencode-server-{port}.log"
        log_file = open(log_path, "ab")
        return log_path, log_file
    except OSError:
        logger.warning("Could not open OpenCode server log file; falling back to /dev/null")
        return None, None


def _drop_server_state(config_hash: str) -> None:
    """Forget a server and every cache entry tied to its config hash."""
    _opencode_servers.pop(config_hash, None)
    for key in [key for key in _opencode_session_cache if key[0] == config_hash]:
        _opencode_session_cache.pop(key, None)
    for key in [key for key in _opencode_warm_sessions if key[0] == config_hash]:
        _opencode_warm_sessions.pop(key, None)
    for key in [key for key in _opencode_prewarm_tasks if key[0] == config_hash]:
        task = _opencode_prewarm_tasks.pop(key)
        if not task.done():
            task.cancel()


async def _retire_stale_servers(keep_hash: str) -> None:
    """Reap dead servers and idle servers beyond the cap.

    Servers with in-flight turns are never terminated here — that is the whole
    point of running one server per config instead of restarting the shared
    one whenever the config changes.
    """
    for config_hash, server in list(_opencode_servers.items()):
        if config_hash != keep_hash and not server.is_alive:
            await server.terminate()
            _drop_server_state(config_hash)

    idle = sorted(
        (
            (config_hash, server)
            for config_hash, server in _opencode_servers.items()
            if config_hash != keep_hash and server.active_turns == 0
        ),
        key=lambda item: item[1].last_used_at,
    )
    excess = len(_opencode_servers) - OPENCODE_MAX_SERVERS
    for config_hash, server in idle[: max(0, excess)]:
        logger.info("Retiring idle OpenCode server %s (config rotation)", server.base_url)
        await server.terminate()
        _drop_server_state(config_hash)


async def _ensure_server_instance(
    user_mcp_overrides: dict | None = None,
) -> _OpenCodeServer:
    """Return a healthy server for this config, starting one if needed.

    Servers are keyed by config hash: a config change starts a *new* server on
    a fresh port instead of restarting the shared one, so in-flight runs from
    other users/configs are never killed mid-turn. If OPENCODE_SERVER_URL is
    explicitly set, we assume that external server is intentionally managed by
    the operator.
    """
    if os.environ.get("OPENCODE_SERVER_URL"):
        base_url = _configured_server_url()
        if not await _health_check(base_url):
            raise OpenCodeError(f"Configured OPENCODE_SERVER_URL is not reachable: {base_url}")
        server = _opencode_servers.get(_EXTERNAL_SERVER_HASH)
        if server is None:
            parsed = urlparse(base_url)
            server = _OpenCodeServer(
                config_hash=_EXTERNAL_SERVER_HASH,
                config_home=PROJECT_ROOT,
                host=parsed.hostname or DEFAULT_OPENCODE_HOST,
                port=parsed.port or 80,
                process=None,
            )
            _opencode_servers[_EXTERNAL_SERVER_HASH] = server
        server.touch()
        return server

    config_home, config_hash = await _write_managed_opencode_config(
        user_mcp_overrides=user_mcp_overrides,
    )

    async with _opencode_server_lock:
        server = _opencode_servers.get(config_hash)
        if server is not None and server.is_alive and await _health_check(server.base_url):
            server.touch()
            return server
        if server is not None:
            tail = server.log_tail()
            if tail:
                logger.warning(
                    "OpenCode server %s is unhealthy; last output:\n%s", server.base_url, tail
                )
            await server.terminate()
            _drop_server_state(config_hash)

        opencode_bin = shutil.which("opencode")
        if not opencode_bin:
            raise OpenCodeError("opencode binary not found on PATH")

        host = os.environ.get("OPENCODE_HOST", DEFAULT_OPENCODE_HOST)
        port = _pick_server_port(host)
        log_path, log_file = _open_server_log(port)
        logger.info(
            "Starting OpenCode server on %s:%d (config=%s log=%s)",
            host,
            port,
            config_hash[:12],
            log_path or "/dev/null",
        )
        process = await _spawn_opencode_server_process(
            opencode_bin, host=host, port=port, config_home=config_home,
            workspace=_catalog_dir(), log_file=log_file,
        )
        server = _OpenCodeServer(
            config_hash=config_hash,
            config_home=config_home,
            host=host,
            port=port,
            process=process,
            log_path=log_path,
            log_file=log_file,
        )
        _opencode_servers[config_hash] = server
        await _retire_stale_servers(keep_hash=config_hash)

    deadline = asyncio.get_running_loop().time() + OPENCODE_START_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if await _health_check(server.base_url):
            server.touch()
            return server
        if not server.is_alive:
            tail = server.log_tail()
            raise OpenCodeError(
                f"OpenCode server exited with code {server.process.returncode}"
                + (f"; last output:\n{tail}" if tail else "")
            )
        await asyncio.sleep(0.5)

    tail = server.log_tail()
    raise OpenCodeError(
        f"OpenCode server did not become ready at {server.base_url}"
        + (f"; last output:\n{tail}" if tail else "")
    )


async def ensure_opencode_server(
    user_mcp_overrides: dict | None = None,
) -> str:
    """Return a reachable OpenCode server URL, starting one if needed."""
    server = await _ensure_server_instance(user_mcp_overrides=user_mcp_overrides)
    return server.base_url


def _prepare_opencode_data_home(config_home: Path,
                                workspace: str | Path | None = None) -> Path:
    """Empty per-worker provider store. Real provider auth stays backend-only.

    Only gateway-configured providers are available to isolated runs.
    Subscription-only provider plugins need separate reviewed adapters.
    """
    data_home = config_home / "share"
    target_dir = data_home / "opencode"
    target_dir.mkdir(parents=True, exist_ok=True)
    # Refuse stale shared catalog data rather than loading legacy auth.
    if (target_dir / "auth.json").exists():
        raise OpenCodeError("Legacy OpenCode credentials must be removed from the catalog cache")
    worker_mod.grant_worker_access(data_home, workspace=workspace)
    return data_home


async def _spawn_opencode_server_process(
    opencode_bin: str,
    *,
    host: str,
    port: int,
    config_home: Path,
    workspace: str,
    log_file,
) -> asyncio.subprocess.Process:
    """Spawn `opencode serve` as an isolated worker.

    The backend environment is NOT inherited: the server receives only the
    scrubbed worker env plus its isolated XDG config/data homes. Model
    provider API keys stay on the backend and are reached through the
    credential gateway (see _provider_gateway_overrides).
    """
    data_home = _prepare_opencode_data_home(config_home, workspace=workspace)
    # The sandboxed (non-root) server must read its isolated config home.
    worker_mod.grant_worker_access(config_home, workspace=workspace)
    from broker.controller import run_worker_env_extra

    base_url = f"http://{host}:{port}"
    password = "loma_ocserver_" + secrets.token_urlsafe(32)
    _managed_server_passwords[base_url] = password
    env = worker_mod.build_worker_env(workspace, extra={
        "OPENCODE_SERVER_USERNAME": "opencode",
        "OPENCODE_SERVER_PASSWORD": password,
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        **run_worker_env_extra(),
    })
    try:
        return await worker_mod.spawn_worker(
            [opencode_bin, "serve", "--port", str(port), "--hostname", host],
            workspace=workspace,
            env=env,
            stdout=log_file if log_file is not None else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT if log_file is not None else asyncio.subprocess.DEVNULL,
        )
    except BaseException:
        _managed_server_passwords.pop(base_url, None)
        raise


def _provider_gateway_overrides(run_capability: str | None) -> dict:
    """Route env-key model providers through the credential gateway.

    Providers whose API keys live in the backend env are configured with a
    gateway baseURL and the run capability as the apiKey; the gateway admits
    the call via the broker and injects the real key server-side. Providers
    without a configured gateway credential are unavailable, never loaded from a worker auth file.
    """
    if not run_capability:
        return {}
    from broker.gateway import configured_model_providers, gateway_base_url

    base = gateway_base_url()
    paths = {
        "anthropic": "/model/anthropic/v1",
        "openai": "/model/openai/v1",
        "openrouter": "/model/openrouter/v1",
        "opencode": "/model/opencode",
    }
    overrides: dict = {}
    for provider in configured_model_providers():
        path = paths.get(provider)
        if path:
            overrides[provider] = {
                "options": {"baseURL": base + path, "apiKey": run_capability},
            }
    return overrides


async def _start_dedicated_server(
    user_mcp_overrides: dict | None,
    run_ctx,
) -> _OpenCodeServer:
    """Start a per-run, sandboxed OpenCode server (isolated worker).

    Every agent run gets its own server process with a fresh workspace and
    a per-run config: MCP servers are rewritten through the credential
    gateway with revocable proxy tokens, model providers go through the
    gateway with the run capability, and stdio MCP servers are disabled
    (fail closed).
    """
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        raise OpenCodeError("opencode binary not found on PATH")

    workspace = getattr(run_ctx, "workspace", None)
    if workspace is None:
        workspace = worker_mod.create_workspace(prefix="opencode")
    workspace = str(workspace)

    app_config = await _load_current_agent_config()
    mcp_servers = dict(app_config.get("mcp_servers", {}))
    if user_mcp_overrides:
        mcp_servers.update(user_mcp_overrides)

    if run_ctx and run_ctx.user_email:
        from agent.client import get_excluded_integrations_for_user
        excluded = await get_excluded_integrations_for_user(run_ctx.user_email)
        mcp_servers = {name: cfg for name, cfg in mcp_servers.items() if name not in excluded}
    else:
        mcp_servers = {}

    proxy_tokens: list[str] = []
    try:
        from broker.controller import proxy_mcp_servers_for_worker

        proxied, proxy_tokens, disabled = proxy_mcp_servers_for_worker(mcp_servers)
    except Exception:
        proxied, disabled = {}, sorted(mcp_servers)
        logger.error(
            "Execution gateway unavailable — all MCP servers disabled for "
            "this OpenCode run (fail closed): %s", disabled,
        )
    global _opencode_mcp_names
    _opencode_mcp_names = set(proxied.keys())

    opencode_config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": claude_mcp_to_opencode(proxied),
        "permission": _opencode_permission_config(),
    }
    provider_overrides = _provider_gateway_overrides(getattr(run_ctx, "capability", None))
    if provider_overrides:
        opencode_config["provider"] = provider_overrides

    config_home = Path(workspace) / "opencode-config"
    config_dir = config_home / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"
    config_path.write_text(json.dumps(opencode_config, indent=2, sort_keys=True))
    config_path.chmod(0o600)

    host = os.environ.get("OPENCODE_HOST", DEFAULT_OPENCODE_HOST)
    port = _find_free_port(host)
    log_path, log_file = _open_server_log(port)
    logger.info(
        "Starting dedicated OpenCode server on %s:%d (workspace=%s log=%s)",
        host, port, workspace, log_path or "/dev/null",
    )
    process = await _spawn_opencode_server_process(
        opencode_bin, host=host, port=port, config_home=config_home,
        workspace=workspace, log_file=log_file,
    )
    server = _OpenCodeServer(
        config_hash=f"dedicated-{port}",
        config_home=config_home,
        host=host,
        port=port,
        process=process,
        log_path=log_path,
        log_file=log_file,
    )
    server.workspace = workspace  # type: ignore[attr-defined]
    server.proxy_tokens = proxy_tokens  # type: ignore[attr-defined]

    deadline = asyncio.get_running_loop().time() + OPENCODE_START_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if await _health_check(server.base_url):
            server.touch()
            return server
        if not server.is_alive:
            tail = server.log_tail()
            await _teardown_dedicated_server(server)
            raise OpenCodeError(
                f"OpenCode server exited with code {server.process.returncode}"
                + (f"; last output:\n{tail}" if tail else "")
            )
        await asyncio.sleep(0.5)

    tail = server.log_tail()
    await _teardown_dedicated_server(server)
    raise OpenCodeError(
        f"OpenCode server did not become ready at {server.base_url}"
        + (f"; last output:\n{tail}" if tail else "")
    )


async def _teardown_dedicated_server(server: _OpenCodeServer) -> None:
    """Terminate a per-run server and revoke its gateway proxy tokens."""
    try:
        await server.terminate()
    finally:
        for token in getattr(server, "proxy_tokens", None) or []:
            try:
                from broker.controller import get_proxy_registry
                get_proxy_registry().revoke(token)
            except Exception:
                pass


async def _write_managed_opencode_config(
    user_mcp_overrides: dict | None = None,
) -> tuple[Path, str]:
    """Write the config for the shared CATALOG server.

    Agent runs no longer execute on shared servers — each run gets its own
    sandboxed dedicated server (see _start_dedicated_server). The shared
    server only answers /config/providers, so its config intentionally
    carries NO MCP servers and therefore no integration credentials.
    """
    del user_mcp_overrides  # catalog server never runs agent turns

    now = time.monotonic()
    cached = _opencode_config_cache.get("catalog")
    if cached is not None and now - cached[2] < OPENCODE_CONFIG_TTL_SECONDS:
        return cached[0], cached[1]

    opencode_config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {},
        "permission": _opencode_permission_config(),
    }
    config_text = json.dumps(opencode_config, indent=2, sort_keys=True)
    config_hash = hashlib.sha256(config_text.encode()).hexdigest()

    config_home = Path(tempfile.gettempdir()) / f"loma-opencode-config-{config_hash[:12]}"
    config_dir = config_home / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"
    config_path.write_text(config_text)
    config_path.chmod(0o600)
    _opencode_config_cache["catalog"] = (config_home, config_hash, now)
    return config_home, config_hash


async def _load_current_agent_config() -> dict:
    """Load config.yaml and overlay active DB integrations."""
    try:
        from agent.client import load_config, merge_db_integrations

        return await merge_db_integrations(load_config())
    except Exception:
        logger.exception("Failed to load app MCP config for OpenCode")
        return {"mcp_servers": {}}


def claude_mcp_to_opencode(mcp_servers: dict) -> dict:
    """Convert Claude Agent SDK MCP config into OpenCode's MCP config shape."""
    converted: dict[str, dict] = {}

    for name, conf in (mcp_servers or {}).items():
        if not isinstance(conf, dict):
            continue

        server_type = conf.get("type")
        if server_type in ("stdio", "local"):
            command = conf.get("command")
            if not command:
                continue
            args = conf.get("args") or []
            command_list = command if isinstance(command, list) else [command]
            command_list = [*command_list, *list(args)]
            entry = {
                "type": "local",
                "command": command_list,
                "enabled": True,
            }
            env = conf.get("env") or conf.get("environment")
            if env:
                entry["environment"] = env
            converted[name] = entry
            continue

        if server_type in ("http", "sse", "remote", "streamable-http"):
            url = conf.get("url")
            if not url:
                continue
            entry = {
                "type": "remote",
                "url": url,
                "enabled": True,
            }
            headers = conf.get("headers")
            if headers:
                entry["headers"] = headers
                # These app integrations provide explicit auth headers; don't
                # let OpenCode try global/dynamic OAuth for the same endpoint.
                entry["oauth"] = False
            converted[name] = entry

    return converted


def _is_supported_model(model: dict) -> bool:
    capabilities = model.get("capabilities") or {}
    input_caps = capabilities.get("input") or {}
    output_caps = capabilities.get("output") or {}
    return bool(
        input_caps.get("text")
        and output_caps.get("text")
        and capabilities.get("toolcall")
    )


def _model_payload(provider: dict, model_id: str, model: dict) -> dict:
    provider_id = provider.get("id", "")
    full_id = f"{provider_id}/{model_id}"
    capabilities = model.get("capabilities") or {}
    input_caps = capabilities.get("input") or {}
    cost = model.get("cost") or {}
    cache_cost = cost.get("cache") or {}
    limit = model.get("limit") or {}
    return {
        "id": full_id,
        "provider_id": provider_id,
        "model_id": model_id,
        "label": f"{provider.get('name', provider_id)} · {model.get('name', model_id)}",
        "context_limit": limit.get("context"),
        "supports_attachments": bool(input_caps.get("image") or input_caps.get("pdf")),
        "supports_reasoning": bool(capabilities.get("reasoning")),
        "status": model.get("status", "active"),
        "cost": {
            "input": cost.get("input"),
            "output": cost.get("output"),
            "cache_read": cache_cost.get("read"),
            "cache_write": cache_cost.get("write"),
        },
    }


def parse_configured_models(payload: dict) -> dict:
    """Normalize OpenCode /config/providers into the dashboard API shape."""
    providers = payload.get("providers") or []
    defaults = payload.get("default") or {}
    models: list[dict] = []

    for provider in providers:
        provider_id = provider.get("id")
        if not provider_id:
            continue
        for model_id, model in (provider.get("models") or {}).items():
            if not isinstance(model, dict) or not _is_supported_model(model):
                continue
            models.append(_model_payload(provider, model_id, model))

    default_model = None
    for provider_id, model_id in defaults.items():
        full_id = f"{provider_id}/{model_id}"
        if any(model["id"] == full_id for model in models):
            default_model = full_id
            break
    if default_model is None and models:
        default_model = models[0]["id"]

    return {"default_model": default_model, "models": models}


async def get_agent_models() -> dict:
    global _opencode_model_catalog, _opencode_model_catalog_checked_at

    now = time.monotonic()
    if (
        _opencode_model_catalog is not None
        and now - _opencode_model_catalog_checked_at < OPENCODE_MODEL_CATALOG_TTL_SECONDS
    ):
        return _opencode_model_catalog

    server = await _ensure_server_instance()
    payload = await _request_json(
        "GET",
        "/config/providers",
        params={"directory": _catalog_dir()},
        timeout=30,
        base_url=server.base_url,
    )
    catalog = parse_configured_models(payload)
    _opencode_model_catalog = catalog
    _opencode_model_catalog_checked_at = now
    _schedule_configured_prewarm(server, catalog)
    return catalog


async def is_known_model(model_id: str) -> bool:
    catalog = await get_agent_models()
    return any(model["id"] == model_id for model in catalog.get("models", []))


def _split_model(full_model_id: str) -> tuple[str, str]:
    if "/" not in full_model_id:
        raise OpenCodeError(f"OpenCode model must use provider/model format: {full_model_id}")
    provider_id, model_id = full_model_id.split("/", 1)
    if not provider_id or not model_id:
        raise OpenCodeError(f"OpenCode model must use provider/model format: {full_model_id}")
    return provider_id, model_id


def _configured_prewarm_models() -> set[str]:
    if os.environ.get("OPENCODE_PREWARM_ENABLED", "1").lower() in {"0", "false", "no"}:
        return set()
    raw_models = os.environ.get("OPENCODE_PREWARM_MODELS", OPENCODE_DEFAULT_PREWARM_MODELS)
    return {model.strip() for model in raw_models.split(",") if model.strip()}


def _should_prewarm_model(model_id: str) -> bool:
    return OPENCODE_PREWARM_POOL_SIZE > 0 and model_id in _configured_prewarm_models()


def get_opencode_pool_status() -> dict:
    """Return OpenCode warm-session pool status for dashboard indicators."""
    configured_models = sorted(_configured_prewarm_models())
    known_models = sorted(
        set(configured_models)
        | {key[1] for key in _opencode_warm_sessions}
        | {key[1] for key in _opencode_prewarm_tasks}
    )
    models = []
    for model_id in known_models:
        warming = sum(
            1
            for key, task in _opencode_prewarm_tasks.items()
            if key[1] == model_id and not task.done()
        )
        available = sum(
            len(sessions)
            for key, sessions in _opencode_warm_sessions.items()
            if key[1] == model_id
        )
        models.append({
            "model": model_id,
            "enabled": _should_prewarm_model(model_id),
            "pool_size": OPENCODE_PREWARM_POOL_SIZE if _should_prewarm_model(model_id) else 0,
            "available": available,
            "warming": warming,
        })

    return {
        "enabled": bool(configured_models) and OPENCODE_PREWARM_POOL_SIZE > 0,
        "pool_size": OPENCODE_PREWARM_POOL_SIZE,
        "configured_models": configured_models,
        "active_sessions": len(_opencode_session_cache),
        "servers": len(_opencode_servers),
        "total_available": sum(model["available"] for model in models),
        "total_warming": sum(model["warming"] for model in models),
        "models": models,
    }


def _schedule_configured_prewarm(server: _OpenCodeServer, catalog: dict) -> None:
    available_models = {model["id"] for model in catalog.get("models", [])}
    for model_id in _configured_prewarm_models() & available_models:
        _schedule_prewarm(server, model_id)


def _schedule_prewarm(server: _OpenCodeServer, model_id: str) -> None:
    # Isolated worker mode: agent runs execute on dedicated per-run servers,
    # so shared warm sessions can never be handed to a run. Prewarming is
    # disabled; the pool-status endpoint reports available=0 accordingly.
    del server, model_id
    return


async def _checkout_warm_session(server: _OpenCodeServer, model_id: str) -> str | None:
    if not _should_prewarm_model(model_id):
        return None

    key = (server.config_hash, model_id)
    async with _opencode_prewarm_lock:
        sessions = _opencode_warm_sessions.get(key) or []
        if sessions:
            session_id = sessions.pop(0)
            logger.info("Checked out prewarmed OpenCode session %s for model=%s", session_id, model_id)
            return session_id

    running_task = _opencode_prewarm_tasks.get(key)
    if running_task is not None and not running_task.done() and OPENCODE_PREWARM_WAIT_SECONDS > 0:
        try:
            await asyncio.wait_for(asyncio.shield(running_task), timeout=OPENCODE_PREWARM_WAIT_SECONDS)
        except asyncio.TimeoutError:
            pass
        except Exception:
            logger.exception("OpenCode prewarm task failed for model=%s", model_id)

    async with _opencode_prewarm_lock:
        sessions = _opencode_warm_sessions.get(key) or []
        if sessions:
            session_id = sessions.pop(0)
            logger.info("Checked out prewarmed OpenCode session %s for model=%s after wait", session_id, model_id)
            return session_id
    return None


async def _create_session(
    title: str, *, base_url: str | None = None, directory: str | None = None,
) -> str:
    session = await _request_json(
        "POST",
        "/session",
        json_body={
            "title": title[:120],
            "permission": _opencode_session_permission_rules(),
        },
        params={"directory": directory or _catalog_dir()},
        timeout=30,
        base_url=base_url,
    )
    session_id = session.get("id")
    if not session_id:
        raise OpenCodeError("OpenCode did not return a session id")
    return session_id


async def _delete_session_messages(session_id: str, *, base_url: str | None = None) -> None:
    try:
        for _ in range(3):
            messages = await _request_json(
                "GET",
                f"/session/{session_id}/message",
                params={"directory": _catalog_dir(), "limit": 20},
                timeout=30,
                base_url=base_url,
            )
            if not isinstance(messages, list) or not messages:
                return
            deleted_any = False
            for message in messages:
                message_id = (message.get("info") or {}).get("id") if isinstance(message, dict) else None
                if not message_id:
                    continue
                await _request_json(
                    "DELETE",
                    f"/session/{session_id}/message/{message_id}",
                    params={"directory": _catalog_dir()},
                    timeout=30,
                    base_url=base_url,
                )
                deleted_any = True
            if not deleted_any:
                return
    except Exception:
        logger.warning("Failed to clean OpenCode warmup messages for session=%s", session_id, exc_info=True)


async def _warm_opencode_session(server: _OpenCodeServer, model_id: str) -> str:
    provider_id, raw_model_id = _split_model(model_id)
    session_id = await _create_session(
        f"OpenCode warm session · {model_id}", base_url=server.base_url
    )
    started_at = time.perf_counter()
    body = {
        "model": {"providerID": provider_id, "modelID": raw_model_id},
        "system": _opencode_system_prompt(),
        "parts": [{"type": "text", "text": OPENCODE_WARMUP_PROMPT}],
    }
    response = await _request_json(
        "POST",
        f"/session/{session_id}/message",
        json_body=body,
        params={"directory": _catalog_dir()},
        timeout=OPENCODE_REQUEST_TIMEOUT_SECONDS,
        base_url=server.base_url,
    )
    await _delete_session_messages(session_id, base_url=server.base_url)

    info = response.get("info") if isinstance(response, dict) else {}
    tokens = (info or {}).get("tokens") or {}
    cache = tokens.get("cache") or {}
    logger.info(
        "OpenCode session prewarmed model=%s session=%s total=%.3fs input_tokens=%s cache_read=%s cache_write=%s",
        model_id,
        session_id,
        time.perf_counter() - started_at,
        tokens.get("input"),
        cache.get("read"),
        cache.get("write"),
    )
    return session_id


async def _prewarm_model_sessions(server: _OpenCodeServer, model_id: str, count: int) -> None:
    key = (server.config_hash, model_id)
    try:
        for _ in range(max(0, count)):
            try:
                session_id = await _warm_opencode_session(server, model_id)
                async with _opencode_prewarm_lock:
                    sessions = _opencode_warm_sessions.setdefault(key, [])
                    if len(sessions) < OPENCODE_PREWARM_POOL_SIZE:
                        sessions.append(session_id)
            except Exception:
                logger.exception("Failed to prewarm OpenCode session for model=%s", model_id)
                break
    finally:
        task = _opencode_prewarm_tasks.get(key)
        if task is asyncio.current_task():
            _opencode_prewarm_tasks.pop(key, None)


def _usage_from_info(info: dict) -> tuple[dict | None, float | None]:
    tokens = info.get("tokens") or {}
    if not tokens and info.get("cost") is None:
        return None, None
    cache = tokens.get("cache") or {}
    # Anthropic-API key names so downstream (observer) sees one shape.
    return {
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "cache_read_input_tokens": cache.get("read", 0),
        "cache_creation_input_tokens": cache.get("write", 0),
    }, info.get("cost")


def _event_session_id(properties: dict) -> str | None:
    if properties.get("sessionID"):
        return properties.get("sessionID")

    for key in ("info", "part", "message"):
        value = properties.get(key)
        if isinstance(value, dict) and value.get("sessionID"):
            return value.get("sessionID")
    return None


def _tool_display_input(tool_name: str, state: dict) -> str:
    title = state.get("title")
    if title:
        return str(title)

    tool_input = state.get("input") or {}
    if isinstance(tool_input, str):
        return tool_input[:200]
    if not isinstance(tool_input, dict):
        return ""

    preferred_keys = (
        "filePath",
        "path",
        "command",
        "query",
        "url",
        "pattern",
        "glob",
        "text",
    )
    for key in preferred_keys:
        value = tool_input.get(key)
        if value:
            return str(value)[:200]

    for key, value in tool_input.items():
        if isinstance(value, str) and value:
            return f"{key}: {value[:160]}"
    return tool_name


def _merge_usage(total_usage: dict, usage: dict | None, cost: float | None) -> float:
    if usage:
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            total_usage[key] = total_usage.get(key, 0) + int(usage.get(key) or 0)
    return float(cost or 0)


def _decode_sse_event_line(raw_line: bytes) -> dict | None:
    line = raw_line.decode("utf-8", errors="ignore").strip()
    if not line or not line.startswith("data:"):
        return None

    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("Skipping non-JSON OpenCode event payload: %s", payload[:200])
        return None
    return event if isinstance(event, dict) else None


async def _iter_opencode_turn_events(
    base_url: str,
    *,
    session_id: str,
    body: dict,
    idle_timeout_seconds: int,
    request_timeout_seconds: int,
    directory: str | None = None,
) -> AsyncGenerator[dict, None]:
    timeout = _turn_client_timeout(request_timeout_seconds)
    directory = directory or _catalog_dir()
    prompt_task: asyncio.Task | None = None
    async with aiohttp.ClientSession(timeout=timeout, auth=_auth(base_url)) as session:
        try:
            async with session.get(
                f"{base_url}/event",
                params={"directory": directory},
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise OpenCodeError(f"OpenCode event stream failed ({resp.status}): {text[:500]}")

                prompt_task = asyncio.create_task(
                    _post_prompt_async(
                        session, base_url, session_id=session_id, body=body,
                        directory=directory,
                    )
                )

                buffer = b""
                loop = asyncio.get_running_loop()
                last_event_at = loop.time()
                last_status_at = loop.time()
                read_task: asyncio.Task | None = asyncio.create_task(resp.content.read(64 * 1024))
                try:
                    while read_task is not None:
                        wait_set = {read_task}
                        if prompt_task is not None:
                            wait_set.add(prompt_task)

                        done, _ = await asyncio.wait(
                            wait_set,
                            timeout=1,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if not done:
                            now = loop.time()
                            if now - last_status_at >= 5:
                                last_status_at = now
                                yield {
                                    "type": "__status",
                                    "message": "Still waiting for OpenCode events...",
                                    "elapsed_seconds": round(now - last_event_at),
                                }
                            if prompt_task is None and loop.time() - last_event_at > idle_timeout_seconds:
                                raise OpenCodeError(
                                    "Timed out waiting for OpenCode response events "
                                    f"after {idle_timeout_seconds}s"
                                )
                            continue

                        if prompt_task is not None and prompt_task in done:
                            finished_prompt_task = prompt_task
                            prompt_task = None
                            try:
                                await finished_prompt_task
                            except Exception:
                                logger.exception("OpenCode prompt_async request failed")
                                raise

                        if read_task not in done:
                            continue

                        raw_chunk = await read_task
                        if not raw_chunk:
                            read_task = None
                            break
                        read_task = asyncio.create_task(resp.content.read(64 * 1024))

                        buffer += raw_chunk
                        if len(buffer) > OPENCODE_EVENT_BUFFER_LIMIT_BYTES:
                            raise OpenCodeError(
                                "OpenCode event exceeded "
                                f"{OPENCODE_EVENT_BUFFER_LIMIT_BYTES // (1024 * 1024)}MB while streaming"
                            )

                        while b"\n" in buffer:
                            raw_line, buffer = buffer.split(b"\n", 1)
                            event = _decode_sse_event_line(raw_line)
                            if event is not None:
                                event_session_id = _event_session_id(event.get("properties") or {})
                                if event_session_id == session_id:
                                    last_event_at = loop.time()
                                    last_status_at = loop.time()
                                    yield event

                        now = loop.time()
                        if now - last_status_at >= 5:
                            last_status_at = now
                            yield {
                                "type": "__status",
                                "message": "Still waiting for OpenCode events...",
                                "elapsed_seconds": round(now - last_event_at),
                            }
                        if prompt_task is None and now - last_event_at > idle_timeout_seconds:
                            raise OpenCodeError(
                                "Timed out waiting for OpenCode response events "
                                f"after {idle_timeout_seconds}s"
                            )
                finally:
                    if read_task is not None and not read_task.done():
                        read_task.cancel()

                if buffer:
                    event = _decode_sse_event_line(buffer)
                    event_session_id = _event_session_id(event.get("properties") or {}) if event is not None else None
                    if event is not None and event_session_id == session_id:
                        yield event
        except asyncio.TimeoutError as exc:
            raise OpenCodeError(_describe_turn_timeout(request_timeout_seconds)) from exc
        finally:
            if prompt_task is not None:
                try:
                    await prompt_task
                except Exception:
                    logger.exception("OpenCode prompt_async request failed")
                    raise


def _turn_client_timeout(request_timeout_seconds: int) -> aiohttp.ClientTimeout:
    """aiohttp timeout for a turn's event stream.

    A positive value is a hard wall-clock cap on the whole turn. Zero (the
    chat default) disables the cap; the idle watchdog is then the only guard,
    so a turn can run as long as OpenCode keeps producing events.
    """
    if request_timeout_seconds > 0:
        return aiohttp.ClientTimeout(total=request_timeout_seconds)
    return aiohttp.ClientTimeout(total=None, sock_connect=30)


def _describe_turn_timeout(request_timeout_seconds: int) -> str:
    if request_timeout_seconds > 0:
        return (
            f"OpenCode turn exceeded the {request_timeout_seconds}s wall-clock limit "
            "(raise OPENCODE_REQUEST_TIMEOUT / OPENCODE_FLOW_REQUEST_TIMEOUT to allow longer turns)"
        )
    return "OpenCode event stream connection timed out"


async def _post_prompt_async(
    session: aiohttp.ClientSession,
    base_url: str,
    *,
    session_id: str,
    body: dict,
    directory: str | None = None,
) -> dict:
    async with session.post(
        f"{base_url}/session/{session_id}/prompt_async",
        json=body,
        params={"directory": directory or _catalog_dir()},
    ) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise OpenCodeError(
                f"OpenCode POST /session/{session_id}/prompt_async failed "
                f"({resp.status}): {_format_opencode_error(text)}"
            )
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpenCodeError(f"OpenCode returned non-JSON for prompt_async: {text[:500]}") from exc


async def _emit_text(
    text: str,
    *,
    turn_count: int,
    observer,
    include_steps: bool,
    source: str,
    emitted_artifact_ids: set[str],
    emitted_file_paths: set[str],
    emit_text: bool = True,
) -> AsyncGenerator[str | dict, None]:
    from agent.client import _detect_artifacts, _detect_file_paths, _get_artifact_version

    if observer:
        await observer.record_text(turn_count, text)

    if include_steps and source == "dashboard":
        detected = _detect_artifacts(text)
        if detected:
            if emit_text:
                yield text
            for art in detected:
                if art["artifact_id"] in emitted_artifact_ids:
                    continue
                emitted_artifact_ids.add(art["artifact_id"])
                version = _get_artifact_version(None, art["title"])
                artifact_event = {
                    "type": "artifact",
                    "artifact_id": art["artifact_id"],
                    "title": art["title"],
                    "content": art["content"],
                    "language": art["language"],
                    "version": version,
                }
                if observer:
                    await observer.record_artifact({
                        "artifact_id": art["artifact_id"],
                        "title": art["title"],
                        "language": art["language"],
                        "version": version,
                        "artifact_type": "code",
                        "content": art["content"],
                    })
                yield artifact_event
            return

        file_paths = _detect_file_paths(text)
        if file_paths:
            if emit_text:
                yield text
            for fpath in file_paths:
                if fpath in emitted_file_paths:
                    continue
                emitted_file_paths.add(fpath)
                try:
                    from api.routes import register_served_file
                    file_info = register_served_file(
                        fpath,
                        conversation_id=getattr(observer, "conversation_id", None),
                        owner_email=(getattr(observer, "metadata", None) or {}).get("user_name"),
                    )
                    yield {
                        "type": "file",
                        "file_id": file_info["file_id"],
                        "name": file_info["name"],
                        "url": file_info["url"],
                        "mime_type": file_info["mime_type"],
                        "size": file_info["size"],
                    }
                except Exception as exc:
                    logger.warning("OpenCode file registration failed for %s: %s", fpath, exc)
            return

    if emit_text:
        yield text


async def run_opencode_agent(
    *,
    full_prompt: str,
    selected_model: str,
    observer=None,
    include_steps: bool = False,
    source: str = "dashboard",
    user_email: str | None = None,
    user_mcp_overrides: dict | None = None,
    image_files: list[dict] | None = None,
    run_ctx=None,
) -> AsyncGenerator[str | dict, None]:
    """Run one turn through a DEDICATED, sandboxed per-run OpenCode server.

    Each run gets its own `opencode serve` worker process with a private
    workspace, a scrubbed environment, gateway-proxied MCP servers, and
    gateway-brokered model providers. The server is terminated and its proxy
    tokens revoked when the turn ends. Conversation continuity comes from
    the textual conversation context in the prompt.
    """
    started_at = time.perf_counter()
    if not await is_known_model(selected_model):
        raise OpenCodeError(f"Unknown or unavailable OpenCode model: {selected_model}")
    catalog_checked_at = time.perf_counter()

    provider_id, model_id = _split_model(selected_model)
    run_source = (getattr(observer, "metadata", {}) or {}).get("source") or source
    is_flow_run = run_source in {"flow", "webhook", "task_step"}
    idle_timeout_seconds = (
        OPENCODE_FLOW_EVENT_IDLE_TIMEOUT_SECONDS
        if is_flow_run
        else OPENCODE_EVENT_IDLE_TIMEOUT_SECONDS
    )
    request_timeout_seconds = (
        OPENCODE_FLOW_REQUEST_TIMEOUT_SECONDS
        if is_flow_run
        else OPENCODE_REQUEST_TIMEOUT_SECONDS
    )
    if request_timeout_seconds > 0:
        request_timeout_seconds = max(request_timeout_seconds, idle_timeout_seconds + 30)

    # Dedicated sandboxed server per run — never a shared server, never a
    # shared session. The private workspace comes from the run context.
    server = await _start_dedicated_server(user_mcp_overrides, run_ctx)
    base_url = server.base_url
    run_directory = getattr(server, "workspace", None) or _catalog_dir()

    conversation_id = getattr(observer, "conversation_id", None)
    warm_session_used = False
    reused_session = False
    session_id = await _create_session(
        full_prompt[:80] or "Dashboard chat",
        base_url=base_url,
        directory=run_directory,
    )
    session_created_at = time.perf_counter()

    pool_status = get_opencode_pool_status()
    model_pool = next((model for model in pool_status["models"] if model["model"] == selected_model), None)
    if include_steps:
        yield {
            "type": "account_info",
            "runtime": "opencode",
            "provider": provider_id,
            "model": model_id,
            "warm_session_used": warm_session_used,
            "pool_available": model_pool["available"] if model_pool else 0,
            "pool_size": model_pool["pool_size"] if model_pool else 0,
            "pool_warming": model_pool["warming"] if model_pool else 0,
            "active_sessions": pool_status["active_sessions"],
        }
        yield {
            "type": "status",
            "message": (
                "Checked out warm OpenCode session"
                if warm_session_used
                else "Using existing OpenCode session"
                if reused_session
                else "Created cold OpenCode session"
            ),
        }

    system_prompt = _opencode_system_prompt()

    parts: list[dict] = [{"type": "text", "text": full_prompt}]
    if image_files:
        for img in image_files:
            parts.append({
                "type": "file",
                "mime": img.get("mimetype", "image/png"),
                "filename": img.get("name"),
                "url": f"data:{img.get('mimetype', 'image/png')};base64,{img['data']}",
            })
        logger.info("[OPENCODE] Attached %d image(s) as file parts", len(image_files))

    body = {
        "model": {"providerID": provider_id, "modelID": model_id},
        "system": system_prompt,
        "parts": parts,
    }
    logger.info(
        "OpenCode turn prepared model=%s source=%s system_chars=%d prompt_chars=%d mcp_servers=%d catalog=%.3fs session=%.3fs idle_timeout=%ds request_timeout=%ds",
        selected_model,
        run_source,
        len(system_prompt),
        len(full_prompt),
        len(_opencode_mcp_names),
        catalog_checked_at - started_at,
        session_created_at - catalog_checked_at,
        idle_timeout_seconds,
        request_timeout_seconds,
    )
    turn_count = 1
    last_text = ""
    emitted_artifact_ids: set[str] = set()
    emitted_file_paths: set[str] = set()

    if observer:
        observer.turn_count = turn_count
    if include_steps:
        yield {"type": "turn", "turn_number": turn_count}
        yield {"type": "status", "message": "Sent prompt to OpenCode; waiting for model/tool events"}

    assistant_message_ids: set[str] = set()
    emitted_text_part_ids: set[str] = set()
    emitted_text_by_part_id: dict[str, str] = {}
    part_type_by_id: dict[str, str] = {}
    emitted_tool_calls: set[str] = set()
    emitted_tool_results: set[str] = set()
    completed_usage_messages: set[str] = set()
    pending_parts: dict[str, list[dict]] = {}
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    total_cost = 0.0
    stream_completed = False
    first_event_at: float | None = None
    first_text_at: float | None = None

    async def handle_part(part: dict) -> AsyncGenerator[str | dict, None]:
        nonlocal last_text, first_text_at

        part_type = part.get("type")
        part_id = part.get("id") or hashlib.sha256(str(part).encode()).hexdigest()[:12]
        part_type_by_id[part_id] = part_type or ""
        if part_type == "text":
            text = part.get("text") or ""
            if not text.strip():
                return

            previous_text = emitted_text_by_part_id.get(part_id, "")
            completed = bool(part.get("time", {}).get("end"))
            delta = ""

            if text.startswith(previous_text):
                delta = text[len(previous_text):]
            elif not previous_text:
                delta = text
            elif completed:
                logger.debug(
                    "OpenCode text part %s was rewritten before completion; skipping duplicate streaming append",
                    part_id,
                )
            else:
                return

            if delta:
                emitted_text_by_part_id[part_id] = text
                last_text = text
                if first_text_at is None:
                    first_text_at = time.perf_counter()
                yield {"type": "text", "text": delta, "append": True}

            if completed and part_id not in emitted_text_part_ids:
                emitted_text_part_ids.add(part_id)
                final_text = text.strip()
                last_text = final_text
                async for output_event in _emit_text(
                    final_text,
                    turn_count=turn_count,
                    observer=observer,
                    include_steps=include_steps,
                    source=source,
                    emitted_artifact_ids=emitted_artifact_ids,
                    emitted_file_paths=emitted_file_paths,
                    emit_text=False,
                ):
                    yield output_event
        elif part_type == "reasoning":
            if include_steps:
                yield {"type": "status", "message": "OpenCode is reasoning about the next step"}
        elif part_type in ("step-start", "step-finish"):
            if include_steps and part_type == "step-start":
                yield {"type": "status", "message": "OpenCode started a work step"}
        elif part_type == "tool":
            tool_name = part.get("tool") or "tool"
            tool_use_id = part.get("callID") or part_id
            raw_state = part.get("state") or {}
            if isinstance(raw_state, dict):
                state = raw_state
                status = state.get("status")
                tool_input = state.get("input") or {}
            else:
                state = {"status": raw_state}
                status = str(raw_state)
                tool_input = {}

            if status in ("running", "completed", "error") and tool_use_id not in emitted_tool_calls:
                emitted_tool_calls.add(tool_use_id)
                if observer:
                    await observer.record_tool_call(turn_count, tool_name, tool_use_id, tool_input)
                if include_steps:
                    yield {
                        "type": "tool_call",
                        "name": tool_name,
                        "tool_use_id": tool_use_id,
                        "input": _tool_display_input(tool_name, state),
                    }
                    yield {
                        "type": "status",
                        "message": f"Running {tool_name}",
                    }

            if status in ("completed", "error") and tool_use_id not in emitted_tool_results:
                emitted_tool_results.add(tool_use_id)
                is_error = status == "error"
                output = state.get("error") if is_error else state.get("output", "")
                if observer:
                    await observer.record_tool_result(tool_use_id, is_error, str(output))
                if include_steps:
                    yield {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "is_error": is_error,
                    }

    async def handle_part_delta(properties: dict) -> AsyncGenerator[dict, None]:
        nonlocal last_text, first_text_at

        if properties.get("field") != "text":
            return

        message_id = properties.get("messageID")
        part_id = properties.get("partID")
        delta = properties.get("delta") or ""
        if not message_id or not part_id or not delta:
            return
        if message_id not in assistant_message_ids:
            return

        # OpenCode streams both reasoning and final text through
        # message.part.delta. Only final assistant text should be rendered in
        # dashboard chat; reasoning deltas stay hidden behind status updates.
        if part_type_by_id.get(part_id) != "text":
            return

        emitted_text_by_part_id[part_id] = emitted_text_by_part_id.get(part_id, "") + delta
        last_text = emitted_text_by_part_id[part_id]
        if first_text_at is None:
            first_text_at = time.perf_counter()
        yield {"type": "text", "text": delta, "append": True}

    server.active_turns += 1
    attempt = 1
    try:
        while True:
            try:
                async for event in _iter_opencode_turn_events(
                    base_url,
                    session_id=session_id,
                    body=body,
                    idle_timeout_seconds=idle_timeout_seconds,
                    request_timeout_seconds=request_timeout_seconds,
                    directory=run_directory,
                ):
                    if first_event_at is None:
                        first_event_at = time.perf_counter()
                    event_type = event.get("type")
                    if event_type == "__status":
                        if include_steps:
                            yield {
                                "type": "status",
                                "message": event.get("message") or "Still waiting for OpenCode events...",
                                "elapsed_seconds": event.get("elapsed_seconds"),
                            }
                        continue

                    properties = event.get("properties") or {}
                    if _event_session_id(properties) != session_id:
                        continue

                    if event_type == "permission.asked":
                        permission_id = properties.get("id")
                        permission_name = properties.get("permission") or "permission"
                        patterns = properties.get("patterns") or []
                        if include_steps:
                            yield {
                                "type": "status",
                                "message": f"OpenCode requested {permission_name}; auto-approving for dashboard run",
                            }
                        if OPENCODE_AUTO_APPROVE_PERMISSIONS and permission_id:
                            await _request_json(
                                "POST",
                                f"/session/{session_id}/permissions/{permission_id}",
                                json_body={"response": "always"},
                                params={"directory": run_directory},
                                timeout=30,
                                base_url=base_url,
                            )
                            logger.info(
                                "Auto-approved OpenCode permission session=%s permission=%s patterns=%s",
                                session_id,
                                permission_name,
                                patterns,
                            )
                        continue

                    if event_type == "message.updated":
                        info = properties.get("info") or {}
                        message_id = info.get("id")
                        if info.get("role") != "assistant" or not message_id:
                            continue

                        assistant_message_ids.add(message_id)
                        if info.get("error"):
                            raise OpenCodeModelError(_format_opencode_error(info["error"]))
                        for pending_part in pending_parts.pop(message_id, []):
                            async for output_event in handle_part(pending_part):
                                yield output_event

                        if info.get("time", {}).get("completed") and message_id not in completed_usage_messages:
                            completed_usage_messages.add(message_id)
                            usage, cost = _usage_from_info(info)
                            total_cost += _merge_usage(total_usage, usage, cost)

                            finish = info.get("finish")
                            if finish and finish != "tool-calls":
                                stream_completed = True

                        if stream_completed:
                            break

                    if event_type == "message.part.delta":
                        async for output_event in handle_part_delta(properties):
                            yield output_event
                        continue

                    if event_type != "message.part.updated":
                        continue

                    part = properties.get("part") or {}
                    message_id = part.get("messageID")
                    if message_id not in assistant_message_ids:
                        if message_id:
                            pending_parts.setdefault(message_id, []).append(part)
                        continue

                    async for output_event in handle_part(part):
                        yield output_event
                break
            except Exception as exc:
                can_retry = (
                    attempt < OPENCODE_TURN_MAX_ATTEMPTS
                    and first_text_at is None
                    and not emitted_tool_calls
                    and _is_retryable_turn_error(exc)
                )
                if not can_retry:
                    raise
                attempt += 1
                logger.warning(
                    "OpenCode turn failed before any output (%s); retrying with a fresh session (attempt %d/%d)",
                    exc,
                    attempt,
                    OPENCODE_TURN_MAX_ATTEMPTS,
                )
                if include_steps:
                    yield {
                        "type": "status",
                        "message": "OpenCode stream dropped before any output; retrying with a fresh session",
                    }
                for container in (
                    assistant_message_ids,
                    emitted_text_part_ids,
                    emitted_text_by_part_id,
                    part_type_by_id,
                    emitted_tool_calls,
                    emitted_tool_results,
                    completed_usage_messages,
                    pending_parts,
                ):
                    container.clear()
                stream_completed = False
                first_event_at = None
                server.active_turns = max(0, server.active_turns - 1)
                await _teardown_dedicated_server(server)
                server = await _start_dedicated_server(user_mcp_overrides, run_ctx)
                server.active_turns += 1
                base_url = server.base_url
                run_directory = getattr(server, "workspace", None) or _catalog_dir()
                session_id = await _create_session(
                    full_prompt[:80] or "Dashboard chat",
                    base_url=base_url,
                    directory=run_directory,
                )
    finally:
        server.active_turns = max(0, server.active_turns - 1)
        # Per-run server: terminate the worker and revoke its proxy tokens.
        await _teardown_dedicated_server(server)

    if observer:
        usage_payload = total_usage if total_usage["input_tokens"] or total_usage["output_tokens"] else None
        await observer.record_usage(usage_payload, total_cost if total_cost else None)
        await observer.finish(final_response=last_text)

    completed_at = time.perf_counter()
    logger.info(
        "OpenCode turn completed model=%s total=%.3fs first_event=%s first_text=%s input_tokens=%s output_tokens=%s",
        selected_model,
        completed_at - started_at,
        f"{first_event_at - started_at:.3f}s" if first_event_at else "none",
        f"{first_text_at - started_at:.3f}s" if first_text_at else "none",
        total_usage.get("input_tokens"),
        total_usage.get("output_tokens"),
    )

    if not last_text:
        yield "I didn't generate a response. Please try again."
