"""Validation and normalization for manual client onboarding records."""

from datetime import datetime, timedelta, timezone


STAGES = (
    "kickoff",
    "sdk_installation",
    "identification",
    "events_attributes",
    "pages_elements",
    "test_validation",
    "production_deployment",
    "first_campaign",
    "handover",
)
HEALTH_STATES = ("on_track", "needs_attention", "blocked", "silent", "at_risk", "escalated")
PLATFORMS = ("android", "ios", "react_native", "flutter", "web", "unity", "kmp")
ENVIRONMENTS = ("development", "staging", "production")
WORK_ITEM_TYPES = ("milestone", "task", "risk", "blocker")
WORK_ITEM_STATUSES = {
    "task": ("todo", "in_progress", "blocked", "completed", "cancelled"),
    "milestone": ("pending", "in_progress", "achieved", "missed", "cancelled"),
    "risk": ("open", "mitigating", "accepted", "resolved"),
    "blocker": ("open", "mitigating", "resolved"),
}
RISK_SEVERITIES = ("low", "medium", "high", "critical")
ACTIVITY_TYPES = ("note", "decision", "update")
SOURCE_TYPES = ("grain", "slack", "linear", "pylon", "hubspot", "document", "other")
SOURCE_MAPPING_STATUSES = ("active", "paused")
SYNC_SOURCE_TYPES = ("slack", "grain", "pylon")
CONVERSATION_STATES = (
    "waiting_on_plotline", "waiting_on_customer", "internally_blocked",
    "resolved", "monitoring", "no_action_required",
)
INTERACTION_DIRECTIONS = ("customer_to_plotline", "plotline_to_customer", "internal")
PROJECT_STATUSES = ("active", "paused", "completed", "cancelled")
ACCOUNT_STATUSES = ("active", "inactive", "archived")
PLAYBOOKS = {
    "mobile_sdk": {
        "name": "Mobile SDK onboarding",
        "items": (
            ("milestone", "Kickoff complete"),
            ("task", "Install SDK in development"),
            ("task", "Configure identification, events, and attributes"),
            ("milestone", "Validate test campaign"),
            ("milestone", "Production release"),
        ),
    },
    "web_sdk": {
        "name": "Web SDK onboarding",
        "items": (
            ("milestone", "Kickoff complete"),
            ("task", "Install Web SDK"),
            ("task", "Configure pages, elements, and events"),
            ("milestone", "Validate test campaign"),
            ("milestone", "Production release"),
        ),
    },
}


class ValidationError(ValueError):
    pass


def _text(value, field, *, required=False, maximum=1000):
    text = (value or "").strip()
    if required and not text:
        raise ValidationError(f"{field} is required")
    if len(text) > maximum:
        raise ValidationError(f"{field} must be {maximum} characters or less")
    return text or None


def normalize_email(value, field="owner_email"):
    email = _text(value, field, maximum=320)
    if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
        raise ValidationError(f"{field} must be a valid email address")
    return email.lower() if email else None


def normalize_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("target_go_live_at must be an ISO date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_source_mapping(data):
    source = data.get("source")
    if source not in SYNC_SOURCE_TYPES:
        raise ValidationError("source does not support read-only sync")
    status = data.get("status") or "active"
    if status not in SOURCE_MAPPING_STATUSES:
        raise ValidationError("status is invalid")
    config = data.get("config") or {}
    if not isinstance(config, dict):
        raise ValidationError("config must be an object")
    allowed_config = {
        "thread_ts", "source_url", "limit", "customer_user_ids",
        "plotline_user_ids", "recording_ids", "sync_interval_minutes",
        "customer_name", "issue_ids",
    }
    if set(config) - allowed_config:
        raise ValidationError("config contains unsupported fields")
    if "limit" in config and (isinstance(config["limit"], bool) or not isinstance(config["limit"], int) or not 1 <= config["limit"] <= 200):
        raise ValidationError("config.limit must be between 1 and 200")
    if "sync_interval_minutes" in config and (
        isinstance(config["sync_interval_minutes"], bool)
        or not isinstance(config["sync_interval_minutes"], int)
        or not 15 <= config["sync_interval_minutes"] <= 1440
    ):
        raise ValidationError("config.sync_interval_minutes must be between 15 and 1440")
    for field in ("customer_user_ids", "plotline_user_ids", "recording_ids", "issue_ids"):
        if field in config:
            config[field] = _text_list(config[field], f"config.{field}", maximum_items=100)
    return {
        "source": source,
        "tenant_id": _text(data.get("tenant_id"), "tenant_id", required=True, maximum=255),
        "external_id": _text(data.get("external_id"), "external_id", required=True, maximum=255),
        "label": _text(data.get("label"), "label", maximum=200),
        "status": status,
        "config": {key: value for key, value in config.items() if value not in (None, "")},
    }


def normalize_interaction(data):
    source = data.get("source")
    if source not in SOURCE_TYPES:
        raise ValidationError("source is invalid")
    direction = data.get("direction")
    if direction not in INTERACTION_DIRECTIONS:
        raise ValidationError("direction is invalid")
    state = data.get("conversation_state") or "monitoring"
    if state not in CONVERSATION_STATES:
        raise ValidationError("conversation_state is invalid")
    confidence = data.get("confidence", 1)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValidationError("confidence must be between 0 and 1")
    return {
        "source": source,
        "tenant_id": _text(data.get("tenant_id"), "tenant_id", required=True, maximum=255),
        "source_id": _text(data.get("source_id"), "source_id", required=True, maximum=255),
        "source_url": _text(data.get("source_url"), "source_url", maximum=2048),
        "occurred_at": normalize_date(data.get("occurred_at")),
        "direction": direction,
        "classification": _text(data.get("classification"), "classification", maximum=100),
        "requires_response": _boolean(data.get("requires_response"), "requires_response"),
        "meaningful_contact": _boolean(data.get("meaningful_contact"), "meaningful_contact", default=True),
        "conversation_state": state,
        "summary": _text(data.get("summary"), "summary", required=True, maximum=1000),
        "confidence": float(confidence),
        "classifier_version": _text(data.get("classifier_version"), "classifier_version", maximum=100) or "rules-v1",
        "conversation_id": _text(data.get("conversation_id"), "conversation_id", maximum=255),
        "evidence": data.get("evidence") if isinstance(data.get("evidence"), dict) else {},
        "raw": data.get("raw") if isinstance(data.get("raw"), dict) else {},
    }


def _choice_list(value, field, choices):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list")
    normalized = list(dict.fromkeys(value))
    invalid = set(normalized) - set(choices)
    if invalid:
        raise ValidationError(f"{field} contains invalid values")
    return normalized


def _text_list(value, field, maximum_items=50):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValidationError(f"{field} must be a list of at most {maximum_items} items")
    return [
        _text(item, field, required=True, maximum=200)
        for item in value
    ]


def _boolean(value, field, *, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValidationError(f"{field} must be a boolean")
    return value


def normalize_create(data):
    stage = data.get("stage") or "kickoff"
    health = data.get("health") or "on_track"
    if stage not in STAGES:
        raise ValidationError("stage is invalid")
    if health not in HEALTH_STATES:
        raise ValidationError("health is invalid")
    name = _text(data.get("name"), "name", required=True, maximum=200)
    return {
        "name": name,
        "name_key": " ".join(name.casefold().split()),
        "status": "active",
        "owner_email": normalize_email(data.get("owner_email")),
        "stage": stage,
        "health": health,
        "health_override_enabled": _boolean(
            data.get("health_override_enabled"), "health_override_enabled"
        ),
        "health_reason": _text(data.get("health_reason"), "health_reason"),
        "target_go_live_at": normalize_date(data.get("target_go_live_at")),
        "current_blocker": _text(data.get("current_blocker"), "current_blocker"),
        "next_action": _text(data.get("next_action"), "next_action", maximum=500),
        "platforms": _choice_list(data.get("platforms"), "platforms", PLATFORMS),
        "environments": _choice_list(
            data.get("environments"), "environments", ENVIRONMENTS
        ),
        "stakeholders": _text_list(data.get("stakeholders"), "stakeholders"),
        "go_live_criteria": _text(data.get("go_live_criteria"), "go_live_criteria"),
        "completion_percentage": 0,
    }


def normalize_update(data):
    allowed = {
        "name", "owner_email", "stage", "health", "health_reason",
        "target_go_live_at", "current_blocker", "next_action",
        "platforms", "environments", "stakeholders", "go_live_criteria",
        "completion_percentage", "health_override_enabled",
        "status",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError(f"Unknown fields: {', '.join(sorted(unknown))}")
    result = {}
    if "status" in data:
        if data["status"] not in ("active", "inactive"):
            raise ValidationError("status is invalid")
        result["status"] = data["status"]
    if "name" in data:
        result["name"] = _text(data["name"], "name", required=True, maximum=200)
        result["name_key"] = " ".join(result["name"].casefold().split())
    if "owner_email" in data:
        result["owner_email"] = normalize_email(data["owner_email"])
    if "stage" in data:
        if data["stage"] not in STAGES:
            raise ValidationError("stage is invalid")
        result["stage"] = data["stage"]
    if "health" in data:
        if data["health"] not in HEALTH_STATES:
            raise ValidationError("health is invalid")
        result["health"] = data["health"]
    if "health_override_enabled" in data:
        if not isinstance(data["health_override_enabled"], bool):
            raise ValidationError("health_override_enabled must be a boolean")
        result["health_override_enabled"] = data["health_override_enabled"]
    if "health_reason" in data:
        result["health_reason"] = _text(data["health_reason"], "health_reason")
    if "target_go_live_at" in data:
        result["target_go_live_at"] = normalize_date(data["target_go_live_at"])
    if "current_blocker" in data:
        result["current_blocker"] = _text(data["current_blocker"], "current_blocker")
    if "next_action" in data:
        result["next_action"] = _text(data["next_action"], "next_action", maximum=500)
    if "platforms" in data:
        result["platforms"] = _choice_list(data["platforms"], "platforms", PLATFORMS)
    if "environments" in data:
        result["environments"] = _choice_list(
            data["environments"], "environments", ENVIRONMENTS
        )
    if "stakeholders" in data:
        result["stakeholders"] = _text_list(data["stakeholders"], "stakeholders")
    if "go_live_criteria" in data:
        result["go_live_criteria"] = _text(data["go_live_criteria"], "go_live_criteria")
    if "completion_percentage" in data:
        value = data["completion_percentage"]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            raise ValidationError("completion_percentage must be an integer from 0 to 100")
        result["completion_percentage"] = value
    return result


def normalize_contact(data):
    name = _text(data.get("name"), "name", required=True, maximum=200)
    email = normalize_email(data.get("email"))
    if not email:
        raise ValidationError("email is required")
    return {
        "name": name,
        "email": email,
        "role": _text(data.get("role"), "role", maximum=200),
        "phone": _text(data.get("phone"), "phone", maximum=50),
    }


def normalize_work_item(data):
    item_type = data.get("type")
    if item_type not in WORK_ITEM_TYPES:
        raise ValidationError("type is invalid")
    defaults = {"task": "todo", "milestone": "pending", "risk": "open", "blocker": "open"}
    status = data.get("status") or defaults[item_type]
    if status not in WORK_ITEM_STATUSES[item_type]:
        raise ValidationError("status is invalid")
    severity = data.get("severity")
    if severity and severity not in RISK_SEVERITIES:
        raise ValidationError("severity is invalid")
    return {
        "type": item_type,
        "title": _text(data.get("title"), "title", required=True, maximum=200),
        "description": _text(data.get("description"), "description"),
        "status": status,
        "owner_email": normalize_email(data.get("owner_email")),
        "due_at": normalize_date(data.get("due_at")),
        "severity": severity if item_type in ("risk", "blocker") else None,
        "dependency": _text(data.get("dependency"), "dependency", maximum=500),
        "resolution": _text(data.get("resolution"), "resolution"),
        "escalated": _boolean(data.get("escalated"), "escalated"),
        "project_id": _text(data.get("project_id"), "project_id", maximum=100),
        "depends_on": _text_list(data.get("depends_on"), "depends_on", maximum_items=20),
    }


def normalize_project(data):
    status = data.get("status") or "active"
    if status not in PROJECT_STATUSES:
        raise ValidationError("status is invalid")
    stage = data.get("stage") or "kickoff"
    health = data.get("health") or "on_track"
    if stage not in STAGES: raise ValidationError("stage is invalid")
    if health not in HEALTH_STATES: raise ValidationError("health is invalid")
    playbook = data.get("playbook")
    if playbook and playbook not in PLAYBOOKS:
        raise ValidationError("playbook is invalid")
    return {
        "name": _text(data.get("name"), "name", required=True, maximum=200),
        "description": _text(data.get("description"), "description"),
        "status": status,
        "stage": stage,
        "health": health,
        "health_reason": _text(data.get("health_reason"), "health_reason"),
        "owner_email": normalize_email(data.get("owner_email")),
        "target_at": normalize_date(data.get("target_at")),
        "playbook": playbook or None,
    }


def validate_status_transition(current, target):
    allowed = {
        "active": {"active", "inactive", "archived"},
        "inactive": {"inactive", "active", "archived"},
        "archived": {"archived", "active"},
    }
    if target not in allowed.get(current, set()):
        raise ValidationError(f"Cannot transition account from {current} to {target}")


def normalize_activity(data):
    activity_type = data.get("type") or "note"
    if activity_type not in ACTIVITY_TYPES:
        raise ValidationError("type is invalid")
    return {
        "type": activity_type,
        "message": _text(data.get("message"), "message", required=True, maximum=2000),
    }


def normalize_source_link(data):
    source_type = data.get("type") or "other"
    if source_type not in SOURCE_TYPES:
        raise ValidationError("type is invalid")
    url = _text(data.get("url"), "url", required=True, maximum=2000)
    if not url.startswith(("https://", "http://")):
        raise ValidationError("url must start with http:// or https://")
    return {
        "type": source_type,
        "title": _text(data.get("title"), "title", required=True, maximum=200),
        "url": url,
        "notes": _text(data.get("notes"), "notes", maximum=1000),
    }


def as_utc(value):
    """Normalize MongoDB's naive UTC datetimes before date comparisons."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def calculate_account_health(account, now=None):
    """Return the derived health, explanations, and urgency counters."""
    now = as_utc(now or datetime.now(timezone.utc))
    upcoming_cutoff = now + timedelta(days=7)
    closed_statuses = {
        "task": {"completed", "cancelled"},
        "milestone": {"achieved", "cancelled"},
        "risk": {"resolved", "accepted"},
        "blocker": {"resolved"},
    }
    open_items = [
        item for item in account.get("work_items", [])
        if item.get("status") not in closed_statuses.get(item.get("type"), set())
    ]
    overdue = [
        item for item in open_items
        if item.get("due_at") and as_utc(item["due_at"]) < now
    ]
    upcoming = [
        item for item in open_items
        if item.get("due_at") and now <= as_utc(item["due_at"]) <= upcoming_cutoff
    ]
    blockers = [item for item in open_items if item.get("type") == "blocker"]
    severe = [
        item for item in open_items
        if item.get("type") in ("risk", "blocker")
        and item.get("severity") in ("high", "critical")
    ]
    reasons = []
    if blockers:
        reasons.append(f"{len(blockers)} unresolved blocker{'s' if len(blockers) != 1 else ''}")
    if overdue:
        reasons.append(f"{len(overdue)} overdue item{'s' if len(overdue) != 1 else ''}")
    if severe:
        reasons.append(f"{len(severe)} high-severity risk{'s' if len(severe) != 1 else ''}")
    target = as_utc(account.get("target_go_live_at"))
    if target and target < now and account.get("completion_percentage", 0) < 100:
        reasons.append("Target go-live date has passed")
    elif target and target <= upcoming_cutoff and account.get("completion_percentage", 0) < 100:
        reasons.append("Go-live is within 7 days")

    if any(item.get("escalated") for item in severe):
        calculated = "escalated"
    elif blockers:
        calculated = "blocked"
    elif severe or len(overdue) >= 2 or (target and target < now):
        calculated = "at_risk"
    elif overdue or (target and target <= upcoming_cutoff and account.get("completion_percentage", 0) < 100):
        calculated = "needs_attention"
    else:
        calculated = "on_track"

    effective = account.get("health", calculated) if account.get("health_override_enabled") else calculated
    return {
        "calculated_health": calculated,
        "effective_health": effective,
        "calculated_health_reasons": reasons,
        "overdue_count": len(overdue),
        "upcoming_count": len(upcoming),
        "open_blocker_count": len(blockers),
    }
