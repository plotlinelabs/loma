"""Prompt evaluation API — draft a prompt, run it against test cases, compare
to the version currently live, and only then promote.

Two subjects share every route here except /promote (which only makes sense
for the Loma subject — see DESIGN.md):
  - "loma": draft = an edit to one of agent.prompt.RULEBOOK_KEYS (the subset
    of PROMPT_SETTING_KEYS that actually feeds the system prompt).
  - "generic": draft/current are both supplied directly in the run request —
    a one-off comparison, nothing persisted beyond the run itself.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict

from aiohttp import web

from agent.opencode_runtime import AGENT_PROFILES
from agent.prompt import PROMPT_SETTING_KEYS, RULEBOOK_KEYS
from api.auth_helpers import get_user_email, require_maintainer_or_above
from api.prompt_settings_routes import write_prompt_setting
from eval import csv_import
from eval import queue as eval_queue
from eval import service as eval_service
from eval.prompt_subject import loma_current_and_draft
from eval.runner import DEFAULT_EVAL_MODEL
from eval.schema import Variant
from observability.db import get_db

logger = logging.getLogger(__name__)


def _require_db():
    db = get_db()
    if db is None:
        raise web.HTTPServiceUnavailable(text='{"error": "DB not configured"}', content_type="application/json")
    return db


async def handle_create_suite(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = _require_db()
    body = await request.json()

    subject_type = body.get("subject_type")
    if subject_type not in ("loma", "generic"):
        return web.json_response({"error": "subject_type must be 'loma' or 'generic'"}, status=400)

    setting_key = body.get("setting_key")
    if subject_type == "loma" and setting_key not in RULEBOOK_KEYS:
        return web.json_response({
            "error": f"'{setting_key}' isn't part of the system prompt — only {list(RULEBOOK_KEYS)} are eval-able",
        }, status=400)

    label = (body.get("label") or "").strip()
    if not label:
        return web.json_response({"error": "label is required"}, status=400)

    suite = await eval_service.create_suite(
        db,
        subject_type=subject_type,
        setting_key=setting_key if subject_type == "loma" else None,
        label=label,
        cases=body.get("cases") or [],
        actor=get_user_email(request),
    )
    logger.info(
        "Eval suite created: suite_id=%s subject=%s setting_key=%s cases=%d actor=%s",
        suite["suite_id"], subject_type, setting_key, len(suite.get("cases") or []), get_user_email(request),
    )
    return web.json_response({"suite": suite}, status=201)


async def handle_list_suites(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = _require_db()
    suites = await eval_service.list_suites(
        db,
        subject_type=request.query.get("subject_type"),
        setting_key=request.query.get("setting_key"),
    )
    return web.json_response({"suites": suites})


async def handle_get_suite(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = _require_db()
    suite = await eval_service.get_suite(db, request.match_info["suite_id"])
    if suite is None:
        return web.json_response({"error": "Suite not found"}, status=404)
    had_prior_eval = (
        await eval_service.has_eval_run_for_setting(db, suite["setting_key"])
        if suite.get("setting_key") else None
    )
    return web.json_response({"suite": suite, "had_prior_eval": had_prior_eval})


async def handle_update_suite_cases(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = _require_db()
    suite_id = request.match_info["suite_id"]
    body = await request.json()
    cases = body.get("cases")
    if not isinstance(cases, list):
        return web.json_response({"error": "cases must be a list"}, status=400)
    suite = await eval_service.update_suite_cases(db, suite_id, cases)
    if suite is None:
        return web.json_response({"error": "Suite not found"}, status=404)
    return web.json_response({"suite": suite})


async def handle_upload_suite_cases(request: web.Request) -> web.Response:
    """POST /api/prompt-eval/suites/{suite_id}/cases/upload — bulk-add test
    cases from a CSV file (the golden-dataset upload path). Multipart, field
    name "file". Appends to whatever cases the suite already has — no dedup,
    re-uploading the same file twice adds duplicates with fresh case_ids;
    cases stay hand-editable/removable afterward via the same CaseEditor any
    other case goes through, so this isn't a one-way door. All-or-nothing:
    a single bad row rejects the whole file (see eval/csv_import.py) rather
    than silently importing most of it.
    """
    require_maintainer_or_above(request)
    db = _require_db()
    suite = await eval_service.get_suite(db, request.match_info["suite_id"])
    if suite is None:
        return web.json_response({"error": "Suite not found"}, status=404)

    reader = await request.multipart()
    data = b""
    async for part in reader:
        if part.name == "file":
            data = await part.read(decode=False)
            break
    if not data:
        return web.json_response({"error": "Multipart field 'file' is required"}, status=400)

    try:
        new_cases = csv_import.parse_cases_csv(data)
    except csv_import.CsvParseError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    merged = eval_service.suite_cases(suite) + new_cases
    updated = await eval_service.update_suite_cases(db, suite["suite_id"], [asdict(c) for c in merged])
    actor = get_user_email(request)
    logger.info(
        "CSV upload: suite_id=%s rows=%d total_cases=%d actor=%s",
        suite["suite_id"], len(new_cases), len(merged), actor,
    )
    return web.json_response({"suite": updated, "added": len(new_cases)})


def _build_variants(body: dict, suite: dict) -> list[Variant] | web.Response:
    """Turn a run request body into the list of Variants to compare.
    Returns a web.Response directly on validation failure — callers check
    isinstance(..., web.Response) and return it as-is.

    Loma subject body: {"draft_text": str, "model"?: str, "agent_profile"?: str}
    — request shape unchanged from before N-way existed; the admin panel
    doesn't expose N-way comparison this pass. Builds exactly 2 variants:
    "current" (build_pooled_system_prompt(), unmodified) and "draft". The
    current variant defaults to mirroring the draft's model/agent_profile —
    preserves the old "only the prompt content differs" guarantee by
    default; there's no UI yet to override that independently.

    Generic subject body: {"variants": [{"label", "prompt_text", "model",
    "agent_profile"?}, ...]} — true N-way, >= 2 variants required.
    """
    default_model = body.get("model") or DEFAULT_EVAL_MODEL
    default_profile = body.get("agent_profile") or "default"
    if default_profile not in AGENT_PROFILES:
        return web.json_response({"error": f"Unknown agent_profile: {default_profile!r}"}, status=400)

    if suite["subject_type"] == "loma":
        draft_text = body.get("draft_text")
        if not isinstance(draft_text, str) or not draft_text.strip():
            return web.json_response({"error": "draft_text is required"}, status=400)
        current_prompt, draft_prompt = loma_current_and_draft(suite["setting_key"], draft_text)
        return [
            Variant(variant_id="current", label="Current (live)", prompt_text=current_prompt,
                    model=default_model, agent_profile=default_profile),
            Variant(variant_id="draft", label="Draft", prompt_text=draft_prompt,
                    model=default_model, agent_profile=default_profile),
        ]

    raw_variants = body.get("variants")
    if not isinstance(raw_variants, list) or len(raw_variants) < 2:
        return web.json_response({"error": "variants must be a list of at least 2 entries"}, status=400)

    variants = []
    for i, rv in enumerate(raw_variants):
        if not isinstance(rv, dict):
            return web.json_response({"error": f"variants[{i}] must be an object"}, status=400)
        prompt_text = rv.get("prompt_text")
        model = rv.get("model") or default_model
        profile = rv.get("agent_profile") or default_profile
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            return web.json_response({"error": f"variants[{i}].prompt_text is required"}, status=400)
        if not isinstance(model, str) or not model.strip():
            return web.json_response({"error": f"variants[{i}].model is required"}, status=400)
        if profile not in AGENT_PROFILES:
            return web.json_response({"error": f"variants[{i}].agent_profile is unknown: {profile!r}"}, status=400)
        variants.append(Variant(
            variant_id=rv.get("variant_id") or uuid.uuid4().hex[:8],
            label=(rv.get("label") or f"Variant {i + 1}").strip(),
            prompt_text=prompt_text, model=model, agent_profile=profile,
        ))
    return variants


async def handle_run_suite(request: web.Request) -> web.Response:
    """POST /api/prompt-eval/suites/{suite_id}/run — enqueues a run onto the
    real worker pool (eval/queue.py + eval/worker.py — embedded workers in
    this process by default, plus any `eval-worker` container replicas) and
    returns immediately (202) with a pending run doc. Poll GET /runs/{id};
    status flips pending -> running -> completed|failed|incomplete,
    case_results grows as cases land, exactly as before this moved off a
    single in-process asyncio.create_task — see DESIGN.md."""
    require_maintainer_or_above(request)
    db = _require_db()
    suite = await eval_service.get_suite(db, request.match_info["suite_id"])
    if suite is None:
        return web.json_response({"error": "Suite not found"}, status=404)

    body = await request.json()
    variants = _build_variants(body, suite)
    if isinstance(variants, web.Response):
        return variants

    # Loma's real system prompt legitimately uses tools — disabling them
    # would make the eval stop reflecting what the live agent does. A pasted
    # generic persona has no legitimate reason to touch a real tool — see
    # run_eval()'s docstring for why this can't be decided in eval/runner.py
    # itself. Found the hard way (see NOTES.md): a generic-subject test case
    # got answered from a live web search of an unrelated company's pricing
    # page when tools were left on.
    disable_tools = suite["subject_type"] != "loma"

    cases = eval_service.suite_cases(suite)
    if not cases:
        return web.json_response({"error": "Suite has no test cases"}, status=400)

    actor = get_user_email(request)
    run_doc = await eval_service.start_run(
        db, suite_id=suite["suite_id"], variants=variants, total_cases=len(cases), actor=actor,
        disable_tools=disable_tools, suite_label=suite.get("label", suite["suite_id"]),
        subject_type=suite["subject_type"],
    )
    run_id = run_doc["run_id"]
    await eval_queue.enqueue_run(db, run_id=run_id, cases=cases)
    await eval_service.mark_run_status(db, run_id, "running")
    logger.info(
        "Eval run enqueued: suite_id=%s run_id=%s subject=%s variants=%d cases=%d disable_tools=%s actor=%s",
        suite["suite_id"], run_id, suite["subject_type"], len(variants), len(cases), disable_tools, actor,
    )
    run_doc["status"] = "running"
    return web.json_response({"run": run_doc}, status=202)


async def handle_get_run(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = _require_db()
    run = await eval_service.get_run(db, request.match_info["run_id"])
    if run is None:
        return web.json_response({"error": "Run not found"}, status=404)
    return web.json_response({"run": run})


async def handle_get_latest_run_for_suite(request: web.Request) -> web.Response:
    """GET /api/prompt-eval/suites/{suite_id}/latest-run — the most recent
    run for a suite, or {"run": null} if it's never been run. Lets a client
    re-hydrate a run's results after remounting (e.g. switching Admin tabs
    away from and back to Settings) instead of showing a blank state for a
    run that already finished."""
    require_maintainer_or_above(request)
    db = _require_db()
    suite = await eval_service.get_suite(db, request.match_info["suite_id"])
    if suite is None:
        return web.json_response({"error": "Suite not found"}, status=404)
    run = await eval_service.get_latest_run_for_suite(db, suite["suite_id"])
    return web.json_response({"run": run})


async def handle_promote(request: web.Request) -> web.Response:
    """POST /api/prompt-eval/promote — write a draft live. Loma subject only
    (see module docstring). Soft-gated: warns if no eval run exists for this
    setting_key, but never blocks — see DESIGN.md."""
    require_maintainer_or_above(request)
    db = _require_db()
    body = await request.json()

    setting_key = body.get("setting_key")
    if setting_key not in PROMPT_SETTING_KEYS:
        return web.json_response({"error": "Unknown prompt setting"}, status=400)
    content = body.get("content")
    if not isinstance(content, str):
        return web.json_response({"error": "content must be a string"}, status=400)
    run_id = body.get("run_id")

    had_prior_eval = await eval_service.has_eval_run_for_setting(db, setting_key)
    actor = get_user_email(request)
    if not had_prior_eval:
        logger.warning("Promote with no prior eval run: key=%s actor=%s", setting_key, actor)
    doc = await write_prompt_setting(
        db, setting_key=setting_key, content=content, actor=actor,
        source="promote", eval_run_id=run_id,
    )
    return web.json_response({"setting": eval_service.serialize_doc(doc), "had_prior_eval": had_prior_eval})


async def handle_prompt_history(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = _require_db()
    setting_key = request.match_info["setting_key"]
    if setting_key not in PROMPT_SETTING_KEYS:
        return web.json_response({"error": "Unknown prompt setting"}, status=404)
    history = await eval_service.prompt_version_history(db, setting_key)
    return web.json_response({"history": history})


async def handle_prompt_diff(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = _require_db()
    setting_key = request.match_info["setting_key"]
    if setting_key not in PROMPT_SETTING_KEYS:
        return web.json_response({"error": "Unknown prompt setting"}, status=404)
    from_version = request.query.get("from")
    if not from_version:
        return web.json_response({"error": "?from=<version_id> is required"}, status=400)
    to_version = request.query.get("to", "HEAD")
    try:
        diff = await eval_service.prompt_version_diff(db, setting_key, from_version, to_version)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    return web.json_response({"diff": diff})


def setup_prompt_eval_routes(app: web.Application):
    app.router.add_post("/api/prompt-eval/suites", handle_create_suite)
    app.router.add_get("/api/prompt-eval/suites", handle_list_suites)
    app.router.add_get("/api/prompt-eval/suites/{suite_id}", handle_get_suite)
    app.router.add_patch("/api/prompt-eval/suites/{suite_id}", handle_update_suite_cases)
    app.router.add_post("/api/prompt-eval/suites/{suite_id}/cases/upload", handle_upload_suite_cases)
    app.router.add_post("/api/prompt-eval/suites/{suite_id}/run", handle_run_suite)
    app.router.add_get("/api/prompt-eval/runs/{run_id}", handle_get_run)
    app.router.add_get("/api/prompt-eval/suites/{suite_id}/latest-run", handle_get_latest_run_for_suite)
    app.router.add_post("/api/prompt-eval/promote", handle_promote)
    app.router.add_get("/api/prompt-settings/{setting_key}/history", handle_prompt_history)
    app.router.add_get("/api/prompt-settings/{setting_key}/diff", handle_prompt_diff)
