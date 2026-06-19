"""Small Linear sizing audit helpers used by the optional Linear webhook."""

from __future__ import annotations

import re
from datetime import datetime, timezone


def parse_single_result(text: str) -> dict:
    match = re.search(r"([A-Z]+-\d+)\s*[-:>]\s*(\d+)\s*pts?\s*(?:[|:-]\s*(.*))?", text, re.I)
    if not match:
        return {"identifier": None, "points": None, "reason": text.strip()}
    return {
        "identifier": match.group(1).upper(),
        "points": int(match.group(2)),
        "reason": (match.group(3) or "").strip(),
    }


async def write_audit_doc(db, **kwargs):
    if db is None:
        return None
    doc = {**kwargs, "created_at": datetime.now(timezone.utc)}
    await db.eng_sizing_log.insert_one(doc)
    return doc
