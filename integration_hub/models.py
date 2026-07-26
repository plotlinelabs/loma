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
    }


def normalize_update(data):
    allowed = {
        "name", "owner_email", "stage", "health", "health_reason",
        "target_go_live_at", "current_blocker", "next_action",
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
    return result
