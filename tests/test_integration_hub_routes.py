"""Route contract tests for authorization and optimistic concurrency."""

import json

import pytest
from aiohttp.test_utils import make_mocked_request

from api import integration_hub_routes as routes


def _request(headers=None, user=None):
    request = make_mocked_request("PATCH", "/api/integration-hub/accounts/a", headers=headers)
    request["request_id"] = "request-1"
    request["user"] = user or {}
    return request


def test_read_only_role_cannot_mutate(monkeypatch):
    monkeypatch.setattr(routes, "get_system_role", lambda request: "chatter")
    request = _request(user={
        "tool_assignments": {"integration_hub": {"role": "Read-only"}},
    })

    routes._require_module(request, write=False)
    with pytest.raises(routes.web.HTTPForbidden):
        routes._require_module(request, write=True)


def test_if_match_requires_strong_numeric_etag():
    assert routes._if_match(_request({"If-Match": '"7"'})) == 7
    with pytest.raises(routes.ValidationError):
        routes._if_match(_request({"If-Match": 'W/"7"'}))
    with pytest.raises(routes.ValidationError):
        routes._if_match(_request())


@pytest.mark.asyncio
async def test_version_conflict_has_structured_412_response():
    async def conflict():
        raise RuntimeError("version_conflict")

    response = await routes._run(_request(), conflict)
    body = json.loads(response.text)

    assert response.status == 412
    assert body["error"]["code"] == "precondition_failed"
    assert body["error"]["request_id"] == "request-1"
