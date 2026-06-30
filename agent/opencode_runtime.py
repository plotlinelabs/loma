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
import tempfile
import time
from pathlib import Path
from typing import AsyncGenerator

import aiohttp

from agent.prompt import build_pooled_system_prompt

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OPENCODE_HOST = "127.0.0.1"
DEFAULT_OPENCODE_PORT = 4097
OPENCODE_START_TIMEOUT_SECONDS = 20
OPENCODE_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_REQUEST_TIMEOUT", "900"))
OPENCODE_FLOW_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_FLOW_REQUEST_TIMEOUT", "1800"))
OPENCODE_EVENT_BUFFER_LIMIT_BYTES = int(
    os.environ.get("OPENCODE_EVENT_BUFFER_LIMIT_BYTES", str(64 * 1024 * 1024))
)
OPENCODE_EVENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_EVENT_IDLE_TIMEOUT", "120"))
OPENCODE_FLOW_EVENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_FLOW_EVENT_IDLE_TIMEOUT", "900"))
OPENCODE_CONFIG_TTL_SECONDS = int(os.environ.get("OPENCODE_CONFIG_TTL_SECONDS", "30"))
OPENCODE_MODEL_CATALOG_TTL_SECONDS = int(os.environ.get("OPENCODE_MODEL_CATALOG_TTL_SECONDS", "60"))
OPENCODE_PREWARM_POOL_SIZE = int(os.environ.get("OPENCODE_PREWARM_POOL_SIZE", "2"))
OPENCODE_PREWARM_WAIT_SECONDS = float(os.environ.get("OPENCODE_PREWARM_WAIT_SECONDS", "0.5"))
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

_opencode_process: asyncio.subprocess.Process | None = None
_opencode_config_hash: str | None = None
_opencode_config_home: Path | None = None
_opencode_mcp_names: set[str] = set()
_opencode_config_checked_at: float = 0.0
_opencode_model_catalog: dict | None = None
_opencode_model_catalog_checked_at: float = 0.0
_opencode_session_cache: dict[tuple[str, str], str] = {}
_opencode_warm_sessions: dict[str, list[str]] = {}
_opencode_prewarm_tasks: dict[str, asyncio.Task] = {}
_opencode_prewarm_lock = asyncio.Lock()


async def reset_opencode_runtime(reason: str = "") -> None:
    """Clear OpenCode caches and restart the managed server on next use."""
    global _opencode_process, _opencode_config_hash, _opencode_config_checked_at
    global _opencode_model_catalog, _opencode_model_catalog_checked_at

    _opencode_session_cache.clear()
    _opencode_warm_sessions.clear()
    _opencode_model_catalog = None
    _opencode_model_catalog_checked_at = 0.0
    _opencode_config_hash = None
    _opencode_config_checked_at = 0.0

    for task in list(_opencode_prewarm_tasks.values()):
        if not task.done():
            task.cancel()
    _opencode_prewarm_tasks.clear()

    if _opencode_process is not None and _opencode_process.returncode is None:
        logger.info("Restarting OpenCode runtime%s", f" ({reason})" if reason else "")
        _opencode_process.terminate()
        try:
            await asyncio.wait_for(_opencode_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            _opencode_process.kill()
            await _opencode_process.wait()
    _opencode_process = None


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


def _auth() -> aiohttp.BasicAuth | None:
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if not password:
        return None
    username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
    return aiohttp.BasicAuth(username, password)


async def _request_json(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: int | None = None,
) -> dict:
    base_url = await ensure_opencode_server()
    request_timeout = aiohttp.ClientTimeout(total=timeout or OPENCODE_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=request_timeout, auth=_auth()) as session:
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


async def _health_check(base_url: str) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout, auth=_auth()) as session:
            async with session.get(f"{base_url}/config/providers", params={"directory": str(PROJECT_ROOT)}) as resp:
                return resp.status == 200
    except Exception:
        return False


async def ensure_opencode_server() -> str:
    """Return a reachable OpenCode server URL, starting one if needed.

    By default the app starts OpenCode with an isolated config home so global
    ~/.config/opencode MCP settings do not leak into dashboard chat. If
    OPENCODE_SERVER_URL is explicitly set, we assume that external server is
    intentionally managed by the operator.
    """
    base_url = _configured_server_url()

    if os.environ.get("OPENCODE_SERVER_URL"):
        if await _health_check(base_url):
            return base_url
        raise OpenCodeError(f"Configured OPENCODE_SERVER_URL is not reachable: {base_url}")

    config_home, config_hash = await _write_managed_opencode_config()

    global _opencode_process, _opencode_config_hash
    should_start = _opencode_process is None or _opencode_process.returncode is not None
    config_changed = _opencode_config_hash is not None and _opencode_config_hash != config_hash
    if config_changed and _opencode_process is not None and _opencode_process.returncode is None:
        logger.info("OpenCode managed config changed; restarting server")
        _opencode_session_cache.clear()
        _opencode_warm_sessions.clear()
        _opencode_process.terminate()
        try:
            await asyncio.wait_for(_opencode_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            _opencode_process.kill()
            await _opencode_process.wait()
        should_start = True

    if not should_start and await _health_check(base_url):
        return base_url

    if should_start:
        opencode_bin = shutil.which("opencode")
        if not opencode_bin:
            raise OpenCodeError("opencode binary not found on PATH")

        port = int(os.environ.get("OPENCODE_PORT", str(DEFAULT_OPENCODE_PORT)))
        host = os.environ.get("OPENCODE_HOST", DEFAULT_OPENCODE_HOST)
        logger.info("Starting OpenCode server on %s:%d", host, port)
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": str(config_home),
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        }
        _opencode_process = await asyncio.create_subprocess_exec(
            opencode_bin,
            "serve",
            "--port",
            str(port),
            "--hostname",
            host,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _opencode_config_hash = config_hash

    deadline = asyncio.get_running_loop().time() + OPENCODE_START_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if await _health_check(base_url):
            return base_url
        if _opencode_process and _opencode_process.returncode is not None:
            raise OpenCodeError(f"OpenCode server exited with code {_opencode_process.returncode}")
        await asyncio.sleep(0.5)

    raise OpenCodeError(f"OpenCode server did not become ready at {base_url}")


async def _write_managed_opencode_config() -> tuple[Path, str]:
    """Write isolated OpenCode config from this app's MCP source of truth."""
    global _opencode_config_home, _opencode_mcp_names, _opencode_config_checked_at

    now = time.monotonic()
    if (
        _opencode_config_home is not None
        and _opencode_config_hash is not None
        and now - _opencode_config_checked_at < OPENCODE_CONFIG_TTL_SECONDS
    ):
        return _opencode_config_home, _opencode_config_hash

    app_config = await _load_current_agent_config()
    mcp_config = claude_mcp_to_opencode(app_config.get("mcp_servers", {}))
    _opencode_mcp_names = set(mcp_config.keys())
    opencode_config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": mcp_config,
        "permission": _opencode_permission_config(),
    }
    config_text = json.dumps(opencode_config, indent=2, sort_keys=True)
    config_hash = hashlib.sha256(config_text.encode()).hexdigest()

    if _opencode_config_home is None:
        _opencode_config_home = Path(tempfile.mkdtemp(prefix="loma-opencode-config-"))
    config_dir = _opencode_config_home / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"
    config_path.write_text(config_text)
    config_path.chmod(0o600)
    _opencode_config_checked_at = now
    return _opencode_config_home, config_hash


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
        _schedule_configured_prewarm(_opencode_model_catalog)
        return _opencode_model_catalog

    payload = await _request_json(
        "GET",
        "/config/providers",
        params={"directory": str(PROJECT_ROOT)},
        timeout=30,
    )
    catalog = parse_configured_models(payload)
    _opencode_model_catalog = catalog
    _opencode_model_catalog_checked_at = now
    _schedule_configured_prewarm(catalog)
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
    known_models = sorted(set(configured_models) | set(_opencode_warm_sessions.keys()) | set(_opencode_prewarm_tasks.keys()))
    models = []
    for model_id in known_models:
        task = _opencode_prewarm_tasks.get(model_id)
        warming = 1 if task is not None and not task.done() else 0
        available = len(_opencode_warm_sessions.get(model_id, []))
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
        "total_available": sum(model["available"] for model in models),
        "total_warming": sum(model["warming"] for model in models),
        "models": models,
    }


def _schedule_configured_prewarm(catalog: dict) -> None:
    available_models = {model["id"] for model in catalog.get("models", [])}
    for model_id in _configured_prewarm_models() & available_models:
        _schedule_prewarm(model_id)


def _schedule_prewarm(model_id: str) -> None:
    if not _should_prewarm_model(model_id):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    sessions = _opencode_warm_sessions.get(model_id, [])
    running_task = _opencode_prewarm_tasks.get(model_id)
    if len(sessions) >= OPENCODE_PREWARM_POOL_SIZE:
        return
    if running_task is not None and not running_task.done():
        return

    needed = OPENCODE_PREWARM_POOL_SIZE - len(sessions)
    _opencode_prewarm_tasks[model_id] = loop.create_task(_prewarm_model_sessions(model_id, needed))


async def _checkout_warm_session(model_id: str) -> str | None:
    if not _should_prewarm_model(model_id):
        return None

    async with _opencode_prewarm_lock:
        sessions = _opencode_warm_sessions.get(model_id) or []
        if sessions:
            session_id = sessions.pop(0)
            logger.info("Checked out prewarmed OpenCode session %s for model=%s", session_id, model_id)
            return session_id

    running_task = _opencode_prewarm_tasks.get(model_id)
    if running_task is not None and not running_task.done() and OPENCODE_PREWARM_WAIT_SECONDS > 0:
        try:
            await asyncio.wait_for(asyncio.shield(running_task), timeout=OPENCODE_PREWARM_WAIT_SECONDS)
        except asyncio.TimeoutError:
            pass
        except Exception:
            logger.exception("OpenCode prewarm task failed for model=%s", model_id)

    async with _opencode_prewarm_lock:
        sessions = _opencode_warm_sessions.get(model_id) or []
        if sessions:
            session_id = sessions.pop(0)
            logger.info("Checked out prewarmed OpenCode session %s for model=%s after wait", session_id, model_id)
            return session_id
    return None


async def _create_session(title: str) -> str:
    session = await _request_json(
        "POST",
        "/session",
        json_body={
            "title": title[:120],
            "permission": _opencode_session_permission_rules(),
        },
        params={"directory": str(PROJECT_ROOT)},
        timeout=30,
    )
    session_id = session.get("id")
    if not session_id:
        raise OpenCodeError("OpenCode did not return a session id")
    return session_id


async def _delete_session_messages(session_id: str) -> None:
    try:
        for _ in range(3):
            messages = await _request_json(
                "GET",
                f"/session/{session_id}/message",
                params={"directory": str(PROJECT_ROOT), "limit": 20},
                timeout=30,
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
                    params={"directory": str(PROJECT_ROOT)},
                    timeout=30,
                )
                deleted_any = True
            if not deleted_any:
                return
    except Exception:
        logger.warning("Failed to clean OpenCode warmup messages for session=%s", session_id, exc_info=True)


async def _warm_opencode_session(model_id: str) -> str:
    provider_id, raw_model_id = _split_model(model_id)
    session_id = await _create_session(f"OpenCode warm session · {model_id}")
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
        params={"directory": str(PROJECT_ROOT)},
        timeout=OPENCODE_REQUEST_TIMEOUT_SECONDS,
    )
    await _delete_session_messages(session_id)

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


async def _prewarm_model_sessions(model_id: str, count: int) -> None:
    try:
        for _ in range(max(0, count)):
            try:
                session_id = await _warm_opencode_session(model_id)
                async with _opencode_prewarm_lock:
                    sessions = _opencode_warm_sessions.setdefault(model_id, [])
                    if len(sessions) < OPENCODE_PREWARM_POOL_SIZE:
                        sessions.append(session_id)
            except Exception:
                logger.exception("Failed to prewarm OpenCode session for model=%s", model_id)
                break
    finally:
        task = _opencode_prewarm_tasks.get(model_id)
        if task is asyncio.current_task():
            _opencode_prewarm_tasks.pop(model_id, None)


def _usage_from_info(info: dict) -> tuple[dict | None, float | None]:
    tokens = info.get("tokens") or {}
    if not tokens and info.get("cost") is None:
        return None, None
    return {
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
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
        total_usage["input_tokens"] = total_usage.get("input_tokens", 0) + int(usage.get("input_tokens") or 0)
        total_usage["output_tokens"] = total_usage.get("output_tokens", 0) + int(usage.get("output_tokens") or 0)
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
) -> AsyncGenerator[dict, None]:
    timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
    prompt_task: asyncio.Task | None = None
    async with aiohttp.ClientSession(timeout=timeout, auth=_auth()) as session:
        try:
            async with session.get(
                f"{base_url}/event",
                params={"directory": str(PROJECT_ROOT)},
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise OpenCodeError(f"OpenCode event stream failed ({resp.status}): {text[:500]}")

                prompt_task = asyncio.create_task(
                    _post_prompt_async(session, base_url, session_id=session_id, body=body)
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
        finally:
            if prompt_task is not None:
                try:
                    await prompt_task
                except Exception:
                    logger.exception("OpenCode prompt_async request failed")
                    raise


async def _post_prompt_async(
    session: aiohttp.ClientSession,
    base_url: str,
    *,
    session_id: str,
    body: dict,
) -> dict:
    async with session.post(
        f"{base_url}/session/{session_id}/prompt_async",
        json=body,
        params={"directory": str(PROJECT_ROOT)},
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
                    file_info = register_served_file(fpath)
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
    image_files: list[dict] | None = None,
) -> AsyncGenerator[str | dict, None]:
    """Run one dashboard chat turn through OpenCode and yield dashboard events."""
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
    request_timeout_seconds = max(request_timeout_seconds, idle_timeout_seconds + 30)

    conversation_id = getattr(observer, "conversation_id", None)
    session_cache_key = (conversation_id, selected_model) if conversation_id else None
    session_id = _opencode_session_cache.get(session_cache_key) if session_cache_key else None
    warm_session_used = False
    reused_session = bool(session_id)
    if not session_id:
        session_id = await _checkout_warm_session(selected_model)
        if session_id is None:
            session_id = await _create_session(full_prompt[:80] or "Dashboard chat")
            _schedule_prewarm(selected_model)
        else:
            warm_session_used = True
            _schedule_prewarm(selected_model)
        if session_cache_key:
            _opencode_session_cache[session_cache_key] = session_id
    else:
        logger.info("Reusing OpenCode session %s for conversation=%s model=%s", session_id, conversation_id, selected_model)
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

    base_url = await ensure_opencode_server()
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

    async for event in _iter_opencode_turn_events(
        base_url,
        session_id=session_id,
        body=body,
        idle_timeout_seconds=idle_timeout_seconds,
        request_timeout_seconds=request_timeout_seconds,
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
                    params={"directory": str(PROJECT_ROOT)},
                    timeout=30,
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
                raise OpenCodeError(_format_opencode_error(info["error"]))
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
