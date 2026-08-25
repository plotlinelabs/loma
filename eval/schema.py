"""Data structures for the prompt evaluation engine.

Mirrors gate/schema.py's shape: plain dataclasses, no I/O. eval/decision.py
turns these into a verdict; eval/runner.py is the only place that does I/O
(model calls) to produce the response/judge fields these carry.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TestCase:
    """One input to run against both the current and draft prompt."""

    case_id: str
    input: str
    expected_contains: list[str] = field(default_factory=list)
    expected_not_contains: list[str] = field(default_factory=list)
    # Optional free-text rubric. When set, the case only passes if a judge
    # score is also present and clears eval.decision.JUDGE_PASS_THRESHOLD.
    rubric: str = ""


@dataclass
class JudgeResult:
    """LLM-as-judge output for one (case, response) pair.

    Self-consistency: the judge is sampled multiple times independently
    (see eval/runner.py's JUDGE_SELF_CONSISTENCY_SAMPLES) rather than
    trusted on a single call. `score` is the median of `samples` — median,
    not mean, so one erratic sample can't drag a stable consensus around.
    `samples` is kept (not just the aggregate) so eval/decision.py can
    detect disagreement between samples, and so a reviewer can see the
    raw spread instead of a single number that looks more confident than
    it is.
    """

    score: float | None  # median of `samples`, or None if no rubric / every sample failed
    reasoning: str = ""
    samples: list[float] = field(default_factory=list)


@dataclass
class ToolCall:
    """One tool invocation the model made while producing a response.

    Captured, not yet scored — there's no `expected_tool` on TestCase and
    no pass/fail logic in eval/decision.py keyed off this. This exists to
    make the tool-permission risk documented in DESIGN.md *visible* per run
    (a Loma-subject test case's input can provoke a real tool call — now you
    can see it happened, and what it did, instead of only knowing it could).
    A tool-correctness metric is the natural next step, not this one.
    """

    tool: str
    input: dict
    output: str
    status: str


@dataclass
class Variant:
    """One arm of an N-way comparison. Replaces the old hardcoded
    current/draft pair — "current vs draft" is now just the 2-variant case
    of this general model, not a separate code path. Not persisted
    independently of a run; it's a per-run input, not part of a suite (a
    suite is just cases).

    agent_profile selects a pre-registered OpenCode agent config (see
    agent.opencode_runtime.AGENT_PROFILES) — the only way to vary
    temperature, since OpenCode has no per-request temperature parameter.
    """

    variant_id: str
    label: str
    prompt_text: str
    model: str
    agent_profile: str = "default"


@dataclass
class VariantRunOutput:
    """One variant's raw I/O result for one case — what eval/runner.py's
    oneshot call, judge call, and timing gathered, handed to
    eval/decision.py to score. No I/O of its own."""

    variant_id: str
    label: str
    response: str
    error: str | None = None
    judge: JudgeResult | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    latency_ms: float | None = None
    cost_usd: float | None = None


@dataclass
class VariantResult:
    """One variant's scored outcome for one case."""

    variant_id: str
    label: str
    response: str
    passed: bool
    judge: JudgeResult | None = None
    failures: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    latency_ms: float | None = None
    cost_usd: float | None = None
    # Keyed by Metric.name (see eval/metrics.py) — every metric's own
    # verdict for this (case, variant) pair, not just the aggregate pass/fail.
    metric_results: dict[str, dict] = field(default_factory=dict)


@dataclass
class CaseResult:
    """Outcome for one TestCase, across every variant being compared."""

    case_id: str
    input: str
    variant_results: list[VariantResult]


@dataclass
class VariantSummary:
    """Aggregate stats for one variant, across every case in a run."""

    variant_id: str
    label: str
    pass_count: int
    pass_rate: float
    avg_judge_score: float | None
    avg_composite_score: float | None
    avg_latency_ms: float | None
    avg_cost_usd: float | None


@dataclass
class RunSummary:
    """Aggregate stats across all cases in a run, per variant."""

    total: int
    variants: list[VariantSummary]


@dataclass
class RunResult:
    case_results: list[CaseResult]
    summary: RunSummary
