"""Durability and feature-gate tests for the Integration Hub sync worker."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from integration_hub import sync_worker


class Jobs:
    def __init__(self, reclaimed):
        self.reclaimed = reclaimed
        self.filters = []
        self.claims = 0

    async def find_one_and_update(self, query, update, **kwargs):
        self.filters.append(query)
        self.claims += 1
        return self.reclaimed if self.claims == 1 else None

    async def update_one(self, *args, **kwargs):
        return type("Result", (), {"modified_count": 1})()


class Collection:
    def __init__(self, row=None):
        self.row = row

    async def find_one(self, *args, **kwargs):
        return self.row


class DB:
    def __init__(self, job=None):
        self.integration_sync_jobs = Jobs(job)
        self.integration_sync_sources = Collection()
        self.integration_accounts = Collection()


@pytest.mark.asyncio
async def test_process_one_reclaims_legacy_running_job_without_lease():
    db = DB()
    assert await sync_worker.process_one(db) is False
    reclaim_filter = db.integration_sync_jobs.filters[0]
    assert reclaim_filter["status"] == "running"
    assert reclaim_filter["lease_expires_at"] == {
        "$not": {"$gt": pytest.approx(datetime.now(timezone.utc), abs=timedelta(seconds=5))}
    }


@pytest.mark.asyncio
async def test_worker_context_uses_same_explicit_kill_switch_as_api(monkeypatch):
    monkeypatch.setenv("INTEGRATION_HUB_ENABLED", "0")
    monkeypatch.setenv("ENV", "DEV")
    loop = AsyncMock()
    monkeypatch.setattr(sync_worker, "worker_loop", loop)

    context = sync_worker.worker_context({})
    await context.__anext__()
    with pytest.raises(StopAsyncIteration):
        await context.__anext__()

    loop.assert_not_awaited()

@pytest.mark.asyncio
async def test_expiry_revokes_batch_and_updates_parent(monkeypatch):
    now = datetime.now(timezone.utc)
    contact = {
        "contact_id": "contact-1", "account_id": "account-1",
        "access_status": "revoking", "revocation_attempts": 1,
        "product_grants": [{"product_id": "product-1", "member_id": "member-1"}],
    }
    contacts = AsyncMock()
    contacts.find_one_and_update.side_effect = [contact, contact, None]
    contacts.update_one = AsyncMock()
    accounts = AsyncMock()
    audit = AsyncMock()
    db = type("ExpiryDB", (), {
        "integration_contacts": contacts,
        "integration_accounts": accounts,
        "integration_audit_log": audit,
    })()
    # Repository construction touches these collection attributes.
    for name in (
        "integration_projects", "integration_tasks", "integration_milestones", "integration_source_mappings", "integration_timeline",
        "integration_risks", "integration_source_links", "integration_sync_sources",
        "integration_sync_jobs", "integration_interactions", "integration_raw_events",
        "integration_external_conversations", "integration_findings",
        "integration_activities", "integration_idempotency",
    ):
        setattr(db, name, AsyncMock())
    revoke = AsyncMock()
    monkeypatch.setattr("tools.product_access.revoke", revoke)

    assert await sync_worker.expire_dashboard_access(db, now) is True
    revoke.assert_awaited_once_with("product-1", "member-1")
    accounts.update_one.assert_awaited_once()
    audit.insert_one.assert_awaited_once()
    claim = contacts.find_one_and_update.await_args_list[0].args[0]
    due_access = claim["$or"][0]
    assert due_access["access_status"]["$in"] == ["active", "partially_granted"]
    assert due_access["access_expires_at"]["$ne"] == ""
    assert due_access["access_expires_at"]["$type"] == "string"
