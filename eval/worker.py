"""Worker loop for the eval job queue (see eval/queue.py).

Runs two ways, both calling `run_worker_loop()`:
  - EMBEDDED_WORKER_COUNT copies started as background asyncio tasks inside
    the main backend process (see app.py::main()) — a plain `docker compose
    up` with no extra containers still processes eval runs out of the box,
    replacing what MAX_CONCURRENT_CASES used to bound in the old
    single-process design.
  - `python3 -m eval.worker` as the entrypoint for the `eval-worker`
    docker-compose service, scaled with `docker compose up -d --scale
    eval-worker=N` for real horizontal throughput on a large run. Each
    worker container spawns its own OpenCode subprocess via the existing,
    unmodified agent.opencode_runtime.ensure_opencode_server() self-spawn
    logic — deliberately simpler than sharing one OpenCode instance across
    containers (see DESIGN.md).

Reuses eval/runner.py's executor (run_one_case) completely unchanged — this
module is purely the claim/execute/finalize loop around it, no new
LLM-calling logic.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from eval import decision
from eval import queue as eval_queue
from eval import service as eval_service
from eval.runner import run_one_case
from eval.schema import Variant
from observability.notifications import create_notification

logger = logging.getLogger(__name__)

WORKER_POLL_INTERVAL_SECONDS = float(os.environ.get("EVAL_WORKER_POLL_INTERVAL", "1.0"))
STALE_SWEEP_INTERVAL_LOOPS = 30  # sweep roughly every 30 * poll interval when idle
EMBEDDED_WORKER_COUNT = int(os.environ.get("EMBEDDED_WORKER_COUNT", "4"))

# Per-process cache: run_id -> (variants, cases_by_id, disable_tools). Suite/
# variant data doesn't change mid-run, so re-fetching it once per claimed
# job (potentially thousands of times for one large run) would be wasted
# work for no benefit — this is invalidated (popped) the moment a run
# finalizes.
_run_context_cache: dict[str, tuple[list[Variant], dict, bool]] = {}


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def _load_run_context(db, run_id: str):
    if run_id in _run_context_cache:
        return _run_context_cache[run_id]
    run = await eval_service.get_run(db, run_id)
    if run is None:
        return None
    suite = await eval_service.get_suite(db, run["suite_id"])
    if suite is None:
        return None
    variants = [Variant(**v) for v in run["variants"]]
    cases_by_id = {c.case_id: c for c in eval_service.suite_cases(suite)}
    ctx = (variants, cases_by_id, bool(run.get("disable_tools", False)))
    _run_context_cache[run_id] = ctx
    return ctx


async def _process_job(db, job: dict, worker_id: str) -> None:
    job_id = job["job_id"]
    run_id = job["run_id"]
    claim_epoch = job["claim_epoch"]

    if await eval_queue.is_cooling_down(db, run_id):
        # This run's circuit breaker is tripped — give the job back
        # unclaimed rather than working on it (or letting it sit claimed
        # until the stale-sweep reclaims it 400s from now, wasting a whole
        # sweep cycle for no reason).
        await eval_queue.release_job(db, job_id=job_id, claim_epoch=claim_epoch)
        return

    ctx = await _load_run_context(db, run_id)
    if ctx is None:
        # Run or suite vanished from under us (shouldn't happen in practice)
        # — fail the job outright rather than loop forever on it.
        await eval_queue.fail_job(db, job_id=job_id, claim_epoch=claim_epoch, error="run or suite not found")
        return
    variants, cases_by_id, disable_tools = ctx
    case = cases_by_id.get(job["case_id"])
    if case is None:
        await eval_queue.fail_job(db, job_id=job_id, claim_epoch=claim_epoch, error="case not found in suite")
        return

    try:
        result = await run_one_case(case, variants, disable_tools)
    except Exception as exc:
        logger.warning("Eval job failed: run_id=%s case_id=%s worker=%s: %s", run_id, case.case_id, worker_id, exc)
        await eval_queue.fail_job(db, job_id=job_id, claim_epoch=claim_epoch, error=str(exc))
        await eval_queue.record_job_failure_and_check_circuit(db, run_id)
        return

    # Fenced: if this update matches zero documents, a stale-claim sweep
    # reclaimed this job while we were still working on it (we were slow,
    # not crashed) — a second worker has already claimed/completed it, or
    # will. Discarding here, not pushing, is what prevents a duplicate
    # case_id in case_results.
    completed = await eval_queue.complete_job(db, job_id=job_id, claim_epoch=claim_epoch)
    if not completed:
        logger.info("Eval job %s lost its claim before completion — discarding a late result", job_id)
        return
    await eval_service.append_case_result(db, run_id, result)
    await eval_queue.record_job_success(db, run_id)
    await _finalize_if_done(db, run_id)


async def _finalize_if_done(db, run_id: str) -> None:
    remaining = await eval_queue.remaining_count(db, run_id)
    if remaining > 0:
        return  # cheap pre-filter — not authoritative, try_finalize re-checks
    run = await eval_queue.try_finalize(db, run_id)
    if run is None:
        return  # lost the race, already finalized, or a recount found more work
    _run_context_cache.pop(run_id, None)

    case_result_docs = run.get("case_results") or []
    total_cases = run.get("total_cases") or 0
    if len(case_result_docs) != total_cases:
        # A crash mid-enqueue_run left this run short jobs that were never
        # inserted at all — "no outstanding jobs" isn't the same as "every
        # case ran." Surface it rather than silently reporting "completed"
        # with fewer results than the suite actually had. See NOTES.md.
        logger.error(
            "Eval run %s finalized short: %d/%d cases have results", run_id, len(case_result_docs), total_cases,
        )
        await eval_service.mark_run_status(db, run_id, "incomplete")
        return

    case_results = [eval_service.case_result_from_doc(doc) for doc in case_result_docs]
    summary = decision.aggregate(case_results)
    await eval_service.finish_run(db, run_id, summary=summary, status="completed")
    logger.info("Eval run completed: run_id=%s cases=%d", run_id, total_cases)
    try:
        await create_notification(
            db, user_email=run.get("created_by", ""),
            title=f"Eval run finished — {run.get('suite_label') or run.get('suite_id', '')}",
            body=f"{total_cases} case(s) across {len(run.get('variants') or [])} variant(s).",
            link="/admin" if run.get("subject_type") == "loma" else "/prompt-lab", source="agent",
        )
    except Exception:
        logger.exception("Failed to send eval-run-finished notification: run_id=%s", run_id)


async def run_worker_loop(worker_id: str) -> None:
    """Runs forever. Claim -> process -> repeat; sleeps and sweeps stale
    claims when there's nothing pending."""
    from observability.db import get_db
    db = get_db()
    logger.info("Eval worker %s starting", worker_id)
    idle_loops = 0
    while True:
        try:
            job = await eval_queue.claim_job(db, worker_id=worker_id)
            if job is None:
                idle_loops += 1
                if idle_loops % STALE_SWEEP_INTERVAL_LOOPS == 0:
                    await eval_queue.requeue_stale_claims(db)
                await asyncio.sleep(WORKER_POLL_INTERVAL_SECONDS)
                continue
            idle_loops = 0
            await _process_job(db, job, worker_id)
        except Exception:
            # Not a double-claim risk (find_one_and_update is atomic
            # regardless) — this is about the worker's own survival. A
            # transient Mongo blip (a brief primary election window,
            # network hiccup) must not silently kill this loop forever —
            # unlike _process_job's own errors (a bad case/model), which
            # are already handled per-job via fail_job/circuit-breaker,
            # this catches failures in claim/sweep themselves, which used
            # to propagate straight out of the loop and quietly drop one
            # worker from the pool until the process restarted.
            logger.exception("Eval worker %s hit an unexpected error in its loop", worker_id)
            await asyncio.sleep(WORKER_POLL_INTERVAL_SECONDS)


def start_embedded_workers() -> list[asyncio.Task]:
    """Starts EMBEDDED_WORKER_COUNT worker loops as background tasks inside
    the current process — called once from app.py::main(), alongside the
    existing pool warmup. Each gets its own worker_id (hostname:pid alone
    isn't unique across these — they share both)."""
    tasks = []
    for _ in range(EMBEDDED_WORKER_COUNT):
        worker_id = make_worker_id()
        tasks.append(asyncio.create_task(run_worker_loop(worker_id)))
    logger.info("Started %d embedded eval workers", EMBEDDED_WORKER_COUNT)
    return tasks


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    from observability.db import init_observability, get_db
    await init_observability()
    await eval_service.ensure_eval_indexes(get_db())
    worker_id = make_worker_id()
    await run_worker_loop(worker_id)


if __name__ == "__main__":
    asyncio.run(_main())
