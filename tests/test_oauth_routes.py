import asyncio
import json
from urllib.parse import parse_qs, urlparse

from aiohttp.test_utils import make_mocked_request

from api.oauth_routes import handle_slack_authorize
from api.oauth_helpers import SLACK_USER_SCOPES


def test_slack_authorize_uses_configured_redirect_uri(monkeypatch):
    monkeypatch.setattr("api.oauth_routes.get_db", lambda: object())
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", "test-key")
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_ID", "slack-client")
    monkeypatch.setenv(
        "SLACK_OAUTH_REDIRECT_URI",
        "https://app.lomahq.com/api/oauth/slack/callback",
    )
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)

    request = make_mocked_request("GET", "/api/oauth/slack/authorize")
    request["user_email"] = "user@example.com"

    response = asyncio.run(handle_slack_authorize(request))
    payload = response.text

    assert response.status == 200
    authorize_url = json.loads(payload)["authorize_url"]
    parsed = urlparse(authorize_url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "slack.com"
    assert query["client_id"] == ["slack-client"]
    assert query["redirect_uri"] == ["https://app.lomahq.com/api/oauth/slack/callback"]
    assert query["user_scope"] == [",".join(SLACK_USER_SCOPES)]
    assert "scope" not in query


def test_slack_authorize_derives_redirect_uri_from_public_base_url(monkeypatch):
    monkeypatch.setattr("api.oauth_routes.get_db", lambda: object())
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", "test-key")
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_ID", "slack-client")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.lomahq.com/")
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("SLACK_OAUTH_REDIRECT_URI", raising=False)

    request = make_mocked_request("GET", "/api/oauth/slack/authorize")
    request["user_email"] = "user@example.com"

    response = asyncio.run(handle_slack_authorize(request))
    authorize_url = json.loads(response.text)["authorize_url"]
    query = parse_qs(urlparse(authorize_url).query)

    assert response.status == 200
    assert query["redirect_uri"] == ["https://app.lomahq.com/api/oauth/slack/callback"]
