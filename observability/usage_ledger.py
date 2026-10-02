"""Per-run usage ledger — one timestamped row per agent run.

`conversations.cost` is a running total with no time dimension: a chat
started on Monday and used on Wednesday could only ever be attributed to
Monday (its `started_at`). Every run now also writes a `usage_events` row
stamped with when that run's usage was recorded, so per-day / per-window
spend is exact.

Invariants:
- `event_id` is unique; writes use `$setOnInsert`, so re-recording the same
  event (retry, concurrent backfill on several replicas) never double counts.
- The observer writes the ledger row *before* incrementing the conversation
  total. The backfill relies on this: `conversation.cost - sum(live events)`
  can only be transiently negative (skipped, retried next startup), never
  transiently too large.
- Legacy spend (recorded before the ledger existed) is backfilled once per
  conversation as a single `approx: true` event at `started_at` — the same
  attribution the old endpoint used, so historical numbers don't move.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

COLLECTION = "usage_events"
BACKFILL_MARKER = "usage_ledger_backfilled"
_EPSILON_USD = 1e-6

# conversation.cost field -> usage_events field
_COST_FIELDS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_tokens",
    "cache_creation_tokens": "cache_creation_tokens",
    "total_cost_usd": "cost_usd",
}


async def ensure_usage_indexes(db) -> None:
    coll = db[COLLECTION]
    await coll.create_index("event_id", unique=True)
    await coll.create_index([("user_email", 1), ("at", -1)])
    await coll.create_index([("conversation_id", 1), ("at", -1)])
    await coll.create_index([("at", -1)])  # org-wide analytics windows


def build_usage_event(
    *,
    event_id: str,
    conversation_id: str,
    at: datetime,
    user_email: str,
    source: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cost_usd: float = 0.0,
    cost_known: bool = True,
    runtime: str = "",
    flow_id: str | None = None,
    flow_name: str | None = None,
    approx: bool = False,
) -> dict:
    return {
        "event_id": event_id,
        "conversation_id": conversation_id,
        "at": at,
        "user_email": user_email or "",
        "source": source or "unknown",
        "model": model or "",
        # claude | opencode | codex; "" on rows written before this field.
        "runtime": runtime or "",
        # Set for flow/webhook runs so org analytics can group by flow.
        "flow_id": flow_id or None,
        "flow_name": flow_name or None,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_read_tokens": int(cache_read_tokens or 0),
        "cache_creation_tokens": int(cache_creation_tokens or 0),
        "cost_usd": round(float(cost_usd or 0), 6),
        # False when the runtime reports tokens but no price (Codex on a
        # ChatGPT plan, unpriced OpenCode models). cost_usd is 0 then, and
        # the UI shows "no price data" instead of a misleading $0.
        "cost_known": bool(cost_known),
        "approx": approx,
        "recorded_at": datetime.now(timezone.utc),
    }


async def record_usage_event(db, event: dict) -> bool:
    """Idempotently insert one ledger row. Returns True if newly inserted."""
    result = await db[COLLECTION].update_one(
        {"event_id": event["event_id"]},
        {"$setOnInsert": event},
        upsert=True,
    )
    return result.upserted_id is not None


async def _live_totals(db, conversation_id: str) -> dict:
    """Sum of non-backfill ledger rows for one conversation."""
    rows = await db[COLLECTION].aggregate([
        {"$match": {"conversation_id": conversation_id, "approx": {"$ne": True}}},
        {"$group": {
            "_id": None,
            **{f: {"$sum": {"$ifNull": [f"${f}", 0]}} for f in _COST_FIELDS.values()},
        }},
    ]).to_list(1)
    return rows[0] if rows else {}


async def backfill_usage_events(db) -> int:
    """Record pre-ledger spend as one approximate event per conversation.

    Safe to run on every startup and on several replicas at once. Returns the
    number of approximate events inserted.
    """
    inserted = 0
    scanned = 0
    cursor = db.conversations.find(
        {"cost": {"$type": "object"}, BACKFILL_MARKER: {"$ne": True}},
        {"conversation_id": 1, "cost": 1, "started_at": 1, "source": 1,
         "model": 1, "metadata.user_name": 1, "metadata.flow_id": 1,
         "metadata.flow_name": 1},
    )
    async for conv in cursor:
        scanned += 1
        conversation_id = conv.get("conversation_id")
        if not conversation_id:
            continue
        try:
            cost = conv.get("cost") or {}
            live = await _live_totals(db, conversation_id)
            remainder = {
                event_field: (cost.get(cost_field) or 0) - (live.get(event_field) or 0)
                for cost_field, event_field in _COST_FIELDS.items()
            }
            if remainder["cost_usd"] < -_EPSILON_USD or any(
                remainder[f] < 0 for f in remainder if f != "cost_usd"
            ):
                # A run is between its ledger write and its conversation
                # $inc. Leave unmarked; the next startup settles it.
                continue
            has_remainder = remainder["cost_usd"] > _EPSILON_USD or any(
                remainder[f] > 0 for f in remainder if f != "cost_usd"
            )
            if has_remainder:
                started_at = conv.get("started_at") or datetime.now(timezone.utc)
                event = build_usage_event(
                    event_id=f"backfill:{conversation_id}",
                    conversation_id=conversation_id,
                    at=started_at,
                    user_email=(conv.get("metadata") or {}).get("user_name", ""),
                    flow_id=(conv.get("metadata") or {}).get("flow_id"),
                    flow_name=(conv.get("metadata") or {}).get("flow_name"),
                    source=conv.get("source", "unknown"),
                    model=conv.get("model", ""),
                    # Pre-ledger Codex runs were stored as $0 with tokens.
                    cost_known=not (
                        remainder["cost_usd"] <= _EPSILON_USD
                        and str(conv.get("model") or "").startswith("codex/")
                    ),
                    approx=True,
                    **{k: max(v, 0) for k, v in remainder.items()},
                )
                if await record_usage_event(db, event):
                    inserted += 1
            await db.conversations.update_one(
                {"conversation_id": conversation_id},
                {"$set": {BACKFILL_MARKER: True}},
            )
        except Exception as e:
            logger.warning("Usage ledger backfill failed for %s: %s", conversation_id, e)
    if scanned:
        logger.info("Usage ledger backfill: scanned %d conversations, inserted %d approx events",
                    scanned, inserted)
    return inserted
