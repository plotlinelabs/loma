"""Credential-injection gateway tests: MCP proxying and model brokering.

All synthetic: upstreams are local test servers, credentials are fakes.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from broker.gateway import (
    McpProxyRegistry,
    configured_model_providers,
    create_gateway_app,
    gateway_base_url,
)
from broker.service import Denied

CAPABILITY = "loma_run_v1_" + "a" * 43


class FakeBroker:
    """Admits only CAPABILITY for model.request; optionally errors."""

    def __init__(self, fail_with: Exception | None = None):
        self.fail_with = fail_with
        self.calls = []

    async def execute(self, token, operation, resource, params=None):
        self.calls.append((token, operation, resource))
        if self.fail_with is not None:
            raise self.fail_with
        if token != CAPABILITY or operation not in {"model.request", "mcp.request"}:
            raise Denied()
        return {"ok": True}


async def make_upstream(recorder: list):
    async def handler(request):
        recorder.append({
            "path": request.path_qs,
            "headers": dict(request.headers),
            "body": (await request.read()).decode(),
        })
        return web.json_response({"upstream": "ok"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    return server


# ── Registry ─────────────────────────────────────────────────────────────


def test_registry_tokens_are_unguessable_and_revocable():
    registry = McpProxyRegistry()
    token = registry.register("linear", "https://mcp.example.test/sse", {"Authorization": "Bearer org-key"})
    assert token.startswith("loma_mcpproxy_") and len(token) > 30
    assert registry.lookup(token)["headers"] == {"Authorization": "Bearer org-key"}
    registry.revoke(token)
    with pytest.raises(Denied):
        registry.lookup(token)


def test_registry_rejects_plain_http_upstreams():
    registry = McpProxyRegistry()
    with pytest.raises(Denied):
        registry.register("x", "http://mcp.example.test/sse", {})
    # Loopback upstreams are allowed (local test/dev servers).
    registry.register("y", "http://127.0.0.1:9/x", {})


def test_registry_expiry(monkeypatch):
    registry = McpProxyRegistry()
    token = registry.register("x", "https://mcp.example.test", {}, ttl_seconds=0)
    with pytest.raises(Denied):
        registry.lookup(token)


# ── MCP proxy ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_proxy_injects_credentials_and_strips_client_auth():
    seen = []
    upstream = await make_upstream(seen)
    try:
        registry = McpProxyRegistry()
        token = registry.register(
            "linear", str(upstream.make_url("/mcp")), {"Authorization": "Bearer org-secret"}, capability=CAPABILITY,
        )
        app = create_gateway_app(FakeBroker(), registry)
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                f"/mcp/{token}",
                json={"jsonrpc": "2.0", "method": "tools/list"},
                headers={"Authorization": "Bearer worker-supplied",
                         "Cookie": "steal=me", "X-Api-Key": "nope"},
            )
            assert response.status == 200
            assert await response.json() == {"upstream": "ok"}
        request = seen[0]
        assert request["headers"]["Authorization"] == "Bearer org-secret"
        assert "Cookie" not in request["headers"]
        assert "X-Api-Key" not in request["headers"]
        assert "tools/list" in request["body"]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_mcp_proxy_denies_unknown_and_revoked_tokens():
    registry = McpProxyRegistry()
    token = registry.register("x", "https://mcp.example.test", {"Authorization": "Bearer k"})
    registry.revoke(token)
    app = create_gateway_app(FakeBroker(), registry)
    async with TestClient(TestServer(app)) as client:
        for bad in (token, "loma_mcpproxy_forged", "anything"):
            response = await client.post(f"/mcp/{bad}", json={})
            assert response.status == 403
            body = await response.json()
            assert "org-secret" not in str(body)


# ── Model proxy ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_proxy_requires_valid_capability(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-synthetic")
    broker = FakeBroker()
    app = create_gateway_app(broker, McpProxyRegistry())
    async with TestClient(TestServer(app)) as client:
        # Missing / forged bearer -> denied before any upstream contact.
        for headers in ({}, {"Authorization": "Bearer forged"},
                        {"x-api-key": "sk-ant-synthetic"}):
            response = await client.post("/model/anthropic/v1/messages", headers=headers, json={})
            assert response.status == 403


@pytest.mark.asyncio
async def test_model_proxy_fails_closed_on_broker_errors(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-synthetic")
    app = create_gateway_app(FakeBroker(fail_with=RuntimeError("db down")), McpProxyRegistry())
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/model/anthropic/v1/messages",
            headers={"Authorization": f"Bearer {CAPABILITY}"}, json={},
        )
        assert response.status == 503


@pytest.mark.asyncio
async def test_model_proxy_denies_unknown_or_unconfigured_providers(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_gateway_app(FakeBroker(), McpProxyRegistry())
    async with TestClient(TestServer(app)) as client:
        for provider in ("evil", "openai"):
            response = await client.post(
                f"/model/{provider}/v1/chat/completions",
                headers={"Authorization": f"Bearer {CAPABILITY}"}, json={},
            )
            assert response.status == 403


@pytest.mark.asyncio
async def test_model_proxy_injects_server_side_key(monkeypatch):
    seen = []
    upstream = await make_upstream(seen)
    try:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-synthetic")
        import broker.gateway as gateway_mod
        monkeypatch.setitem(
            gateway_mod.MODEL_PROVIDERS, "anthropic",
            {**gateway_mod.MODEL_PROVIDERS["anthropic"],
             "upstream": str(upstream.make_url("")).rstrip("/")},
        )
        broker = FakeBroker()
        app = create_gateway_app(broker, McpProxyRegistry())
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/model/anthropic/v1/messages",
                headers={"Authorization": f"Bearer {CAPABILITY}",
                         "anthropic-version": "2023-06-01"},
                json={"model": "claude-x", "messages": []},
            )
            assert response.status == 200
        request = seen[0]
        # The worker's capability never reaches the provider; the real key does.
        assert request["headers"]["x-api-key"] == "sk-ant-synthetic"
        assert CAPABILITY not in str(request["headers"])
        assert request["headers"]["anthropic-version"] == "2023-06-01"
        assert request["path"] == "/v1/messages"
        assert broker.calls == [(CAPABILITY, "model.request", "anthropic")]
    finally:
        await upstream.close()


def test_configured_model_providers_reads_env(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert configured_model_providers() == set()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-synthetic")
    assert configured_model_providers() == {"openai"}


def test_gateway_base_url_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("LOMA_GATEWAY_HOST", raising=False)
    monkeypatch.delenv("LOMA_GATEWAY_PORT", raising=False)
    assert gateway_base_url() == "http://127.0.0.1:3101"


@pytest.mark.asyncio
@pytest.mark.parametrize('capability', [None, 'invalid'])
async def test_mcp_proxy_requires_live_bound_capability(capability):
    seen = []
    upstream = await make_upstream(seen)
    try:
        registry = McpProxyRegistry()
        token = registry.register('linear', str(upstream.make_url('/mcp')), {}, capability=capability)
        async with TestClient(TestServer(create_gateway_app(FakeBroker(), registry))) as client:
            response = await client.post(f'/mcp/{token}', json={'method': 'tools/list'})
            assert response.status == 403
        assert seen == []
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_mcp_proxy_rechecks_authorization_outage_before_upstream():
    seen = []
    upstream = await make_upstream(seen)
    try:
        registry = McpProxyRegistry()
        token = registry.register('linear', str(upstream.make_url('/mcp')), {}, capability=CAPABILITY)
        broker = FakeBroker(fail_with=RuntimeError('unavailable'))
        async with TestClient(TestServer(create_gateway_app(broker, registry))) as client:
            response = await client.post(f'/mcp/{token}', json={'method': 'tools/list'})
            assert response.status == 503
        assert seen == []
    finally:
        await upstream.close()


@pytest.mark.parametrize('url', [None, 42, 'http://localhost.example.test/mcp',
    'http://127.0.0.1.example.test/mcp', 'http://localhost@example.test/mcp',
    'https://user:password@example.test/mcp', 'https://example.test/mcp#fragment',
    'https://example.test:invalid/mcp', 'https://example.test/space here'])
def test_mcp_registry_uses_parsed_authority_not_string_prefix(url):
    with pytest.raises(Denied):
        McpProxyRegistry().register('sample', url, {})


@pytest.mark.asyncio
@pytest.mark.parametrize('method,suffix', [('POST', '/extra'), ('GET', '?override=1'), ('PUT', ''), ('PATCH', '')])
async def test_mcp_endpoint_scope_cannot_expand(method, suffix):
    seen = []
    upstream = await make_upstream(seen)
    try:
        registry = McpProxyRegistry()
        token = registry.register('sample', str(upstream.make_url('/mcp')), {}, capability=CAPABILITY)
        async with TestClient(TestServer(create_gateway_app(FakeBroker(), registry))) as client:
            response = await client.request(method, f'/mcp/{token}{suffix}')
            assert response.status == 403
        assert not seen
    finally:
        await upstream.close()
