from types import SimpleNamespace

import pytest

from api import speech_routes


@pytest.mark.asyncio
async def test_speech_requires_auth(monkeypatch):
    monkeypatch.setattr(speech_routes, "get_user_email", lambda _request: None)
    response = await speech_routes.handle_speech(SimpleNamespace())
    assert response.status == 401


@pytest.mark.asyncio
async def test_speech_requires_explicit_valid_voice(monkeypatch):
    monkeypatch.setattr(speech_routes, "get_user_email", lambda _request: "owner@example.com")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    request = SimpleNamespace(json=lambda: None)

    async def json_body():
        return {"text": "Hello", "voice": "unknown"}

    request.json = json_body
    response = await speech_routes.handle_speech(request)
    assert response.status == 400


@pytest.mark.asyncio
async def test_speech_rejects_empty_text(monkeypatch):
    monkeypatch.setattr(speech_routes, "get_user_email", lambda _request: "owner@example.com")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    async def json_body():
        return {"text": "", "voice": "thalia"}

    response = await speech_routes.handle_speech(SimpleNamespace(json=json_body))
    assert response.status == 400
