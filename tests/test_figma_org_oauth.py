"""Figma as a ready-made Org integration with one shared OAuth login.

One admin logs in; the token lives on the org integration record, is refreshed
on demand (once, even under concurrent runs) and is injected for every user.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import web
from cryptography.fernet import Fernet
from mongomock_motor import AsyncMongoMockClient

from api import integration_routes, oauth_helpers, oauth_routes
from api.oauth_helpers import decrypt_token, encrypt_token
from integrations.registry import PROVIDER_CATALOG, is_shared_oauth, shared_oauth_providers

FIGMA_META = {
    "authorization_endpoint": "https://www.figma.com/oauth/mcp",
    "token_endpoint": "https://api.figma.com/v1/oauth/token",
    "registration_endpoint": "https://api.figma.com/v1/oauth/mcp/register",
    "scopes_supported": ["mcp:connect"],
}
REDIRECT = "https://loma.example/api/oauth/org/figma/callback"


class FakeRequest(dict):
    """Minimal aiohttp-like request: dict for auth context + match_info/query."""

    def __init__(self, email="admin@example.com", role="admin", query=None, provider="figma"):
        super().__init__(user_email=email, system_role=role)
        self.match_info = {"provider": provider}
        self.query = query or {}


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    database = AsyncMongoMockClient().test
    monkeypatch.setattr(integration_routes, "get_db", lambda: database)
    monkeypatch.setattr(oauth_helpers, "get_db", lambda: database)
    monkeypatch.setattr(oauth_routes, "_oauth_redirect_uri", lambda *_: REDIRECT)
    monkeypatch.setattr(
        integration_routes, "_discover_shared_oauth_metadata", AsyncMock(return_value=dict(FIGMA_META)),
    )
    return database


async def _active_figma(db, expires_in_seconds=3600, refresh="refresh-1", status=None):
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    doc = {
        "provider": "figma",
        "status": "active",
        "connected_by": "admin@example.com",
        "oauth_config": {
            "authorization_endpoint": FIGMA_META["authorization_endpoint"],
            "token_endpoint": FIGMA_META["token_endpoint"],
            "client_id_encrypted": encrypt_token("client-1"),
            "client_secret_encrypted": encrypt_token("secret-1"),
            "token_endpoint_auth_method": "client_secret_post",
            "redirect_uri": REDIRECT,
            "scopes": ["mcp:connect"],
        },
        "oauth_tokens": {
            "access_token": encrypt_token("access-1"),
            "refresh_token": encrypt_token(refresh) if refresh else None,
            "token_expiry": expiry,
        },
    }
    if status:
        doc["oauth_status"] = status
    await db.integrations.insert_one(doc)


# ── Catalog ──────────────────────────────────────────────────────────────


def test_figma_is_shared_oauth_catalog_entry():
    entry = PROVIDER_CATALOG["figma"]
    assert entry["auth_type"] == "oauth_shared"
    assert entry["mcp_server_name"] == "figma"
    assert entry["oauth"]["mcp_url"] == "https://mcp.figma.com/mcp"
    assert entry["oauth"]["client_name"] == "Claude Code"
    assert is_shared_oauth("figma") and not is_shared_oauth("github")
    assert shared_oauth_providers() == ["figma"]


# ── Authorize ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authorize_requires_admin(db):
    with pytest.raises(web.HTTPForbidden):
        await integration_routes._org_oauth_authorize(FakeRequest(role="operator"))


@pytest.mark.asyncio
async def test_authorize_rejects_non_shared_provider(db):
    resp = await integration_routes._org_oauth_authorize(FakeRequest(provider="github"))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_authorize_registers_as_claude_code_and_reuses_client(db, monkeypatch):
    register = AsyncMock(return_value={
        "client_id": "figma-client", "client_secret": "figma-secret",
        "token_endpoint_auth_method": "client_secret_post",
    })
    monkeypatch.setattr(integration_routes, "register_oauth_client", register)

    resp = await integration_routes._org_oauth_authorize(FakeRequest())
    assert resp.status == 200
    register.assert_awaited_once_with(
        registration_endpoint=FIGMA_META["registration_endpoint"],
        redirect_uri=REDIRECT,
        client_name="Claude Code",
    )
    url = urlparse(json.loads(resp.body)["authorize_url"])
    params = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == FIGMA_META["authorization_endpoint"]
    assert params["client_id"] == ["figma-client"]
    assert params["redirect_uri"] == [REDIRECT]
    assert params["scope"] == ["mcp:connect"]
    assert params["code_challenge_method"] == ["S256"]
    assert oauth_helpers.verify_oauth_state(params["state"][0]) == "admin@example.com"

    doc = await db.integrations.find_one({"provider": "figma"})
    assert doc["status"] == "pending"  # not usable until the login finishes
    assert decrypt_token(doc["pending_oauth_config"]["client_id_encrypted"]) == "figma-client"

    # A second click reuses the registration instead of creating another client.
    await integration_routes._org_oauth_authorize(FakeRequest())
    register.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconnect_keeps_live_connection_active(db, monkeypatch):
    await _active_figma(db)
    monkeypatch.setattr(integration_routes, "register_oauth_client", AsyncMock())
    resp = await integration_routes._org_oauth_authorize(FakeRequest())
    assert resp.status == 200
    doc = await db.integrations.find_one({"provider": "figma"})
    assert doc["status"] == "active"
    assert decrypt_token(doc["oauth_config"]["client_id_encrypted"]) == "client-1"


# ── Callback ─────────────────────────────────────────────────────────────


async def _start_login(db, monkeypatch):
    monkeypatch.setattr(integration_routes, "register_oauth_client", AsyncMock(return_value={
        "client_id": "figma-client", "client_secret": "figma-secret",
        "token_endpoint_auth_method": "client_secret_post",
    }))
    resp = await integration_routes._org_oauth_authorize(FakeRequest())
    return parse_qs(urlparse(json.loads(resp.body)["authorize_url"]).query)["state"][0]


@pytest.mark.asyncio
async def test_callback_stores_shared_tokens(db, monkeypatch):
    state = await _start_login(db, monkeypatch)
    exchange = AsyncMock(return_value={
        "access_token": "access-new", "refresh_token": "refresh-new",
        "expires_in": 3600, "scope": "mcp:connect",
    })
    monkeypatch.setattr(integration_routes, "_exchange_oauth_code", exchange)

    resp = await integration_routes._org_oauth_callback(
        FakeRequest(query={"code": "abc", "state": state}),
    )
    assert "oauth-complete" in resp.text
    kwargs = exchange.await_args.kwargs
    assert kwargs["client_id"] == "figma-client"
    assert kwargs["client_secret"] == "figma-secret"
    assert kwargs["redirect_uri"] == REDIRECT
    assert kwargs["code_verifier"]

    doc = await db.integrations.find_one({"provider": "figma"})
    assert doc["status"] == "active"
    assert doc["oauth_status"] == "connected"
    assert doc["connected_by"] == "admin@example.com"
    assert "pending_oauth_config" not in doc
    assert decrypt_token(doc["oauth_config"]["client_id_encrypted"]) == "figma-client"
    assert doc["oauth_tokens"]["access_token"] != "access-new"  # encrypted at rest
    assert decrypt_token(doc["oauth_tokens"]["access_token"]) == "access-new"
    assert decrypt_token(doc["oauth_tokens"]["refresh_token"]) == "refresh-new"


@pytest.mark.asyncio
@pytest.mark.parametrize("req_kwargs", [
    {"role": "operator"},
    {"email": "someone-else@example.com"},
])
async def test_callback_rejects_wrong_user(db, monkeypatch, req_kwargs):
    state = await _start_login(db, monkeypatch)
    exchange = AsyncMock()
    monkeypatch.setattr(integration_routes, "_exchange_oauth_code", exchange)
    resp = await integration_routes._org_oauth_callback(
        FakeRequest(query={"code": "abc", "state": state}, **req_kwargs),
    )
    assert "oauth-error" in resp.text
    exchange.assert_not_awaited()
    assert (await db.integrations.find_one({"provider": "figma"}))["status"] == "pending"


@pytest.mark.asyncio
async def test_callback_does_not_echo_provider_error(db):
    resp = await integration_routes._org_oauth_callback(
        FakeRequest(query={"error": "<script>alert(1)</script>"}),
    )
    assert "<script>alert(1)" not in resp.text
    assert "oauth-error" in resp.text


@pytest.mark.asyncio
async def test_callback_rejects_bad_state(db):
    resp = await integration_routes._org_oauth_callback(
        FakeRequest(query={"code": "abc", "state": "forged"}),
    )
    assert "oauth-error" in resp.text


# ── Token refresh ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fresh_token_returned_without_refresh(db, monkeypatch):
    await _active_figma(db)
    refresh = AsyncMock()
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", refresh)
    assert await oauth_helpers.get_valid_org_oauth_token("figma", db=db) == "access-1"
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_token_refreshed_once_under_concurrency(db, monkeypatch):
    await _active_figma(db, expires_in_seconds=-10)

    async def slow_refresh(**kwargs):
        await asyncio.sleep(0.05)
        return {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}

    refresh = AsyncMock(side_effect=slow_refresh)
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", refresh)

    tokens = await asyncio.gather(*[
        oauth_helpers.get_valid_org_oauth_token("figma", db=db) for _ in range(5)
    ])
    assert tokens == ["access-2"] * 5
    refresh.assert_awaited_once()
    kwargs = refresh.await_args.kwargs
    assert kwargs["refresh_token"] == "refresh-1"
    assert kwargs["client_id"] == "client-1"
    doc = await db.integrations.find_one({"provider": "figma"})
    assert decrypt_token(doc["oauth_tokens"]["refresh_token"]) == "refresh-2"


@pytest.mark.asyncio
async def test_refresh_keeps_old_refresh_token_when_not_rotated(db, monkeypatch):
    await _active_figma(db, expires_in_seconds=-10)
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", AsyncMock(
        return_value={"access_token": "access-2", "expires_in": 3600},
    ))
    assert await oauth_helpers.get_valid_org_oauth_token("figma", db=db) == "access-2"
    doc = await db.integrations.find_one({"provider": "figma"})
    assert decrypt_token(doc["oauth_tokens"]["refresh_token"]) == "refresh-1"


@pytest.mark.asyncio
async def test_failed_refresh_marks_expired_and_stops_retrying(db, monkeypatch):
    await _active_figma(db, expires_in_seconds=-10)
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", refresh)

    assert await oauth_helpers.get_valid_org_oauth_token("figma", db=db) is None
    doc = await db.integrations.find_one({"provider": "figma"})
    assert doc["oauth_status"] == "expired"

    assert await oauth_helpers.get_valid_org_oauth_token("figma", db=db) is None
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_refresh_token_marks_expired(db):
    await _active_figma(db, expires_in_seconds=-10, refresh=None)
    assert await oauth_helpers.get_valid_org_oauth_token("figma", db=db) is None
    assert (await db.integrations.find_one({"provider": "figma"}))["oauth_status"] == "expired"


@pytest.mark.asyncio
async def test_pending_login_is_not_used(db):
    await db.integrations.insert_one({"provider": "figma", "status": "pending"})
    assert await oauth_helpers.get_valid_org_oauth_token("figma", db=db) is None


# ── Agent wiring ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_user_gets_shared_figma_server(db):
    from agent.client import build_user_mcp_overrides

    await _active_figma(db)
    with patch("observability.db.get_db", return_value=db):
        for user in ("a@example.com", "b@example.com"):
            overrides = await build_user_mcp_overrides(user)
            assert overrides["figma"] == {
                "type": "http",
                "url": "https://mcp.figma.com/mcp",
                "headers": {"Authorization": "Bearer access-1"},
            }


@pytest.mark.asyncio
async def test_sharing_rules_still_exclude_figma(db):
    from agent.client import get_excluded_integrations_for_user

    await _active_figma(db)
    await db.integrations.update_one(
        {"provider": "figma"},
        {"$set": {"shared_with": {"mode": "specific", "users": ["a@example.com"]}}},
    )
    with patch("observability.db.get_db", return_value=db):
        assert "figma" not in await get_excluded_integrations_for_user("a@example.com")
        assert "figma" in await get_excluded_integrations_for_user("b@example.com")


@pytest.mark.asyncio
async def test_shared_pool_config_skips_figma(db):
    from agent.client import merge_db_integrations

    await _active_figma(db)
    with patch("observability.db.get_db", return_value=db):
        config = await merge_db_integrations({"mcp_servers": {}})
    assert "figma" not in config["mcp_servers"]


# ── Org integration API ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_key_connect_rejected_for_figma(db):
    request = FakeRequest()
    request.json = AsyncMock(return_value={"provider": "figma", "api_key": "x"})
    resp = await integration_routes._connect_integration(request)
    assert resp.status == 400
    assert await db.integrations.count_documents({}) == 0


@pytest.mark.asyncio
async def test_disconnect_figma_requires_admin(db, monkeypatch):
    await _active_figma(db)
    monkeypatch.setattr(integration_routes, "_reload_pool", AsyncMock())
    with pytest.raises(web.HTTPForbidden):
        await integration_routes._disconnect_integration(FakeRequest(role="operator"))
    resp = await integration_routes._disconnect_integration(FakeRequest())
    assert resp.status == 200
    assert await db.integrations.count_documents({"provider": "figma"}) == 0


@pytest.mark.asyncio
async def test_list_shows_figma_card_with_oauth_status(db, monkeypatch):
    monkeypatch.setattr(integration_routes, "get_user_email", lambda _: "admin@example.com")
    resp = await integration_routes._list_integrations(FakeRequest())
    figma = next(i for i in json.loads(resp.body) if i["provider"] == "figma")
    assert figma["auth_type"] == "oauth_shared"
    assert figma["status"] == "not_connected"

    await _active_figma(db, status="expired")
    resp = await integration_routes._list_integrations(FakeRequest())
    figma = next(i for i in json.loads(resp.body) if i["provider"] == "figma")
    assert figma["status"] == "connected"
    assert figma["oauth_status"] == "expired"


@pytest.mark.asyncio
async def test_legacy_custom_figma_connector_is_never_touched(db, monkeypatch):
    await db.integrations.insert_one({
        "provider": "figma", "is_custom": True, "status": "active",
        "mcp_url": "https://mcp.figma.com/mcp", "auth_mode": "oauth",
    })
    register = AsyncMock()
    monkeypatch.setattr(integration_routes, "register_oauth_client", register)
    monkeypatch.setattr(integration_routes, "_reload_pool", AsyncMock())

    resp = await integration_routes._org_oauth_authorize(FakeRequest())
    assert resp.status == 409
    register.assert_not_awaited()

    resp = await integration_routes._disconnect_integration(FakeRequest())
    assert resp.status == 404
    doc = await db.integrations.find_one({"provider": "figma"})
    assert doc["is_custom"] is True and "pending_oauth_config" not in doc
    assert await oauth_helpers.get_valid_org_oauth_token("figma", db=db) is None
