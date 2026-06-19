"""Deterministic pre-send gate.

Pure function: `Assessment` -> `GateResult`. No I/O, no model calls — fully
unit-testable. This is the safety-critical core: it must be auditable and
behave identically every run, which is precisely why the send decision is taken
OUT of the model (the model only proposes the draft + assessment).

Policy (prevention plan §4.1), all conditions must hold to PASS:
  1. no blocking risk flag                 (declares_bug / declares_working / …)
  2. claims_verified is True               (every claim entailed by evidence)
  3. a confidence signal exists AND ≥ threshold
  4. category ∈ allowed (resolved | partial)

Fail-safe: a MISSING confidence signal (None) is treated as HOLD. The current
production path emits no confidence at all, so under this gate everything holds
until the signal is generated — autonomy is then earned back as calibration proves
out (rollout §5), which is the intended "empower-by-gating" trajectory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gate.schema import (
    Assessment,
    BLOCKING_RISK_FLAGS,
    Category,
    Decision,
    GateResult,
)


@dataclass(frozen=True)
class GateConfig:
    min_confidence: float = 0.6
    allowed_categories: frozenset[str] = frozenset(
        {Category.RESOLVED.value, Category.PARTIAL.value}
    )
    blocking_flags: frozenset[str] = BLOCKING_RISK_FLAGS
    require_claims_verified: bool = True
    # Fail-safe: when True, a None confidence_score forces HOLD.
    hold_on_missing_confidence: bool = True


DEFAULT_CONFIG = GateConfig()


def evaluate(assessment: Assessment, config: GateConfig = DEFAULT_CONFIG) -> GateResult:
    """Decide whether a drafted customer reply may be sent (PASS) or must be
    downgraded to an internal note + human escalation (HOLD)."""
    reasons: list[str] = []

    # 1. Blocking risk flags
    blocking = sorted(set(assessment.risk_flags) & set(config.blocking_flags))
    reasons.extend(f"risk:{flag}" for flag in blocking)

    # 2. Claims must be verified (semantic: entailed by evidence)
    if config.require_claims_verified and not assessment.claims_verified:
        reasons.append("claims_unverified")

    # 3. Confidence signal present and sufficient
    score = assessment.confidence_score
    if score is None:
        if config.hold_on_missing_confidence:
            reasons.append("no_confidence_signal")
    elif score < config.min_confidence:
        reasons.append(f"low_confidence:{score:.2f}<{config.min_confidence:.2f}")

    # 4. Category must be an answerable one
    if assessment.category not in config.allowed_categories:
        reasons.append(f"category:{assessment.category}")

    if reasons:
        return GateResult(
            decision=Decision.HOLD,
            reasons=reasons,
            action="internal_note_escalate",
        )
    return GateResult(decision=Decision.PASS, reasons=[], action="customer_reply")
