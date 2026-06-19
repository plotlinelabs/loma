"""Decision-only shadow for the pre-send gate — Pylon first-response only.

Scope (per product intent): the gate exists for the **customer-facing first
response** Loma sends on a Pylon ticket. So the shadow only records a decision
when the run actually posted a customer reply (`pylon.py reply`). Internal notes,
follow-ups, no-op runs, and non-Pylon webhook flows (Linear/GitHub) are ignored.

It records what the gate *would* have decided WITHOUT changing behaviour (the
reply was already sent inside the agent loop). Inert unless GATE_SHADOW_ENABLED.
Always safe-fail.
"""

from __future__ import annotations

import json
import logging
import os
import re

from gate.decision import GateConfig, evaluate
from gate.verifier import LLMVerifier, get_verifier

logger = logging.getLogger(__name__)

# The "Pylon Support Ticket Handler" webhook flow. Override via env if it changes.
PYLON_FLOW_ID = os.environ.get("GATE_PYLON_FLOW_ID", "8fe89f18-625a-4bcc-ac5b-bffd051cc0f4")

_KIND_MAP = [
    ("clickhouse", "clickhouse"), ("mongodb", "mongo"),
    ("docs", "docs"), ("searchDocumentation", "docs"), ("getPage", "docs"),
    ("github", "github"), ("Grep", "codebase"), ("Glob", "codebase"), ("Read", "codebase"),
]
# Heredoc body of a `pylon.py reply` command (the customer-facing reply HTML).
_REPLY_RE = re.compile(r"pylon\.py\s+reply\b")
# `cat <<'EOF' | python3 ... reply ...\n<body>\nEOF` — the rest of the `<<` line is
# the command (the pipe), the body is the lines up to the closing delimiter.
_HEREDOC_RE = re.compile(r"<<\s*'?([A-Za-z0-9_]+)'?[^\n]*\n(.*?)\n\1\b", re.S)


def is_enabled() -> bool:
    return os.environ.get("GATE_SHADOW_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def is_pylon_flow(flow_id: str | None) -> bool:
    return flow_id == PYLON_FLOW_ID


def _command_of(tool_call: dict) -> str:
    raw = tool_call.get("input")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
    return raw.get("command", "") if isinstance(raw, dict) else str(raw or "")


def _reconstruct_from_turns(db, conversation_id: str) -> tuple[str, list[str], str]:
    """Return (customer_reply_body, evidence_kinds, evidence_digest) from a run's
    turns. reply_body is "" when no `pylon.py reply` was posted."""
    reply_body = ""
    kinds: set[str] = set()
    digest: list[str] = []
    for t in db.turns.find({"conversation_id": conversation_id}, {"tool_calls": 1, "_id": 0}):
        for tc in (t.get("tool_calls") or []):
            name = tc.get("tool_name", "")
            for needle, kind in _KIND_MAP:
                if needle in name:
                    kinds.add(kind)
                    break
            if name.lower() == "bash":
                cmd = _command_of(tc)
                if not reply_body and _REPLY_RE.search(cmd):
                    m = _HEREDOC_RE.search(cmd)
                    reply_body = (m.group(2) if m else cmd).strip()
                if len(digest) < 20 and "pylon.py" in cmd:
                    digest.append(f"- bash: {cmd[:150]}")
            elif len(digest) < 20:
                digest.append(f"- {name}: {str(tc.get('input', ''))[:150]}")
    return reply_body, sorted(kinds), "\n".join(digest)


async def _fetch_pylon_meta(issue_id: str | None) -> dict:
    """Resolve ticket number/title/link/body/customer from Pylon (best-effort)."""
    meta = {"number": None, "title": "", "link": "", "body": "", "customer": ""}
    if not issue_id:
        return meta
    try:
        from tools import pylon
        r = await pylon.get_issue(issue_id)
        d = r.get("data") or r
        meta["number"] = d.get("number")
        meta["title"] = d.get("title", "") or ""
        meta["link"] = d.get("link", "") or ""
        meta["body"] = (d.get("body_html", "") or "")[:2000]
        acct = d.get("account") or {}
        meta["customer"] = acct.get("name", "") if isinstance(acct, dict) else ""
    except Exception:
        logger.warning("[GATE-SHADOW] pylon meta lookup failed for issue %s", issue_id)
    return meta


async def record_gate_shadow(db, *, conversation_id: str, flow_id: str, issue_id: str | None) -> None:
    """Compute + log the gate decision for a finished Pylon first-response run.

    Safe for fire-and-forget. Never raises. Records nothing unless a customer
    reply was actually posted.
    """
    try:
        import asyncio
        from datetime import datetime, timezone

        reply_body, evidence_kinds, evidence_digest = _reconstruct_from_turns(db, conversation_id)
        if not reply_body:
            return  # no customer-facing reply → not a first response; out of scope

        meta = await _fetch_pylon_meta(issue_id)
        ticket_context = f"Ticket #{meta['number']}: {meta['title']}\n{meta['body']}".strip()

        verifier = get_verifier()
        assessment = await asyncio.to_thread(
            verifier.assess,
            ticket_context=ticket_context,
            draft_reply=reply_body,
            evidence_kinds=evidence_kinds,
            evidence_digest=evidence_digest,
        )
        is_llm = isinstance(verifier, LLMVerifier)
        result = evaluate(assessment, GateConfig(hold_on_missing_confidence=is_llm))

        await db.gate_shadow.insert_one({
            "conversation_id": conversation_id,
            "flow_id": flow_id,
            "issue_id": issue_id,
            "issue_number": meta["number"],
            "issue_title": meta["title"],
            "issue_link": meta["link"],
            "customer": meta["customer"],
            "verifier": type(verifier).__name__,
            "decision": result.decision.value,
            "action": result.action,
            "reasons": result.reasons,
            "confidence_score": assessment.confidence_score,
            "category": assessment.category,
            "claims_verified": assessment.claims_verified,
            "risk_flags": assessment.risk_flags,
            "reasoning": assessment.reasoning,
            "evidence_kinds": evidence_kinds,
            "draft_preview": reply_body[:1500],
            "recorded_at": datetime.now(timezone.utc),
        })
        logger.info(
            "[GATE-SHADOW] ticket #%s conv=%s decision=%s reasons=%s",
            meta["number"], conversation_id, result.decision.value, result.reasons,
        )
    except Exception:
        logger.exception("[GATE-SHADOW] failed (non-fatal) for conv=%s", conversation_id)
