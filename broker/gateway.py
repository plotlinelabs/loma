"""Credential-injection gateway for isolated workers.

Workers never receive org integration keys, per-user OAuth tokens, or model
provider API keys. Instead:

- **MCP proxying** — HTTP MCP servers configured for a run are registered
  here with their real upstream URL + auth headers. The worker's MCP config
  points at ``/mcp/{proxy_token}`` with no real credentials; the gateway
  validates the unguessable, revocable proxy token, strips client-supplied
  auth headers, and forwards with the registered headers injected.
  Stdio MCP servers cannot be proxied and are disabled in isolated mode
  (fail closed — never a silent fallback to org credentials).

- **Model proxying** — workers reach ``/model/{provider}/...`` with their
  run capability as the bearer token. The gateway asks the broker to admit
  the call (operation ``model.request``), then forwards to the pinned
  upstream with the server-side API key injected.

Bind this app to loopback (or a private, TLS-protected network in
distributed deployments). It must never be exposed publicly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time

import aiohttp
from aiohttp import web

from broker.service import Denied

logger = logging.getLogger(__name__)

_MAX_BODY = 32 * 1024 * 1024
_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=600, sock_connect=30)

# Headers never forwarded from the worker to an upstream.
_STRIP_REQUEST_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "host",
    "content-length", "transfer-encoding", "connection", "x-api-key",
}
_STRIP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "set-cookie",
}

# Pinned model provider upstreams and their credential injection.
MODEL_PROVIDERS = {
    "anthropic": {
        "upstream": "https://api.anthropic.com",
        "env_key": "ANTHROPIC_API_KEY",
        "header": "x-api-key",
        "prefix": "",
    },
    "openai": {
        "upstream": "https://api.openai.com",
        "env_key": "OPENAI_API_KEY",
        "header": "Authorization",
        "prefix": "Bearer ",
    },
    "openrouter": {
        "upstream": "https://openrouter.ai/api",
        "env_key": "OPENROUTER_API_KEY",
        "header": "Authorization",
        "prefix": "Bearer ",
    },
    "opencode": {
        "upstream": "https://opencode.ai/zen/v1",
        "env_key": "OPENCODE_API_KEY",
        "header": "Authorization",
        "prefix": "Bearer ",
    },
}


def configured_model_providers() -> set[str]:
    """Providers with a server-side credential available for injection."""
    return {
        name for name, conf in MODEL_PROVIDERS.items()
        if os.environ.get(conf["env_key"], "").strip()
    }


def gateway_base_url() -> str:
    host = os.environ.get("LOMA_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("LOMA_GATEWAY_PORT", "3101"))
    return f"http://{host}:{port}"


class McpProxyRegistry:
    """Server-side registry of MCP upstreams keyed by unguessable tokens.

    Registered by the trusted controller when it builds a worker's MCP
    config; revoked when the owning client/run is discarded. The upstream
    URL and headers stay in backend memory only.
    """

    def __init__(self):
        self._entries: dict[str, dict] = {}

    def register(self, server_name: str, url: str, headers: dict | None,
                 *, ttl_seconds: int = 24 * 3600, capability: str | None = None) -> str:
        if not isinstance(url, str) or not url.startswith("https://"):
            # Plain-http upstreams would leak injected credentials in transit.
            if not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
                raise Denied()
        token = "loma_mcpproxy_" + secrets.token_urlsafe(24)
        self._entries[token] = {
            "name": server_name,
            "capability": capability,
            "url": url.rstrip("/"),
            "headers": dict(headers or {}),
            "expires_at": time.monotonic() + ttl_seconds,
        }
        return token

    def revoke(self, token: str) -> None:
        self._entries.pop(token, None)

    def lookup(self, token: str) -> dict:
        entry = self._entries.get(token)
        if not entry or entry["expires_at"] <= time.monotonic():
            self._entries.pop(token, None)
            raise Denied()
        return entry


def create_gateway_app(broker, registry: McpProxyRegistry) -> web.Application:
    async def _forward(request: web.Request, upstream_url: str,
                       inject_headers: dict[str, str]) -> web.StreamResponse:
        headers = {
            key: value for key, value in request.headers.items()
            if key.lower() not in _STRIP_REQUEST_HEADERS
        }
        headers.update(inject_headers)
        body = await request.read()
        if len(body) > _MAX_BODY:
            return web.json_response({"error": "Request too large"}, status=413)

        async with aiohttp.ClientSession(
            timeout=_UPSTREAM_TIMEOUT, trust_env=False, auto_decompress=False,
        ) as session:
            async with session.request(
                request.method, upstream_url, data=body or None,
                headers=headers, allow_redirects=False,
            ) as upstream:
                response = web.StreamResponse(status=upstream.status)
                for key, value in upstream.headers.items():
                    if key.lower() not in _STRIP_RESPONSE_HEADERS:
                        response.headers[key] = value
                response.headers["Cache-Control"] = "no-store"
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response

    async def mcp_proxy(request: web.Request) -> web.StreamResponse:
        try:
            entry = registry.lookup(request.match_info["token"])
            if not entry.get("capability"):
                raise Denied()
            await asyncio.wait_for(broker.execute(
                entry["capability"], "mcp.request", entry["name"],
            ), timeout=15)
        except Denied:
            return web.json_response({"error": "Access denied"}, status=403)
        except Exception:
            return web.json_response({"error": "Authorization unavailable"}, status=503)
        tail = request.match_info.get("tail", "")
        upstream_url = entry["url"] + (f"/{tail}" if tail else "")
        if request.query_string:
            upstream_url += f"?{request.query_string}"
        try:
            return await _forward(request, upstream_url, entry["headers"])
        except Exception:
            # Never relay upstream/connection error text (may embed secrets).
            return web.json_response({"error": "Upstream unavailable"}, status=502)

    async def model_proxy(request: web.Request) -> web.StreamResponse:
        provider = request.match_info["provider"]
        conf = MODEL_PROVIDERS.get(provider)
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-api-key", "")
        if conf is None:
            return web.json_response({"error": "Unknown model provider"}, status=403)
        api_key = os.environ.get(conf["env_key"], "").strip()
        if not api_key:
            return web.json_response(
                {"error": f"Model provider {provider} is not configured on this deployment"},
                status=403,
            )
        try:
            await asyncio.wait_for(
                broker.execute(token, "model.request", provider), timeout=15,
            )
        except Denied:
            return web.json_response({"error": "Access denied"}, status=403)
        except Exception:
            # Fail closed on any admission-path failure.
            return web.json_response({"error": "Broker unavailable"}, status=503)

        tail = request.match_info.get("tail", "")
        upstream_url = conf["upstream"].rstrip("/") + (f"/{tail}" if tail else "")
        if request.query_string:
            upstream_url += f"?{request.query_string}"
        try:
            return await _forward(
                request, upstream_url, {conf["header"]: conf["prefix"] + api_key},
            )
        except Exception:
            return web.json_response({"error": "Upstream unavailable"}, status=502)

    app = web.Application(client_max_size=_MAX_BODY)
    app.router.add_route("*", "/mcp/{token}", mcp_proxy)
    app.router.add_route("*", "/mcp/{token}/{tail:.*}", mcp_proxy)
    app.router.add_route("*", "/model/{provider}/{tail:.*}", model_proxy)
    app.router.add_route("*", "/model/{provider}", model_proxy)
    return app
