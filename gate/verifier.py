"""Independent verifier — produces the `Assessment` a drafted reply is gated on.

Implementations:

* `LLMVerifier` (base) — a cheap, *adversarial* model pass that sees the customer's
  question, the drafted reply, and a summary of the evidence the agent gathered, then
  independently scores confidence and raises risk flags. It defaults to skepticism: a
  claim not entailed by the cited evidence is flagged `no_evidence`. This is the
  component that catches "verified-but-wrong" (the agent ran 6 ClickHouse queries yet
  its conclusion wasn't entailed — §2.2 row 6). Provider subclasses:
    * `ClaudeCLIVerifier` — **production**; Claude via the OAuth `claude` CLI (in-house, no egress).
    * `AnthropicVerifier` — Anthropic SDK (API-key envs only; prod is OAuth).
    * `OpenAIVerifier` — test backend, used only where an OpenAI key is available.

* `HeuristicVerifier` — no-LLM fallback. Pattern-matches the explicit risk
  language ("it's a bug", "working as expected", "no mismatch", fabricated
  specifics, speculation) and checks evidence presence. Weaker (it cannot judge
  semantic entailment) but lets the gate run end-to-end offline and serves as a
  cheap pre-filter in production.

The agent must NOT grade its own homework: the verifier is a separate pass over
the draft, not the same generation.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

from gate.schema import Assessment, Category


class Verifier(Protocol):
    def assess(
        self,
        *,
        ticket_context: str,
        draft_reply: str,
        evidence_kinds: list[str],
        evidence_digest: str = "",
    ) -> Assessment: ...


# --------------------------------------------------------------------------- #
# Heuristic fallback (no LLM)                                                  #
# --------------------------------------------------------------------------- #

_BUG_RX = re.compile(
    r"\bit'?s a bug\b|bug on our end|this is a (?:known )?bug|a bug in (?:our|the)",
    re.I,
)
_WORK_RX = re.compile(
    r"working (?:as expected|correctly|fine|as intended)|no mismatch|"
    r"no discrepancy|behaving correctly|is correct|expected behaviou?r|"
    r"there'?s no (?:mismatch|issue|problem)",
    re.I,
)
_SPECULATION_RX = re.compile(
    r"\b(likely|probably|might be|could be|i (?:believe|think|suspect)|"
    r"seems to|appears to|should be|my (?:guess|hypothesis))\b",
    re.I,
)
# a precise numeric/ratio value (e.g. "1.5x", "1.2x", "16px") — risky if no evidence
_SPECIFIC_VALUE_RX = re.compile(r"\b\d+(?:\.\d+)?\s?(?:x|px|em|rem|%)\b", re.I)


class HeuristicVerifier:
    """Rule-based verifier. Conservative: anything ambiguous trends toward HOLD."""

    def assess(
        self,
        *,
        ticket_context: str,
        draft_reply: str,
        evidence_kinds: list[str],
        evidence_digest: str = "",
    ) -> Assessment:
        text = draft_reply or ""
        if not text.strip():
            return _empty_draft_assessment()
        flags: list[str] = []

        if _BUG_RX.search(text):
            flags.append("declares_bug")
        if _WORK_RX.search(text):
            flags.append("declares_working_as_intended")
        has_evidence = bool(evidence_kinds)
        speculates = bool(_SPECULATION_RX.search(text))
        if _SPECIFIC_VALUE_RX.search(text) and not has_evidence:
            flags.append("fabricated_specifics")
        # "no_evidence": makes a substantive claim but gathered nothing, or hedges
        if not has_evidence or speculates:
            flags.append("no_evidence")

        # Heuristic verification: evidence present AND not hedging.
        claims_verified = has_evidence and not speculates and "no_evidence" not in flags

        # No calibrated score available heuristically -> None (gate fail-safe HOLD).
        # We still emit a coarse category so the result is inspectable.
        if flags:
            category = Category.ESCALATION_NEEDED.value
        elif claims_verified:
            category = Category.PARTIAL.value
        else:
            category = Category.UNKNOWN.value

        return Assessment(
            confidence_score=None,
            category=category,
            claim_types=[],
            evidence=[],
            claims_verified=claims_verified,
            risk_flags=sorted(set(flags)),
            reasoning="heuristic verifier (no LLM): pattern + evidence-presence only",
        )


# --------------------------------------------------------------------------- #
# LLM verifier (production)                                                    #
# --------------------------------------------------------------------------- #

_VERIFIER_PROMPT = """You are an adversarial reviewer of a SUPPORT REPLY drafted by an AI agent for a customer.
Your job is to decide whether this reply is safe to auto-send. Default to skepticism.

CUSTOMER CONTEXT:
{ticket_context}

EVIDENCE THE AGENT ACTUALLY GATHERED (tool kinds): {evidence_kinds}
WHAT IT QUERIED / CHECKED (digest):
{evidence_digest}

DRAFTED REPLY:
{draft_reply}

Assess strictly and return ONLY JSON (no prose, no fences):
{{
  "confidence_score": 0.0-1.0,        // your calibrated confidence the reply is CORRECT and complete
  "category": "resolved|partial|unresolved|escalation_needed",
  "claims_verified": true|false,      // is EVERY product/root-cause claim ENTAILED by the gathered evidence?
  "risk_flags": [                     // include any that apply
     "declares_bug",                  // asserts a company bug without engineering confirmation
     "declares_working_as_intended",  // asserts it works / "no mismatch" without proof
     "contradicts_customer",          // tells the customer their report is wrong
     "no_evidence",                   // a claim not supported by the gathered evidence
     "fabricated_specifics"           // invented concrete values/configs
  ],
  "reasoning": "one sentence"
}}
Rules: if the reply states a root cause, capability, or bug but the evidence does not ENTAIL it, set claims_verified=false and add "no_evidence". Running a query is NOT the same as the query supporting the claim."""


def _build_prompt(ticket_context, draft_reply, evidence_kinds, evidence_digest) -> str:
    return _VERIFIER_PROMPT.format(
        ticket_context=(ticket_context or "")[:4000],
        draft_reply=(draft_reply or "")[:4000],
        evidence_kinds=", ".join(evidence_kinds) or "none",
        evidence_digest=(evidence_digest or "(none)")[:3000],
    )


def _empty_draft_assessment() -> Assessment:
    """Neutral assessment when there is no reply to judge — never confabulate
    risk flags on an empty draft (that caused the early shadow artifacts)."""
    return Assessment(
        confidence_score=None,
        category=Category.UNKNOWN.value,
        claims_verified=False,
        risk_flags=[],
        reasoning="no customer reply to assess",
    )


def _parse_assessment(raw: str) -> Assessment:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    data = json.loads(raw)
    return Assessment(
        confidence_score=data.get("confidence_score"),
        category=data.get("category", Category.UNKNOWN.value),
        claim_types=data.get("claim_types", []),
        evidence=[],
        claims_verified=bool(data.get("claims_verified", False)),
        risk_flags=list(data.get("risk_flags", [])),
        reasoning=data.get("reasoning", ""),
    )


class LLMVerifier:
    """Base for adversarial LLM verifiers. Shares the prompt + JSON parsing; a
    subclass only implements `_complete(prompt) -> raw_json_str` for its provider.

    `isinstance(v, LLMVerifier)` distinguishes the calibrated (confidence-emitting)
    verifiers from the no-LLM `HeuristicVerifier` — used by the gate's fail-safe.
    """

    def assess(
        self,
        *,
        ticket_context: str,
        draft_reply: str,
        evidence_kinds: list[str],
        evidence_digest: str = "",
    ) -> Assessment:
        if not (draft_reply or "").strip():
            return _empty_draft_assessment()
        prompt = _build_prompt(ticket_context, draft_reply, evidence_kinds, evidence_digest)
        return _parse_assessment(self._complete(prompt))

    def _complete(self, prompt: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class ClaudeCLIVerifier(LLMVerifier):
    """Production verifier — Claude via the `claude` CLI subprocess.

    Uses the same OAuth-backed pattern as scheduler/memory.py (`claude -p
    --output-format json`), so it works with the Max/OAuth login the app already
    uses — no API key, and ticket content stays in-house (no external egress).
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001", timeout: float = 40.0):
        self._model = model
        self._timeout = timeout

    def _complete(self, prompt: str) -> str:
        import json
        import subprocess

        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", self._model,
             "--max-turns", "1", "--output-format", "json"],
            capture_output=True, text=True, timeout=self._timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed (rc={proc.returncode}): {(proc.stderr or '')[:200]}")
        out = (proc.stdout or "").strip()
        # `--output-format json` wraps the model's text in a {"result": ...} envelope.
        try:
            return json.loads(out).get("result", out)
        except json.JSONDecodeError:
            return out


class AnthropicVerifier(LLMVerifier):
    """API-key Anthropic verifier (SDK). Requires ANTHROPIC_API_KEY — not used in
    prod (which is OAuth via `ClaudeCLIVerifier`); kept for key-based environments."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001", timeout: float = 30.0):
        import anthropic  # lazy import so the module loads without a key

        self._client = anthropic.Anthropic(timeout=timeout)
        self._model = model

    def _complete(self, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


class OpenAIVerifier(LLMVerifier):
    """Test-backend verifier — OpenAI via the SDK. Used only where an OpenAI key
    is available; production uses `AnthropicVerifier` (OAuth-Anthropic)."""

    def __init__(self, model: str | None = None, timeout: float = 40.0):
        import openai  # lazy import

        self._client = openai.OpenAI(timeout=timeout)
        self._model = model or os.environ.get("OPENAI_VERIFIER_MODEL", "gpt-4o-mini")

    def _complete(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            max_tokens=400,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content


def get_verifier() -> Verifier:
    """Pick a verifier, preferring the in-house OAuth path.

    Order: Claude CLI (OAuth, prod) > Anthropic SDK (API key) > OpenAI (test) >
    heuristic. Set GATE_VERIFIER to force one of: claude_cli | anthropic | openai |
    heuristic.
    """
    import shutil

    forced = os.environ.get("GATE_VERIFIER", "").strip().lower()
    if forced == "heuristic":
        return HeuristicVerifier()
    if forced == "openai":
        return OpenAIVerifier()
    if forced == "anthropic":
        return AnthropicVerifier()
    if forced == "claude_cli":
        return ClaudeCLIVerifier()

    # Auto: prefer OAuth `claude` CLI when present (production).
    if shutil.which("claude"):
        try:
            return ClaudeCLIVerifier()
        except Exception:
            pass
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicVerifier()
        except Exception:
            pass
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return OpenAIVerifier()
        except Exception:
            pass
    return HeuristicVerifier()
