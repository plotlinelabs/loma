from types import SimpleNamespace

import pytest

from api import voice_listen_routes


@pytest.mark.asyncio
async def test_listen_token_requires_auth(monkeypatch):
    monkeypatch.setattr(voice_listen_routes, "get_user_email", lambda _request: None)
    response = await voice_listen_routes.handle_listen_token(SimpleNamespace())
    assert response.status == 401


@pytest.mark.asyncio
async def test_listen_token_is_one_time_and_short_lived(monkeypatch):
    monkeypatch.setattr(voice_listen_routes, "get_user_email", lambda _request: "owner@example.com")
    response = await voice_listen_routes.handle_listen_token(SimpleNamespace())
    assert response.status == 200
    assert len(voice_listen_routes._tokens) == 1
    token, expiry = next(iter(voice_listen_routes._tokens.items()))
    assert token
    assert expiry > voice_listen_routes.time.time()
