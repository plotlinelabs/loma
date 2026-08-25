"""MongoDB persistence for the prompt eval engine.

Suites (with embedded cases) and runs live here; so does promotion's version
history, which mirrors api/skill_service.py's skill_versions pattern exactly
(_record_version -> record_prompt_version) — see DESIGN.md.
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from eval.schema import CaseResult, JudgeResult, RunResult, TestCase, Variant, VariantResult


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def serialize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


async def ensure_eval_indexes(db) -> None:
    await db.prompt_eval_suites.create_index("suite_id", unique=True)
    await db.prompt_eval_suites.create_index([("setting_key", 1), ("created_at", -1)])
    await db.prompt_eval_runs.create_index("run_id", unique=True)
    await db.prompt_eval_runs.create_index([("suite_id", 1), ("created_at", -1)])
    await db.prompt_settings_versions.create_index([("setting_key", 1), ("created_at", -1)])
    await db.prompt_settings_versions.create_index("version_id", unique=True)
    from eval.queue import ensure_queue_indexes
    await ensure_queue_indexes(db)


# ---------------------------------------------------------------- suites --- #

def _case_to_doc(case: TestCase) -> dict[str, Any]:
    return asdict(case)


def _case_from_doc(doc: dict[str, Any]) -> TestCase:
    return TestCase(
        case_id=doc.get("case_id") or uuid.uuid4().hex[:12],
        input=doc.get("input", ""),
        expected_contains=doc.get("expected_contains") or [],
        expected_not_contains=doc.get("expected_not_contains") or [],
        rubric=doc.get("rubric", ""),
    )


async def create_suite(
    db, *, subject_type: str, label: str, actor: str,
    setting_key: str | None = None, cases: list[dict] | None = None,
) -> dict[str, Any]:
    if subject_type not in ("loma", "generic"):
        raise ValueError(f"Unknown subject_type: {subject_type}")
    suite_id = uuid.uuid4().hex
    normalized_cases = []
    for raw in cases or []:
        raw = dict(raw)
        raw.setdefault("case_id", uuid.uuid4().hex[:12])
        normalized_cases.append(_case_to_doc(_case_from_doc(raw)))
    doc = {
        "suite_id": suite_id,
        "subject_type": subject_type,
        "setting_key": setting_key,
        "label": label,
        "cases": normalized_cases,
        "created_by": actor,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    await db.prompt_eval_suites.insert_one(doc)
    return serialize_doc(doc) or {}


async def list_suites(db, *, subject_type: str | None = None, setting_key: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if subject_type:
        query["subject_type"] = subject_type
    if setting_key:
        query["setting_key"] = setting_key
    docs = await db.prompt_eval_suites.find(query, {"_id": 0}).sort("created_at", -1).to_list(200)
    return [serialize_doc(doc) or {} for doc in docs]


async def get_suite(db, suite_id: str) -> dict[str, Any] | None:
    doc = await db.prompt_eval_suites.find_one({"suite_id": suite_id}, {"_id": 0})
    return serialize_doc(doc)


async def update_suite_cases(db, suite_id: str, cases: list[dict]) -> dict[str, Any] | None:
    normalized_cases = []
    for raw in cases:
        raw = dict(raw)
        raw.setdefault("case_id", uuid.uuid4().hex[:12])
        normalized_cases.append(_case_to_doc(_case_from_doc(raw)))
    await db.prompt_eval_suites.update_one(
        {"suite_id": suite_id},
        {"$set": {"cases": normalized_cases, "updated_at": now_utc()}},
    )
    return await get_suite(db, suite_id)


def suite_cases(suite: dict[str, Any]) -> list[TestCase]:
    return [_case_from_doc(doc) for doc in (suite.get("cases") or [])]


# ------------------------------------------------------------------ runs --- #

def _judge_to_doc(judge: JudgeResult | None) -> dict[str, Any] | None:
    return asdict(judge) if judge is not None else None


def _case_result_to_doc(result: CaseResult) -> dict[str, Any]:
    doc = asdict(result)
    return doc


def case_result_from_doc(doc: dict[str, Any]) -> CaseResult:
    """Reverse of _case_result_to_doc — needed wherever a run's case_results
    (stored as plain Mongo docs) have to be scored/aggregated again, e.g.
    eval/worker.py's finalization step, which reads a run doc's
    already-persisted case_results back rather than holding them in memory
    from a single in-process run. Only `judge` needs reconstructing into a
    real JudgeResult — decision.aggregate() reads vr.judge.score by
    attribute; every other field aggregate() touches (passed, latency_ms,
    cost_usd, metric_results) works fine as the plain dict/primitive Mongo
    already stores it as."""
    return CaseResult(
        case_id=doc["case_id"],
        input=doc["input"],
        variant_results=[
            VariantResult(**{
                **vr,
                "judge": JudgeResult(**vr["judge"]) if vr.get("judge") else None,
            })
            for vr in doc.get("variant_results", [])
        ],
    )


async def record_run(
    db, *, suite_id: str, variants: list[Variant], run_result: RunResult, actor: str,
) -> dict[str, Any]:
    """variants is stored (not just referenced by id) so a run's history is
    a complete, standalone record of the comparison it performed — prompt
    text, model, and agent_profile for every arm, not just the winning one.
    For the Loma subject the "current" variant's text is reconstructable
    later from setting_key + prompt_settings_versions anyway, but for the
    generic subject this is the only durable copy of whatever was pasted in.

    No migration from the old current_text/draft_text/model shape — existing
    prompt_eval_runs documents predate this and simply won't deserialize
    into the new dashboard types. Deliberate: this is a pre-production
    feature, and hash-matching or dual-reading two document shapes is real
    work for a problem that `db.prompt_eval_runs.deleteMany({})` before
    deploying solves for free. See NOTES.md.
    """
    run_id = uuid.uuid4().hex
    doc = {
        "run_id": run_id,
        "suite_id": suite_id,
        "variants": [asdict(v) for v in variants],
        "status": "completed",
        "case_results": [_case_result_to_doc(r) for r in run_result.case_results],
        "summary": asdict(run_result.summary),
        "created_by": actor,
        "created_at": now_utc(),
        "finished_at": now_utc(),
    }
    await db.prompt_eval_runs.insert_one(doc)
    return serialize_doc(doc) or {}


async def start_run(
    db, *, suite_id: str, variants: list[Variant], total_cases: int, actor: str,
    disable_tools: bool, suite_label: str, subject_type: str,
) -> dict[str, Any]:
    """Insert a run doc immediately with status="pending", before any case
    has actually run — the async-run path (eval/queue.py + eval/worker.py)
    fills this same document incrementally via append_case_result()/
    finish_run() rather than waiting to write everything at once, mirroring
    the asyncio.create_task-plus-poll convention already used by
    api/flow_routes.py's handle_run_now and api/task_routes.py's
    handle_create_task. disable_tools/suite_label/subject_type are stored
    (not just passed as local variables) because the worker that eventually
    executes and finalizes this run is a genuinely separate process — it
    can't read them off the request that started the run, only off Mongo."""
    run_id = uuid.uuid4().hex
    doc = {
        "run_id": run_id,
        "suite_id": suite_id,
        "variants": [asdict(v) for v in variants],
        "status": "pending",
        "total_cases": total_cases,
        "disable_tools": disable_tools,
        "suite_label": suite_label,
        "subject_type": subject_type,
        "case_results": [],
        "summary": None,
        "consecutive_failures": 0,
        "cooldown_until": None,
        "created_by": actor,
        "created_at": now_utc(),
        "finished_at": None,
    }
    await db.prompt_eval_runs.insert_one(doc)
    return serialize_doc(doc) or {}


async def mark_run_status(db, run_id: str, status: str) -> None:
    await db.prompt_eval_runs.update_one({"run_id": run_id}, {"$set": {"status": status}})


async def append_case_result(db, run_id: str, result: CaseResult) -> None:
    """Push one case's result onto an in-progress run as soon as it's ready
    — this is what lets a client polling GET /runs/{run_id} mid-run see
    partial progress instead of nothing until the whole batch finishes."""
    await db.prompt_eval_runs.update_one(
        {"run_id": run_id}, {"$push": {"case_results": _case_result_to_doc(result)}},
    )


async def finish_run(db, run_id: str, *, summary, status: str = "completed") -> None:
    await db.prompt_eval_runs.update_one(
        {"run_id": run_id},
        {"$set": {"summary": asdict(summary), "status": status, "finished_at": now_utc()}},
    )


async def get_run(db, run_id: str) -> dict[str, Any] | None:
    doc = await db.prompt_eval_runs.find_one({"run_id": run_id}, {"_id": 0})
    return serialize_doc(doc)


async def get_latest_run_for_suite(db, suite_id: str) -> dict[str, Any] | None:
    """Most recent run for a suite, or None if it's never been run. Backs
    re-hydrating a run's results after the component that was showing them
    unmounts and remounts — e.g. Admin's Settings tab, whose Radix Tabs
    unmounts inactive TabsContent by default, which was silently discarding
    a completed run's results on every tab switch since the run only ever
    lived in that component's local React state. Uses the existing
    (suite_id, created_at desc) index."""
    doc = await db.prompt_eval_runs.find_one(
        {"suite_id": suite_id}, {"_id": 0}, sort=[("created_at", -1)],
    )
    return serialize_doc(doc)


async def has_eval_run_for_setting(db, setting_key: str) -> bool:
    """Backs the soft "run an eval before promoting" banner — see DESIGN.md
    on why this doesn't hash-match a specific draft."""
    suite_ids = await db.prompt_eval_suites.find(
        {"setting_key": setting_key}, {"_id": 0, "suite_id": 1},
    ).to_list(1000)
    if not suite_ids:
        return False
    ids = [s["suite_id"] for s in suite_ids]
    count = await db.prompt_eval_runs.count_documents({"suite_id": {"$in": ids}})
    return count > 0


# ------------------------------------------------------- prompt versions --- #

async def record_prompt_version(
    db, *, setting_key: str, content: str, actor: str, message: str, eval_run_id: str | None = None,
) -> str:
    version_id = uuid.uuid4().hex
    await db.prompt_settings_versions.insert_one({
        "version_id": version_id,
        "setting_key": setting_key,
        "content": content,
        "actor_email": actor,
        "message": message,
        "eval_run_id": eval_run_id,
        "created_at": now_utc(),
    })
    return version_id


async def prompt_version_history(db, setting_key: str) -> list[dict[str, Any]]:
    docs = await db.prompt_settings_versions.find(
        {"setting_key": setting_key}, {"_id": 0},
    ).sort("created_at", -1).to_list(100)
    return [serialize_doc(doc) or {} for doc in docs]


async def prompt_version_diff(db, setting_key: str, from_version: str, to_version: str = "HEAD") -> str:
    from_doc = await db.prompt_settings_versions.find_one({"setting_key": setting_key, "version_id": from_version})
    if not from_doc:
        raise ValueError(f"Unknown version: {from_version}")
    from_text = (from_doc.get("content") or "").splitlines(keepends=True)

    if to_version == "HEAD":
        live = await db.prompt_settings.find_one({"setting_key": setting_key})
        to_text = ((live or {}).get("content") or "").splitlines(keepends=True)
    else:
        to_doc = await db.prompt_settings_versions.find_one({"setting_key": setting_key, "version_id": to_version})
        if not to_doc:
            raise ValueError(f"Unknown version: {to_version}")
        to_text = (to_doc.get("content") or "").splitlines(keepends=True)

    return "".join(difflib.unified_diff(from_text, to_text, fromfile=from_version, tofile=to_version))
