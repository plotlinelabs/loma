#!/usr/bin/env python3
"""rca_agent.py — per-ticket root-cause analysis for the Pylon RCA sheet.

Processes ONE sheet row per unit of work (idempotent, resumable). For each
target ticket it:
  1. resolves the sheet row -> Pylon issue UUID via a cached local index
  2. fetches the ticket's real conversation from Pylon
  3. loads the analyst's 50 worked root-cause examples as a few-shot library
  4. calls opencode/glm-5.2 for a structured root-cause judgement
  5. writes the result to column H + an "[AI draft]" marker in column L

Tuning is done on the system prompt (SYSTEM_PROMPT below), not the code.

Usage:
  python3 rca_agent.py --row 52 --dry-run         # one ticket, write nothing
  python3 rca_agent.py --rows 52-61 --dry-run     # the test batch, preview
  python3 rca_agent.py --rows 52-61               # write H + L for real
  python3 rca_agent.py --rows 52-961 --resume     # later: scale, skip filled
  python3 rca_agent.py --rebuild-index            # refresh the Pylon index

Env (with sensible defaults baked in):
  OPENCODE_API_KEY   required (model access)        [preflight-checked]
  PYLON_API_KEY      required (ticket bodies)
  LOMA_USER_EMAIL    Google account that owns the sheet  (default adarsh@plotline.so)
  LOMA_AUTH_TOKEN    HMAC auth token for that user       (required for writes)
"""

import argparse
import asyncio
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

# ── Configuration ───────────────────────────────────────────────────────────
SPREADSHEET_ID = "1ejqI5cSuq6E8OER_xObbij1VvZ6PRcB3Rknu70CcYjg"
TAB = "Pylon Tickets — Last 3 Months (RCA)"
MODEL = "opencode/glm-5.2"

USER_EMAIL = os.environ.get("LOMA_USER_EMAIL", "adarsh@plotline.so")
AUTH_TOKEN = os.environ.get("LOMA_AUTH_TOKEN", "")

ROOT = Path(__file__).resolve().parent
INDEX_FILE = ROOT / "rca_index.json"
EXAMPLES_FILE = ROOT / "rca_examples.json"
RUNLOG_FILE = ROOT / "rca_runlog.jsonl"

# Sheet columns (1-based letters)
COL_NUMBER = "A"
COL_SUBJECT = "B"
COL_LINK = "C"
COL_STATE = "D"
COL_MODULE = "F"
COL_QTYPE = "G"
COL_ROOT_CAUSE = "H"   # write target
COL_NOTES = "L"        # marker target
AI_MARKER = "[AI draft]"

EXAMPLES_FIRST_ROW = 2     # first analysed data row
EXAMPLES_LAST_ROW = 51     # last analysed row (#2272); rows >=52 are targets
MAX_CONV_CHARS = 9000      # cap ticket conversation passed to the model

# ── System prompt (THE tunable surface) ─────────────────────────────────────
SYSTEM_PROMPT = """You are an internal root-cause analyst for Plotline, a mobile \
user-engagement SaaS. You are completing a root-cause-analysis (RCA) spreadsheet \
of customer support tickets. A human analyst has already written the "Root cause" \
column for the first 50 tickets. Your job is to write the "Root cause" cell for a \
new ticket in EXACTLY that analyst's voice, depth, and framing.

How the analyst writes root causes (study the examples carefully and imitate):
- They go PAST the symptom to the systemic cause: a missing process, a product/\
tooling gap, an absent safeguard. Not "the widget broke" but "HTML has lots of \
sizing issues in the widget" or "No QA to even check the happy flow".
- They reuse canonical phrasings across similar tickets. When a new ticket matches \
a prior one, REUSE/ADAPT the analyst's existing phrasing rather than inventing new \
wording. Only write fresh phrasing (still in their terse, systemic style) when no \
prior example fits.
- Format is usually a short numbered list (1. ... 2. ...) or a single terse line. \
Plain text, no markdown headers, no fluff. Lowercase-ish, blunt, internal-note tone.
- When they lack enough information to conclude, they write an open question to \
themselves (e.g. "What was the widget issue in 5Paisa?", "Why did the same issue \
happen twice?") rather than a confident guess.

Special cases:
- Some tickets are NOT product issues (e.g. billing/payment inquiries, pure \
how-to questions with no underlying defect). For these set is_na=true and put a \
short note like "N/A — billing inquiry" in root_cause.

You will receive: the target ticket's metadata + its real conversation, and the 50 \
analyst examples. Identify the closest example(s) by ticket number, then produce \
the root cause.

Respond with ONE minified JSON object and nothing else (no markdown fences):
{"root_cause": <string>, "matched_example_ticket_ids": [<int>...], \
"confidence": "high"|"medium"|"low", "is_na": <bool>}
- root_cause: the cell text, in the analyst's style.
- matched_example_ticket_ids: ticket numbers of the prior examples you drew on (may be empty).
- confidence: how well grounded the root cause is in the ticket content + examples.
- is_na: true only for non-product tickets."""


# ── HTML → text ─────────────────────────────────────────────────────────────
def html_to_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


# ── Pylon index ─────────────────────────────────────────────────────────────
async def build_index(days: int = 95) -> dict:
    from tools.pylon import _api_post
    after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    filt = {"field": "created_at", "operator": "time_is_after", "value": after}
    out, cursor = {}, None
    for _ in range(40):
        body = {"filter": filt, "limit": 100}
        if cursor:
            body["cursor"] = cursor
        res = await _api_post("/issues/search", body)
        if "error" in res:
            raise RuntimeError(f"Pylon search failed: {res['error']}")
        for it in res.get("data", []):
            num = it.get("number")
            if num is None:
                continue
            cf = it.get("custom_fields") or {}

            def cfv(k):
                v = cf.get(k)
                if isinstance(v, dict):
                    return v.get("value") or v.get("label") or ""
                return v or ""

            out[str(num)] = {
                "id": it.get("id"),
                "number": num,
                "title": it.get("title", ""),
                "link": it.get("link", ""),
                "state": it.get("state", ""),
                "created_at": it.get("created_at", ""),
                "module": cfv("module"),
                "question_type": cfv("question_type"),
            }
        pg = res.get("pagination", {})
        if not pg.get("has_next_page"):
            break
        cursor = pg.get("cursor")
        if not cursor:
            break
    return out


async def load_index(rebuild: bool = False) -> dict:
    if INDEX_FILE.exists() and not rebuild:
        return json.loads(INDEX_FILE.read_text())
    idx = await build_index()
    INDEX_FILE.write_text(json.dumps(idx))
    print(f"[index] built {len(idx)} tickets -> {INDEX_FILE.name}", file=sys.stderr)
    return idx


# ── Sheet helpers ───────────────────────────────────────────────────────────
def _rng(a1: str) -> str:
    return f"'{TAB}'!{a1}"


async def read_rows(first: int, last: int) -> list[list]:
    from tools.google_sheets import read_range
    r = await read_range(USER_EMAIL, SPREADSHEET_ID, _rng(f"A{first}:L{last}"))
    return r.get("values", [])


async def write_cell(col: str, row: int, value: str) -> None:
    from tools.google_sheets import write_range
    await write_range(USER_EMAIL, SPREADSHEET_ID, _rng(f"{col}{row}"), [[value]])


def cell(row: list, col_letter: str) -> str:
    i = ord(col_letter) - ord("A")
    return row[i] if len(row) > i else ""


# ── Examples snapshot ───────────────────────────────────────────────────────
async def build_examples(rebuild: bool = False) -> list[dict]:
    if EXAMPLES_FILE.exists() and not rebuild:
        return json.loads(EXAMPLES_FILE.read_text())
    rows = await read_rows(EXAMPLES_FIRST_ROW, EXAMPLES_LAST_ROW)
    examples = []
    for row in rows:
        rc = cell(row, COL_ROOT_CAUSE).strip()
        if not rc:
            continue
        examples.append({
            "number": cell(row, COL_NUMBER).strip(),
            "subject": cell(row, COL_SUBJECT).strip(),
            "module": cell(row, COL_MODULE).strip(),
            "question_type": cell(row, COL_QTYPE).strip(),
            "root_cause": rc,
        })
    EXAMPLES_FILE.write_text(json.dumps(examples, ensure_ascii=False, indent=2))
    print(f"[examples] snapshotted {len(examples)} -> {EXAMPLES_FILE.name}", file=sys.stderr)
    return examples


def render_examples(examples: list[dict]) -> str:
    lines = []
    for e in examples:
        meta = f"#{e['number']} | {e['subject']}"
        tags = " | ".join(t for t in (e.get("module"), e.get("question_type")) if t)
        if tags:
            meta += f" | {tags}"
        lines.append(f"{meta}\nROOT CAUSE: {e['root_cause']}")
    return "\n\n".join(lines)


# ── Pylon conversation ──────────────────────────────────────────────────────
async def fetch_conversation(issue_id: str) -> str:
    from tools.pylon import get_messages
    res = await get_messages(issue_id)
    if isinstance(res, dict) and "error" in res:
        return f"(could not fetch messages: {res['error']})"
    data = res.get("data") if isinstance(res, dict) else None
    if not isinstance(data, list):
        return "(no messages)"
    parts = []
    for m in data:
        body = html_to_text(m.get("message_html") or m.get("body_html") or m.get("body_text") or "")
        if not body:
            continue
        author = m.get("author") or {}
        who = author.get("name") or author.get("email") or m.get("source") or "?"
        kind = "internal-note" if m.get("is_private") else "message"
        parts.append(f"[{kind} · {who}] {body}")
    text = "\n\n".join(parts)
    if len(text) > MAX_CONV_CHARS:
        text = text[:MAX_CONV_CHARS] + "\n…(truncated)"
    return text or "(messages present but empty after cleaning)"


# ── Model call ──────────────────────────────────────────────────────────────
def extract_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    # defensive: grab the largest {...} block
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


async def call_model(system: str, user: str) -> tuple[dict | None, str]:
    from agent.opencode_runtime import (
        ensure_opencode_server, _create_session, _request_json,
        _split_model, PROJECT_ROOT,
    )
    await ensure_opencode_server()
    provider_id, model_id = _split_model(MODEL)
    sid = await _create_session("rca-agent")
    body = {
        "model": {"providerID": provider_id, "modelID": model_id},
        "system": system,
        "parts": [{"type": "text", "text": user}],
    }
    resp = await _request_json(
        "POST", f"/session/{sid}/message",
        json_body=body, params={"directory": str(PROJECT_ROOT)}, timeout=300,
    )
    parts = resp.get("parts", []) if isinstance(resp, dict) else []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
    raw = texts[-1] if texts else ""
    parsed = extract_json(raw)
    if parsed is None and raw:
        # one reprompt on malformed JSON
        retry_user = user + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
        sid2 = await _create_session("rca-agent-retry")
        body["parts"] = [{"type": "text", "text": retry_user}]
        resp2 = await _request_json(
            "POST", f"/session/{sid2}/message",
            json_body=body, params={"directory": str(PROJECT_ROOT)}, timeout=300,
        )
        parts2 = resp2.get("parts", []) if isinstance(resp2, dict) else []
        texts2 = [p.get("text", "") for p in parts2 if isinstance(p, dict) and p.get("type") == "text"]
        raw = texts2[-1] if texts2 else raw
        parsed = extract_json(raw)
    return parsed, raw


def build_user_prompt(meta: dict, conversation: str, examples_block: str) -> str:
    return f"""=== ANALYST'S 50 EXAMPLE ROOT CAUSES (few-shot library) ===
{examples_block}

=== TARGET TICKET TO ANALYSE ===
Ticket #: {meta['number']}
Subject: {meta['subject']}
State: {meta['state']}
Module: {meta['module'] or '(none)'}
Question type: {meta['question_type'] or '(none)'}

--- Conversation ---
{conversation}

=== TASK ===
Write the "Root cause" cell for this ticket in the analyst's style. Match the closest \
example(s) and reuse/adapt their phrasing where it fits; only write fresh when nothing \
matches. Return the JSON object only."""


# ── Per-row processing ──────────────────────────────────────────────────────
def log_run(record: dict) -> None:
    with open(RUNLOG_FILE, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def process_row(row_num: int, sheet_row: list, index: dict,
                      examples_block: str, dry_run: bool, force: bool) -> dict:
    number = cell(sheet_row, COL_NUMBER).strip()
    subject = cell(sheet_row, COL_SUBJECT).strip()
    existing_h = cell(sheet_row, COL_ROOT_CAUSE).strip()

    if existing_h and not force:
        print(f"\n── row {row_num} (#{number}) SKIPPED — H already filled (use --force)")
        return {"row": row_num, "number": number, "status": "skipped"}

    entry = index.get(number)
    if not entry:
        print(f"\n── row {row_num} (#{number}) ERROR — not in Pylon index")
        return {"row": row_num, "number": number, "status": "no_index"}

    meta = {
        "number": number,
        "subject": subject,
        "state": cell(sheet_row, COL_STATE).strip() or entry.get("state", ""),
        "module": cell(sheet_row, COL_MODULE).strip() or entry.get("module", ""),
        "question_type": cell(sheet_row, COL_QTYPE).strip() or entry.get("question_type", ""),
    }
    conversation = await fetch_conversation(entry["id"])
    user_prompt = build_user_prompt(meta, conversation, examples_block)
    parsed, raw = await call_model(SYSTEM_PROMPT, user_prompt)

    if parsed is None:
        print(f"\n── row {row_num} (#{number}) ERROR — model returned non-JSON")
        print("RAW:", raw[:500])
        return {"row": row_num, "number": number, "status": "bad_json", "raw": raw[:1000]}

    root_cause = str(parsed.get("root_cause", "")).strip()
    matched = parsed.get("matched_example_ticket_ids", [])
    confidence = parsed.get("confidence", "")
    is_na = bool(parsed.get("is_na", False))

    print(f"\n{'='*78}\n── row {row_num} · #{number} · {subject}")
    print(f"   module={meta['module'] or '-'} | qtype={meta['question_type'] or '-'} | conv_chars={len(conversation)}")
    print(f"   matched_examples={matched} | confidence={confidence} | is_na={is_na}")
    print(f"   ROOT CAUSE -> H{row_num}:\n   {root_cause}")
    print(f"   NOTES -> L{row_num}: {AI_MARKER}")

    record = {
        "row": row_num, "number": number, "subject": subject,
        "matched": matched, "confidence": confidence, "is_na": is_na,
        "root_cause": root_cause, "dry_run": dry_run,
    }

    if dry_run:
        print("   [dry-run] nothing written")
        record["status"] = "dry_run"
        log_run(record)
        return record

    if not root_cause:
        record["status"] = "empty_output"
        log_run(record)
        return record

    await write_cell(COL_ROOT_CAUSE, row_num, root_cause)
    await write_cell(COL_NOTES, row_num, AI_MARKER)
    print("   [written] H + L updated")
    record["status"] = "written"
    log_run(record)
    return record


# ── CLI ─────────────────────────────────────────────────────────────────────
def parse_rows(args) -> list[int]:
    if args.row is not None:
        return [args.row]
    if args.rows:
        m = re.match(r"^(\d+)-(\d+)$", args.rows.strip())
        if m:
            return list(range(int(m.group(1)), int(m.group(2)) + 1))
        return [int(x) for x in args.rows.split(",") if x.strip()]
    return []


async def amain(args) -> None:
    rows = parse_rows(args)
    if not rows:
        print("Specify --row N or --rows A-B", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run and not AUTH_TOKEN:
        print("LOMA_AUTH_TOKEN is required for writes (set it or use --dry-run).", file=sys.stderr)
        sys.exit(1)

    index = await load_index(rebuild=args.rebuild_index)
    examples = await build_examples(rebuild=args.rebuild_examples)
    examples_block = render_examples(examples)
    print(f"[ready] {len(examples)} examples, {len(index)} indexed tickets, model={MODEL}", file=sys.stderr)

    sheet_rows = await read_rows(min(rows), max(rows))
    by_num = {}
    base = min(rows)
    results = []
    for rn in rows:
        idx = rn - base
        sheet_row = sheet_rows[idx] if idx < len(sheet_rows) else []
        res = await process_row(rn, sheet_row, index, examples_block,
                                dry_run=args.dry_run, force=args.force)
        results.append(res)

    print(f"\n{'='*78}\nSUMMARY")
    for r in results:
        print(f"  row {r['row']} #{r.get('number','?')}: {r['status']}"
              + (f" (conf={r.get('confidence')})" if r.get('confidence') else ""))


def main():
    ap = argparse.ArgumentParser(description="Per-ticket RCA agent for the Pylon sheet")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--row", type=int, help="single sheet row number")
    g.add_argument("--rows", type=str, help="row range 'A-B' or comma list")
    ap.add_argument("--dry-run", action="store_true", help="print only, write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite a filled H cell")
    ap.add_argument("--resume", action="store_true", help="skip filled rows (default behaviour)")
    ap.add_argument("--rebuild-index", action="store_true", help="rebuild rca_index.json")
    ap.add_argument("--rebuild-examples", action="store_true", help="rebuild rca_examples.json")
    args = ap.parse_args()

    # Preflight: model key must be present (fail loud, not mid-loop)
    if not os.environ.get("OPENCODE_API_KEY"):
        print("OPENCODE_API_KEY is not set in the environment.", file=sys.stderr)
        sys.exit(2)
    if not os.environ.get("PYLON_API_KEY"):
        print("PYLON_API_KEY is not set in the environment.", file=sys.stderr)
        sys.exit(2)

    if args.rebuild_index and args.row is None and not args.rows:
        asyncio.run(load_index(rebuild=True))
        return

    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
