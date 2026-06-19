"""Unit tests for the deterministic pre-send gate (gate/decision.py).

Pure logic, no LLM. Run: `.venv/bin/python tests/test_gate_decision.py`
(self-running; also works under pytest if available).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gate.decision import DEFAULT_CONFIG, GateConfig, evaluate
from gate.schema import Assessment, Category, Decision


def _good(**over):
    """A fully clean assessment that should PASS, with optional overrides."""
    base = dict(
        confidence_score=0.9,
        category=Category.RESOLVED.value,
        claims_verified=True,
        risk_flags=[],
    )
    base.update(over)
    return Assessment(**base)


def test_clean_answer_passes():
    r = evaluate(_good())
    assert r.decision is Decision.PASS, r.reasons
    assert r.action == "customer_reply"
    assert r.reasons == []


def test_partial_category_passes():
    assert evaluate(_good(category=Category.PARTIAL.value)).decision is Decision.PASS


def test_confidence_at_threshold_passes():
    # boundary: exactly the threshold is allowed (>=)
    assert evaluate(_good(confidence_score=0.6)).decision is Decision.PASS


def test_low_confidence_holds():
    r = evaluate(_good(confidence_score=0.59))
    assert r.decision is Decision.HOLD
    assert any("low_confidence" in x for x in r.reasons)


def test_missing_confidence_holds_failsafe():
    r = evaluate(_good(confidence_score=None))
    assert r.decision is Decision.HOLD
    assert "no_confidence_signal" in r.reasons


def test_unverified_claims_hold():
    r = evaluate(_good(claims_verified=False))
    assert r.decision is Decision.HOLD
    assert "claims_unverified" in r.reasons


def test_each_blocking_flag_holds():
    for flag in (
        "declares_bug",
        "declares_working_as_intended",
        "contradicts_customer",
        "no_evidence",
        "fabricated_specifics",
    ):
        r = evaluate(_good(risk_flags=[flag]))
        assert r.decision is Decision.HOLD, flag
        assert f"risk:{flag}" in r.reasons, flag


def test_non_blocking_flag_does_not_hold():
    # an unknown/non-blocking flag alone must not force HOLD
    assert evaluate(_good(risk_flags=["minor_style_nit"])).decision is Decision.PASS


def test_escalation_category_holds():
    r = evaluate(_good(category=Category.ESCALATION_NEEDED.value))
    assert r.decision is Decision.HOLD
    assert any(x.startswith("category:") for x in r.reasons)


def test_multiple_reasons_accumulate():
    r = evaluate(
        Assessment(
            confidence_score=0.2,
            category=Category.UNRESOLVED.value,
            claims_verified=False,
            risk_flags=["declares_bug"],
        )
    )
    assert r.decision is Decision.HOLD
    assert len(r.reasons) >= 3  # risk + unverified + low_conf + category


def test_config_can_relax_missing_confidence():
    cfg = GateConfig(hold_on_missing_confidence=False)
    # with fail-safe off, None confidence no longer blocks on its own
    assert evaluate(_good(confidence_score=None), cfg).decision is Decision.PASS


def test_default_config_threshold():
    assert DEFAULT_CONFIG.min_confidence == 0.6


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
