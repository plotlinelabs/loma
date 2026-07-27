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
        return value
    try:
        return datetime.fromisoformat(str(value).replace(" UTC", "+00:00").replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _interaction(source, tenant_id, source_id, occurred_at, summary, url=None, direction="internal"):
    return {
        "source": source, "tenant_id": tenant_id, "source_id": str(source_id),
        "source_url": url, "occurred_at": _parse_timestamp(occurred_at),
        "direction": direction, "classification": None, "requires_response": False,
        "meaningful_contact": True, "conversation_state": "monitoring",
        "summary": (summary or "Activity imported from connected source")[:1000],
        "confidence": 1.0, "classifier_version": "connector-v1",
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
    return [
        _interaction("slack", result.get("channel_id") or mapping["tenant_id"],
                     f'{result.get("channel_id", mapping["external_id"])}:{msg.get("timestamp", index)}',
                     msg.get("timestamp"), f'{msg.get("user", "Unknown")}: {msg.get("text", "")}',
                     config.get("source_url"))
        for index, msg in enumerate(result.get("messages", [])) if msg.get("text")
    ]


async def _grain(mapping, _actor):
    from tools.grain import search_recordings
    result = await search_recordings(mapping["external_id"])
    if result.get("error"):
        raise SyncError(result["error"])
    return [
        _interaction("grain", mapping["tenant_id"], row.get("id"),
                     row.get("date") or row.get("created_at"),
                     row.get("title") or "Grain meeting", row.get("url"))
        for row in result.get("recordings", []) if row.get("id")
    ]


async def _pylon(mapping, _actor):
    from tools.pylon import get_issue, get_messages
    issue, messages = await asyncio.gather(get_issue(mapping["external_id"]), get_messages(mapping["external_id"]))
    for result in (issue, messages):
        if result.get("error"):
            raise SyncError(result["error"])
    rows = messages.get("data") or messages.get("messages") or []
    if isinstance(rows, dict):
        rows = rows.get("data", [])
    return [
        _interaction("pylon", mapping["tenant_id"], row.get("id"),
                     row.get("created_at") or row.get("timestamp"),
                     row.get("body_html") or row.get("body") or row.get("text") or "Pylon message",
                     row.get("url"), "customer_to_plotline" if row.get("source") == "customer" else "internal")
        for row in rows if row.get("id")
    ]


READERS = {"slack": _slack, "grain": _grain, "pylon": _pylon}


async def pull(mapping, actor):
    reader = READERS.get(mapping.get("source"))
    if not reader:
        raise SyncError(f'Read-only sync is not available for {mapping.get("source")} yet')
    return await reader(mapping, actor)
