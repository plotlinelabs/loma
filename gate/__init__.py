"""Pre-send response gate for customer-facing replies (Pylon).

See docs/prevention-plan-agent-response-quality.md (Lever ①). The gate decouples
the *send decision* from the model: the agent proposes a draft + structured
self-assessment, an independent verifier disputes its claims, and a deterministic
rule decides whether to send to the customer (PASS) or downgrade to an internal
note + human escalation (HOLD).

Shadow-first: nothing here sends or holds in production yet. It computes a
decision that callers may log (shadow) before it is wired into the live path.
"""

from gate.schema import Assessment, Evidence, GateResult, Decision, Category
from gate.decision import evaluate, GateConfig, DEFAULT_CONFIG

__all__ = [
    "Assessment",
    "Evidence",
    "GateResult",
    "Decision",
    "Category",
    "evaluate",
    "GateConfig",
    "DEFAULT_CONFIG",
]
