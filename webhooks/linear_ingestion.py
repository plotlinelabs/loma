"""
Linear Event Ingestion (Pass 1)

Normalizes Linear webhook events into the unified event schema and stores
them in MongoDB. Called as fire-and-forget from the existing webhook handler.
"""

import difflib
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from observability.db import get_db

logger = logging.getLogger(__name__)

# Action → past tense for subtype
_ACTION_PAST_TENSE = {
    "create": "created",
    "update": "updated",
    "remove": "removed",
}


def _content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_timestamp(body: dict) -> datetime:
    """Convert Linear's webhookTimestamp (milliseconds) to datetime."""
    ts_ms = body.get("webhookTimestamp")
    if ts_ms:
        try:
            return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass
    return datetime.now(timezone.utc)


# Linear numeric priority → human label
_PRIORITY_LABELS = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}

# Fields whose changes are pure noise and should not show up in the summary.
_NOISE_FIELDS = {
    "sortOrder", "boardOrder", "subIssueSortOrder", "updatedAt",
    "prioritySortOrder", "slaType", "triagedAt", "slaStartedAt",
    "slaMediumRiskAt", "slaHighRiskAt", "slaBreachesAt",
    "addedLabelIds", "removedLabelIds",
}

# Long-text fields — flag as edited rather than dumping old/new content.
_EDIT_FLAG_FIELDS = {"description", "content"}

# Relation fields: (display label, resolver that returns the NEW value from data).
# We usually don't have the old entity name, only its ID in `updatedFrom`.
_RELATION_FIELDS: dict[str, tuple[str, object]] = {
    "stateId": ("state", lambda d: (d.get("state") or {}).get("name") or "none"),
    "assigneeId": ("assignee", lambda d: (d.get("assignee") or {}).get("name") or "unassigned"),
    "creatorId": ("creator", lambda d: (d.get("creator") or {}).get("name") or "none"),
    "projectId": ("project", lambda d: (d.get("project") or {}).get("name") or "none"),
    "cycleId": ("cycle", lambda d: (d.get("cycle") or {}).get("name")
                or (f"#{(d.get('cycle') or {}).get('number')}" if d.get("cycle") else "none")),
    "parentId": ("parent", lambda d: (d.get("parent") or {}).get("identifier") or "none"),
    "leadId": ("lead", lambda d: (d.get("lead") or {}).get("name") or "none"),
    "labelIds": ("labels", lambda d: ", ".join(
        l.get("name", "") for l in (d.get("labels") or []) if l.get("name")
    ) or "none"),
}

# Scalar fields: (display label, value formatter).
_SCALAR_FIELDS: dict[str, tuple[str, object]] = {
    "priority": ("priority", lambda v: _PRIORITY_LABELS.get(v, str(v)) if v is not None else "none"),
    "title": ("title", lambda v: str(v) if v else "(empty)"),
    "name": ("name", lambda v: str(v) if v else "(empty)"),
    "dueDate": ("due date", lambda v: str(v) if v else "none"),
    "estimate": ("estimate", lambda v: str(v) if v is not None else "none"),
    "trashed": ("trashed", lambda v: "yes" if v else "no"),
    "startedAt": ("started at", lambda v: str(v) if v else "none"),
    "completedAt": ("completed at", lambda v: str(v) if v else "none"),
    "canceledAt": ("canceled at", lambda v: str(v) if v else "none"),
    "archivedAt": ("archived at", lambda v: str(v) if v else "none"),
    "targetDate": ("target date", lambda v: str(v) if v else "none"),
}


def _diff_text(old: str | None, new: str | None, max_lines: int = 40) -> str:
    """Return a compact unified diff between two text blobs."""
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    diff = list(
        difflib.unified_diff(old_lines, new_lines, lineterm="", n=2)
    )
    # Drop the "--- " / "+++ " file header lines difflib emits by default.
    if len(diff) >= 2 and diff[0].startswith("---") and diff[1].startswith("+++"):
        diff = diff[2:]
    if not diff:
        return "(no textual change)"
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... ({len(diff) - max_lines} more lines)"]
    return "\n".join(diff)


def _summarize_update(data: dict, updated_from: dict) -> str | None:
    """Build a compact, human-readable diff summary from Linear's updatedFrom map."""
    if not updated_from:
        return None
    lines: list[str] = []
    for key in updated_from.keys():
        if key in _NOISE_FIELDS:
            continue
        if key in _EDIT_FLAG_FIELDS:
            old_val = updated_from.get(key)
            new_val = data.get(key)
            diff = _diff_text(old_val, new_val)
            indented = "\n".join("  " + line for line in diff.splitlines())
            lines.append(f"{key} edited:\n{indented}")
            continue
        if key in _RELATION_FIELDS:
            label, resolver = _RELATION_FIELDS[key]
            lines.append(f"{label} → {resolver(data)}")  # type: ignore[operator]
            continue
        if key in _SCALAR_FIELDS:
            label, fmt = _SCALAR_FIELDS[key]
            old = updated_from.get(key)
            new = data.get(key)
            lines.append(f"{label}: {fmt(old)} → {fmt(new)}")  # type: ignore[operator]
            continue
        # Unknown field — at least signal that something changed without dumping.
        lines.append(f"{key} changed")
    return "\n".join(lines) if lines else None


def _extract_text(body: dict) -> str | None:
    """Extract meaningful text content from a Linear webhook body.

    For update events, produces a compact diff summary (driven by ``updatedFrom``)
    instead of re-storing the full current entity body.
    """
    event_type = body.get("type", "")
    action = body.get("action", "")
    data = body.get("data", {}) or {}
    updated_from = body.get("updatedFrom") or {}

    if event_type == "Issue":
        if action == "update":
            summary = _summarize_update(data, updated_from)
            if summary:
                identifier = data.get("identifier", "")
                title = data.get("title", "")
                header = f"{identifier} {title}".strip()
                return f"{header}\n{summary}" if header else summary
            # No recognized field changed — fall through.
        title = data.get("title", "")
        description = data.get("description") or ""
        if action == "remove":
            return title or None
        return f"{title}\n\n{description}".strip() if title else description or None

    if event_type == "Comment":
        return data.get("body")

    if event_type == "Project":
        if action == "update":
            summary = _summarize_update(data, updated_from)
            if summary:
                name = data.get("name", "")
                return f"{name}\n{summary}" if name else summary
        name = data.get("name", "")
        description = data.get("description") or ""
        return f"{name}\n\n{description}".strip() if name else description or None

    if event_type == "Cycle":
        if action == "update":
            summary = _summarize_update(data, updated_from)
            if summary:
                name = data.get("name") or ""
                return f"{name}\n{summary}" if name else summary
        name = data.get("name") or ""
        description = data.get("description") or ""
        return f"{name}\n\n{description}".strip() if name or description else None

    return None


def _extract_thread_ref(event_type: str, data: dict) -> str | None:
    """Extract a thread reference for grouping related events."""
    if event_type == "Issue":
        return data.get("identifier")  # e.g., "GO-123"

    if event_type == "Comment":
        issue = data.get("issue", {})
        return issue.get("identifier")  # parent issue identifier

    if event_type == "Project":
        return f"PROJECT:{data.get('id', '')}"

    if event_type == "Cycle":
        return f"CYCLE:{data.get('id', '')}"

    return None


def _extract_user(data: dict) -> tuple[str | None, str | None]:
    """Extract user ID and name from the data payload."""
    # Comments have a user field directly
    user = data.get("user") or data.get("actor") or {}
    if isinstance(user, dict):
        return user.get("id"), user.get("name")
    return None, None


def _extract_channel(event_type: str, data: dict) -> tuple[str, str | None]:
    """Extract team key (channel_id) and team name (channel_name)."""
    team = data.get("team") or {}
    if not team and event_type == "Comment":
        team = data.get("issue", {}).get("team") or {}

    if isinstance(team, dict) and team:
        return team.get("key", ""), team.get("name")

    return "", None


def normalize_linear_event(body: dict) -> dict | None:
    """Normalize a Linear webhook payload into the unified event schema."""
    event_type = body.get("type", "")
    action = body.get("action", "")
    data = body.get("data", {})
    webhook_ts = body.get("webhookTimestamp", "")

    if not event_type or not data:
        return None

    # Build source_event_id from available fields
    data_id = data.get("id", "")
    source_event_id = f"linear:{event_type}:{data_id}:{action}:{webhook_ts}"

    # Normalize type and subtype
    normalized_type = event_type.lower()  # "Issue" → "issue", "Comment" → "comment"
    normalized_subtype = _ACTION_PAST_TENSE.get(action, action)

    # Extract fields
    channel_id, channel_name = _extract_channel(event_type, data)
    user_id, user_name = _extract_user(data)
    text = _extract_text(body)

    return {
        "event_id": str(uuid.uuid4()),
        "source": "linear",
        "source_event_id": source_event_id,
        "event_type": normalized_type,
        "event_subtype": normalized_subtype,
        "timestamp": _parse_timestamp(body),
        "ingested_at": datetime.now(timezone.utc),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "thread_ts": _extract_thread_ref(event_type, data),
        "thread_refs": {
            k: [v] for k, v in {
                "linear_issue": _extract_thread_ref(event_type, data),
            }.items() if v
        },
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
        "raw_event": body,
        "processed": False,
        "processing_version": 1,
    }


async def _store_event(normalized: dict) -> None:
    """Upsert a normalized event into the changestreams collection."""
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
        logger.exception("[LINEAR-INGESTION] Failed to store event: %s", normalized.get("source_event_id"))


async def ingest_linear_event(body: dict) -> None:
    """Normalize and store a Linear webhook event. Safe for fire-and-forget."""
    try:
        normalized = normalize_linear_event(body)
        if normalized is None:
            return
        await _store_event(normalized)
        logger.debug("[LINEAR-INGESTION] Stored %s event: %s", normalized["event_type"], normalized["source_event_id"])
    except Exception:
        logger.exception("[LINEAR-INGESTION] Failed to ingest event")
