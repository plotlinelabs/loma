"""Pure scoring core for prompt eval runs.

No I/O, no model calls — mirrors gate/decision.py's shape exactly. The
runner (eval/runner.py) gets response text and (optionally) judge scores
from the outside and hands them here; this module only ever turns
(TestCase, VariantRunOutput) into a VariantResult / CaseResult / RunSummary,
running each configured Metric (see eval/metrics.py) along the way.
"""

from __future__ import annotations

from dataclasses import asdict

from eval.metrics import DEFAULT_METRICS, Metric, MetricInputs
from eval.schema import CaseResult, RunSummary, TestCase, VariantResult, VariantRunOutput, VariantSummary

# Re-exported for import-compat with anything still doing
# `from eval.decision import JUDGE_PASS_THRESHOLD` — the real values now
# live in eval/metrics.py, co-located with LLMJudgeMetric, the only thing
# that uses them.
from eval.metrics import JUDGE_MAX_DISAGREEMENT, JUDGE_PASS_THRESHOLD  # noqa: F401


def score_variant(
    case: TestCase, output: VariantRunOutput, metrics: list[Metric] | None = None,
) -> VariantResult:
    """Turn one variant's raw output for one case into a scored VariantResult.

    An errored variant fails outright — checking substring/rubric
    expectations against an error message is meaningless, and a side that
    never produced a real response can't "trivially pass" a case with no
    other expectations.
    """
    if output.error:
        return VariantResult(
            variant_id=output.variant_id, label=output.label, response=output.response,
            passed=False, failures=[f"error:{output.error}"],
        )

    metrics = metrics if metrics is not None else DEFAULT_METRICS
    inputs = MetricInputs(
        response=output.response, error=None, judge=output.judge,
        tool_calls=output.tool_calls, latency_ms=output.latency_ms, cost_usd=output.cost_usd,
    )
    metric_results: dict[str, dict] = {}
    failures: list[str] = []
    for metric in metrics:
        result = metric.evaluate(case, inputs)
        metric_results[metric.name] = asdict(result)
        if result.passed is False:
            failures.extend(result.failures or [result.detail or metric.name])

    return VariantResult(
        variant_id=output.variant_id, label=output.label, response=output.response,
        passed=not failures, judge=output.judge, failures=failures,
        tool_calls=output.tool_calls, latency_ms=output.latency_ms, cost_usd=output.cost_usd,
        metric_results=metric_results,
    )


def score_case(
    case: TestCase, variant_outputs: list[VariantRunOutput], metrics: list[Metric] | None = None,
) -> CaseResult:
    """Turn one case's per-variant outputs into a CaseResult. Pure — no I/O."""
    return CaseResult(
        case_id=case.case_id,
        input=case.input,
        variant_results=[score_variant(case, output, metrics) for output in variant_outputs],
    )


def aggregate(results: list[CaseResult]) -> RunSummary:
    """Pass rate + average judge score/latency/cost per variant, across
    every case in the run. Variant order is first-seen (not alphabetical),
    so the results table's column order matches however the caller listed
    its variants."""
    total = len(results)
    variant_order: list[str] = []
    seen: set[str] = set()
    for result in results:
        for vr in result.variant_results:
            if vr.variant_id not in seen:
                seen.add(vr.variant_id)
                variant_order.append(vr.variant_id)

    summaries = []
    for variant_id in variant_order:
        vrs = [vr for r in results for vr in r.variant_results if vr.variant_id == variant_id]
        pass_count = sum(1 for vr in vrs if vr.passed)
        scores = [vr.judge.score for vr in vrs if vr.judge is not None and vr.judge.score is not None]
        composite_scores = [
            vr.metric_results["composite_score"]["score"]
            for vr in vrs
            if "composite_score" in vr.metric_results and vr.metric_results["composite_score"]["score"] is not None
        ]
        latencies = [vr.latency_ms for vr in vrs if vr.latency_ms is not None]
        costs = [vr.cost_usd for vr in vrs if vr.cost_usd is not None]
        summaries.append(VariantSummary(
            variant_id=variant_id,
            label=vrs[0].label if vrs else variant_id,
            pass_count=pass_count,
            pass_rate=(pass_count / total) if total else 0.0,
            avg_judge_score=(sum(scores) / len(scores)) if scores else None,
            avg_composite_score=(sum(composite_scores) / len(composite_scores)) if composite_scores else None,
            avg_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
            avg_cost_usd=(sum(costs) / len(costs)) if costs else None,
        ))

    return RunSummary(total=total, variants=summaries)
