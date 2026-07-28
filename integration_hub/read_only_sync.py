"""Pull-only adapters for Integration Hub communication monitoring.

This module intentionally exposes no write operation. It reuses the same credentials as
Loma's existing integrations, but is isolated from agent execution and webhook responders.
"""
import asyncio
from datetime import datetime, timezone
from functools import partial


class SyncError(RuntimeError):
    pass


def _parse_timestamp(value):
    if not value:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    try:
        raw = str(value).strip()
        if raw.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(raw), timezone.utc)
        parsed = datetime.fromisoformat(raw.replace(" UTC", "+00:00").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return datetime.now(timezone.utc)


def _checkpoint(mapping):
    value = (mapping.get("checkpoint") or {}).get("last_occurred_at")
    return _parse_timestamp(value) if value else None


def _analyse(summary, direction):
    text = (summary or "").lower()
    issue = any(word in text for word in (
        "error", "fail", "broken", "block", "issue", "unable", "not working",
    ))
    question = "?" in text or any(word in text for word in ("help", "how do", "can you"))
    requires_response = direction == "inbound" and (issue or question)
    return {
        "classification": "reported_issue" if issue else
                          ("customer_question" if question else "update"),
        "requires_response": requires_response,
        "conversation_state": "waiting_on_us" if requires_response else "monitoring",
        "confidence": 0.65 if (issue or question) else 0.5,
    }


def _interaction(source, tenant_id, source_id, occurred_at, summary, url=None,
                 direction="internal", conversation_id=None, raw=None):
    analysis = _analyse(summary, direction)
    return {
        "source": source, "tenant_id": tenant_id, "source_id": str(source_id),
        "source_url": url, "occurred_at": _parse_timestamp(occurred_at),
        "direction": direction, **analysis,
        "meaningful_contact": True,
        "summary": (summary or "Activity imported from connected source")[:1000],
        "classifier_version": "rules-v2", "conversation_id": conversation_id,
        "evidence": {"source_id": str(source_id), "excerpt": (summary or "")[:500]},
        "raw": raw or {},
    }


async def _slack(mapping, _actor):
    # The existing bot token is reused. Only conversations.history/replies are called.
    from tools.slack_reader import read_history
    config = mapping.get("config", {})
    result = await asyncio.to_thread(
        partial(read_history, mapping["external_id"], limit=min(int(config.get("limit", 50)), 200),
                thread_ts=config.get("thread_ts"))
    )
    if result.get("error"):
        raise SyncError(result["error"])
    customer_ids = set(config.get("customer_user_ids", []))
    internal_ids = set(config.get("internal_user_ids", []))
    checkpoint = _checkpoint(mapping)
    rows = [
        _interaction("slack", result.get("channel_id") or mapping["tenant_id"],
                     f'{result.get("channel_id", mapping["external_id"])}:{msg.get("ts") or msg.get("client_msg_id")}',
                     msg.get("ts") or msg.get("timestamp"),
                     f'{msg.get("user", "Unknown")}: {msg.get("text", "")}',
                     config.get("source_url"),
                     "inbound" if msg.get("user_id") in customer_ids else
                     ("outbound" if msg.get("user_id") in internal_ids else "internal"),
                     msg.get("thread_ts") or config.get("thread_ts") or msg.get("ts"),
                     msg)
        for index, msg in enumerate(result.get("messages", [])) if msg.get("text")
    ]
    return [row for row in rows if not checkpoint or row["occurred_at"] >= checkpoint]


async def _grain(mapping, _actor):
    from tools.grain import discover_client_recordings, find_recording_by_id, get_transcript
    explicit_ids = mapping.get("config", {}).get("recording_ids", [])
    if explicit_ids:
        found = await asyncio.gather(*(find_recording_by_id(item, days=3650) for item in explicit_ids))
        rows = [row for row in found if row]
    else:
        config = mapping.get("config", {})
        result = await discover_client_recordings(
            config.get("customer_name") or mapping.get("label") or mapping["external_id"],
            config.get("customer_emails") or [],
        )
        if result.get("error"):
            raise SyncError(result["error"])
        rows = result.get("recordings", [])
    interactions = []
    for row in rows:
        recording_id = row.get("id")
        transcript = await get_transcript(recording_id, "text")
        transcript_text = "" if transcript.get("error") else transcript.get("transcript", "")
        summary = row.get("ai_summary") or row.get("title") or "Grain meeting"
        if row.get("action_items"):
            summary += " Actions: " + "; ".join(item.get("text", "") for item in row["action_items"])
        interactions.append(
        _interaction("grain", mapping["tenant_id"], row.get("id"),
                     row.get("date") or row.get("created_at"),
                     summary, row.get("url"), "internal", recording_id, {
                         "participants": row.get("participants", []),
                         "action_items": row.get("action_items", []),
                         "transcript_excerpt": transcript_text[:5000],
                     })
        )
    return interactions


async def _pylon(mapping, _actor):
    from tools.pylon import get_issue, get_messages, list_account_issues_page, normalize_message
    config = mapping.get("config", {})
    issue_ids = list(config.get("issue_ids", []))
    if not issue_ids:
        if not mapping.get("external_id"):
            raise SyncError("Pylon customer mapping is missing")
        result = await list_account_issues_page(mapping["external_id"], limit=100)
        if result.get("error"):
            raise SyncError(result["error"])
        issue_ids = [row["id"] for row in result.get("issues", [])]
    checkpoint = _checkpoint(mapping)
    interactions = []
    for issue_id in issue_ids[:500]:
        issue, messages = await asyncio.gather(get_issue(issue_id), get_messages(issue_id))
        for result in (issue, messages):
            if result.get("error"):
                raise SyncError(result["error"])
        rows = messages.get("data") or messages.get("messages") or []
        if isinstance(rows, dict):
            rows = rows.get("data", [])
        issue_data = issue.get("data") or issue
        conversation_id = str(issue_data.get("id") or issue_id)
        issue_state = str(issue_data.get("state") or issue_data.get("status") or "").lower()
        for row in rows:
            if not row.get("id"):
                continue
            normalized = normalize_message(row)
            if normalized.get("is_private"):
                continue
            direction = (
                "inbound"
                if row.get("source") in {"customer", "email", "slack_customer"}
                or row.get("sender_type") in {"customer", "contact"}
                else "outbound"
            )
            item = _interaction(
                "pylon", mapping["tenant_id"], row["id"],
                row.get("created_at") or row.get("timestamp"),
                normalized.get("body") or "Support message",
                row.get("url") or issue_data.get("url"), direction, conversation_id, {
                    "issue_id": conversation_id, "issue_title": issue_data.get("title"),
                    "issue_status": issue_state, "assignee": issue_data.get("assignee"),
                    "customer": issue_data.get("account"), "message": row,
                },
            )
            if issue_state in {"closed", "resolved"}:
                item.update(requires_response=False, conversation_state="resolved")
            elif direction == "inbound":
                item.update(requires_response=True, conversation_state="waiting_on_us")
            else:
                item.update(requires_response=False, conversation_state="waiting_on_customer")
            interactions.append(item)
    interactions.sort(key=lambda row: row["occurred_at"])
    return [row for row in interactions
            if not checkpoint or row["occurred_at"] >= checkpoint]


async def discover_pylon_customers(query):
    """Discover Pylon customers without exposing any write operation."""
    from tools.pylon import search_accounts
    result = await search_accounts(query, limit=50)
    if result.get("error"):
        raise SyncError(result["error"])
    return [{
        "customer_id": account["customer_id"],
        "name": account["name"],
        "domains": account.get("domains", []),
        "issue_count": None,
        "preview_issues": [],
    } for account in result.get("accounts", [])]


READERS = {"slack": _slack, "grain": _grain, "pylon": _pylon}


async def pull(mapping, actor):
    reader = READERS.get(mapping.get("source"))
    if not reader:
        raise SyncError(f'Read-only sync is not available for {mapping.get("source")} yet')
    return await reader(mapping, actor)
