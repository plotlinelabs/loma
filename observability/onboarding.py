"""Onboarding tracker: one record per customer app integration.

Collections:
  onboarding_records  - one doc per app/engagement, grouped by `account`
  onboarding_events   - append-only change log (who/what changed which field)
  onboarding_settings - optional `{_id: "config"}` doc overriding DEFAULT_CONFIG

Every field write records its source (`human` for dashboard edits, or a
connector name such as `hubspot` / `mongodb` for agent syncs). An automated
write never overwrites a value a human set unless `force=True`.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

HUMAN = "human"
STALE_DAYS = 7

_MODULES = ["Basic SDK", "FE events", "Backend API", "PN", "Widgets", "Elements",
            "Event listener", "Redirect listener", "Deeplinks"]

DEFAULT_CONFIG: dict[str, Any] = {
    "stages": [
        {"key": "signed", "label": "Signed - Not Started"},
        {"key": "kickoff", "label": "Kickoff Done"},
        {"key": "sdk", "label": "SDK Integration"},
        {"key": "advanced", "label": "Advanced Modules"},
        {"key": "qa", "label": "QA / UAT"},
        {"key": "live", "label": "Live on Prod"},
        {"key": "pilot", "label": "Pilot / Hypercare"},
        {"key": "handed_over", "label": "Handed Over to CSM", "terminal": True},
        {"key": "closed", "label": "Closed - Lost / Churned", "terminal": True},
    ],
    "fields": [
        {"key": "owner", "label": "Owner", "type": "person"},
        {"key": "engagement_type", "label": "Engagement Type", "type": "select",
         "options": ["New logo", "Existing - new app", "PN upsell", "Module upsell"]},
        {"key": "health", "label": "Account Health", "type": "select",
         "options": ["Healthy", "At risk", "Paused", "Churned pre-live", "Churned post-live"]},
        {"key": "org_id", "label": "Org ID", "type": "text"},
        {"key": "product_ids", "label": "Product IDs", "type": "text"},
        {"key": "hubspot_url", "label": "HubSpot", "type": "link"},
        {"key": "platforms", "label": "Platforms", "type": "multiselect",
         "options": ["Android XML", "Android Compose", "iOS UIKit", "iOS SwiftUI", "React Native",
                     "Flutter", "Flutter Web", "Web", "Unity", "KMP"]},
        {"key": "modules_in_scope", "label": "Modules in Scope", "type": "multiselect",
         "options": _MODULES},
        {"key": "modules_pending", "label": "Modules Pending", "type": "multiselect",
         "options": _MODULES},
        {"key": "arr", "label": "ARR ($)", "type": "number"},
        {"key": "billing_status", "label": "Billing Status", "type": "select",
         "options": ["Not invoiced", "Invoiced", "Paid", "Overdue"]},
        {"key": "contract_start", "label": "Contract Start", "type": "date"},
        {"key": "kickoff_date", "label": "Kickoff", "type": "date"},
        {"key": "dev_provisioned", "label": "Dev Provisioned", "type": "date"},
        {"key": "target_go_live", "label": "Target Go-Live", "type": "date"},
        {"key": "first_live", "label": "First Live", "type": "date"},
        {"key": "pilot_end", "label": "Pilot End", "type": "date"},
        {"key": "client_blocked_days", "label": "Client-Blocked Days", "type": "number"},
        {"key": "blocker_owner", "label": "Blocker Owner", "type": "select",
         "options": ["None", "Client eng", "Client business", "Internal eng", "Internal SA",
                     "Infosec-Legal"]},
        {"key": "next_step", "label": "Next Step", "type": "text"},
        {"key": "next_step_due", "label": "Next Step Due", "type": "date"},
        {"key": "go_live_checklist", "label": "Go-Live Checklist", "type": "multiselect",
         "options": ["Invoice paid", "Contract linked", "Metric events = ALL",
                     "PN verified on prod", "UAT sign-off"]},
        {"key": "pilot_outcome", "label": "Pilot Outcome", "type": "select",
         "options": ["Converted", "Extended", "Lost"]},
        {"key": "loss_reason", "label": "Loss Reason", "type": "select",
         "options": ["Infosec", "Priorities changed", "No POC", "Regulatory", "Not legit", "Other"]},
        {"key": "notes", "label": "Notes", "type": "longtext"},
    ],
    # Minimum system role allowed to edit. "chatter" = every signed-in user.
    "edit_min_role": "chatter",
}

# Columns stored on the record itself rather than under `fields`.
CORE_KEYS = ("name", "account", "stage")


def now() -> datetime:
    return datetime.now(timezone.utc)


async def get_config(db) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    override = await db.onboarding_settings.find_one({"_id": "config"})
    if override:
        for key in ("stages", "fields", "edit_min_role"):
            if override.get(key):
                cfg[key] = override[key]
    return cfg


def _field_map(cfg: dict) -> dict[str, dict]:
    return {f["key"]: f for f in cfg["fields"]}


def coerce(field: dict, value: Any) -> Any:
    """Normalise an incoming value to the field's type. Empty -> None."""
    if value is None or value == "" or value == []:
        return None
    ftype = field.get("type")
    if ftype == "number":
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field['key']}: expected a number")
        return int(num) if num.is_integer() else num
    if ftype == "date":
        text = str(value)[:10]
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{field['key']}: expected YYYY-MM-DD")
        return text
    if ftype == "multiselect":
        if isinstance(value, str):
            value = value.split(",")
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        return cleaned or None
    return str(value).strip() or None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _aware(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def derive(record: dict, cfg: dict, today: date | None = None) -> dict:
    """Computed columns: days to live (gross/net), pilot days left, flags."""
    today = today or now().date()
    f = record.get("fields") or {}
    terminal = {s["key"] for s in cfg["stages"] if s.get("terminal")}
    start = _parse_date(f.get("kickoff_date")) or _parse_date(f.get("dev_provisioned"))
    live = _parse_date(f.get("first_live"))
    gross = (live - start).days if start and live else None
    try:
        blocked = int(f.get("client_blocked_days") or 0)
    except (TypeError, ValueError):
        blocked = 0
    net = max(gross - blocked, 0) if gross is not None else None
    pilot_end = _parse_date(f.get("pilot_end"))
    due = _parse_date(f.get("next_step_due"))
    target = _parse_date(f.get("target_go_live"))
    active = record.get("stage") not in terminal
    updated = _aware(record.get("updated_at"))
    flags = []
    if active and due and due < today:
        flags.append("overdue")
    if active and isinstance(updated, datetime) and (now() - updated).days >= STALE_DAYS:
        flags.append("stale")
    if active and f.get("blocker_owner") not in (None, "", "None"):
        flags.append("blocked")
    if active and pilot_end and 0 <= (pilot_end - today).days <= 14:
        flags.append("pilot_ending")
    if active and target and not live and target < today:
        flags.append("late")
    return {
        "days_to_live_gross": gross,
        "days_to_live_net": net,
        "pilot_days_left": (pilot_end - today).days if pilot_end else None,
        "flags": flags,
    }


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def serialize(record: dict, cfg: dict) -> dict:
    out = {k: v for k, v in record.items() if k != "_id"}
    for key in ("created_at", "updated_at"):
        out[key] = _iso(out.get(key))
    out["meta"] = {k: {**m, "at": _iso(m.get("at"))} for k, m in (out.get("meta") or {}).items()}
    out["derived"] = derive(record, cfg)
    return out


def serialize_event(event: dict) -> dict:
    out = {k: v for k, v in event.items() if k != "_id"}
    out["at"] = _iso(out.get("at"))
    return out


async def list_records(db) -> list[dict]:
    return await db.onboarding_records.find({"archived": {"$ne": True}}).sort(
        [("account_lower", 1), ("name_lower", 1)]).to_list(2000)


async def get_record(db, record_id: str) -> dict | None:
    return await db.onboarding_records.find_one({"record_id": record_id})


async def find_record(db, *, org_id: str | None = None, name: str | None = None) -> dict | None:
    """Match by app name (case-insensitive) first, then by org ID when unique.

    One org can own several app records, so an org-only match is used only
    when exactly one record carries that org ID.
    """
    if name:
        doc = await db.onboarding_records.find_one({"name_lower": name.strip().lower()})
        if doc:
            return doc
    if org_id:
        docs = await db.onboarding_records.find({"fields.org_id": org_id}).to_list(2)
        if len(docs) == 1:
            return docs[0]
    return None


async def apply_changes(db, record: dict, changes: dict, *, actor: str, source: str = HUMAN,
                        note: str | None = None, force: bool = False) -> tuple[dict, list, list]:
    """Write field/core changes onto a record and log one event.

    Returns (updated_record, applied_changes, skipped_fields). Raises
    ValueError on unknown fields/stages or bad values (nothing is written).
    """
    cfg = await get_config(db)
    fmap = _field_map(cfg)
    stage_keys = {s["key"] for s in cfg["stages"]}
    ts = now()
    fields = record.get("fields") or {}
    meta = record.get("meta") or {}
    sets: dict[str, Any] = {}
    applied: list[dict] = []
    skipped: list[str] = []

    for key, raw in changes.items():
        if key in CORE_KEYS:
            value = str(raw).strip() if raw is not None else ""
            if key == "stage" and value not in stage_keys:
                raise ValueError(f"Unknown stage '{value}'")
            if key in ("name", "account") and not value:
                raise ValueError(f"{key} cannot be empty")
            old = record.get(key)
            target = key
        elif key in fmap:
            value = coerce(fmap[key], raw)
            old = fields.get(key)
            target = f"fields.{key}"
        else:
            raise ValueError(f"Unknown field '{key}'")

        prev_source = (meta.get(key) or {}).get("source")
        if source != HUMAN and prev_source == HUMAN and not force and old not in (None, "", []):
            skipped.append(key)
            continue
        if value == old:
            continue
        sets[target] = value
        sets[f"meta.{key}"] = {"source": source, "by": actor, "at": ts}
        if key in ("name", "account"):
            sets[f"{key}_lower"] = value.lower()
        applied.append({"field": key, "old": old, "new": value})

    if applied or note:
        sets["updated_at"] = ts
        sets["updated_by"] = actor
        await db.onboarding_records.update_one({"record_id": record["record_id"]}, {"$set": sets})
        await db.onboarding_events.insert_one({
            "event_id": uuid.uuid4().hex[:12],
            "record_id": record["record_id"],
            "at": ts,
            "actor": actor,
            "source": source,
            "changes": applied,
            "note": note or None,
        })
    updated = await get_record(db, record["record_id"]) or record
    return updated, applied, skipped


async def create_record(db, data: dict, *, actor: str, source: str = HUMAN) -> dict:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    cfg = await get_config(db)
    stage = data.get("stage") or cfg["stages"][0]["key"]
    if stage not in {s["key"] for s in cfg["stages"]}:
        raise ValueError(f"Unknown stage '{stage}'")
    fmap = _field_map(cfg)
    unknown = [k for k in (data.get("fields") or {}) if k not in fmap]
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(unknown)}")
    account = str(data.get("account") or name).strip()
    ts = now()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "record"
    doc = {
        "record_id": f"{slug}-{uuid.uuid4().hex[:6]}",
        "name": name, "name_lower": name.lower(),
        "account": account, "account_lower": account.lower(),
        "stage": stage,
        "fields": {}, "meta": {},
        "created_at": ts, "created_by": actor,
        "updated_at": ts, "updated_by": actor,
    }
    await db.onboarding_records.insert_one(doc)
    doc.pop("_id", None)
    updated, _, _ = await apply_changes(db, doc, dict(data.get("fields") or {}), actor=actor,
                                        source=source, note=data.get("note") or "Record created")
    return updated


async def list_events(db, record_id: str, limit: int = 200) -> list[dict]:
    return await db.onboarding_events.find({"record_id": record_id}).sort("at", -1).to_list(limit)
