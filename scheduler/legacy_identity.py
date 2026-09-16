"""One-time compatibility backfill, never a runtime identity fallback.

The operator-approved policy is to use legacy *recorded* creator metadata. That
metadata predates authenticated creation and is not proof of provenance. Conflicting
emails and unknown/inactive accounts require explicit admin repair instead.
"""
import logging
from datetime import datetime, timezone

from pymongo import UpdateOne

logger = logging.getLogger(__name__)
# Fixed boundary: merge of the authenticated-creation fix (#187). Never advance
# this on startup: new workflows must go through authenticated account validation.
LEGACY_BEFORE = datetime(2026, 9, 14, 21, 43, 12, tzinfo=timezone.utc)
MIGRATION = "legacy_creator_identity_v1"


def recorded_creator(flow: dict) -> str | None:
    creator = flow.get("created_by")
    if not isinstance(creator, dict):
        return None
    emails = set()
    for field in ("source", "user_name"):
        value = creator.get(field)
        if not isinstance(value, str):
            continue
        value = value.strip().lower()
        if value.count("@") == 1 and not any(c.isspace() for c in value):
            local, domain = value.split("@")
            if local and domain:
                emails.add(value)
    return emails.pop() if len(emails) == 1 else None


async def backfill_legacy_identities(db, *, dry_run: bool = False) -> dict:
    """Bounded batches with compare-and-set writes; safe across simultaneous boots.

    Only missing/null/empty identities on pre-fix, non-agent workflows qualify.
    Never change a saved account or a new flow, and never run a job here. Account
    activity is rechecked by require_execution_account at actual execution time.
    """
    counts = {"eligible": 0, "assigned": 0, "unresolved": 0}
    if db is None:
        return counts
    query = {
        "created_at": {"$type": "date", "$lt": LEGACY_BEFORE},
        "run_as": {"$in": [None, ""]},
        "agent_id": None,
        "identity_version": {"$exists": False},
        "run_as_backfill": {"$exists": False},
    }

    async def process(batch):
        candidates = [(flow, recorded_creator(flow)) for flow in batch]
        emails = sorted({email for _, email in candidates if email})
        users = await db.users.find(
            {"email": {"$in": emails}, "status": "active"}, {"email": 1},
        ).to_list(None)
        active = {user["email"] for user in users}
        writes = []
        for flow, email in candidates:
            if not email or email not in active:
                counts["unresolved"] += 1
                logger.warning("Legacy flow %s needs admin account assignment", flow.get("flow_id"))
                continue
            counts["eligible"] += 1
            # Guard exact metadata as well as the missing-account condition, so an
            # admin edit between reading and writing wins over the backfill.
            condition = {**query, "_id": flow["_id"], "created_by": flow["created_by"]}
            if "updated_at" in flow:
                condition["updated_at"] = flow["updated_at"]
            else:
                condition["updated_at"] = {"$exists": False}
            now = datetime.now(timezone.utc)
            writes.append(UpdateOne(condition, {"$set": {
                "run_as": email,
                "run_as_backfill": {"migration": MIGRATION, "account": email, "at": now},
                "updated_at": now,
            }}))
        if writes and not dry_run:
            result = await db.flows.bulk_write(writes, ordered=False)
            counts["assigned"] += result.modified_count

    batch = []
    async for flow in db.flows.find(query):
        batch.append(flow)
        if len(batch) == 100:
            await process(batch)
            batch = []
    if batch:
        await process(batch)
    logger.info("Legacy identity backfill (dry_run=%s): %s", dry_run, counts)
    return counts
