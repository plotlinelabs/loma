"""Mongo-backed job queue for distributing an eval run's cases across a real
worker pool — embedded coroutines inside the backend process by default, and
any number of separate `eval-worker` containers on top (see eval/worker.py,
docker-compose.yml).

Why Mongo instead of Redis/Celery/RQ: no new infra dependency in an already
Mongo-centric app, and there's no multi-document transaction to lean on here
— Motor/Mongo gives exactly one thing for free, a single-document atomic
`find_one_and_update`, and every guarantee below is built from that alone,
not assumed from anything broader. One job document per *case* (not per
case x variant) — a job runs eval.runner.run_one_case's existing
all-variants-concurrently logic unchanged, so eval/decision.py and
eval_service.append_case_result need zero changes for this to work.

Fencing (claim_epoch): a claim's owner isn't just "whoever grabbed it" — every
write a worker makes (append_case_result, complete_job, fail_job) must be
conditioned on still holding the same claim_epoch it started with. Without
this, a worker that's merely slow (not crashed) can have its job swept back
to "pending" by requeue_stale_claims, reclaimed and completed by a second
worker, and then still push its OWN result on top when it finally finishes —
duplicating a case_id in case_results and corrupting aggregate(). A write
that loses the fence (claim_epoch no longer matches) is simply discarded.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from eval.schema import CaseResult, Variant

logger = logging.getLogger(__name__)

# A "claimed" job whose claimed_at is older than this is assumed to belong to
# a dead worker and gets swept back to "pending". eval.runner.run_one_case's
# per-variant work is SEQUENTIAL (the response oneshot, then the judge call),
# so one variant's own worst-case wall time is close to two full
# EVAL_ONESHOT_TIMEOUT_SECONDS back-to-back, not one — a tighter buffer here
# would false-positive-sweep perfectly healthy, just-slow workers.
from agent.opencode_runtime import EVAL_ONESHOT_TIMEOUT_SECONDS

STALE_CLAIM_SECONDS = 2 * EVAL_ONESHOT_TIMEOUT_SECONDS + 30

MAX_JOB_ATTEMPTS = 3

# Consecutive job failures on one run before it cools down — the provider
# fails by going *unavailable*, not by returning clean 429s (see NOTES.md's
# documented live incident), so this is a circuit breaker, not rate-limit
# backoff. Reuses the exact cooldown window already established elsewhere in
# this codebase for the same underlying concept ("this provider is currently
# unhealthy, stop hammering it") — see agent/pool.py's ACCOUNT_COOLDOWN_SECONDS.
CIRCUIT_BREAKER_THRESHOLD = 5
EVAL_PROVIDER_COOLDOWN_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_queue_indexes(db) -> None:
    # Unique on (run_id, case_id) is what makes enqueue_run idempotent/
    # resumable — a crash mid-enqueue can safely re-run without ever
    # double-inserting a job for the same case.
    await db.prompt_eval_case_jobs.create_index([("run_id", 1), ("case_id", 1)], unique=True)
    # What claim_job's query filters and sorts by.
    await db.prompt_eval_case_jobs.create_index([("run_id", 1), ("status", 1), ("created_at", 1)])


async def enqueue_run(db, *, run_id: str, cases) -> int:
    """Insert one pending job per case. Idempotent: re-running this for a
    run_id that was already (partially) enqueued only inserts the cases
    that don't have a job yet, via the unique (run_id, case_id) index rather
    than a pre-check (avoids a race between the check and the insert)."""
    from pymongo.errors import BulkWriteError

    docs = [
        {
            "job_id": uuid.uuid4().hex,
            "run_id": run_id,
            "case_id": case.case_id,
            "status": "pending",
            "claimed_by": None,
            "claimed_at": None,
            "claim_epoch": 0,
            "attempts": 0,
            "error": None,
            "created_at": _now(),
        }
        for case in cases
    ]
    if not docs:
        return 0
    try:
        result = await db.prompt_eval_case_jobs.insert_many(docs, ordered=False)
        return len(result.inserted_ids)
    except BulkWriteError as exc:
        # Duplicate-key errors (code 11000) are expected on a re-run of a
        # partially-enqueued run — every other case still got inserted since
        # ordered=False. Anything else is a real failure.
        inserted = exc.details.get("nInserted", 0)
        non_dupe = [e for e in exc.details.get("writeErrors", []) if e.get("code") != 11000]
        if non_dupe:
            raise
        return inserted


async def claim_job(db, *, worker_id: str) -> dict[str, Any] | None:
    """Atomically claim the oldest pending job across ALL runs — a real
    shared pool, not one worker set per run. Returns the claimed doc (with
    its fresh claim_epoch) or None if nothing's pending. Two workers can
    never win the same claim — find_one_and_update on a single document is
    Mongo's one real atomicity guarantee here, and this is the only thing
    standing between "no two workers process the same case" and a race."""
    doc = await db.prompt_eval_case_jobs.find_one_and_update(
        {"status": "pending"},
        {
            "$set": {"status": "claimed", "claimed_by": worker_id, "claimed_at": _now()},
            "$inc": {"claim_epoch": 1},
        },
        sort=[("created_at", 1)],
        return_document=True,
    )
    return doc


async def release_job(db, *, job_id: str, claim_epoch: int) -> None:
    """Put a claimed job back to pending without counting it as a failure
    or bumping `attempts` — used when a worker claims a job for a run
    that's currently cooling down (circuit breaker tripped) and needs to
    give it back rather than work on it right now."""
    await db.prompt_eval_case_jobs.update_one(
        {"job_id": job_id, "claim_epoch": claim_epoch},
        {"$set": {"status": "pending", "claimed_by": None, "claimed_at": None}, "$inc": {"claim_epoch": 1}},
    )


async def complete_job(db, *, job_id: str, claim_epoch: int) -> bool:
    """Mark a job done, but only if the caller still holds the claim it
    started with. Returns False (and the caller must discard its result,
    NOT push it) if the fence was lost to a stale-claim reclaim."""
    result = await db.prompt_eval_case_jobs.update_one(
        {"job_id": job_id, "claim_epoch": claim_epoch},
        {"$set": {"status": "done"}},
    )
    return result.matched_count > 0


async def fail_job(db, *, job_id: str, claim_epoch: int, error: str) -> bool:
    """Requeue under MAX_JOB_ATTEMPTS, else mark permanently failed. Same
    fencing as complete_job — a fenced-out write is discarded, not retried
    by the caller (whoever reclaimed the job owns it now)."""
    job = await db.prompt_eval_case_jobs.find_one({"job_id": job_id, "claim_epoch": claim_epoch})
    if job is None:
        return False
    attempts = job.get("attempts", 0) + 1
    if attempts >= MAX_JOB_ATTEMPTS:
        update = {"$set": {"status": "failed", "attempts": attempts, "error": error}}
    else:
        update = {
            "$set": {"status": "pending", "attempts": attempts, "error": error, "claimed_by": None, "claimed_at": None},
            "$inc": {"claim_epoch": 1},
        }
    result = await db.prompt_eval_case_jobs.update_one({"job_id": job_id, "claim_epoch": claim_epoch}, update)
    return result.matched_count > 0


async def requeue_stale_claims(db, *, older_than_seconds: int = STALE_CLAIM_SECONDS) -> int:
    """Sweep: any "claimed" job whose claimed_at predates the threshold goes
    back to "pending" with a bumped claim_epoch — which is exactly what
    invalidates the original (possibly-still-alive, possibly-crashed)
    worker's fenced writes. Cheap to call frequently; every worker calls
    this itself before polling rather than needing a separate sweeper
    process."""
    cutoff = _now() - timedelta(seconds=older_than_seconds)
    result = await db.prompt_eval_case_jobs.update_many(
        {"status": "claimed", "claimed_at": {"$lt": cutoff}},
        {"$set": {"status": "pending", "claimed_by": None, "claimed_at": None}, "$inc": {"claim_epoch": 1}},
    )
    if result.modified_count:
        logger.warning("Requeued %d stale eval job claim(s)", result.modified_count)
    return result.modified_count


async def remaining_count(db, run_id: str) -> int:
    """Cheap pre-filter only — NOT the real correctness mechanism for
    finalization (that's the atomic running->finalizing status flip on the
    run doc itself, done by the caller). This can race (another worker's
    write lands between this count and the caller's next step), which is
    exactly why it's only ever used as a pre-filter before that atomic flip,
    never as the sole gate."""
    return await db.prompt_eval_case_jobs.count_documents(
        {"run_id": run_id, "status": {"$in": ["pending", "claimed"]}},
    )


async def record_job_failure_and_check_circuit(db, run_id: str) -> bool:
    """Atomically increments the run's consecutive-failure counter and
    returns True exactly once — for the single worker that observes the
    count crossing CIRCUIT_BREAKER_THRESHOLD — via find_one_and_update's
    return_document, not a separate read-then-branch (a bare $inc is atomic,
    but acting on the post-increment value is not, unless it comes from the
    same atomic operation)."""
    doc = await db.prompt_eval_runs.find_one_and_update(
        {"run_id": run_id},
        {"$inc": {"consecutive_failures": 1}},
        return_document=True,
    )
    failures = (doc or {}).get("consecutive_failures", 0)
    if failures == CIRCUIT_BREAKER_THRESHOLD:
        cooldown_until = _now() + timedelta(seconds=EVAL_PROVIDER_COOLDOWN_SECONDS)
        await db.prompt_eval_runs.update_one(
            {"run_id": run_id}, {"$set": {"cooldown_until": cooldown_until}},
        )
        logger.warning(
            "Eval run %s tripped the circuit breaker after %d consecutive failures — cooling down until %s",
            run_id, failures, cooldown_until.isoformat(),
        )
        return True
    return False


async def record_job_success(db, run_id: str) -> None:
    await db.prompt_eval_runs.update_one({"run_id": run_id}, {"$set": {"consecutive_failures": 0}})


async def is_cooling_down(db, run_id: str) -> bool:
    doc = await db.prompt_eval_runs.find_one({"run_id": run_id}, {"cooldown_until": 1})
    cooldown_until = (doc or {}).get("cooldown_until")
    if cooldown_until is None:
        return False
    # Motor/pymongo deserializes BSON dates as naive datetimes (representing
    # UTC) by default, even though we always write them as tz-aware UTC via
    # _now() — comparing the two directly raises TypeError. Normalize the
    # value read back rather than relying on driver-specific tz_aware config.
    if cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    return cooldown_until > _now()


async def try_finalize(db, run_id: str) -> dict[str, Any] | None:
    """Atomically claim the right to finalize a run. Returns the run doc
    (so the caller can inspect case_results without a second read) exactly
    once, for the single worker that wins the running->finalizing flip —
    Mongo's single-document atomicity is the whole guarantee, and it holds
    regardless of how many embedded or containerized workers race it
    simultaneously. Returns None if no jobs remain outstanding but this
    caller lost the race (someone else already flipped it), or if jobs are
    still pending/claimed.

    Does NOT by itself guarantee every case actually made it into
    case_results — a crash mid-enqueue_run can leave a run short jobs that
    were never inserted at all, so "no outstanding jobs" isn't the same as
    "every case ran." The caller MUST compare
    len(doc["case_results"]) to doc["total_cases"] and finalize as
    "completed" vs "incomplete" accordingly — this function only hands over
    the exclusive right to decide, not the decision itself."""
    remaining = await db.prompt_eval_case_jobs.count_documents(
        {"run_id": run_id, "status": {"$in": ["pending", "claimed"]}},
    )
    if remaining > 0:
        return None
    doc = await db.prompt_eval_runs.find_one_and_update(
        {"run_id": run_id, "status": "running"},
        {"$set": {"status": "finalizing"}},
        return_document=True,
    )
    return doc
