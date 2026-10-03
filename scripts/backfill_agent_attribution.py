"""Backfill agent attribution on assistant messages saved before it existed.

Old assistant messages carry no agent. They are attributed to Loma (the default
agent) and marked `attribution: "inferred"`, so analytics can tell them apart
from attribution recorded at run time. The dashboard shows them as "Loma" with
or without this backfill; the script makes the data itself consistent.

Writes go out in `bulk_write` batches, and it is safe to re-run: messages that
already have an agent are left alone.

    python3 scripts/backfill_agent_attribution.py            # dry run: count only
    python3 scripts/backfill_agent_attribution.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UNATTRIBUTED = {"role": "assistant", "agent_name": {"$exists": False}}
BATCH_SIZE = 500


async def backfill(db, apply: bool) -> dict:
    """Attribute unattributed assistant replies to Loma, in bulk-write batches.

    Each update targets message positions and re-checks the conversation still
    has an unattributed reply, so concurrent appends (which only add to the end
    of `messages`) can't shift what gets written.
    """
    from pymongo import UpdateOne

    match = {"messages": {"$elemMatch": UNATTRIBUTED}}
    conversations = await db.conversations.count_documents(match)
    result = {"conversations": conversations, "modified": 0}
    if not apply or not conversations:
        return result

    ops: list = []

    async def flush():
        if ops:
            res = await db.conversations.bulk_write(ops, ordered=False)
            result["modified"] += res.modified_count
            ops.clear()

    async for convo in db.conversations.find(match, {"conversation_id": 1, "messages.role": 1,
                                                     "messages.agent_name": 1}):
        fields = {}
        for i, msg in enumerate(convo.get("messages") or []):
            if msg.get("role") == "assistant" and "agent_name" not in msg:
                fields[f"messages.{i}.agent_id"] = None
                fields[f"messages.{i}.agent_name"] = "Loma"
                fields[f"messages.{i}.attribution"] = "inferred"
        if fields:
            ops.append(UpdateOne({"_id": convo["_id"]}, {"$set": fields}))
        if len(ops) >= BATCH_SIZE:
            await flush()
    await flush()
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    from observability.db import get_db, init_observability

    await init_observability()
    db = get_db()
    if db is None:
        sys.exit("MongoDB is not configured (set OBSERVABILITY_MONGODB_URI).")
    result = await backfill(db, args.apply)
    mode = "Updated" if args.apply else "Would update"
    print(f"{mode} {result['conversations']} conversations "
          f"(modified: {result['modified']}).")


if __name__ == "__main__":
    asyncio.run(main())
