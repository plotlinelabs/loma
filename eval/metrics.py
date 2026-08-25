"""Pluggable scoring metrics for the prompt eval engine.

Extracted from eval/decision.py's previously-hardcoded two checks
(substring matching, LLM-judge rubric grading). Purely additive in this
commit — not wired into eval/decision.py::score_case() yet, so this file
changes no external behavior on its own. Milestone 2 wires it in.

A Metric stays synchronous and pure, same discipline as the rest of
eval/decision.py and gate/decision.py: it only ever turns
(TestCase, MetricInputs) into a MetricResult, no I/O. Every field a metric
might need is gathered up front by eval/runner.py (the only I/O layer) into
MetricInputs before a Metric ever runs. That's a real, honest ceiling —
Coherence/Consistency/Safety below would each need their own LLM call to
implement for real, which doesn't fit this shape without eval/runner.py
gaining a callback into the metric layer. Named as future work, not solved
here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from eval.schema import JudgeResult, TestCase, ToolCall

# A case with a rubric only passes if the judge score clears this bar.
# Not configurable per suite/case — see DESIGN.md for why. Matches
# gate/decision.py's DEFAULT_CONFIG.min_confidence for consistency.
JUDGE_PASS_THRESHOLD = 0.6

# Self-consistency: if the judge's own samples span more than this (max -
# min), the "score" is an average of disagreement, not a signal — distrust
# it rather than reporting a number that looks more confident than it is.
# Same fail-safe philosophy as a missing judge signal below.
JUDGE_MAX_DISAGREEMENT = 0.4


@dataclass
class MetricInputs:
    """Everything a Metric might read for one (case, variant) pair, gathered
    by eval/runner.py before eval/decision.py touches anything."""

    response: str
    error: str | None = None
    judge: JudgeResult | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    latency_ms: float | None = None
    cost_usd: float | None = None


@dataclass
class MetricResult:
    """One metric's verdict for one (case, variant) pair.

    passed=None means informational only — it never gates a VariantResult's
    overall pass/fail (see LatencyMetric/CostMetric below). passed=True/False
    means it does.
    """

    metric: str
    passed: bool | None
    score: float | None = None
    detail: str = ""
    failures: list[str] = field(default_factory=list)


class Metric(ABC):
    """Base class every scoring check implements."""

    name: str

    @abstractmethod
    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult: ...


class SubstringMetric(Metric):
    """Deterministic expected_contains / expected_not_contains check.

    Wraps the exact logic eval/decision.py::_deterministic_failures() had —
    unchanged behavior, just relocated."""

    name = "substring"

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        haystack = inputs.response.lower()
        failures = []
        for needle in case.expected_contains:
            if needle.lower() not in haystack:
                failures.append(f"missing:{needle!r}")
        for needle in case.expected_not_contains:
            if needle.lower() in haystack:
                failures.append(f"forbidden:{needle!r}")
        return MetricResult(metric=self.name, passed=not failures, failures=failures)


def _judge_spread(judge: JudgeResult) -> float | None:
    """max - min across the judge's self-consistency samples. None when
    there's fewer than 2 samples — disagreement isn't measurable from one."""
    if len(judge.samples) < 2:
        return None
    return max(judge.samples) - min(judge.samples)


class LLMJudgeMetric(Metric):
    """LLM-as-judge rubric grading, with self-consistency disagreement
    detection. Wraps the exact logic eval/decision.py::_evaluate_response()
    had for the rubric branch — unchanged behavior, just relocated.

    Only applies when the case has a rubric — passed=None (not gating) when
    it doesn't, since there's nothing to grade.
    """

    name = "llm_judge"

    def __init__(self, pass_threshold: float = JUDGE_PASS_THRESHOLD, max_disagreement: float = JUDGE_MAX_DISAGREEMENT):
        self.pass_threshold = pass_threshold
        self.max_disagreement = max_disagreement

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        if not case.rubric:
            # No author-written rubric — eval/runner.py may still have
            # graded this response against a generic default rubric (see
            # _DEFAULT_RUBRIC there) purely so CompositeScoreMetric has a
            # real judge signal on every case, not just rubric'd ones. That
            # score is surfaced here too, informationally — it still never
            # gates pass/fail without a real, case-specific rubric to grade
            # against.
            judge = inputs.judge
            score = judge.score if judge is not None else None
            detail = judge.reasoning if judge is not None else "no rubric on this case"
            return MetricResult(metric=self.name, passed=None, score=score, detail=detail)

        judge = inputs.judge
        if judge is None or judge.score is None:
            return MetricResult(metric=self.name, passed=False, failures=["no_judge_signal"])

        spread = _judge_spread(judge)
        if spread is not None and spread > self.max_disagreement:
            return MetricResult(
                metric=self.name, passed=False, score=judge.score,
                failures=[f"judge_disagreement:{spread:.2f}>{self.max_disagreement:.2f}"],
                detail=judge.reasoning,
            )
        if judge.score < self.pass_threshold:
            return MetricResult(
                metric=self.name, passed=False, score=judge.score,
                failures=[f"low_judge_score:{judge.score:.2f}<{self.pass_threshold:.2f}"],
                detail=judge.reasoning,
            )
        return MetricResult(metric=self.name, passed=True, score=judge.score, detail=judge.reasoning)


# Composite score weighting — quality dominates, latency is a mild
# tie-breaker, not a hard gate. Below COMPOSITE_LATENCY_BUDGET_MS, latency
# contributes full credit; it degrades linearly to zero credit at 2x the
# budget (clamped beyond that). Deliberately generous so an ordinary slow
# response isn't crushed by the latency term, while something taking
# multiples longer than its peers visibly drags the composite down.
COMPOSITE_JUDGE_WEIGHT = 0.8
COMPOSITE_LATENCY_WEIGHT = 0.2
COMPOSITE_LATENCY_BUDGET_MS = 10_000


class CompositeScoreMetric(Metric):
    """A single comparable 0-1 quality score per (case, variant), blending
    the judge's confidence score with response latency. Informational only
    (passed is always None) — SubstringMetric/LLMJudgeMetric still decide
    real pass/fail; this exists because pass/fail alone often can't
    distinguish two variants that trip the exact same lexical check but
    clearly differ in response quality (see NOTES.md). Needs a judge score
    to mean anything, which is why eval/runner.py now grades every case,
    falling back to a generic rubric when the case has none of its own —
    otherwise this metric would only ever have a signal on the minority of
    cases that happened to have an author-written rubric."""

    name = "composite_score"

    def __init__(
        self,
        judge_weight: float = COMPOSITE_JUDGE_WEIGHT,
        latency_weight: float = COMPOSITE_LATENCY_WEIGHT,
        latency_budget_ms: float = COMPOSITE_LATENCY_BUDGET_MS,
    ):
        self.judge_weight = judge_weight
        self.latency_weight = latency_weight
        self.latency_budget_ms = latency_budget_ms

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        judge_score = inputs.judge.score if inputs.judge is not None else None
        if judge_score is None:
            return MetricResult(metric=self.name, passed=None, detail="no judge score available")

        if inputs.latency_ms is None:
            return MetricResult(
                metric=self.name, passed=None, score=judge_score,
                detail="judge only (latency unavailable)",
            )

        latency_score = max(0.0, min(1.0, 2 - inputs.latency_ms / self.latency_budget_ms))
        composite = self.judge_weight * judge_score + self.latency_weight * latency_score
        return MetricResult(
            metric=self.name, passed=None, score=composite,
            detail=f"judge={judge_score:.2f} latency={latency_score:.2f} ({inputs.latency_ms:.0f}ms)",
        )


class LatencyMetric(Metric):
    """Wall-clock time for the oneshot call. Informational only — no suite-
    level latency budget exists to gate against yet, so passed is always
    None. Real once eval/runner.py starts timing calls (Milestone 2)."""

    name = "latency"

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        if inputs.latency_ms is None:
            return MetricResult(metric=self.name, passed=None, detail="unavailable")
        return MetricResult(metric=self.name, passed=None, score=inputs.latency_ms, detail=f"{inputs.latency_ms:.0f}ms")


class CostMetric(Metric):
    """Token/cost estimate for the oneshot call, if OpenCode's response
    actually populates it. Informational only, same reasoning as
    LatencyMetric. NOT YET VERIFIED live whether info.cost is populated for
    a oneshot (non-streaming) call — _usage_from_info() has only been proven
    against the streaming run_opencode_agent path so far. Verify before
    trusting these numbers; detail="unavailable" already degrades gracefully
    either way."""

    name = "cost"

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        if inputs.cost_usd is None:
            return MetricResult(metric=self.name, passed=None, detail="unavailable")
        return MetricResult(metric=self.name, passed=None, score=inputs.cost_usd, detail=f"${inputs.cost_usd:.4f}")


class CoherenceMetric(Metric):
    """Not implemented. Would need its own LLM call (is the response
    logically structured and well-organized?) — a real metric, not a
    trivial addition, and out of scope for this pass. See NOTES.md."""

    name = "coherence"

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        raise NotImplementedError("CoherenceMetric is not implemented — see NOTES.md")


class ConsistencyMetric(Metric):
    """Not implemented. Would need multiple independent runs of the SAME
    variant against the SAME case to compare against each other (does this
    prompt produce similar outputs on similar inputs?) — a different shape
    of work than self-consistency judging, and out of scope. See NOTES.md."""

    name = "consistency"

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        raise NotImplementedError("ConsistencyMetric is not implemented — see NOTES.md")


class SafetyMetric(Metric):
    """Not implemented. Would need either a moderation API or another
    LLM-judge pass with its own rubric (is the response free of bias,
    toxicity, or other harmful content?) — real design work, out of scope.
    See NOTES.md."""

    name = "safety"

    def evaluate(self, case: TestCase, inputs: MetricInputs) -> MetricResult:
        raise NotImplementedError("SafetyMetric is not implemented — see NOTES.md")


DEFAULT_METRICS: list[Metric] = [SubstringMetric(), LLMJudgeMetric(), CompositeScoreMetric()]
