"""Validation and normalization for manual client onboarding records."""

from datetime import datetime, timezone


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
WORK_ITEM_STATUSES = ("not_started", "in_progress", "blocked", "completed")
RISK_SEVERITIES = ("low", "medium", "high", "critical")


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


def normalize_create(data):
    stage = data.get("stage") or "kickoff"
    health = data.get("health") or "on_track"
    if stage not in STAGES:
        raise ValidationError("stage is invalid")
    if health not in HEALTH_STATES:
        raise ValidationError("health is invalid")
    return {
        "name": _text(data.get("name"), "name", required=True, maximum=200),
        "status": "active",
        "owner_email": normalize_email(data.get("owner_email")),
        "stage": stage,
        "health": health,
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
        "work_items": [],
    }


def normalize_update(data):
    allowed = {
        "name", "owner_email", "stage", "health", "health_reason",
        "target_go_live_at", "current_blocker", "next_action",
        "platforms", "environments", "stakeholders", "go_live_criteria",
        "completion_percentage",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError(f"Unknown fields: {', '.join(sorted(unknown))}")
    result = {}
    if "name" in data:
        result["name"] = _text(data["name"], "name", required=True, maximum=200)
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


def normalize_work_item(data):
    item_type = data.get("type")
    if item_type not in WORK_ITEM_TYPES:
        raise ValidationError("type is invalid")
    status = data.get("status") or "not_started"
    if status not in WORK_ITEM_STATUSES:
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
        "escalated": bool(data.get("escalated", False)),
    }
