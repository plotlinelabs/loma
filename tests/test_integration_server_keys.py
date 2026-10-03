"""Providers whose keys are set on the server show as system-managed, so agents can pick them."""
import json
from unittest.mock import MagicMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from api import integration_routes
from integrations.registry import SERVER_ENV, PROVIDER_CATALOG, server_configured


def test_server_keys_need_one_complete_set():
    assert server_configured("pylon", {"PYLON_API_KEY": "k"})
    assert not server_configured("pylon", {"PYLON_API_KEY": "  "})
    assert not server_configured("zoho_books", {"ZOHO_CLIENT_ID_IN": "a", "ZOHO_REFRESH_TOKEN_IN": "b"})
    us = {f"ZOHO_{k}_US": "x" for k in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "ORGANIZATION_ID")}
    assert server_configured("zoho_books", us)
    assert not server_configured("github", {"GITHUB_API_KEY": "k"})  # MCP providers still need a connection


def test_only_cli_providers_are_mapped():
    for provider in SERVER_ENV:
        assert provider in PROVIDER_CATALOG
        assert not PROVIDER_CATALOG[provider].get("mcp_config_template"), provider


@pytest.mark.asyncio
async def test_list_marks_server_keyed_providers_system_managed(monkeypatch):
    db = AsyncMongoMockClient().test
    await db.integrations.insert_one({"provider": "apollo", "status": "active"})
    monkeypatch.setattr(integration_routes, "get_db", lambda: db)
    monkeypatch.setattr(integration_routes, "get_user_email", lambda _: None)
    for name in [n for groups in SERVER_ENV.values() for group in groups for n in group]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYLON_API_KEY", "k")
    monkeypatch.setenv("MONETIZE_NOW_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    response = await integration_routes._list_integrations(MagicMock())
    status = {item["provider"]: item["status"] for item in json.loads(response.text)}
    assert status["pylon"] == status["monetize_now"] == "system_managed"
    assert status["apollo"] == "connected"  # a real connection still wins
    assert status["zoho_books"] == status["grain"] == "not_connected"
    assert status["sentry"] == "system_managed"
    assert "server_configured" not in json.loads(response.text)[0]
