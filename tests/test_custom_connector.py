"""Tests for custom (admin-added) remote MCP connectors.

Covers merge_db_integrations: an `is_custom` integration record is turned into
an inline remote http MCP server, with optional token/header auth, without
needing a PROVIDER_CATALOG entry.
"""

import pytest
from unittest.mock import MagicMock, patch

from agent.client import merge_db_integrations


async def _aiter(docs):
    for doc in docs:
        yield doc


def _mock_db(docs):
    db = MagicMock()
    db.integrations.find = MagicMock(return_value=_aiter(docs))
    return db


@pytest.mark.asyncio
async def test_custom_connector_with_token():
    doc = {
        "provider": "acme",
        "is_custom": True,
        "status": "active",
        "mcp_url": "https://mcp.acme.com/mcp",
        "auth_header": "Authorization",
        "api_key_encrypted": "ENC",
    }
    db = _mock_db([doc])
    with patch("observability.db.get_db", return_value=db), \
         patch("api.oauth_helpers.decrypt_token", return_value="tok-123"):
        config = await merge_db_integrations({"mcp_servers": {}})

    assert config["mcp_servers"]["acme"] == {
        "type": "http",
        "url": "https://mcp.acme.com/mcp",
        "headers": {"Authorization": "tok-123"},
    }


@pytest.mark.asyncio
async def test_custom_connector_custom_header():
    doc = {
        "provider": "acme",
        "is_custom": True,
        "status": "active",
        "mcp_url": "https://mcp.acme.com/mcp",
        "auth_header": "X-Api-Key",
        "api_key_encrypted": "ENC",
    }
    db = _mock_db([doc])
    with patch("observability.db.get_db", return_value=db), \
         patch("api.oauth_helpers.decrypt_token", return_value="tok-123"):
        config = await merge_db_integrations({"mcp_servers": {}})

    assert config["mcp_servers"]["acme"]["headers"] == {"X-Api-Key": "tok-123"}


@pytest.mark.asyncio
async def test_custom_connector_without_token_has_no_headers():
    doc = {
        "provider": "acme",
        "is_custom": True,
        "status": "active",
        "mcp_url": "https://mcp.acme.com/mcp",
        "auth_header": "Authorization",
        "api_key_encrypted": None,
    }
    db = _mock_db([doc])
    with patch("observability.db.get_db", return_value=db):
        config = await merge_db_integrations({"mcp_servers": {}})

    assert config["mcp_servers"]["acme"] == {
        "type": "http",
        "url": "https://mcp.acme.com/mcp",
    }
    assert "headers" not in config["mcp_servers"]["acme"]
