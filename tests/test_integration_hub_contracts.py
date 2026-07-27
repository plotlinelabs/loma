from datetime import datetime, timezone

import pytest

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


@pytest.mark.asyncio
async def test_empty_access_grants_deny_cross_account_access():
    class Repository:
        async def get_access(self, account_id, actor):
            return None

    service = AccountService(Repository())
    assert await service.authorize("acc_other", "user@example.com", "read", "chatter") is None
    assert await service.authorize("acc_other", "user@example.com", "edit", "chatter") is None
    assert await service.authorize("acc_other", "admin@example.com", "edit", "admin") == "owner"


@pytest.mark.asyncio
async def test_access_grant_roles_enforce_read_and_edit_permissions():
    class Repository:
        async def get_access(self, account_id, actor):
            return {"role": "viewer"}

    service = AccountService(Repository())
    assert await service.authorize("acc_1", "viewer@example.com", "read") == "viewer"
    assert await service.authorize("acc_1", "viewer@example.com", "edit") is None


def test_dependency_self_cycle_is_rejected_before_write():
    service = AccountService(None)
    with pytest.raises(ValidationError, match="depend on itself"):
        import asyncio
        asyncio.run(service.validate_references("acc_1", {"depends_on": ["item_1"]}, "item_1"))
