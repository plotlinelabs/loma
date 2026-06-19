"""
Grain Event Ingestion

Normalizes a Grain meeting recording + transcript into the unified event schema
and stores it in the ``changestreams`` collection. Triggered by a Zapier webhook
that fires when a meeting completes (delivering the recording_id).
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from observability.db import get_db
from tools.grain import get_transcript, find_recording_by_id

logger = logging.getLogger(__name__)


def _content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_timestamp(recording: dict) -> datetime:
    ts_str = recording.get("start_datetime")
    if ts_str:
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


def _extract_user(recording: dict) -> tuple[str | None, str | None]:
    """Return (user_id=email, user_name) for the organizer, else first attendee."""
    participants = recording.get("participants") or []
    organizer = None
    for p in participants:
        if (p.get("scope") or "").lower() == "organizer":
            organizer = p
            break
    if organizer is None:
        # Fall back to the first confirmed attendee.
        for p in participants:
            if p.get("confirmed_attendee", True):
                organizer = p
                break
    if organizer is None:
        return None, None
    return organizer.get("email") or None, organizer.get("name") or None


def _extract_channel(recording: dict) -> tuple[str, str | None]:
    """Group recordings by meeting_type so the dashboard filter is meaningful."""
    meeting_type = recording.get("meeting_type") or {}
    name = meeting_type.get("name") if isinstance(meeting_type, dict) else None
    if name:
        channel_id = name.lower().replace(" ", "_")
        return channel_id, name
    return "uncategorized", None


def _format_event_text(recording: dict, transcript: str | None) -> str:
    """Build a self-contained text blob: header + transcript."""
    title = recording.get("title") or "Untitled meeting"
    parts: list[str] = [title, ""]

    participants = recording.get("participants") or []
    names = [p.get("name") for p in participants if p.get("name") and p.get("confirmed_attendee", True)]
    if names:
        parts.append(f"Participants: {', '.join(names)}")

    duration_ms = recording.get("duration_ms")
    if duration_ms:
        parts.append(f"Duration: {round(duration_ms / 60000, 1)} min")

    url = recording.get("url")
    if url:
        parts.append(f"URL: {url}")

    ai_summary = recording.get("ai_summary") or {}
    summary_text = ai_summary.get("text") if isinstance(ai_summary, dict) else None
    if summary_text:
        parts.append("")
        parts.append("Summary:")
        parts.append(summary_text.strip())

    action_items = recording.get("ai_action_items") or []
    if action_items:
        parts.append("")
        parts.append("Action items:")
        for item in action_items:
            text = (item.get("text") or "").strip()
            if not text:
                continue
            status = item.get("status") or ""
            assignee = ((item.get("assignee") or {}).get("name") or "").strip()
            suffix = []
            if status:
                suffix.append(f"[{status}]")
            if assignee:
                suffix.append(f"\u2014 {assignee}")
            parts.append(f"- {text} {' '.join(suffix)}".rstrip())

    if transcript:
        parts.append("")
        parts.append("Transcript:")
        parts.append(transcript.strip())

    return "\n".join(parts).strip()


def normalize_grain_event(
    webhook_body: dict, recording: dict, transcript: str | None
) -> dict | None:
    recording_id = recording.get("id")
    if not recording_id:
        return None

    channel_id, channel_name = _extract_channel(recording)
    user_id, user_name = _extract_user(recording)
    text = _format_event_text(recording, transcript)

    return {
        "event_id": str(uuid.uuid4()),
        "source": "grain",
        "source_event_id": f"grain:{recording_id}",
        "event_type": "recording",
        "event_subtype": recording.get("source") or None,
        "timestamp": _parse_timestamp(recording),
        "ingested_at": datetime.now(timezone.utc),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "thread_ts": None,
        "thread_refs": {"grain_recording_id": [recording_id]},
        "user_id": user_id,
        "user_name": user_name,
        "is_bot": False,
        "text": text,
        "content_hash": _content_hash(text),
        "files": [],
        "reaction": None,
        "reaction_target_ts": None,
        "entities": [],
        "embedding": None,
        "raw_event": {
            "webhook_body": webhook_body,
            "recording": recording,
            "transcript": transcript,
        },
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
            "[GRAIN-INGESTION] Failed to store event: %s",
            normalized.get("source_event_id"),
        )


async def ingest_grain_webhook(webhook_body: dict, recording_id: str) -> None:
    """Fetch transcript (+ best-effort metadata) from Grain and store as a change-stream event."""
    try:
        transcript_resp = await get_transcript(recording_id, fmt="text")
        transcript_text: str | None = None
        if "error" in transcript_resp:
            logger.warning(
                "[GRAIN-INGESTION] transcript fetch failed recording_id=%s error=%s",
                recording_id, transcript_resp["error"],
            )
        else:
            transcript_text = transcript_resp.get("transcript")

        # Best-effort metadata lookup. If it fails we still store the event
        # with the transcript and a minimal header.
        metadata = await find_recording_by_id(recording_id, days=2)
        if metadata is None:
            logger.warning(
                "[GRAIN-INGESTION] metadata not found for recording_id=%s — storing with minimal header",
                recording_id,
            )
            metadata = {"id": recording_id, "title": "Untitled meeting"}

        normalized = normalize_grain_event(webhook_body, metadata, transcript_text)
        if normalized is None:
            logger.warning("[GRAIN-INGESTION] normalize returned None recording_id=%s", recording_id)
            return

        await _store_event(normalized)
        logger.info(
            "[GRAIN-INGESTION] stored event recording=%s title=%r transcript_chars=%d",
            recording_id,
            metadata.get("title"),
            len(transcript_text or ""),
        )
    except Exception:
        logger.exception("[GRAIN-INGESTION] Failed to ingest recording_id=%s", recording_id)
