"""Trusted execution controller: run lifecycle, grants, and worker configs.

The controller is the ONLY component allowed to mint or revoke run
capabilities. It runs inside the backend process, next to the broker, and:

- starts/ends runs (fresh workspace + capability issuance + revocation),
- decides resource grants from deployment policy and integration sharing
  rules — never from prompts or model output,
- rewrites MCP configs so workers talk to the credential-injection gateway
  instead of holding real credentials, and disables (fail closed) anything
  the broker/gateway cannot mediate yet.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from contextvars import ContextVar
from pathlib import Path

from broker.gateway import (
    McpProxyRegistry,
    configured_model_providers,
    gateway_base_url,
)
from broker.grain import GrainTranscript
from broker.operations import (
    ALL_TOOLS,
    INTEGRATION_TOOLS,
    PERSONAL_TOOLS,
    UTILITY_TOOLS,
    ModelRequest,
    McpRequest,
    ToolInvoke,
)
from broker.service import Broker
from broker import worker as worker_mod

logger = logging.getLogger(__name__)

current_run: ContextVar = ContextVar("loma_run", default=None)

_broker: Broker | None = None
_registry: McpProxyRegistry | None = None


class ExecutionUnavailable(RuntimeError):
    """The execution controller is not initialized; runs must fail closed."""


def broker_url() -> str:
    host = os.environ.get("LOMA_BROKER_HOST", "127.0.0.1")
    port = int(os.environ.get("LOMA_BROKER_PORT", "3100"))
    return f"http://{host}:{port}"


def _deployment_id() -> str:
    explicit = os.environ.get("LOMA_DEPLOYMENT_ID", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("PUBLIC_BASE_URL", "").strip() or "loma-default-deployment"
    return "dep-" + hashlib.sha256(base.encode()).hexdigest()[:16]


async def init_execution_controller(db) -> Broker:
    """Create the broker + proxy registry singletons. Called at startup."""
    global _broker, _registry
    operations = {
        "tool.invoke": ToolInvoke(),
        "mcp.request": McpRequest(),
        "grain.transcript": GrainTranscript(),
        "model.request": ModelRequest(configured_model_providers()),
    }
    _broker = Broker(db, _deployment_id(), operations)
    await _broker.initialize()
    _registry = McpProxyRegistry()
    logger.info(
        "Execution controller initialized (deployment=%s, model_providers=%s)",
        _deployment_id(), sorted(configured_model_providers()),
    )
    return _broker


def get_broker() -> Broker:
    if _broker is None:
        raise ExecutionUnavailable("Execution broker is not initialized")
    return _broker


def get_proxy_registry() -> McpProxyRegistry:
    if _registry is None:
        raise ExecutionUnavailable("Execution gateway registry is not initialized")
    return _registry


def controller_ready() -> bool:
    return _broker is not None and _registry is not None


# ── Run lifecycle ────────────────────────────────────────────────────────


@dataclass
class RunContext:
    run_id: str | None
    capability: str | None
    workspace: Path
    user_email: str | None
    proxy_tokens: list[str] = field(default_factory=list)

    @property
    def worker_env_extra(self) -> dict[str, str]:
        extra = {
            "LOMA_BROKER_URL": broker_url(),
            "LOMA_GATEWAY_URL": gateway_base_url(),
        }
        if self.run_id:
            extra["LOMA_RUN_ID"] = self.run_id
        return extra


async def _denied_integration_providers(db, user_email: str) -> set[str]:
    """Providers whose sharing rules exclude this user (fail closed on error)."""
    from integrations.access import can_access
    denied = set(INTEGRATION_TOOLS.values())
    async for integration in db.integrations.find({"status": "active"}):
        if await can_access(db, integration, user_email):
            denied.discard(integration.get("provider"))
    return denied


async def _tool_grants(db, user_email: str) -> list[str]:
    denied_providers = await _denied_integration_providers(db, user_email)
    tools = set(PERSONAL_TOOLS) | set(UTILITY_TOOLS)
    for tool, provider in INTEGRATION_TOOLS.items():
        if provider not in denied_providers:
            tools.add(tool)
    return sorted(tools)


async def start_run(user_email: str | None, *, source: str = "run") -> RunContext:
    """Create a workspace and (when the run has an owner) a run capability.

    Runs without an authenticated owner (e.g. anonymous webhooks) get a
    workspace but no capability: every brokered operation fails closed.
    """
    workspace = worker_mod.create_workspace(prefix=source)
    worker_mod.populate_tool_shims(workspace, sorted(ALL_TOOLS))

    if not user_email or not controller_ready():
        if user_email and not controller_ready():
            logger.error(
                "Execution controller unavailable — run for %s proceeds with "
                "NO tool/credential access (fail closed)", user_email,
            )
        return RunContext(run_id=None, capability=None, workspace=workspace,
                          user_email=user_email)

    broker = get_broker()
    grants: dict = {"tool.invoke": await _tool_grants(broker.db, user_email)}
    from integrations.access import can_access
    from integrations.registry import PROVIDER_CATALOG
    mcp_grants = []
    async for integration in broker.db.integrations.find({"status": "active"}):
        if await can_access(broker.db, integration, user_email):
            provider = integration["provider"]
            mcp_grants.append(PROVIDER_CATALOG.get(provider, {}).get("mcp_server_name") or provider)
    for provider in ("grain", "hubspot", "notion"):
        if await broker.db.oauth_tokens.find_one({"provider": provider, "user_email": user_email}):
            mcp_grants.append(PROVIDER_CATALOG.get(provider, {}).get("mcp_server_name") or provider)
    if mcp_grants:
        grants["mcp.request"] = sorted(set(mcp_grants))
    providers = configured_model_providers()
    if providers:
        grants["model.request"] = sorted(providers)

    ttl = int(os.environ.get("LOMA_RUN_TTL_SECONDS", "3600"))
    max_calls = int(os.environ.get("LOMA_RUN_MAX_CALLS", "500"))
    try:
        run_id, capability = await broker.issue(
            user_email=user_email, grants=grants,
            ttl_seconds=ttl, max_calls=max_calls,
        )
    except Exception:
        # Inactive user, DB failure, invalid grants: fail closed with no
        # capability rather than falling back to ambient credentials.
        logger.exception("Capability issuance failed for %s — run gets no tool access", user_email)
        return RunContext(run_id=None, capability=None, workspace=workspace,
                          user_email=user_email)
    return RunContext(run_id=run_id, capability=capability, workspace=workspace,
                      user_email=user_email)


async def end_run(ctx: RunContext) -> None:
    """Revoke the run's capability and proxy tokens; clean its workspace."""
    if ctx is None:
        return
    if ctx.run_id and controller_ready():
        try:
            await get_broker().revoke(ctx.run_id)
        except Exception:
            logger.exception("Failed to revoke run %s", ctx.run_id)
    if ctx.proxy_tokens and _registry is not None:
        for token in ctx.proxy_tokens:
            _registry.revoke(token)
    worker_mod.cleanup_workspace(ctx.workspace)


# ── Worker-facing MCP config rewriting ───────────────────────────────────


def proxy_mcp_servers_for_worker(mcp_servers: dict) -> tuple[dict, list[str], list[str]]:
    """Rewrite an MCP server config so no credentials enter the worker.

    HTTP-type servers are re-pointed at the gateway with a revocable proxy
    token; their real URL + auth headers stay server-side. Stdio servers
    (which would carry credentials in env vars inside the worker) are
    DISABLED in isolated mode — fail closed, never a silent org-credential
    fallback.

    Returns (proxied_config, proxy_tokens, disabled_server_names).
    """
    registry = get_proxy_registry()
    ctx = current_run.get()
    if ctx is None or not ctx.capability:
        return {}, [], sorted(mcp_servers or {})
    proxied: dict = {}
    tokens: list[str] = []
    disabled: list[str] = []
    for name, conf in (mcp_servers or {}).items():
        if not isinstance(conf, dict):
            continue
        server_type = conf.get("type")
        if server_type in ("http", "sse", "remote", "streamable-http") and conf.get("url"):
            try:
                token = registry.register(name, conf["url"], conf.get("headers"), capability=ctx.capability)
            except Exception:
                logger.warning("MCP server %s could not be proxied; disabling for workers", name)
                disabled.append(name)
                continue
            tokens.append(token)
            ctx.proxy_tokens.append(token)
            proxied[name] = {
                "type": "http",
                "url": f"{gateway_base_url()}/mcp/{token}",
            }
        else:
            disabled.append(name)
    if disabled:
        logger.info(
            "Isolated worker mode: MCP servers disabled pending broker support: %s",
            sorted(disabled),
        )
    return proxied, tokens, disabled
