"""Shadow backtest of the pre-send gate over the real complaint tickets.

Reconstructs each ticket's substantive conversation from observability Mongo,
runs the verifier + deterministic gate, and reports the decision. Known-bad
tickets SHOULD be HELD (recall). Uses the LLM verifier when ANTHROPIC_API_KEY is
set, else the heuristic fallback.

Run: `.venv/bin/python gate/backtest.py`
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gate._offline import offline_db
from gate.decision import GateConfig, evaluate
from gate.verifier import LLMVerifier, get_verifier

# The 11 complaint tickets → Pylon issue ids (resolved by title match, all 1.00).
KNOWN_BAD = {
    "#1937 banner outside-clicks": "444b9632-9fb0-4085-bf89-0988ed5dcd20",
    "#2112 CTR formula": "7047193b-cb2a-4288-ae5b-bba0d99dbbe0",
    "#1725 notification mention": "5ddf4d51-e8cd-475f-b4ff-731b3c2519e5",
    "#1730 event visibility": "781e1974-1ddd-444f-872d-62ff8776fab4",
    "#1905 access issue": "1b83b40a-e8c8-4f16-9c4a-85904d4a2b99",
    "#1988 variant-A discrepancy": "77bd2e91-5249-40f6-8c02-5570e0a4ba45",
    "#1915 audience filter": "a5a95262-7552-49e6-af60-f94aa8cb7561",
    "#1981 impr vs completions": "244454d7-9151-443e-b75c-d26009585919",
    "#2056 consent campaigns": "9dcc08f2-1714-462f-b9e8-f049237fb1af",
    "#1752 line-height": "9126bea6-632a-4fef-94d4-098b80cf0969",
    "#2360 segment targeting": "c86d02c3-bd2b-495e-aa4e-2769c8214d12",
}

_KIND = [
    ("clickhouse", re.compile(r"clickhouse", re.I)),
    ("mongo", re.compile(r"mongodb", re.I)),
    ("docs", re.compile(r"docs|searchDocumentation|getPage", re.I)),
    ("github", re.compile(r"github", re.I)),
    ("codebase", re.compile(r"\b(Grep|Glob|Read)\b")),
]


def _best_conversation(db, issue_id: str):
    """Pick the substantive conversation for an issue (most tool calls) and build
    an evidence digest from the tool-call inputs (what it queried/checked)."""
    convs = list(
        db.conversations.find(
            {
                "$or": [
                    {"prompt": {"$regex": issue_id}},
                    {"final_response": {"$regex": issue_id}},
                    {"messages.content": {"$regex": issue_id}},
                ]
            },
            {"conversation_id": 1, "prompt": 1, "final_response": 1, "_id": 0},
        )
    )
    best, best_kinds, best_digest, best_n = None, [], "", -1
    for c in convs:
        cid = c["conversation_id"]
        calls = [
            tc
            for t in db.turns.find({"conversation_id": cid}, {"tool_calls": 1, "_id": 0})
            for tc in (t.get("tool_calls") or [])
        ]
        names = [tc.get("tool_name", "") for tc in calls]
        kinds = sorted({k for k, rx in _KIND if any(rx.search(n) for n in names)})
        digest_lines = []
        for tc in calls:
            nm = tc.get("tool_name", "")
            if any(rx.search(nm) for _, rx in _KIND):
                inp = tc.get("input", "")
                digest_lines.append(f"- {nm}: {str(inp)[:180]}")
        digest = "\n".join(digest_lines[:20])
        if len(names) > best_n:
            best, best_kinds, best_digest, best_n = c, kinds, digest, len(names)
    return best, best_kinds, best_digest


def main():
    db = offline_db()
    verifier = get_verifier()
    print(f"Verifier: {type(verifier).__name__}  (set ANTHROPIC_API_KEY for the production verifier)\n")

    # LLM verifiers emit a confidence score → run full fail-safe gate. The
    # heuristic emits none, so relax the fail-safe to isolate the risk/verify signal.
    is_llm = isinstance(verifier, LLMVerifier)
    cfg = GateConfig(hold_on_missing_confidence=is_llm)
    if not is_llm:
        print("NOTE: heuristic verifier emits no confidence — running with "
              "hold_on_missing_confidence=False to isolate the risk/verify signal.\n")

    held = 0
    n = 0
    print(f"{'ticket':32} {'decision':6} conf  reasons")
    print("-" * 100)
    for label, iid in KNOWN_BAD.items():
        conv, kinds, digest = _best_conversation(db, iid)
        if conv is None:
            print(f"{label:32} {'N/A':6} no conversation found")
            continue
        n += 1
        a = verifier.assess(
            ticket_context=conv.get("prompt", "") or "",
            draft_reply=conv.get("final_response", "") or "",
            evidence_kinds=kinds,
            evidence_digest=digest,
        )
        r = evaluate(a, cfg)
        if r.decision.value == "HOLD":
            held += 1
        cs = f"{a.confidence_score:.2f}" if a.confidence_score is not None else " -- "
        print(f"{label:32} {r.decision.value:6} {cs}  {', '.join(r.reasons) or 'clean → would auto-send'}")
    print("-" * 92)
    print(f"RECALL (known-bad HELD): {held}/{n} = {100*held//max(n,1)}%")
    print("\nProduction config (fail-safe ON, no confidence signal exists yet): "
          "ALL would HOLD — safe default until the LLM verifier supplies a calibrated score.")


if __name__ == "__main__":
    main()
