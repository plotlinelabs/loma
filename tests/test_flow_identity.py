"""Execution-account regression tests. No external tools or production data."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from api import flow_routes as routes
from scheduler.run_identity import require_execution_account


class Request(dict):
    match_info = {"flow_id": "f1"}
    def __init__(self, body=None, email="owner@example.com", role="operator"):
        super().__init__(user_email=email, system_role=role)
        self.json = AsyncMock(return_value=body or {})


def database():
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value={"status": "active"})
    db.flows.insert_one = AsyncMock()
    db.flows.update_one = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_create_ignores_forged_creator_and_binds_default_account():
    db = database()
    request = Request({"name": "Test", "prompt": "Draft", "schedule_type": "recurring", "cron": "0 9 * * *", "status": "paused", "created_by": {"source": "victim@example.com", "user_name": "victim@example.com"}})
    with patch.object(routes, "get_db", return_value=db):
        response = await routes.handle_create_flow(request)
    flow = json.loads(response.text)["flow"]
    assert response.status == 201
    assert flow["run_as"] == "owner@example.com"
    assert flow["identity_version"] == 1
    assert set(flow["created_by"].values()) == {"owner@example.com"}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", {}, 42])
async def test_invalid_execution_accounts_rejected(value):
    with pytest.raises(web.HTTPBadRequest):
        await routes._validate_run_as(database(), value, Request())


@pytest.mark.asyncio
async def test_foreign_account_requires_admin():
    with pytest.raises(web.HTTPForbidden):
        await routes._validate_run_as(database(), "victim@example.com", Request())
    assert await routes._validate_run_as(database(), "VICTIM@example.com ", Request(role="admin")) == "victim@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [routes.handle_update_flow, routes.handle_delete_flow, routes.handle_pause_flow, routes.handle_resume_flow, routes.handle_run_now, routes.handle_update_flow_labels])
async def test_shared_flow_does_not_grant_mutation_or_execution(handler):
    flow = {"flow_id": "f1", "visibility": "shared", "created_by": {"source": "victim@example.com"}, "run_as": "victim@example.com"}
    with patch.object(routes, "get_db", return_value=database()), patch.object(routes, "get_flow", AsyncMock(return_value=flow)):
        with pytest.raises(web.HTTPForbidden):
            await handler(Request())


def test_creator_cannot_edit_after_admin_assigns_foreign_account():
    flow = {"created_by": {"source": "owner@example.com"}, "run_as": "victim@example.com"}
    assert not routes._can_manage_flow(flow, Request())
    assert routes._can_manage_flow(flow, Request(role="admin"))


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, {}, {"status": "inactive"}, {"status": "pending"}])
async def test_account_must_be_explicitly_active(user):
    db = database()
    db.users.find_one.return_value = user
    with pytest.raises(ValueError):
        await require_execution_account(db, {"run_as": "owner@example.com"})


@pytest.mark.asyncio
async def test_legacy_creator_is_not_an_execution_grant():
    with pytest.raises(ValueError):
        await require_execution_account(database(), {"created_by": {"source": "owner@example.com"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduled", "webhook"])
async def test_inactive_account_never_starts_agent(kind):
    import scheduler.executor as scheduled
    import scheduler.webhook_executor as webhook
    db = database()
    flow = {"flow_id": "f1", "name": "Test", "status": "active", "created_by": {}, "run_as": "owner@example.com"}
    db.flows.find_one = AsyncMock(return_value=flow)
    db.users.find_one.return_value = {"status": "inactive"}
    db.webhook_logs.update_one = AsyncMock()
    module = scheduled if kind == "scheduled" else webhook
    with patch.object(module, "get_db", return_value=db), patch.object(module, "stream_agent") as agent:
        if kind == "scheduled":
            await module.execute_flow("f1")
        else:
            await module.execute_webhook_flow(flow, b"{}", {}, "log1")
        agent.assert_not_called()
    assert "blocked" in db.flows.update_one.call_args.args[1]["$set"]["last_error"]
