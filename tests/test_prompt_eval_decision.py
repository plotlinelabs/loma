"""Unit tests for the pure prompt-eval scoring core (eval/decision.py).

Pure logic, no LLM. Run: `.venv/bin/python tests/test_prompt_eval_decision.py`
(self-running; also works under pytest if available).

eval/decision.py orchestrates the Metric pipeline (see eval/metrics.py and
tests/test_prompt_eval_metrics.py for individual metric behavior — substring
matching, judge threshold/disagreement, latency/cost) across an arbitrary
number of variants. These tests focus on that orchestration: error handling,
tool-call passthrough, multi-metric failure accumulation, and N-way
aggregation — not re-testing what a single metric does in isolation.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.decision import aggregate, score_case, score_variant
from eval.schema import JudgeResult, TestCase, ToolCall, VariantRunOutput


def _case(**over):
    base = dict(
        case_id="c1", input="hello",
        expected_contains=[], expected_not_contains=[], rubric="",
    )
    base.update(over)
    return TestCase(**base)


def _output(variant_id="a", label="A", response="resp", **over):
    base = dict(variant_id=variant_id, label=label, response=response)
    base.update(over)
    return VariantRunOutput(**base)


# ------------------------------------------------------------- score_variant --- #

def test_no_expectations_always_passes():
    r = score_variant(_case(), _output(response="anything"))
    assert r.passed
    assert r.failures == []


def test_errored_variant_fails_outright_even_with_no_expectations():
    # A case with zero checks would otherwise trivially "pass" anything —
    # an error must never be let through by having nothing to fail against.
    r = score_variant(_case(), _output(response="irrelevant", error="Invalid API key"))
    assert not r.passed
    assert "error:Invalid API key" in r.failures


def test_errored_variant_skips_metric_checks_entirely():
    case = _case(expected_contains=["refund"], rubric="Is it polite?")
    r = score_variant(case, _output(response="no relevant word here", error="boom"))
    assert r.failures == ["error:boom"]  # not also missing:'refund' / no_judge_signal


def test_tool_calls_pass_through_without_affecting_the_verdict():
    # captured, not scored (see eval/schema.py's ToolCall docstring) — a
    # case with no expectations at all still passes regardless of what
    # tools were used to produce the response.
    calls = [ToolCall(tool="bash", input={"command": "ls"}, output="a.py\nb.py\n", status="completed")]
    r = score_variant(_case(), _output(response="3", tool_calls=calls))
    assert r.passed
    assert r.tool_calls == calls


def test_tool_calls_default_to_empty_list():
    r = score_variant(_case(), _output())
    assert r.tool_calls == []


def test_multiple_metric_failures_accumulate():
    case = _case(expected_contains=["hi"], rubric="Is it friendly?")
    r = score_variant(case, _output(response="bye"))
    assert not r.passed
    assert len(r.failures) == 2  # substring miss + no_judge_signal


def test_metric_results_recorded_per_metric_name():
    case = _case(expected_contains=["hi"])
    r = score_variant(case, _output(response="hi there"))
    assert "substring" in r.metric_results
    assert r.metric_results["substring"]["passed"] is True


def test_custom_metric_list_is_respected():
    # A caller-supplied metrics list overrides DEFAULT_METRICS entirely —
    # proves score_variant doesn't hardcode which metrics run.
    case = _case(expected_contains=["hi"])
    r = score_variant(case, _output(response="bye"), metrics=[])
    assert r.passed  # no metrics ran at all, nothing to fail on
    assert r.metric_results == {}


# ----------------------------------------------------------------- score_case --- #

def test_score_case_scores_every_variant_independently():
    case = _case(expected_contains=["refund"])
    outputs = [
        _output(variant_id="a", label="A", response="We offer a refund."),
        _output(variant_id="b", label="B", response="No mention of that here."),
    ]
    result = score_case(case, outputs)
    assert len(result.variant_results) == 2
    by_id = {vr.variant_id: vr for vr in result.variant_results}
    assert by_id["a"].passed
    assert not by_id["b"].passed


def test_score_case_handles_three_or_more_variants():
    # N isn't secretly hardcoded to 2 anywhere in this path.
    case = _case(rubric="Is it friendly?")
    judge_good = JudgeResult(score=0.9, reasoning="great", samples=[0.9, 0.9])
    judge_bad = JudgeResult(score=0.1, reasoning="terse", samples=[0.1, 0.1])
    outputs = [
        _output(variant_id="a", label="A", response="hi", judge=judge_good),
        _output(variant_id="b", label="B", response="hi", judge=judge_bad),
        _output(variant_id="c", label="C", response="", error="timeout"),
        _output(variant_id="d", label="D", response="hi", judge=judge_good),
    ]
    result = score_case(case, outputs)
    assert len(result.variant_results) == 4
    by_id = {vr.variant_id: vr for vr in result.variant_results}
    assert by_id["a"].passed
    assert not by_id["b"].passed
    assert not by_id["c"].passed and by_id["c"].failures == ["error:timeout"]
    assert by_id["d"].passed


# ------------------------------------------------------------------- aggregate --- #

def test_aggregate_empty_results():
    summary = aggregate([])
    assert summary.total == 0
    assert summary.variants == []


def test_aggregate_mixed_pass_rates_two_variants():
    results = [
        score_case(_case(case_id="a"), [_output("x", "X", "yes"), _output("y", "Y", "yes")]),
        score_case(
            _case(case_id="b", expected_contains=["refund"]),
            [_output("x", "X", "no mention of it"), _output("y", "Y", "we offer a refund")],
        ),
    ]
    summary = aggregate(results)
    assert summary.total == 2
    by_id = {vs.variant_id: vs for vs in summary.variants}
    assert by_id["x"].pass_count == 1 and by_id["x"].pass_rate == 0.5
    assert by_id["y"].pass_count == 2 and by_id["y"].pass_rate == 1.0


def test_aggregate_preserves_first_seen_variant_order():
    results = [
        score_case(_case(case_id="a"), [_output("z", "Z", "hi"), _output("a", "A", "hi"), _output("m", "M", "hi")]),
    ]
    summary = aggregate(results)
    assert [vs.variant_id for vs in summary.variants] == ["z", "a", "m"]


def test_aggregate_with_four_variants_not_hardcoded_to_two():
    ids = ["v1", "v2", "v3", "v4"]
    results = [
        score_case(_case(case_id="a"), [_output(i, i, "hi") for i in ids]),
        score_case(_case(case_id="b"), [_output(i, i, "hi") for i in ids]),
    ]
    summary = aggregate(results)
    assert len(summary.variants) == 4
    assert all(vs.pass_rate == 1.0 for vs in summary.variants)


def test_aggregate_averages_judge_scores_only_when_present():
    j1 = JudgeResult(score=0.8)
    j2 = JudgeResult(score=0.4)
    results = [
        score_case(_case(case_id="a", rubric="r"), [_output("x", "X", "hi", judge=j1)]),
        score_case(_case(case_id="b"), [_output("x", "X", "hi")]),  # no rubric -> no judge score
        score_case(_case(case_id="c", rubric="r"), [_output("x", "X", "hi", judge=j2)]),
    ]
    summary = aggregate(results)
    assert summary.variants[0].avg_judge_score == (0.8 + 0.4) / 2


def test_aggregate_averages_composite_score_across_all_cases_not_just_rubricd_ones():
    # CompositeScoreMetric only needs a judge score (not a rubric) — every
    # case with one contributes, which is the whole point: unlike
    # avg_judge_score (only rubric'd cases), this shouldn't silently be an
    # average of one lucky case.
    j1 = JudgeResult(score=0.9)
    j2 = JudgeResult(score=0.5)
    results = [
        score_case(_case(case_id="a", rubric="r"), [_output("x", "X", "hi", judge=j1, latency_ms=1000.0)]),
        score_case(_case(case_id="b"), [_output("x", "X", "hi", judge=j2, latency_ms=1000.0)]),  # no rubric, still judged
    ]
    summary = aggregate(results)
    assert summary.variants[0].avg_composite_score is not None
    # Both cases contributed — not just the rubric'd one.
    scores = [
        r.variant_results[0].metric_results["composite_score"]["score"] for r in results
    ]
    assert summary.variants[0].avg_composite_score == sum(scores) / len(scores)


def test_aggregate_averages_latency_and_cost_when_present():
    results = [
        score_case(_case(case_id="a"), [_output("x", "X", "hi", latency_ms=100.0, cost_usd=0.01)]),
        score_case(_case(case_id="b"), [_output("x", "X", "hi", latency_ms=200.0, cost_usd=0.02)]),
    ]
    summary = aggregate(results)
    assert summary.variants[0].avg_latency_ms == 150.0
    assert summary.variants[0].avg_cost_usd == 0.015


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
