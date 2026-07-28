"""Durable, pull-only Integration Hub sync worker.

The worker only calls read adapters. Jobs are claimed atomically in MongoDB,
which prevents concurrent web workers from syncing the same mapping.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from integration_hub.read_only_sync import pull
from integration_hub.repository import AccountRepository
from integration_hub.service import AccountService


async def process_one(db):
    now = datetime.now(timezone.utc)
    job = await db.integration_sync_jobs.find_one_and_update(
        {"status": "queued", "next_attempt_at": {"$lte": now}},
        {"$set": {"status": "running", "started_at": now}, "$inc": {"attempt": 1}},
        sort=[("next_attempt_at", 1), ("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if not job:
        return False
    repository = AccountRepository(db)
    service = AccountService(repository)
    mapping = await repository.sync_sources.find_one({
        "mapping_id": job["mapping_id"], "status": "active", "archived_at": None,
    })
    account = await repository.get(job["account_id"])
    if not mapping or not account:
        await repository.sync_jobs.update_one(
            {"job_id": job["job_id"]},
            {"$set": {"status": "failed", "last_error": "Mapping or account is unavailable",
                      "finished_at": now}},
        )
        return True
    try:
        records = await pull(mapping, job["created_by"])
        created = 0
        for record in records:
            _, inserted = await service.ingest_interaction(
                account, record, job["created_by"], job["request_id"]
            )
            created += int(inserted)
        occurred = [record["occurred_at"] for record in records if record.get("occurred_at")]
        checkpoint = {
            "records_seen": len(records),
            "last_occurred_at": max(occurred).isoformat() if occurred else
                                (mapping.get("checkpoint") or {}).get("last_occurred_at"),
        }
        finished = datetime.now(timezone.utc)
        await repository.update_sync_result(
            mapping["mapping_id"], status="succeeded", error=None,
            checkpoint=checkpoint, synced_at=finished,
        )
        await repository.sync_jobs.update_one(
            {"job_id": job["job_id"]},
            {"$set": {"status": "succeeded", "created_count": created,
                      "seen_count": len(records), "checkpoint": checkpoint,
                      "finished_at": finished}},
        )
    except Exception as exc:
        attempt = job["attempt"]
        exhausted = attempt >= job.get("max_attempts", 5)
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** attempt))
        await repository.sync_jobs.update_one(
            {"job_id": job["job_id"]},
            {"$set": {"status": "failed" if exhausted else "queued",
                      "last_error": str(exc)[:500],
                      "next_attempt_at": retry_at,
                      **({"finished_at": datetime.now(timezone.utc)} if exhausted else {})}},
        )
        await repository.update_sync_result(
            mapping["mapping_id"], status="failed" if exhausted else "queued",
            error=str(exc)[:500], checkpoint=mapping.get("checkpoint"),
            synced_at=datetime.now(timezone.utc),
        )
    return True


async def worker_loop(app):
    last_schedule = None
    while True:
        db = app.get("integration_hub_db")
        if db is None:
            from observability.db import get_db
            db = get_db()
        now = datetime.now(timezone.utc)
        if db is not None and (last_schedule is None or (now - last_schedule).total_seconds() >= 60):
            await AccountRepository(db).schedule_due_syncs(now)
            last_schedule = now
        processed = await process_one(db) if db is not None else False
        await asyncio.sleep(0 if processed else 5)


async def worker_context(app):
    task = asyncio.create_task(worker_loop(app))
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
