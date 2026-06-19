"""
Pylon Event Ingestion

Normalizes a Pylon webhook payload (fired on new issue, new message, or
status change) into the unified event schema and stores it in the
``changestreams`` collection.

Expected webhook body (configured in Pylon's workflow builder):
{
  "account": "Example Corp",
  "author": "John Doe",
  "message": "The latest message text",
  "id": "issue-uuid",
  "status": "open"
}
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from observability.db import get_db

logger = logging.getLogger(__name__)


def _content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_source_event_id(body: dict) -> str:
    """Build a dedup key from the payload.

    Since Pylon sends the same shape for all triggers (no event_type field),
    we hash the full body to distinguish different deliveries for the same
    issue (new message vs. status change).  Identical re-deliveries dedup
    naturally via $setOnInsert.
    """
    issue_id = body.get("id", "")
    payload_hash = hashlib.sha256(
        f"{body.get('message', '')}:{body.get('status', '')}".encode()
    ).hexdigest()[:12]
    return f"pylon:{issue_id}:{payload_hash}"


def _format_event_text(body: dict) -> str:
    """Build the text field from the webhook body."""
    account = body.get("account", "").strip()
    author = body.get("author", "").strip()
    message = body.get("message", "").strip()
    status = body.get("status", "").strip()

    parts: list[str] = []
    if account:
        parts.append(f"[{account}]")
    if status:
        parts.append(f"Status: {status}")
    if author:
        parts.append(f"Author: {author}")
    if parts:
        parts.append("")
    if message:
        parts.append(message)

    return "\n".join(parts).strip()


def normalize_pylon_event(body: dict) -> dict | None:
    issue_id = body.get("id")
    if not issue_id:
        return None

    text = _format_event_text(body)

    return {
        "event_id": str(uuid.uuid4()),
        "source": "pylon",
        "source_event_id": _build_source_event_id(body),
        "event_type": "issue",
        "event_subtype": body.get("status") or None,
        "timestamp": datetime.now(timezone.utc),
        "ingested_at": datetime.now(timezone.utc),
        "channel_id": (body.get("account") or "pylon").lower().replace(" ", "_"),
        "channel_name": body.get("account") or None,
        "thread_ts": issue_id,
        "thread_refs": {"pylon_issue_id": [issue_id]},
        "user_id": None,
        "user_name": body.get("author") or None,
        "is_bot": False,
        "text": text,
        "content_hash": _content_hash(text),
        "files": [],
        "reaction": None,
        "reaction_target_ts": None,
        "entities": [],
        "embedding": None,
        "raw_event": body,
        "processed": False,
        "processing_version": 1,
    }


async def _store_event(normalized: dict) -> None:
    db = get_db()
    if db is None:
        return
    try:
        await db.changestreams.update_one(
            {"source_event_id": normalized["source_event_id"]},
            {"$setOnInsert": normalized},
            upsert=True,
        )
    except Exception:
        logger.exception(
            "[PYLON-INGESTION] Failed to store event: %s",
            normalized.get("source_event_id"),
        )


async def ingest_pylon_event(body: dict) -> None:
    """Normalize and store a Pylon webhook event. Safe for fire-and-forget."""
    try:
        normalized = normalize_pylon_event(body)
        if normalized is None:
            logger.warning("[PYLON-INGESTION] normalize returned None body=%s", str(body)[:200])
            return
        await _store_event(normalized)
        logger.info(
            "[PYLON-INGESTION] stored event issue=%s account=%s status=%s",
            body.get("id"), body.get("account"), body.get("status"),
        )
        # Live pipeline: mirror to pylon_tickets + classify (ISSUE-3424/3427).
        # Safe-fail; classifier errors don't break ingestion.
        try:
            from metrics.pylon_classifier import process_live_event
            await process_live_event(body)
        except Exception:
            logger.exception("[PYLON-INGESTION] live classifier failed (non-fatal)")
    except Exception:
        logger.exception("[PYLON-INGESTION] Failed to ingest event")
