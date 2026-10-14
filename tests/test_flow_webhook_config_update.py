"""Updating a webhook flow's config must not drop its stored HMAC secret."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api import flow_routes as routes
from scheduler import models

STORED = {"auth_method": "hmac_sha256", "signature_header": "X-Hub-Signature-256",
          "auth_secret_encrypted": "enc:old"}


class Request(dict):
    match_info = {"flow_id": "f1"}

    def __init__(self, body):
        super().__init__(user_email="owner@example.com", system_role="admin")
        self.json = AsyncMock(return_value=body)


def test_no_new_secret_keeps_the_encrypted_one():
    assert models._encrypt_webhook_secret(dict(STORED, event_filter={"accept_any": []})) == dict(
        STORED, event_filter={"accept_any": []})


def test_blank_secret_keeps_the_encrypted_one():
    out = models._encrypt_webhook_secret(dict(STORED, auth_secret=""))
    assert out["auth_secret_encrypted"] == "enc:old" and "auth_secret" not in out


def test_new_secret_replaces_the_encrypted_one(monkeypatch):
    monkeypatch.setattr(models, "encrypt_token", lambda s: f"enc:{s}")
    out = models._encrypt_webhook_secret(dict(STORED, auth_secret="new"))
    assert out["auth_secret_encrypted"] == "enc:new" and "auth_secret" not in out


@pytest.mark.asyncio
async def test_partial_webhook_config_patch_is_merged_with_stored_config():
    flow = {"flow_id": "f1", "trigger_type": "webhook", "status": "active", "visibility": "shared",
            "created_by": {"source": "owner@example.com"}, "run_as": "owner@example.com",
            "webhook_config": dict(STORED)}
    update = AsyncMock(return_value={**flow, "trigger_type": "webhook"})
    body = {"webhook_config": {"event_filter": {"accept_any": [[{"path": "action", "eq": "labeled"}]]}}}
    with patch.object(routes, "get_db", return_value=MagicMock()), \
            patch.object(routes, "get_flow", AsyncMock(return_value=flow)), \
            patch.object(routes, "update_flow", update):
        await routes.handle_update_flow(Request(body))
    sent = update.call_args.args[2]["webhook_config"]
    assert sent["auth_secret_encrypted"] == "enc:old"
    assert sent["auth_method"] == "hmac_sha256" and sent["signature_header"] == "X-Hub-Signature-256"
    assert sent["event_filter"] == body["webhook_config"]["event_filter"]


@pytest.mark.asyncio
async def test_client_cannot_supply_its_own_encrypted_secret():
    flow = {"flow_id": "f1", "trigger_type": "webhook", "status": "active", "visibility": "shared",
            "created_by": {"source": "owner@example.com"}, "run_as": "owner@example.com",
            "webhook_config": dict(STORED)}
    update = AsyncMock(return_value=flow)
    body = {"webhook_config": {"auth_secret_encrypted": "forged"}}
    with patch.object(routes, "get_db", return_value=MagicMock()), \
            patch.object(routes, "get_flow", AsyncMock(return_value=flow)), \
            patch.object(routes, "update_flow", update):
        await routes.handle_update_flow(Request(body))
    assert update.call_args.args[2]["webhook_config"]["auth_secret_encrypted"] == "enc:old"
