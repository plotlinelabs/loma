"""Dynamic client registration picks an auth method the server supports."""
import pytest

from api import oauth_helpers
from api.oauth_helpers import choose_auth_method, register_oauth_client

PUBLIC_ONLY = '{"error":"invalid_client_metadata","error_description":"Only public clients using none authentication are supported."}'


@pytest.mark.parametrize("supported,expected", [
    (None, "client_secret_post"),
    ([], "client_secret_post"),
    (["none"], "none"),
    (["client_secret_basic", "none"], "client_secret_basic"),
    (["none", "client_secret_post", "client_secret_basic"], "client_secret_post"),
    (["private_key_jwt"], "private_key_jwt"),
])
def test_choose_auth_method(supported, expected):
    assert choose_auth_method(supported) == expected


def _fake_server(responses):
    calls = []

    async def post(_endpoint, body):
        calls.append(dict(body))
        return responses[len(calls) - 1]

    return post, calls


@pytest.mark.asyncio
async def test_registers_public_client_when_only_none_supported(monkeypatch):
    post, calls = _fake_server([(201, {"client_id": "pub", "client_secret": "ignored"})])
    monkeypatch.setattr(oauth_helpers, "_post_registration", post)

    result = await register_oauth_client("https://as/register", "https://loma/cb", auth_methods_supported=["none"])

    assert [c["token_endpoint_auth_method"] for c in calls] == ["none"]
    assert result == {"client_id": "pub", "client_secret": "", "token_endpoint_auth_method": "none"}


@pytest.mark.asyncio
async def test_default_request_unchanged_when_no_methods_listed(monkeypatch):
    post, calls = _fake_server([(201, {"client_id": "c", "client_secret": "s"})])
    monkeypatch.setattr(oauth_helpers, "_post_registration", post)

    result = await register_oauth_client("https://as/register", "https://loma/cb")

    assert calls == [{
        "client_name": "Loma",
        "redirect_uris": ["https://loma/cb"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }]
    assert result == {"client_id": "c", "client_secret": "s", "token_endpoint_auth_method": "client_secret_post"}


@pytest.mark.asyncio
async def test_retries_as_public_client_when_methods_unknown(monkeypatch):
    post, calls = _fake_server([(400, PUBLIC_ONLY), (201, {"client_id": "pub"})])
    monkeypatch.setattr(oauth_helpers, "_post_registration", post)

    result = await register_oauth_client("https://as/register", "https://loma/cb")

    assert [c["token_endpoint_auth_method"] for c in calls] == ["client_secret_post", "none"]
    assert result["token_endpoint_auth_method"] == "none"
    assert result["client_id"] == "pub"


@pytest.mark.asyncio
async def test_no_retry_when_methods_were_advertised(monkeypatch):
    post, calls = _fake_server([(400, PUBLIC_ONLY)])
    monkeypatch.setattr(oauth_helpers, "_post_registration", post)

    result = await register_oauth_client(
        "https://as/register", "https://loma/cb", auth_methods_supported=["client_secret_post"],
    )

    assert len(calls) == 1
    assert "Only public clients" in result["error"]


@pytest.mark.asyncio
async def test_no_retry_on_unrelated_error(monkeypatch):
    post, calls = _fake_server([(400, '{"error":"invalid_redirect_uri"}')])
    monkeypatch.setattr(oauth_helpers, "_post_registration", post)

    result = await register_oauth_client("https://as/register", "https://loma/cb")

    assert len(calls) == 1
    assert result == {"error": "Registration rejected (400): invalid_redirect_uri"}


@pytest.mark.asyncio
async def test_response_auth_method_wins(monkeypatch):
    post, _ = _fake_server([(201, {"client_id": "c", "client_secret": "s", "token_endpoint_auth_method": "client_secret_basic"})])
    monkeypatch.setattr(oauth_helpers, "_post_registration", post)

    result = await register_oauth_client("https://as/register", "https://loma/cb")

    assert result["token_endpoint_auth_method"] == "client_secret_basic"
