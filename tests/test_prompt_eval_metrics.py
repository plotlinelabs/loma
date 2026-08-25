"""Unit tests for eval/metrics.py — the pluggable Metric abstraction.

Pure logic, no LLM. Run: `.venv/bin/python tests/test_prompt_eval_metrics.py`
(self-running; also works under pytest if available).

These port the rubric/threshold/disagreement/substring cases from
tests/test_prompt_eval_decision.py to exercise the Metric classes directly
via MetricInputs, now that that logic lives in eval/metrics.py instead of
being inlined in eval/decision.py.
"""

import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.metrics import (
    JUDGE_MAX_DISAGREEMENT,
    JUDGE_PASS_THRESHOLD,
    CoherenceMetric,
    CompositeScoreMetric,
    ConsistencyMetric,
    CostMetric,
    LatencyMetric,
    LLMJudgeMetric,
    MetricInputs,
    SafetyMetric,
    SubstringMetric,
)
from eval.schema import JudgeResult, TestCase


def _case(**over):
    base = dict(
        case_id="c1", input="hello",
        expected_contains=[], expected_not_contains=[], rubric="",
    )
    base.update(over)
    return TestCase(**base)


# --------------------------------------------------------------- substring --- #

def test_substring_no_expectations_always_passes():
    r = SubstringMetric().evaluate(_case(), MetricInputs(response="anything"))
    assert r.passed is True
    assert r.failures == []


def test_substring_expected_contains_is_case_insensitive():
    case = _case(expected_contains=["refund"])
    r = SubstringMetric().evaluate(case, MetricInputs(response="We offer a Refund policy."))
    assert r.passed is True


def test_substring_missing_expected_contains_fails():
    case = _case(expected_contains=["refund"])
    r = SubstringMetric().evaluate(case, MetricInputs(response="No mention of that here."))
    assert r.passed is False
    assert "missing:'refund'" in r.failures


def test_substring_expected_not_contains_fails_when_present():
    case = _case(expected_not_contains=["guarantee"])
    r = SubstringMetric().evaluate(case, MetricInputs(response="We do not guarantee results."))
    assert r.passed is False
    assert "forbidden:'guarantee'" in r.failures


def test_substring_both_kinds_of_failure_accumulate():
    case = _case(expected_contains=["hi"], expected_not_contains=["bye"])
    r = SubstringMetric().evaluate(case, MetricInputs(response="bye"))
    assert r.passed is False
    assert len(r.failures) == 2


# --------------------------------------------------------------- llm judge --- #

def test_judge_no_rubric_is_informational_only():
    r = LLMJudgeMetric().evaluate(_case(), MetricInputs(response="resp"))
    assert r.passed is None


def test_judge_no_rubric_still_surfaces_a_generic_judge_score():
    # eval/runner.py grades every case now, falling back to a generic
    # rubric when the case has none of its own — the score should still
    # show up here informationally, just never gate pass/fail.
    judge = JudgeResult(score=0.75, reasoning="generally helpful", samples=[0.75])
    r = LLMJudgeMetric().evaluate(_case(), MetricInputs(response="resp", judge=judge))
    assert r.passed is None
    assert r.score == 0.75
    assert r.detail == "generally helpful"


def test_judge_missing_result_fails_failsafe():
    case = _case(rubric="Is the tone friendly?")
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=None))
    assert r.passed is False
    assert "no_judge_signal" in r.failures


def test_judge_none_score_fails_failsafe():
    case = _case(rubric="Is the tone friendly?")
    judge = JudgeResult(score=None, reasoning="every sample failed")
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=judge))
    assert r.passed is False
    assert "no_judge_signal" in r.failures


def test_judge_score_at_threshold_passes():
    case = _case(rubric="Is the tone friendly?")
    judge = JudgeResult(score=JUDGE_PASS_THRESHOLD, reasoning="borderline but ok")
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=judge))
    assert r.passed is True
    assert r.score == JUDGE_PASS_THRESHOLD


def test_judge_low_score_fails():
    case = _case(rubric="Is the tone friendly?")
    judge = JudgeResult(score=0.1, reasoning="too terse")
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=judge))
    assert r.passed is False
    assert any(f.startswith("low_judge_score") for f in r.failures)


def test_judge_agreeing_samples_pass():
    case = _case(rubric="Is it friendly?")
    judge = JudgeResult(score=0.85, reasoning="agrees", samples=[0.8, 0.85, 0.9])
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=judge))
    assert r.passed is True


def test_judge_disagreeing_samples_fail_even_with_passing_median():
    # median is 0.7 (passes on its own) but samples span 0.9 - 0.1 = 0.8,
    # far past JUDGE_MAX_DISAGREEMENT — a confidently averaged number hiding
    # real disagreement must not pass silently.
    case = _case(rubric="Is it friendly?")
    judge = JudgeResult(score=0.7, reasoning="disagrees", samples=[0.1, 0.7, 0.9])
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=judge))
    assert r.passed is False
    assert any(f.startswith("judge_disagreement") for f in r.failures)


def test_judge_disagreement_exactly_at_threshold_passes():
    # boundary: spread exactly at JUDGE_MAX_DISAGREEMENT is allowed (only
    # strictly greater distrusts the score).
    case = _case(rubric="Is it friendly?")
    spread_samples = [0.6, 0.6 + JUDGE_MAX_DISAGREEMENT]
    judge = JudgeResult(score=statistics.median(spread_samples), reasoning="ok", samples=spread_samples)
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=judge))
    assert r.passed is True


def test_judge_single_sample_has_no_measurable_disagreement():
    case = _case(rubric="Is it friendly?")
    judge = JudgeResult(score=0.9, reasoning="ok", samples=[0.9])
    r = LLMJudgeMetric().evaluate(case, MetricInputs(response="resp", judge=judge))
    assert r.passed is True
    assert r.failures == []


# ---------------------------------------------------------- latency / cost --- #

def test_latency_metric_is_always_informational():
    r = LatencyMetric().evaluate(_case(), MetricInputs(response="resp", latency_ms=123.4))
    assert r.passed is None
    assert r.score == 123.4


def test_latency_metric_handles_missing_value():
    r = LatencyMetric().evaluate(_case(), MetricInputs(response="resp", latency_ms=None))
    assert r.passed is None
    assert r.detail == "unavailable"


def test_cost_metric_is_always_informational():
    r = CostMetric().evaluate(_case(), MetricInputs(response="resp", cost_usd=0.002))
    assert r.passed is None
    assert r.score == 0.002


def test_cost_metric_handles_missing_value():
    r = CostMetric().evaluate(_case(), MetricInputs(response="resp", cost_usd=None))
    assert r.passed is None
    assert r.detail == "unavailable"


# ------------------------------------------------------------ composite --- #

def test_composite_score_needs_a_judge_score():
    r = CompositeScoreMetric().evaluate(_case(), MetricInputs(response="resp", judge=None))
    assert r.passed is None
    assert r.score is None
    assert r.detail == "no judge score available"


def test_composite_score_falls_back_to_judge_only_without_latency():
    judge = JudgeResult(score=0.9, reasoning="great", samples=[0.9])
    r = CompositeScoreMetric().evaluate(_case(), MetricInputs(response="resp", judge=judge, latency_ms=None))
    assert r.passed is None
    assert r.score == 0.9


def test_composite_score_blends_judge_and_latency_within_budget():
    # At/under the latency budget, latency contributes full credit — so a
    # fast response with a perfect judge score should land near 1.0, not
    # get needlessly docked.
    judge = JudgeResult(score=1.0, reasoning="perfect", samples=[1.0])
    metric = CompositeScoreMetric(judge_weight=0.8, latency_weight=0.2, latency_budget_ms=10_000)
    r = metric.evaluate(_case(), MetricInputs(response="resp", judge=judge, latency_ms=5_000))
    assert r.passed is None
    assert r.score == 1.0  # 0.8*1.0 + 0.2*1.0 (latency clamped to full credit under budget)


def test_composite_score_degrades_for_slow_responses():
    # Same judge score, but well past the latency budget — the composite
    # should be measurably lower than the fast case above, without being
    # crushed to zero by latency alone (quality still dominates the blend).
    judge = JudgeResult(score=1.0, reasoning="perfect", samples=[1.0])
    metric = CompositeScoreMetric(judge_weight=0.8, latency_weight=0.2, latency_budget_ms=10_000)
    r = metric.evaluate(_case(), MetricInputs(response="resp", judge=judge, latency_ms=20_000))
    assert r.passed is None
    assert r.score == 0.8  # 0.8*1.0 + 0.2*0.0 (latency clamped to zero credit at 2x budget)


def test_composite_score_two_variants_with_same_pass_fail_can_still_differ():
    # The exact scenario that prompted this metric: two variants that trip
    # the same substring miss (so pass/fail alone can't distinguish them)
    # but clearly differ in judged quality and speed.
    metric = CompositeScoreMetric(latency_budget_ms=10_000)
    strong = metric.evaluate(
        _case(), MetricInputs(response="resp", judge=JudgeResult(score=0.95, reasoning="thorough"), latency_ms=4_000),
    )
    weak = metric.evaluate(
        _case(), MetricInputs(response="resp", judge=JudgeResult(score=0.4, reasoning="thin"), latency_ms=18_000),
    )
    assert strong.score > weak.score


# ----------------------------------------------------------------- stubs --- #

def test_coherence_metric_is_not_implemented():
    try:
        CoherenceMetric().evaluate(_case(), MetricInputs(response="resp"))
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


def test_consistency_metric_is_not_implemented():
    try:
        ConsistencyMetric().evaluate(_case(), MetricInputs(response="resp"))
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


def test_safety_metric_is_not_implemented():
    try:
        SafetyMetric().evaluate(_case(), MetricInputs(response="resp"))
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    fns = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed}/{len(fns)} tests passed")
    sys.exit(0 if passed == len(fns) else 1)
