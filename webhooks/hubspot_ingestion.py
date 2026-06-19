"""
HubSpot Event Ingestion

Normalizes batched HubSpot webhook subscription events into the unified event
schema and stores them in the ``changestreams`` collection. Each event only
contains an objectId, so we make follow-up API calls to fetch full object
details and deal associations.

Supported subscription types:
  deal.creation, deal.propertyChange, deal.deletion,
  conversation.creation, conversation.newMessage,
  email.creation, note.creation, call.creation
"""

import hashlib
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import aiohttp

from observability.db import get_db

logger = logging.getLogger(__name__)

_HUBSPOT_API = "https://api.hubapi.com"

# ── Pipeline / stage / owner label caches (refreshed every 10 min) ──────

_pipeline_cache: dict = {}  # {pipeline_id: {"label": str, "stages": {stage_id: label}}}
_pipeline_cache_ts: float = 0.0
_owner_cache: dict = {}  # {owner_id: "Name"}
_owner_cache_ts: float = 0.0
_CACHE_TTL = 600  # 10 minutes

# Properties to fetch per object type.
_OBJECT_PROPERTIES: dict[str, str] = {
    "deals": "dealname,dealstage,pipeline,amount,closedate,hubspot_owner_id",
    "emails": "hs_email_subject,hs_email_text,hs_email_from,hs_email_to_email",
    "notes": "hs_note_body,hs_timestamp",
    "calls": "hs_call_body,hs_call_duration,hs_call_disposition,hs_call_title",
}

# Map subscriptionType prefix → HubSpot CRM object type (plural, for API paths).
_OBJECT_TYPE_MAP: dict[str, str] = {
    "deal": "deals",
    "email": "emails",
    "note": "notes",
    "call": "calls",
    "conversation": "conversations",
}


def _content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_token() -> str | None:
    return (os.environ.get("HUBSPOT_ACCESS_TOKEN") or "").strip() or None


async def _hubspot_get(path: str, params: dict | None = None) -> dict | None:
    """Thin async GET wrapper for the HubSpot API."""
    token = _get_token()
    if not token:
        return None
    url = f"{_HUBSPOT_API}{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                body_text = (await resp.text())[:200]
                logger.warning("[HUBSPOT-INGESTION] GET %s → %s body=%s", path, resp.status, body_text)
                return None
    except Exception:
        logger.exception("[HUBSPOT-INGESTION] GET %s failed", path)
        return None


async def _load_pipelines() -> None:
    """Fetch all deal pipelines + stages and populate the cache."""
    global _pipeline_cache, _pipeline_cache_ts
    data = await _hubspot_get("/crm/v3/pipelines/deals")
    if not data:
        return
    cache: dict = {}
    for pipeline in data.get("results") or []:
        pid = str(pipeline.get("id", ""))
        stages: dict[str, str] = {}
        for stage in pipeline.get("stages") or []:
            stages[str(stage.get("id", ""))] = stage.get("label", str(stage.get("id", "")))
        cache[pid] = {"label": pipeline.get("label", pid), "stages": stages}
    _pipeline_cache = cache
    _pipeline_cache_ts = time.monotonic()
    logger.debug("[HUBSPOT-INGESTION] loaded %d pipelines", len(cache))


async def _resolve_pipeline(pipeline_id: str | None) -> str:
    """Return human-readable pipeline name, or the raw id."""
    if not pipeline_id:
        return "HubSpot"
    global _pipeline_cache_ts
    if not _pipeline_cache or (time.monotonic() - _pipeline_cache_ts > _CACHE_TTL):
        await _load_pipelines()
    entry = _pipeline_cache.get(str(pipeline_id))
    return entry["label"] if entry else str(pipeline_id)


async def _resolve_stage(pipeline_id: str | None, stage_id: str | None) -> str:
    """Return human-readable stage label, or the raw id."""
    if not stage_id:
        return ""
    if not _pipeline_cache or (time.monotonic() - _pipeline_cache_ts > _CACHE_TTL):
        await _load_pipelines()
    entry = _pipeline_cache.get(str(pipeline_id or ""))
    if entry:
        return entry["stages"].get(str(stage_id), str(stage_id))
    # Try across all pipelines if pipeline_id unknown.
    for p in _pipeline_cache.values():
        if str(stage_id) in p["stages"]:
            return p["stages"][str(stage_id)]
    return str(stage_id)


async def _load_owners() -> None:
    """Fetch all owners and populate the cache."""
    global _owner_cache, _owner_cache_ts
    data = await _hubspot_get("/crm/v3/owners", {"limit": "500"})
    if not data:
        return
    cache: dict = {}
    for owner in data.get("results") or []:
        oid = str(owner.get("id", ""))
        name_parts = [owner.get("firstName") or "", owner.get("lastName") or ""]
        name = " ".join(p for p in name_parts if p).strip()
        email = owner.get("email") or ""
        cache[oid] = name or email or oid
    _owner_cache = cache
    _owner_cache_ts = time.monotonic()
    logger.debug("[HUBSPOT-INGESTION] loaded %d owners", len(cache))


async def _resolve_owner(owner_id: str | None) -> str:
    """Return owner name/email, or the raw id."""
    if not owner_id:
        return ""
    global _owner_cache_ts
    if not _owner_cache or (time.monotonic() - _owner_cache_ts > _CACHE_TTL):
        await _load_owners()
    return _owner_cache.get(str(owner_id), str(owner_id))


async def _fetch_object(object_type: str, object_id: int | str) -> dict | None:
    """Fetch CRM object properties."""
    props = _OBJECT_PROPERTIES.get(object_type)
    params = {"properties": props} if props else None
    return await _hubspot_get(f"/crm/v3/objects/{object_type}/{object_id}", params)


async def _fetch_deal_association(object_type: str, object_id: int | str) -> str | None:
    """Return the first associated deal ID for a non-deal object, or None."""
    data = await _hubspot_get(f"/crm/v4/objects/{object_type}/{object_id}/associations/deals")
    if not data:
        return None
    results = data.get("results") or []
    if results:
        return str(results[0].get("toObjectId", ""))
    return None


async def _fetch_deal(deal_id: str | int) -> dict | None:
    return await _fetch_object("deals", deal_id)


# ── Event type parsing ──────────────────────────────────────────────────

_SUBTYPE_MAP: dict[str, str] = {
    "creation": "created",
    "propertyChange": "property_change",
    "deletion": "deleted",
    "newMessage": "new_message",
}


def _parse_event_type(subscription_type: str) -> tuple[str, str]:
    """Parse 'deal.propertyChange' → ('deal', 'property_change')."""
    parts = subscription_type.split(".", 1)
    event_type = parts[0] if parts else "unknown"
    raw_subtype = parts[1] if len(parts) > 1 else "unknown"
    return event_type, _SUBTYPE_MAP.get(raw_subtype, raw_subtype)


# ── Text formatting ─────────────────────────────────────────────────────

def _deal_props(deal: dict | None) -> dict:
    """Extract deal properties dict, handling nested HubSpot response shape."""
    if not deal:
        return {}
    return deal.get("properties") or deal


async def _format_deal_header(deal: dict | None) -> str:
    props = _deal_props(deal)
    name = props.get("dealname") or f"deal #{props.get('hs_object_id', '?')}"
    parts = [name]
    pipeline_id = props.get("pipeline")
    stage_id = props.get("dealstage")
    pipeline_label = await _resolve_pipeline(pipeline_id) if pipeline_id else ""
    stage_label = await _resolve_stage(pipeline_id, stage_id) if stage_id else ""
    if pipeline_label or stage_label:
        detail = " | ".join(filter(None, [f"Pipeline: {pipeline_label}" if pipeline_label else "", f"Stage: {stage_label}" if stage_label else ""]))
        parts.append(detail)
    amount = props.get("amount")
    closedate = props.get("closedate")
    if amount or closedate:
        detail = " | ".join(filter(None, [f"Amount: ${amount}" if amount else "", f"Close date: {closedate}" if closedate else ""]))
        parts.append(detail)
    owner_id = props.get("hubspot_owner_id")
    if owner_id:
        owner_name = await _resolve_owner(owner_id)
        parts.append(f"Owner: {owner_name}")
    return "\n".join(parts)


async def _format_event_text(event: dict, object_data: dict | None, deal_data: dict | None) -> str:
    event_type, subtype = _parse_event_type(event.get("subscriptionType", ""))
    deal_name = _deal_props(deal_data).get("dealname") or ""

    if event_type == "deal":
        if subtype == "created":
            return f"[New Deal] {await _format_deal_header(object_data)}"
        if subtype == "deleted":
            return f"[Deal Deleted] {deal_name or 'deal #' + str(event.get('objectId', '?'))}"
        if subtype == "property_change":
            prop = event.get("propertyName", "")
            val = event.get("propertyValue", "")
            # Resolve stage/pipeline IDs to labels.
            if prop == "dealstage":
                pipeline_id = _deal_props(object_data).get("pipeline") if object_data else None
                val = await _resolve_stage(pipeline_id, val)
            elif prop == "pipeline":
                val = await _resolve_pipeline(val)
            elif prop == "hubspot_owner_id":
                val = await _resolve_owner(val)
            header = deal_name or f"deal #{event.get('objectId', '?')}"
            return f"[Deal Updated] {header}\n{prop}: → {val}"

    deal_ctx = f" on deal: {deal_name}" if deal_name else ""

    if event_type == "email":
        props = (object_data or {}).get("properties") or {}
        subject = props.get("hs_email_subject") or ""
        frm = props.get("hs_email_from") or ""
        to = props.get("hs_email_to_email") or ""
        body = props.get("hs_email_text") or ""
        lines = [f"[Email]{deal_ctx}"]
        if subject:
            lines.append(f"Subject: {subject}")
        if frm or to:
            lines.append(f"From: {frm} → {to}")
        if body:
            lines.append("")
            lines.append(body[:2000])
        return "\n".join(lines)

    if event_type == "note":
        props = (object_data or {}).get("properties") or {}
        body = props.get("hs_note_body") or ""
        lines = [f"[Note]{deal_ctx}"]
        if body:
            lines.append("")
            lines.append(body[:2000])
        return "\n".join(lines)

    if event_type == "call":
        props = (object_data or {}).get("properties") or {}
        title = props.get("hs_call_title") or ""
        duration = props.get("hs_call_duration") or ""
        disposition = props.get("hs_call_disposition") or ""
        body = props.get("hs_call_body") or ""
        lines = [f"[Call]{deal_ctx}"]
        if title:
            lines.append(title)
        detail = " | ".join(filter(None, [
            f"Duration: {duration}ms" if duration else "",
            f"Outcome: {disposition}" if disposition else "",
        ]))
        if detail:
            lines.append(detail)
        if body:
            lines.append("")
            lines.append(body[:2000])
        return "\n".join(lines)

    if event_type == "conversation":
        lines = [f"[Conversation]{deal_ctx}"]
        lines.append(f"{subtype.replace('_', ' ').title()} — conversation #{event.get('objectId', '?')}")
        return "\n".join(lines)

    # Fallback for unknown types
    return f"[{event_type}]{deal_ctx}\n{event.get('subscriptionType', '')}: object #{event.get('objectId', '?')}"


# ── Normalization + storage ─────────────────────────────────────────────

async def normalize_hubspot_event(event: dict, object_data: dict | None, deal_data: dict | None) -> dict:
    event_type, subtype = _parse_event_type(event.get("subscriptionType", ""))
    text = await _format_event_text(event, object_data, deal_data)

    deal_props = _deal_props(deal_data)
    deal_id = str(event["objectId"]) if event_type == "deal" else (
        str(deal_data.get("id", "")) if deal_data else str(event.get("objectId", ""))
    )

    occurred_at = event.get("occurredAt")
    if occurred_at:
        timestamp = datetime.fromtimestamp(occurred_at / 1000, tz=timezone.utc)
    else:
        timestamp = datetime.now(timezone.utc)

    pipeline_id = deal_props.get("pipeline")
    pipeline_label = await _resolve_pipeline(pipeline_id) if pipeline_id else "HubSpot"

    return {
        "event_id": str(uuid.uuid4()),
        "source": "hubspot",
        "source_event_id": f"hubspot:{event.get('eventId', uuid.uuid4())}",
        "event_type": event_type,
        "event_subtype": subtype,
        "timestamp": timestamp,
        "ingested_at": datetime.now(timezone.utc),
        "channel_id": pipeline_label.lower().replace(" ", "_"),
        "channel_name": pipeline_label,
        "thread_ts": deal_id,
        "thread_refs": {"hubspot_deal_id": [deal_id]},
        "user_id": event.get("sourceId") or None,
        "user_name": None,
        "is_bot": (event.get("changeSource") or "") in ("INTEGRATION", "API"),
        "text": text,
        "content_hash": _content_hash(text),
        "files": [],
        "reaction": None,
        "reaction_target_ts": None,
        "entities": [],
        "embedding": None,
        "raw_event": {
            "webhook_event": event,
            "object": object_data,
            "deal": deal_data,
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
            "[HUBSPOT-INGESTION] Failed to store event: %s",
            normalized.get("source_event_id"),
        )


async def ingest_hubspot_events(events: list[dict]) -> None:
    """Process a batch of HubSpot webhook events. Safe for fire-and-forget."""
    stored = 0
    for event in events:
        try:
            sub_type = event.get("subscriptionType", "")
            event_type, _ = _parse_event_type(sub_type)
            object_id = event.get("objectId")
            if not object_id:
                continue

            crm_type = _OBJECT_TYPE_MAP.get(event_type)
            object_data: dict | None = None
            deal_data: dict | None = None

            # Fetch object details (skip for deletions — object may be gone).
            if "deletion" not in sub_type and crm_type and crm_type != "conversations":
                object_data = await _fetch_object(crm_type, object_id)

            # For deal events, object IS the deal.
            if event_type == "deal":
                deal_data = object_data or await _fetch_deal(object_id)
            else:
                # For non-deal events, look up associated deal.
                if crm_type and crm_type != "conversations":
                    deal_id = await _fetch_deal_association(crm_type, object_id)
                    if deal_id:
                        deal_data = await _fetch_deal(deal_id)

            normalized = await normalize_hubspot_event(event, object_data, deal_data)
            await _store_event(normalized)
            stored += 1
        except Exception:
            logger.exception(
                "[HUBSPOT-INGESTION] Failed to process event eventId=%s",
                event.get("eventId"),
            )

    logger.info(
        "[HUBSPOT-INGESTION] processed=%d stored=%d",
        len(events), stored,
    )
