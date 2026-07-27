from datetime import datetime, timezone

import pytest
from aiohttp import web
from pymongo.errors import DuplicateKeyError

from api.integration_hub_routes import setup_integration_hub_routes
from integration_hub.models import ValidationError
from integration_hub.repository import AccountRepository
from integration_hub.service import AccountService


def test_cursor_is_opaque_and_round_trips_stable_sort_key():
    doc = {"updated_at": datetime(2026, 7, 27, tzinfo=timezone.utc), "account_id": "acc_123"}
    cursor = AccountRepository.encode_cursor(doc)
    assert "acc_123" not in cursor
    assert AccountRepository.decode_cursor(cursor) == (doc["updated_at"], "acc_123")
    with pytest.raises(ValueError):
        AccountRepository.decode_cursor("not-a-cursor")


def test_integration_hub_routes_use_module_permissions_without_access_grants():
    app = web.Application()
    setup_integration_hub_routes(app)
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/api/integration-hub/accounts") in routes
    assert ("GET", "/api/integration-hub/actions") in routes
    assert not any("access-grants" in path for _, path in routes)


@pytest.mark.asyncio
async def test_list_escapes_search_expression_and_keeps_it_bounded():
    class Repository:
        query = None

        async def list(self, query, limit, cursor):
            self.query = query
            return [], None

    repository = Repository()
    service = AccountService(repository)
    await service.list("user@example.com", "chatter", search="Acme.*", limit=25)
    assert repository.query["name"]["$regex"] == "Acme\\.\\*"
    with pytest.raises(ValidationError, match="100 characters"):
        await service.list("user@example.com", "chatter", search="x" * 101)


def test_dependency_self_cycle_is_rejected_before_write():
    service = AccountService(None)
    with pytest.raises(ValidationError, match="depend on itself"):
        import asyncio
        asyncio.run(service.validate_references("acc_1", {"depends_on": ["item_1"]}, "item_1"))


@pytest.mark.asyncio
async def test_idempotent_account_creation_writes_reservation_account_and_audit_atomically():
    writes = []

    class Collection:
        def __init__(self, name):
            self.name = name

        async def insert_one(self, document, session=None):
            writes.append((self.name, document, session))

        async def find_one(self, query):
            return None

    class DB:
        integration_accounts = Collection("account")
        integration_projects = Collection("projects")
        integration_tasks = Collection("tasks")
        integration_milestones = Collection("milestones")
        integration_risks = Collection("risks")
        integration_source_mappings = Collection("sources")
        integration_interactions = Collection("interactions")
        integration_audit_log = Collection("audit")
        integration_idempotency = Collection("idempotency")

    repository = AccountRepository(DB())

    async def transaction(callback):
        return await callback("session")

    repository._transaction = transaction
    response, created = await repository.create_idempotent(
        {"account_id": "acc_1"}, {"audit_id": "audit_1"},
        "owner@example.com", "same-key", {"account": {"account_id": "acc_1"}},
    )

    assert created is True
    assert response["account"]["account_id"] == "acc_1"
    assert [name for name, _, _ in writes] == ["idempotency", "account", "audit"]
    assert all(session == "session" for _, _, session in writes)


@pytest.mark.asyncio
async def test_losing_idempotent_account_creation_returns_winning_response():
    winning = {"account": {"account_id": "acc_winner"}}

    class Collection:
        async def find_one(self, query):
            return {"response": winning}

    class DB:
        integration_accounts = object()
        integration_projects = object()
        integration_tasks = object()
        integration_milestones = object()
        integration_risks = object()
        integration_source_mappings = object()
        integration_interactions = object()
        integration_audit_log = object()
        integration_idempotency = Collection()

    repository = AccountRepository(DB())

    async def duplicate(_callback):
        raise DuplicateKeyError("duplicate actor/key")

    repository._transaction = duplicate
    response, created = await repository.create_idempotent(
        {"account_id": "acc_loser"}, {"audit_id": "audit_loser"},
        "owner@example.com", "same-key", {"account": {"account_id": "acc_loser"}},
    )

    assert created is False
    assert response == winning


@pytest.mark.asyncio
async def test_action_center_filters_and_limits_attention_accounts_in_mongodb():
    captured = {}

    class Aggregate:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, limit):
            captured.setdefault("limits", []).append(limit)
            return self.rows

    class ResourceCollection:
        def aggregate(self, pipeline):
            return Aggregate([])

    class AccountCollection:
        def aggregate(self, pipeline):
            captured["pipeline"] = pipeline
            return Aggregate([])

    class DB:
        integration_accounts = AccountCollection()
        integration_projects = object()
        integration_tasks = ResourceCollection()
        integration_milestones = ResourceCollection()
        integration_risks = object()
        integration_source_mappings = object()
        integration_interactions = object()
        integration_audit_log = object()
        integration_idempotency = object()

    repository = AccountRepository(DB())
    await repository.list_actions("owner@example.com", attention_limit=25)

    pipeline = captured["pipeline"]
    assert any(stage.get("$match", {}).get("$or") for stage in pipeline)
    assert {"$limit": 25} in pipeline
    assert captured["limits"][-1] == 25
