"""Figma's temporary MCP registration name must not affect other connectors."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from api import integration_routes, oauth_routes


@pytest.mark.asyncio
@pytest.mark.parametrize("url,client_name", [
    ("https://mcp.figma.com/mcp", "Claude Code"),
    ("https://mcp.figma.com/mcp/", "Claude Code"),
    ("https://example.com/mcp", "Loma"),
    ("https://mcp.figma.com.evil.example/mcp", "Loma"),
    ("http://mcp.figma.com/mcp", "Loma"),
    ("https://mcp.figma.com/other", "Loma"),
])
@pytest.mark.parametrize("manual_client_id", ["", "manual-client"])
async def test_custom_connector_registration(monkeypatch, url, client_name, manual_client_id):
    db = AsyncMongoMockClient().test
    register = AsyncMock(return_value={
        "client_id": "registered-client",
        "client_secret": "",
        "token_endpoint_auth_method": "none",
    })
    monkeypatch.setattr(integration_routes, "get_db", lambda: db)
    monkeypatch.setattr(integration_routes, "get_user_email", lambda _: "test@example.com")
    monkeypatch.setattr(integration_routes, "encrypt_token", lambda value: "encrypted:" + value)
    monkeypatch.setattr(integration_routes, "register_oauth_client", register)
    monkeypatch.setattr(integration_routes, "_reload_pool", AsyncMock())
    redirect_uri = "https://loma.example/api/oauth/custom-mcp/design_test/callback"
    monkeypatch.setattr(oauth_routes, "_oauth_redirect_uri", lambda *_: redirect_uri)
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "name": "Design test", "url": url, "auth_mode": "oauth",
        "oauth_config": {
            "authorization_endpoint": "https://provider.example/authorize",
            "token_endpoint": "https://provider.example/token",
            "registration_endpoint": "https://provider.example/register",
            "client_id": manual_client_id,
        },
    })

    response = await integration_routes._add_custom_connector(request)

    assert response.status == 201
    doc = await db.integrations.find_one({"provider": "design_test"})
    config = doc["oauth_config"]
    if manual_client_id:
        register.assert_not_awaited()
        assert config["client_id_encrypted"] == "encrypted:manual-client"
    else:
        register.assert_awaited_once_with(
            registration_endpoint="https://provider.example/register",
            redirect_uri=redirect_uri,
            client_name=client_name,
        )
        assert config["client_id_encrypted"] == "encrypted:registered-client"
        assert config["token_endpoint_auth_method"] == "none"
