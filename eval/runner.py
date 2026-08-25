"""Subject-agnostic eval orchestration — the only I/O in the eval/ package.

run_eval()/iter_case_results() take a list of Variants (each its own prompt
text + model + agent_profile) and a list of test cases, and have no idea
whether a variant's prompt text came from Loma's Mongo-backed settings or a
pasted custom prompt — see eval/prompt_subject.py for the Loma adapter, and
DESIGN.md for why. "Current vs draft" is just the 2-variant case of this
general model, not a separate code path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import statistics
import time
from typing import AsyncIterator

from agent.opencode_runtime import run_opencode_oneshot
from eval.decision import aggregate, score_case
from eval.metrics import Metric
from eval.schema import CaseResult, JudgeResult, RunResult, TestCase, ToolCall, Variant, VariantRunOutput

logger = logging.getLogger(__name__)

# Deliberately not the same as AGENT_DEFAULT_MODEL. The eval engine's
# fallback should be something that reliably responds regardless of the
# deployment's own provider/billing state — a case can always override this
# per-run. (Found via manual verification: this deployment's own default,
# opencode-go/deepseek-v4-flash, is currently region-gated on its OpenCode
# Zen account, and the other paid models fail for lack of a payment method —
# this free-tier model has neither problem.) Overridable — this specific
# free-tier model can itself have rough patches (confirmed live: two direct
# probes at the full EVAL_ONESHOT_TIMEOUT_SECONDS ceiling both timed out
# waiting for any response), independent of anything in this codebase.
DEFAULT_EVAL_MODEL = os.environ.get("DEFAULT_EVAL_MODEL", "opencode/nemotron-3.5-lightning-free")

# Only bounds iter_case_results()/run_eval()'s own in-process fan-out — the
# small, simple "gather everything in one process" convenience path, still
# used directly by tests. This is NOT the real concurrency knob for the
# actual async-run API path anymore (see eval/queue.py, eval/worker.py):
# that's now governed by EMBEDDED_WORKER_COUNT (in-process workers) plus
# however many `eval-worker` container replicas are running — real,
# separately-crashable, horizontally-scalable units instead of one
# process's asyncio tasks. Every case now makes N (response) +
# N * JUDGE_SELF_CONSISTENCY_SAMPLES (judge) OpenCode calls, where N is the
# number of variants being compared — self-consistency multiplies the judge
# cost by design (see _judge()), and N-way comparison multiplies both by
# design. This concurrency limit doesn't scale down as variant count grows —
# a deliberate, accepted tradeoff carried over from the single-process era,
# not an oversight: a 4-variant case can still peak at 4 + 4*3 = 16
# concurrent calls per in-flight slot. EVAL_ONESHOT_TIMEOUT_SECONDS is what
# actually bounds the damage from a degraded provider — see NOTES.md for the
# live incident that number was tuned against.
IN_PROCESS_FANOUT_LIMIT = 4

# How many independent times to sample the judge per response. >1 is the whole
# point — a single sample is one model's mood, not a signal. eval/decision.py's
# JUDGE_MAX_DISAGREEMENT is what actually uses the extra samples; this just
# generates them. Overridable, not just for tuning — the free-tier default
# model can make this genuinely slow under real provider load (each rubric'd
# case fans out 2 * JUDGE_SELF_CONSISTENCY_SAMPLES judge calls on top of its
# 2 response calls), and eval/decision.py already degrades correctly at 1
# sample (no measurable disagreement, falls through to the plain threshold
# check — see test_single_sample_has_no_measurable_disagreement). Set to 1
# only to trade reliability for speed while testing; 3 is the real default.
JUDGE_SELF_CONSISTENCY_SAMPLES = int(os.environ.get("JUDGE_SELF_CONSISTENCY_SAMPLES", "3"))

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict grader. You will be given a rubric and a response to "
    "grade against it. Reply with EXACTLY one line in this format and "
    "nothing else:\n"
    "SCORE: <a number from 0.0 to 1.0> REASON: <one short sentence>"
)

# Used to grade a case that has no author-written rubric, so every case
# gets a real judge score to feed eval.metrics.CompositeScoreMetric instead
# of only the minority of cases someone happened to write a rubric for. A
# case's own rubric always takes priority — this is a fallback, not a
# replacement (see _run_variant_for_case).
_DEFAULT_RUBRIC = (
    "Does this response accurately, helpfully, and appropriately address "
    "the user's input, in a tone and level of detail a real user would "
    "find satisfying?"
)

_JUDGE_SCORE_RE = re.compile(r"SCORE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_JUDGE_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


async def _safe_oneshot(
    system_prompt: str, user_prompt: str, model: str, *,
    agent_profile: str = "default", disable_tools: bool = False,
) -> tuple[str, list[ToolCall], str | None, dict | None, float | None]:
    """run_opencode_oneshot(), but never raises.

    Returns (response_text, tool_calls, error_message, usage, cost_usd) —
    error_message is None on success, tool_calls/usage/cost are empty/None
    on failure. One case's OpenCode failure (rate limit, a bad API key, a
    server hiccup) must not take down every other case in the same run.

    disable_tools is passed straight through — see run_opencode_oneshot's
    docstring. Only the judge (below) sets it: grading a response text is
    never a legitimate reason to touch a tool, in any subject.
    """
    try:
        text, raw_tool_calls, usage, cost = await run_opencode_oneshot(
            system_prompt=system_prompt, user_prompt=user_prompt, selected_model=model,
            agent_profile=agent_profile, disable_tools=disable_tools,
        )
        tool_calls = [ToolCall(**tc) for tc in raw_tool_calls]
        return text, tool_calls, None, usage, cost
    except Exception as exc:
        logger.warning("Oneshot completion failed: %s", exc, exc_info=True)
        return "", [], str(exc), None, None


async def _judge_once(rubric: str, response: str, model: str) -> tuple[float | None, str]:
    """One independent judge sample. Returns (score, reasoning) — score is
    None if the call failed or the output didn't parse; reasoning is always
    populated (the error/parse-failure detail in that case) so a caller can
    show *why* a sample didn't count, not just that it didn't."""
    prompt = f"Rubric: {rubric}\n\nResponse to grade:\n{response}"
    # Always agent_profile="default" and disable_tools=True — the judge is
    # a stable measuring instrument, not one of the things being compared.
    # Grading under a variant's own (possibly different) temperature would
    # make a judge-score difference potentially mean "the judge sampled
    # differently," not "the response was actually different in quality."
    text, _tool_calls, error, _usage, _cost = await _safe_oneshot(
        _JUDGE_SYSTEM_PROMPT, prompt, model, agent_profile="default", disable_tools=True,
    )
    if error:
        return None, f"sample failed: {error}"

    score_match = _JUDGE_SCORE_RE.search(text)
    if not score_match:
        return None, f"unparseable judge output: {text[:200]!r}"
    score = max(0.0, min(1.0, float(score_match.group(1))))
    reason_match = _JUDGE_REASON_RE.search(text)
    reasoning = reason_match.group(1).strip() if reason_match else text.strip()
    return score, reasoning


async def _judge(rubric: str, response: str, model: str) -> JudgeResult:
    """Grade one response against a rubric with self-consistency: sample the
    judge JUDGE_SELF_CONSISTENCY_SAMPLES times independently (same rubric,
    same response, same model) rather than trusting a single call. Score is
    the *median* of the valid samples — median, not mean, so one erratic
    sample can't drag an otherwise-agreeing set around.

    Whether the samples actually agree is eval/decision.py's job
    (JUDGE_MAX_DISAGREEMENT) — this function only produces the samples, it
    doesn't decide if they're trustworthy. No separate LLM-client dependency
    either way — every sample goes through the same oneshot executor, no
    assumption ANTHROPIC_API_KEY/OPENAI_API_KEY is set.
    """
    results = await asyncio.gather(
        *(_judge_once(rubric, response, model) for _ in range(JUDGE_SELF_CONSISTENCY_SAMPLES))
    )
    samples = [score for score, _ in results if score is not None]

    if not samples:
        # Every sample failed or was unparseable — surface the last sample's
        # reasoning (its failure detail) rather than a blank explanation.
        return JudgeResult(score=None, reasoning=results[-1][1], samples=[])

    median = statistics.median(samples)
    if len(samples) == 1:
        reasoning = next(r for s, r in results if s is not None)
    else:
        # Show every valid sample's own score + reasoning, not just the
        # aggregate — this is what makes disagreement inspectable instead of
        # hidden behind one number.
        reasoning = " | ".join(f"({s:.2f}) {r}" for s, r in results if s is not None)
    return JudgeResult(score=median, reasoning=reasoning, samples=samples)


async def _judge_or_skip(rubric: str, response: str, model: str, error: str | None) -> JudgeResult | None:
    """Don't waste a judge call grading an error message — score_variant()
    already fails an errored variant outright regardless of the judge result."""
    if error:
        return None
    return await _judge(rubric, response, model)


async def _run_variant_for_case(case: TestCase, variant: Variant, disable_tools: bool) -> VariantRunOutput:
    """Run one variant against one case: the oneshot call, timed, plus a
    judge call if the case has a rubric. Pure I/O gathering — no scoring
    logic here, that's eval/decision.py's job."""
    started = time.perf_counter()
    text, tool_calls, error, usage, cost = await _safe_oneshot(
        variant.prompt_text, case.input, variant.model,
        agent_profile=variant.agent_profile, disable_tools=disable_tools,
    )
    latency_ms = (time.perf_counter() - started) * 1000

    # Always judge now, even without an author-written rubric — falling
    # back to _DEFAULT_RUBRIC so CompositeScoreMetric has a real signal on
    # every case, not just the ones someone wrote a rubric for. This is the
    # one place that multiplies every case's call count, not just rubric'd
    # ones — see IN_PROCESS_FANOUT_LIMIT's comment above for the concurrency
    # consequence.
    judge = await _judge_or_skip(case.rubric or _DEFAULT_RUBRIC, text, variant.model, error)

    return VariantRunOutput(
        variant_id=variant.variant_id,
        label=variant.label,
        response=f"[oneshot failed: {error}]" if error else text,
        error=error,
        judge=judge,
        tool_calls=tool_calls,
        latency_ms=latency_ms,
        cost_usd=cost,
    )


async def run_one_case(
    case: TestCase, variants: list[Variant], disable_tools: bool,
    metrics: list[Metric] | None = None, semaphore: asyncio.Semaphore | None = None,
) -> CaseResult:
    """Run every variant for one case concurrently and score the result.
    Public — eval/worker.py calls this directly, one case at a time per
    worker (its own concurrency comes from how many worker loops/replicas
    exist, not from a semaphore here). semaphore is only used by
    iter_case_results() below, which still fans out many cases at once
    in-process."""
    async def _gather():
        return await asyncio.gather(
            *(_run_variant_for_case(case, variant, disable_tools) for variant in variants)
        )
    if semaphore is not None:
        async with semaphore:
            outputs = await _gather()
    else:
        outputs = await _gather()
    return score_case(case, list(outputs), metrics=metrics)


async def iter_case_results(
    variants: list[Variant], cases: list[TestCase], disable_tools: bool = False,
    metrics: list[Metric] | None = None,
) -> AsyncIterator[CaseResult]:
    """Yield CaseResults as they complete, not in input order — see
    asyncio.as_completed. A single-process, in-memory convenience for
    simple/test callers that just want every result without incremental
    persistence — the real API path (api/prompt_eval_routes.py) goes
    through eval/queue.py + eval/worker.py instead, which calls
    run_one_case() directly, one case at a time per worker, distributed
    across the real worker pool rather than fanned out in one process
    behind a semaphore. run_eval() below is a thin gather-everything
    wrapper over this same path.
    """
    semaphore = asyncio.Semaphore(IN_PROCESS_FANOUT_LIMIT)
    tasks = [
        asyncio.ensure_future(run_one_case(case, variants, disable_tools, metrics, semaphore))
        for case in cases
    ]
    for coro in asyncio.as_completed(tasks):
        yield await coro


async def run_eval(
    variants: list[Variant],
    cases: list[TestCase],
    disable_tools: bool = False,
    metrics: list[Metric] | None = None,
) -> RunResult:
    """Run every case against every variant and score the comparison, all
    in this one process. Simple/test-caller convenience only — the real API
    path uses eval/queue.py + eval/worker.py's real worker pool instead
    (see iter_case_results()'s docstring).

    Cases run concurrently up to IN_PROCESS_FANOUT_LIMIT at a time (each
    case's own per-variant + judge calls are also concurrent within that
    slot) — this is a set of one-shot HTTP calls, not the pooled/tool-using
    agent, so the cap exists for the OpenCode server's sake, not for
    correctness. A single case's failure is isolated (see _safe_oneshot)
    and shows up as a failed VariantResult, not a broken run.

    disable_tools applies to every variant's response call (the judge
    always has tools disabled regardless — see _judge_once). This module
    deliberately doesn't know which subject produced a variant's prompt
    text (see DESIGN.md's "seam"), so it can't decide this on its own: the
    Loma subject's real system prompt legitimately uses tools, so a caller
    evaluating it should leave this False; the generic subject's pasted
    personas have no legitimate reason to touch a real tool, so a caller
    evaluating that should pass True. See api/prompt_eval_routes.py for
    where that call is actually made. Found the hard way: a generic-subject
    test case asking about "the Enterprise plan" got answered from a live
    web search of an unrelated company's real pricing page, fluently and
    completely disconnected from the persona under test.
    """
    case_results = [r async for r in iter_case_results(variants, cases, disable_tools, metrics)]
    return RunResult(case_results=case_results, summary=aggregate(case_results))
