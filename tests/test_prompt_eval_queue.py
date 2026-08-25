"""Tests for eval/queue.py against the real dev MongoDB — matching this
package's existing precedent of no-mocked-DB testing (eval/service.py
itself has no unit tests either, only live verification). Run inside the
backend container, where OBSERVABILITY_MONGODB_URI is set:

  docker exec <backend-container> python3 tests/test_prompt_eval_queue.py

Each test uses its own throwaway run_id and cleans up its own job/run docs
afterward, so this is safe to run repeatedly against a shared dev database.
"""

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import queue as eval_queue
from eval.schema import TestCase
from observability.db import get_db, init_observability


def _cases(n: int) -> list[TestCase]:
    return [TestCase(case_id=f"c{i}", input=f"input {i}") for i in range(n)]


async def _cleanup(db, run_id: str) -> None:
    await db.prompt_eval_case_jobs.delete_many({"run_id": run_id})
    await db.prompt_eval_runs.delete_many({"run_id": run_id})


async def _new_run(db, *, status: str = "running") -> str:
    run_id = uuid.uuid4().hex
    await db.prompt_eval_runs.insert_one({
        "run_id": run_id, "status": status, "case_results": [],
        "total_cases": 0, "consecutive_failures": 0, "cooldown_until": None,
        "created_by": "test", "suite_label": "test", "subject_type": "generic",
        "variants": [],
    })
    return run_id


# ------------------------------------------------------------- enqueue --- #

async def test_enqueue_creates_one_job_per_case():
    db = get_db()
    run_id = uuid.uuid4().hex
    try:
        inserted = await eval_queue.enqueue_run(db, run_id=run_id, cases=_cases(5))
        assert inserted == 5
        count = await db.prompt_eval_case_jobs.count_documents({"run_id": run_id})
        assert count == 5
    finally:
        await _cleanup(db, run_id)


async def test_enqueue_is_idempotent_on_partial_reenqueue():
    # Simulates a crash mid-enqueue: re-running enqueue_run for the same
    # run_id must not duplicate jobs for cases that already have one.
    db = get_db()
    run_id = uuid.uuid4().hex
    try:
        cases = _cases(5)
        first = await eval_queue.enqueue_run(db, run_id=run_id, cases=cases[:3])
        assert first == 3
        second = await eval_queue.enqueue_run(db, run_id=run_id, cases=cases)
        assert second == 2  # only the 2 new ones actually inserted
        count = await db.prompt_eval_case_jobs.count_documents({"run_id": run_id})
        assert count == 5
    finally:
        await _cleanup(db, run_id)


# --------------------------------------------------------------- claim --- #

async def test_two_concurrent_claims_never_win_the_same_job():
    db = get_db()
    run_id = uuid.uuid4().hex
    try:
        await eval_queue.enqueue_run(db, run_id=run_id, cases=_cases(1))
        results = await asyncio.gather(
            *(eval_queue.claim_job(db, worker_id=f"w{i}") for i in range(10)),
        )
        # Only claims for THIS run's one job matter — filter out anything a
        # concurrently-running other test's job might have surfaced.
        ours = [r for r in results if r and r["run_id"] == run_id]
        assert len(ours) == 1, f"expected exactly one worker to win the claim, got {len(ours)}"
        assert ours[0]["claim_epoch"] == 1
    finally:
        await _cleanup(db, run_id)


# ------------------------------------------------------------- fencing --- #

async def test_complete_job_fails_after_losing_claim_epoch():
    db = get_db()
    run_id = uuid.uuid4().hex
    try:
        await eval_queue.enqueue_run(db, run_id=run_id, cases=_cases(1))
        job = await eval_queue.claim_job(db, worker_id="w1")
        assert job is not None
        stale_epoch = job["claim_epoch"]
        # Simulate a stale-claim sweep reclaiming this job out from under w1.
        await eval_queue.requeue_stale_claims(db, older_than_seconds=-1)
        # w1's completion, still using its now-invalid epoch, must be rejected.
        ok = await eval_queue.complete_job(db, job_id=job["job_id"], claim_epoch=stale_epoch)
        assert ok is False, "a fenced-out worker's completion must not succeed"
        # The job is available again (pending) for someone else to actually do.
        doc = await db.prompt_eval_case_jobs.find_one({"job_id": job["job_id"]})
        assert doc["status"] == "pending"
    finally:
        await _cleanup(db, run_id)


async def test_complete_job_succeeds_with_matching_epoch():
    db = get_db()
    run_id = uuid.uuid4().hex
    try:
        await eval_queue.enqueue_run(db, run_id=run_id, cases=_cases(1))
        job = await eval_queue.claim_job(db, worker_id="w1")
        ok = await eval_queue.complete_job(db, job_id=job["job_id"], claim_epoch=job["claim_epoch"])
        assert ok is True
        doc = await db.prompt_eval_case_jobs.find_one({"job_id": job["job_id"]})
        assert doc["status"] == "done"
    finally:
        await _cleanup(db, run_id)


async def test_fail_job_requeues_under_max_attempts_then_permanently_fails():
    db = get_db()
    run_id = uuid.uuid4().hex
    try:
        await eval_queue.enqueue_run(db, run_id=run_id, cases=_cases(1))
        job = await eval_queue.claim_job(db, worker_id="w1")
        job_id = job["job_id"]
        epoch = job["claim_epoch"]
        for _ in range(eval_queue.MAX_JOB_ATTEMPTS - 1):
            ok = await eval_queue.fail_job(db, job_id=job_id, claim_epoch=epoch, error="boom")
            assert ok is True
            doc = await db.prompt_eval_case_jobs.find_one({"job_id": job_id})
            assert doc["status"] == "pending", "should requeue while under the attempt cap"
            reclaimed = await eval_queue.claim_job(db, worker_id="w2")
            assert reclaimed["job_id"] == job_id
            epoch = reclaimed["claim_epoch"]
        await eval_queue.fail_job(db, job_id=job_id, claim_epoch=epoch, error="boom again")
        doc = await db.prompt_eval_case_jobs.find_one({"job_id": job_id})
        assert doc["status"] == "failed"
        assert doc["attempts"] == eval_queue.MAX_JOB_ATTEMPTS
    finally:
        await _cleanup(db, run_id)


# --------------------------------------------------------- stale sweep --- #

async def test_requeue_stale_claims_only_touches_old_claims():
    db = get_db()
    run_id = uuid.uuid4().hex
    try:
        await eval_queue.enqueue_run(db, run_id=run_id, cases=_cases(2))
        fresh = await eval_queue.claim_job(db, worker_id="w1")
        # A very negative threshold makes "fresh" look ancient too — proves
        # the sweep actually uses the threshold, not just claimed-ness.
        swept = await eval_queue.requeue_stale_claims(db, older_than_seconds=10_000)
        assert swept == 0, "a job claimed moments ago should not be swept with a generous threshold"
        doc = await db.prompt_eval_case_jobs.find_one({"job_id": fresh["job_id"]})
        assert doc["status"] == "claimed"
    finally:
        await _cleanup(db, run_id)


# ----------------------------------------------------------- finalize --- #

async def test_try_finalize_wins_exactly_once_under_concurrent_callers():
    db = get_db()
    run_id = await _new_run(db)
    try:
        # No jobs enqueued at all == "no outstanding jobs" from the queue's
        # point of view, which is enough to exercise the atomic flip itself.
        results = await asyncio.gather(*(eval_queue.try_finalize(db, run_id) for _ in range(10)))
        winners = [r for r in results if r is not None]
        assert len(winners) == 1, f"expected exactly one finalizer, got {len(winners)}"
        doc = await db.prompt_eval_runs.find_one({"run_id": run_id})
        assert doc["status"] == "finalizing"
    finally:
        await _cleanup(db, run_id)


async def test_try_finalize_returns_none_while_jobs_outstanding():
    db = get_db()
    run_id = await _new_run(db)
    try:
        await eval_queue.enqueue_run(db, run_id=run_id, cases=_cases(1))
        result = await eval_queue.try_finalize(db, run_id)
        assert result is None
    finally:
        await _cleanup(db, run_id)


# ------------------------------------------------------ circuit breaker --- #

async def test_circuit_breaker_trips_exactly_at_threshold():
    db = get_db()
    run_id = await _new_run(db)
    try:
        tripped_count = 0
        for _ in range(eval_queue.CIRCUIT_BREAKER_THRESHOLD):
            tripped = await eval_queue.record_job_failure_and_check_circuit(db, run_id)
            if tripped:
                tripped_count += 1
        assert tripped_count == 1
        assert await eval_queue.is_cooling_down(db, run_id) is True
    finally:
        await _cleanup(db, run_id)


async def test_circuit_breaker_resets_on_success():
    db = get_db()
    run_id = await _new_run(db)
    try:
        await eval_queue.record_job_failure_and_check_circuit(db, run_id)
        await eval_queue.record_job_failure_and_check_circuit(db, run_id)
        await eval_queue.record_job_success(db, run_id)
        doc = await db.prompt_eval_runs.find_one({"run_id": run_id})
        assert doc["consecutive_failures"] == 0
    finally:
        await _cleanup(db, run_id)


# --------------------------------------------------------------------------- #
async def _run_all():
    await init_observability()
    fns = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    passed = 0
    for fn in fns:
        try:
            await fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed}/{len(fns)} tests passed")
    return passed == len(fns)


if __name__ == "__main__":
    ok = asyncio.run(_run_all())
    sys.exit(0 if ok else 1)
