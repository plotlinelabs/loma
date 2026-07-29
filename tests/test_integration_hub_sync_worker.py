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
