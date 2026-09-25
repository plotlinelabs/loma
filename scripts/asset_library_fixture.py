"""Populate Asset Library sample data through the real registration seam.

Usage:
  python3 scripts/asset_library_fixture.py --owner you@example.com

The script is idempotent: a second run neither duplicates Assets nor errors.
Sign in as that owner and open /library (ticket 02) to see the rows. One Asset
is registered and then has its bytes removed, so the degraded state is visible
once unavailable status lands (ticket 04).

Sample dual-file Chat prompt (reproduces a live OpenCode turn without this
fixture). The fixture records this prompt on the source conversation; tests
reuse the same string:

Create a 1-page PDF at /tmp/loma_q3_sales.pdf and a CSV at /tmp/loma_q3_sales.csv.
Do not upload. Do not wrap either file in a code fence.
In your final reply write exactly:
The file is saved to /tmp/loma_q3_sales.pdf
The file is saved to /tmp/loma_q3_sales.csv
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from observability.assets import asset_recording, list_assets
from observability.db import get_db

CONVERSATION_ID = "asset-library-fixture"
UNAVAILABLE_NAME = "loma_expired.pdf"

DUAL_FILE_PROMPT = """Create a 1-page PDF at /tmp/loma_q3_sales.pdf and a CSV at /tmp/loma_q3_sales.csv.
Do not upload. Do not wrap either file in a code fence.
In your final reply write exactly:
The file is saved to /tmp/loma_q3_sales.pdf
The file is saved to /tmp/loma_q3_sales.csv
"""

# 1x1 PNG; mime type is taken from the filename at registration.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

_SAMPLES = (
    ("loma_q3_sales.pdf", b"%PDF-1.4 fixture\n", False),
    ("loma_chart.png", _PNG, False),
    ("loma_q3_sales.csv", b"sku,qty\nA,1\n", False),
    (UNAVAILABLE_NAME, b"%PDF-1.4 expired\n", True),
)
EXPECTED_NAMES = tuple(name for name, _content, _missing in _SAMPLES)


async def populate(*, owner_email: str) -> dict:
    """Create the fixture conversation and Assets. Safe to run twice."""
    owner_email = (owner_email or "").strip()
    if not owner_email:
        raise ValueError("owner_email is required")
    db = get_db()
    if db is None:
        raise RuntimeError("Observability db unavailable")

    await _ensure_conversation(db, owner_email)
    existing = {
        row["name"]: row
        for row in await list_assets(owner_email)
        if row.get("conversation_id") == CONVERSATION_ID
    }

    from api.routes import register_served_file

    with asset_recording(CONVERSATION_ID, source="opencode"):
        for name, content, missing_bytes in _SAMPLES:
            row = existing.get(name)
            if row is None:
                source = _write_source(name, content)
                register_served_file(str(source), owner_email=owner_email)
                await _wait_for_asset(owner_email, name)
                row = next(
                    item for item in await list_assets(owner_email)
                    if item["name"] == name
                    and item.get("conversation_id") == CONVERSATION_ID
                )
            if missing_bytes:
                _remove_bytes(row["file_id"])

    rows = [
        row for row in await list_assets(owner_email)
        if row.get("conversation_id") == CONVERSATION_ID
    ]
    return {
        "conversation_id": CONVERSATION_ID,
        "count": len(rows),
        "names": [row["name"] for row in rows],
    }


async def _ensure_conversation(db, owner_email: str) -> None:
    existing = await db.conversations.find_one({"conversation_id": CONVERSATION_ID})
    if existing:
        return
    now = datetime.now(timezone.utc)
    await db.conversations.insert_one({
        "conversation_id": CONVERSATION_ID,
        "source": "dashboard",
        "status": "completed",
        "started_at": now,
        "finished_at": now,
        "metadata": {"user_name": owner_email},
        "prompt": DUAL_FILE_PROMPT,
        "title": "Asset Library fixture",
        "messages": [{
            "role": "user",
            "content": DUAL_FILE_PROMPT,
            "timestamp": now,
        }],
    })


def _write_source(name: str, content: bytes) -> Path:
    path = Path(tempfile.mkdtemp(prefix="asset-library-fixture-")) / name
    path.write_bytes(content)
    return path


def _remove_bytes(file_id: str) -> None:
    from api.routes import _served_files
    entry = _served_files.get(file_id)
    if not entry:
        return
    Path(entry["path"]).unlink(missing_ok=True)


async def _wait_for_asset(owner_email: str, name: str) -> None:
    for _ in range(20):
        rows = await list_assets(owner_email)
        if any(
            row["name"] == name and row.get("conversation_id") == CONVERSATION_ID
            for row in rows
        ):
            return
        await asyncio.sleep(0)
    raise RuntimeError(f"Asset {name!r} was not recorded")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--owner", required=True, help="Owner email for the sample Assets")
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
        result = asyncio.run(populate(owner_email=args.owner))
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
