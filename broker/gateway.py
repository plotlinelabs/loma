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
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

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
    "chatgpt-account-id", "openai-organization", "openai-project",
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
        if not isinstance(url, str):
            raise Denied()
        try:
            parsed = urlsplit(url)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or any(c.isspace() for c in url)):
                raise Denied()
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            ):
                raise Denied()
            parsed.port  # Validate a supplied port before issuing a capability.
        except ValueError as exc:
            raise Denied() from exc
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


# Pinned upstreams for subscription-account proxying. Only providers whose
# CLI can be redirected at the protocol level appear here.
SUBSCRIPTION_UPSTREAMS = {
    "claude": "https://api.anthropic.com",
    "codex": "https://chatgpt.com/backend-api/codex",
}


class SubscriptionProxyRegistry:
    """Run-scoped proxies for pooled subscription-account auth.

    A worker never holds the subscription credential. It gets the gateway
    base URL plus an unguessable, revocable proxy token; on each request the
    gateway re-admits the run via the broker (``subscription.request``),
    reads the account's CURRENT auth material server-side, and injects it
    upstream. Tokens are revoked when the run ends.
    """

    def __init__(self):
        self._entries: dict[str, dict] = {}

    def register(self, provider: str, credentials_path: str | Path, *,
                 capability: str, ttl_seconds: int = 24 * 3600) -> str:
        if provider not in SUBSCRIPTION_UPSTREAMS or not capability:
            raise Denied()
        token = "loma_subproxy_" + secrets.token_urlsafe(24)
        self._entries[token] = {
            "provider": provider,
            "credentials_path": str(credentials_path),
            "capability": capability,
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


def _claude_subscription_auth(credentials_path: str) -> tuple[str, str]:
    """Read the CURRENT Claude subscription access token server-side.

    Returns (access_token, oauth_beta_flag). Raises Denied when the account
    has no usable auth material — never falls back to another credential.
    """
    try:
        data = json.loads(Path(credentials_path).read_text())
    except (OSError, ValueError) as exc:
        raise Denied() from exc
    if not isinstance(data, dict):
        raise Denied()
    oauth = data.get("claudeAiOauth") or {}
    if not isinstance(oauth, dict):
        raise Denied()
    access_token = oauth.get("accessToken") or ""
    if not isinstance(access_token, str) or not access_token:
        raise Denied()
    expires_at = oauth.get("expiresAt")
    if isinstance(expires_at, (int, float)) and expires_at / 1000 < time.time():
        raise Denied()
    return access_token, "oauth-2025-04-20"


def _codex_subscription_auth(credentials_path: str) -> dict[str, str]:
    """Read subscription auth only in the backend; never fall back to API billing."""
    try:
        data = json.loads(Path(credentials_path).read_text())
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(tokens, dict):
            raise Denied()
        access_token = tokens.get("access_token")
        account_id = tokens.get("account_id")
        if not all(isinstance(v, str) and v and "\r" not in v and "\n" not in v
                   for v in (access_token, account_id)):
            raise Denied()
        return {"Authorization": "Bearer " + access_token, "ChatGPT-Account-Id": account_id}
    except (OSError, ValueError) as exc:
        raise Denied() from exc


def create_gateway_app(broker, registry: McpProxyRegistry,
                       sub_registry: "SubscriptionProxyRegistry | None" = None) -> web.Application:
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
        # A transport grant is for one endpoint, not an authenticated proxy
        # for every route on that host. Static configured queries are kept;
        # callers cannot append a route, query, or arbitrary HTTP operation.
        if request.match_info.get("tail") or request.query_string or request.method not in {"GET", "POST", "DELETE"}:
            return web.json_response({"error": "Unsupported MCP transport request"}, status=403)
        upstream_url = entry["url"]
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
        inference_paths = {
            "anthropic": {"v1/messages", "v1/messages/count_tokens"},
            "openai": {"v1/responses", "v1/responses/compact", "v1/chat/completions"},
            "openrouter": {"v1/chat/completions", "v1/responses"},
            "opencode": {"chat/completions", "responses", "messages", "messages/count_tokens"},
        }
        model_path = "models" if provider == "opencode" else "v1/models"
        allowed = ((request.method == "POST" and tail in inference_paths[provider])
                   or (request.method == "GET" and tail == model_path))
        allowed_query = {"beta"} if provider == "anthropic" else set()
        if not allowed or set(request.query) - allowed_query:
            return web.json_response({"error": "Unsupported model operation"}, status=403)
        upstream_url = conf["upstream"].rstrip("/") + (f"/{tail}" if tail else "")
        if request.query_string:
            upstream_url += f"?{request.query_string}"
        try:
            return await _forward(
                request, upstream_url, {conf["header"]: conf["prefix"] + api_key},
            )
        except Exception:
            return web.json_response({"error": "Upstream unavailable"}, status=502)

    async def subscription_proxy(request: web.Request) -> web.StreamResponse:
        if sub_registry is None:
            return web.json_response({"error": "Subscription proxy unavailable"}, status=503)
        try:
            entry = sub_registry.lookup(request.match_info["token"])
            if not entry.get("capability"):
                raise Denied()
            await asyncio.wait_for(broker.execute(
                    entry["capability"], "subscription.request", entry["provider"],
                ), timeout=15)
        except Denied:
            return web.json_response({"error": "Access denied"}, status=403)
        except Exception:
            return web.json_response({"error": "Authorization unavailable"}, status=503)

        tail = request.match_info.get("tail", "")
        allowed_paths = {"claude": {"v1/messages", "v1/messages/count_tokens"},
                         "codex": {"responses", "responses/compact"}}
        if request.method != "POST" or tail not in allowed_paths.get(entry["provider"], set()):
            return web.json_response({"error": "Access denied"}, status=403)
        allowed_query = {"beta"} if entry["provider"] == "claude" else set()
        if set(request.query) - allowed_query:
            return web.json_response({"error": "Access denied"}, status=403)
        try:
            if entry["provider"] == "codex":
                inject = _codex_subscription_auth(entry["credentials_path"])
            else:
                access_token, beta_flag = _claude_subscription_auth(entry["credentials_path"])
                inject = {"Authorization": f"Bearer {access_token}"}
                existing_beta = request.headers.get("anthropic-beta", "")
                inject["anthropic-beta"] = ",".join(dict.fromkeys(
                    [*filter(None, existing_beta.split(",")), beta_flag]))
        except Denied:
            return web.json_response(
                {"error": "Subscription auth is unavailable for this account"}, status=503)
        tail = request.match_info.get("tail", "")
        upstream_url = SUBSCRIPTION_UPSTREAMS[entry["provider"]].rstrip("/")
        if tail:
            upstream_url += f"/{tail}"
        if request.query_string:
            upstream_url += f"?{request.query_string}"
        try:
            return await _forward(request, upstream_url, inject)
        except Exception:
            # Never relay upstream/connection error text (may embed secrets).
            return web.json_response({"error": "Upstream unavailable"}, status=502)

    app = web.Application(client_max_size=_MAX_BODY)
    app.router.add_route("*", "/mcp/{token}", mcp_proxy)
    app.router.add_route("*", "/mcp/{token}/{tail:.*}", mcp_proxy)
    app.router.add_route("*", "/model/{provider}/{tail:.*}", model_proxy)
    app.router.add_route("*", "/model/{provider}", model_proxy)
    app.router.add_route("*", "/sub/{token}", subscription_proxy)
    app.router.add_route("*", "/sub/{token}/{tail:.*}", subscription_proxy)
    return app
