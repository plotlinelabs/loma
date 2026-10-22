"""Custom MCP OAuth tokens baked into a run's client must not outlive the messages sent to it.

The bug: a run builds its MCP headers once. Slides MCP tokens last an hour, so a
follow-up message injected after expiry reused the dead header (401) instead of
starting a new run that refreshes it.
"""
from datetime import datetime, timedelta, timezone
from time import time
from unittest.mock import AsyncMock

import pytest

from agent import active_streams
from api import oauth_helpers


class _Coll:
    def __init__(self, doc):
        self.doc = doc
        self.updates = []

    async def find_one(self, *a, **k):
        return self.doc

    async def update_one(self, flt, update, **k):
        self.updates.append(update)


class _DB:
    def __init__(self, token_doc, integration=None):
        self.oauth_tokens = _Coll(token_doc)
        self.integrations = _Coll(integration or {"oauth_config": {
            "token_endpoint": "https://as/token", "token_endpoint_auth_method": "none",
        }})
        self.users = _Coll(None)


@pytest.fixture
def enc(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return oauth_helpers.encrypt_token


def _doc(enc, expires_in, refresh=True):
    doc = {"access_token": enc("old"), "token_expiry": datetime.now(timezone.utc) + expires_in}
    if refresh:
        doc["refresh_token"] = enc("rt")
    return doc


@pytest.mark.asyncio
async def test_token_far_from_expiry_is_used_without_refresh(monkeypatch, enc):
    refresh = AsyncMock()
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", refresh)
    db = _DB(_doc(enc, timedelta(minutes=30)))
    assert await oauth_helpers.get_valid_custom_mcp_token("u@x", "slides_com", db=db) == "old"
    refresh.assert_not_called()


@pytest.mark.asyncio
async def test_token_about_to_expire_is_refreshed_early(monkeypatch, enc):
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token",
                        AsyncMock(return_value={"access_token": "new", "expires_in": 3600}))
    db = _DB(_doc(enc, timedelta(minutes=2)))
    assert await oauth_helpers.get_valid_custom_mcp_token("u@x", "slides_com", db=db) == "new"
    assert db.oauth_tokens.updates


@pytest.mark.asyncio
async def test_failed_early_refresh_keeps_the_still_valid_token(monkeypatch, enc):
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", AsyncMock(return_value=None))
    db = _DB(_doc(enc, timedelta(minutes=2)))
    assert await oauth_helpers.get_valid_custom_mcp_token("u@x", "slides_com", db=db) == "old"
    assert db.users.updates == []  # not marked expired


@pytest.mark.asyncio
async def test_expired_token_with_failed_refresh_is_marked_expired(monkeypatch, enc):
    monkeypatch.setattr(oauth_helpers, "_refresh_oauth_token", AsyncMock(return_value=None))
    monkeypatch.setattr(oauth_helpers, "_ensure_tool_assignments_mapping", AsyncMock())
    db = _DB(_doc(enc, timedelta(minutes=-1)))
    assert await oauth_helpers.get_valid_custom_mcp_token("u@x", "slides_com", db=db) is None
    assert db.users.updates


@pytest.mark.asyncio
async def test_token_without_refresh_token_is_used_until_it_expires(monkeypatch, enc):
    db = _DB(_doc(enc, timedelta(minutes=2), refresh=False))
    assert await oauth_helpers.get_valid_custom_mcp_token("u@x", "slides_com", db=db) == "old"


@pytest.mark.asyncio
async def test_token_expiry_helper_returns_utc(enc):
    naive = datetime(2026, 10, 22, 22, 40, 31)
    db = _DB({"token_expiry": naive})
    assert await oauth_helpers.get_custom_mcp_token_expiry("u@x", "slides_com", db=db) == naive.replace(tzinfo=timezone.utc)
    assert await oauth_helpers.get_custom_mcp_token_expiry("u@x", "slides_com", db=_DB(None)) is None


class _Client:
    def __init__(self):
        self.query = AsyncMock()

    async def interrupt(self):
        pass


@pytest.mark.parametrize("offset,injected", [
    (None, True),   # no OAuth connectors in the run
    (600, True),    # token still valid
    (-1, False),    # token expired: queue for a fresh run
])
@pytest.mark.asyncio
async def test_inject_refused_once_run_tokens_expired(offset, injected):
    client = _Client()
    expire_at = None if offset is None else time() + offset
    stream = await active_streams.register(f"c-{offset}", client, "u@x", credentials_expire_at=expire_at)
    try:
        assert await active_streams.inject(stream, "check now?") is injected
        assert client.query.await_count == (1 if injected else 0)
    finally:
        await active_streams.unregister(stream.conversation_id, stream)
