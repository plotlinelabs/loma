from datetime import datetime, timezone

import pytest
from aiohttp import web

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
