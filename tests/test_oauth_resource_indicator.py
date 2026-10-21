"""Custom MCP OAuth sends the RFC 8707 resource indicator so audience-bound servers accept the token."""
from datetime import datetime, timedelta, timezone

import pytest

from api import oauth_helpers
from api.oauth_helpers import (
    _exchange_oauth_code,
    _refresh_oauth_token,
    canonical_mcp_resource,
    resolve_mcp_resource,
)


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return str(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    """Fake aiohttp.ClientSession: records calls, answers from a url->(status, payload) map."""

    def __init__(self, routes, calls):
        self.routes = routes
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return _Resp(*self.routes.get(url, (404, {})))

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return _Resp(*self.routes.get(url, (200, {"access_token": "at"})))


def _patch_session(monkeypatch, routes=None):
    calls = []
    monkeypatch.setattr(oauth_helpers.aiohttp, "ClientSession", lambda *a, **k: _Session(routes or {}, calls))
    return calls


@pytest.mark.parametrize("url,expected", [
    ("https://mcp.slides.com/", "https://mcp.slides.com"),
    ("https://MCP.Example.com/mcp/", "https://mcp.example.com/mcp"),
    ("https://mcp.example.com/mcp?x=1#f", "https://mcp.example.com/mcp"),
])
def test_canonical_mcp_resource(url, expected):
    assert canonical_mcp_resource(url) == expected


@pytest.mark.asyncio
async def test_resolve_uses_advertised_resource(monkeypatch):
    _patch_session(monkeypatch, {
        "https://mcp.slides.com/.well-known/oauth-protected-resource": (200, {"resource": "https://mcp.slides.com"}),
    })
    assert await resolve_mcp_resource("https://mcp.slides.com/") == "https://mcp.slides.com"


@pytest.mark.asyncio
async def test_resolve_prefers_path_specific_metadata(monkeypatch):
    calls = _patch_session(monkeypatch, {
        "https://a.example/.well-known/oauth-protected-resource/mcp": (200, {"resource": "https://a.example/mcp"}),
    })
    assert await resolve_mcp_resource("https://a.example/mcp") == "https://a.example/mcp"
    assert calls[0][1] == "https://a.example/.well-known/oauth-protected-resource/mcp"


@pytest.mark.asyncio
async def test_resolve_ignores_foreign_origin_and_falls_back(monkeypatch):
    _patch_session(monkeypatch, {
        "https://a.example/.well-known/oauth-protected-resource": (200, {"resource": "https://evil.example"}),
    })
    assert await resolve_mcp_resource("https://a.example/") == "https://a.example"


@pytest.mark.asyncio
async def test_resolve_falls_back_when_no_metadata(monkeypatch):
    _patch_session(monkeypatch)
    assert await resolve_mcp_resource("https://a.example/mcp/") == "https://a.example/mcp"


@pytest.mark.asyncio
async def test_code_exchange_sends_resource_only_when_given(monkeypatch):
    calls = _patch_session(monkeypatch)
    await _exchange_oauth_code("https://as/token", "c", "id", "", "https://cb", auth_method="none",
                               code_verifier="v", resource="https://mcp.slides.com")
    await _exchange_oauth_code("https://as/token", "c", "id", "s", "https://cb")
    assert calls[0][2]["data"]["resource"] == "https://mcp.slides.com"
    assert "resource" not in calls[1][2]["data"]


@pytest.mark.asyncio
async def test_refresh_sends_resource_only_when_given(monkeypatch):
    calls = _patch_session(monkeypatch)
    await _refresh_oauth_token("https://as/token", "rt", "id", "", auth_method="none", resource="https://r")
    await _refresh_oauth_token("https://as/token", "rt", "id", "s")
    assert calls[0][2]["data"]["resource"] == "https://r"
    assert "resource" not in calls[1][2]["data"]


class _Coll:
    def __init__(self, doc):
        self.doc = doc
        self.updates = []

    async def find_one(self, *_a, **_k):
        return self.doc

    async def update_one(self, *a, **k):
        self.updates.append(a)


class _DB:
    def __init__(self, token_doc, integration):
        self.oauth_tokens = _Coll(token_doc)
        self.integrations = _Coll(integration)
        self.users = _Coll(None)


@pytest.mark.parametrize("stored,expected", [("https://mcp.slides.com", "https://mcp.slides.com"), (None, None)])
@pytest.mark.asyncio
async def test_custom_mcp_refresh_uses_resource_from_token(monkeypatch, stored, expected):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    enc = oauth_helpers.encrypt_token
    token_doc = {
        "access_token": enc("old"), "refresh_token": enc("rt"),
        "token_expiry": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    if stored:
        token_doc["resource"] = stored
    integration = {"oauth_config": {
        "client_id_encrypted": enc("cid"), "token_endpoint": "https://as/token",
        "token_endpoint_auth_method": "none", "resource": "https://mcp.slides.com",
    }}
    seen = {}

    async def fake_refresh(**kw):
        seen.update(kw)
        return {"access_token": "new", "expires_in": 3600}

    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", fake_refresh)
    token = await oauth_helpers.get_valid_custom_mcp_token("u@x", "slides_com", db=_DB(token_doc, integration))
    assert token == "new"
    assert seen["resource"] == expected
