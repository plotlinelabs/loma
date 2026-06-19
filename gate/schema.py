"""Data structures for the pre-send response gate.

These mirror the agent-output contract in the prevention plan (§4.1): the agent
emits a draft reply plus an `Assessment`; the gate turns an `Assessment` into a
`GateResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    """Resolution category for the agent's answer."""

    RESOLVED = "resolved"
    PARTIAL = "partial"
    UNRESOLVED = "unresolved"
    ESCALATION_NEEDED = "escalation_needed"
    UNKNOWN = "unknown"


class ClaimType(str, Enum):
    """What kind of claim the reply makes. Hypotheses/declarations are risky."""

    DATA_VERIFIED = "data_verified"
    DOC_BACKED = "doc_backed"
    ROOT_CAUSE_HYPOTHESIS = "root_cause_hypothesis"
    CAPABILITY_CLAIM = "capability_claim"
    BUG_DECLARATION = "bug_declaration"


# Risk flags that BLOCK an auto customer reply (force HOLD) regardless of score.
# These are the exact failure shapes from the complaint sheet (§2.1).
BLOCKING_RISK_FLAGS: frozenset[str] = frozenset(
    {
        "declares_bug",                  # #2056 "it's a bug on our end" w/o engineering confirmation
        "declares_working_as_intended",  # #1988 #1981 "it's working / no mismatch" prematurely
        "contradicts_customer",          # tells the customer their report is wrong
        "no_evidence",                   # a claim not entailed by any evidence entry (#1730 #1725)
        "fabricated_specifics",          # invented values (#1752 "1.5x body, 1.2x titles")
    }
)


@dataclass
class Evidence:
    """One piece of evidence the agent gathered while answering."""

    kind: str           # clickhouse | mongo | docs | codebase | github
    ref: str            # query / doc id / file path — what was actually checked
    supports_claim: bool  # does this evidence ENTAIL the claim it is cited for?


@dataclass
class Assessment:
    """Structured self-assessment + verifier output for a drafted reply.

    `confidence_score` is None when no calibrated signal exists (the current
    production state). The gate treats None as fail-safe (HOLD) — see decision.py.
    """

    confidence_score: float | None
    category: str = Category.UNKNOWN.value
    claim_types: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    claims_verified: bool = False  # every product/root-cause claim entailed by evidence
    risk_flags: list[str] = field(default_factory=list)
    reasoning: str = ""

    def blocking_flags(self) -> set[str]:
        return {f for f in self.risk_flags if f in BLOCKING_RISK_FLAGS}


class Decision(str, Enum):
    PASS = "PASS"   # allow the customer-facing reply
    HOLD = "HOLD"   # downgrade to internal note + escalate to a human


@dataclass
class GateResult:
    decision: Decision
    reasons: list[str]            # why (empty when PASS)
    action: str                   # "customer_reply" | "internal_note_escalate"

    @property
    def passed(self) -> bool:
        return self.decision is Decision.PASS
