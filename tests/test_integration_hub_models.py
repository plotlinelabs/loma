import pytest

from integration_hub.models import (
    ValidationError, normalize_create, normalize_update, normalize_work_item,
)


def test_create_normalizes_manual_onboarding_fields():
    result = normalize_create({
        "name": " Acme ",
        "owner_email": "Owner@Plotline.so",
        "target_go_live_at": "2026-08-01",
    })
    assert result["name"] == "Acme"
    assert result["owner_email"] == "owner@plotline.so"
    assert result["stage"] == "kickoff"
    assert result["health"] == "on_track"
    assert result["target_go_live_at"].isoformat() == "2026-08-01T00:00:00+00:00"


@pytest.mark.parametrize("field,value", [
    ("stage", "unknown"),
    ("health", "green"),
    ("owner_email", "not-an-email"),
])
def test_create_rejects_invalid_fields(field, value):
    with pytest.raises(ValidationError):
        normalize_create({"name": "Acme", field: value})


def test_update_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="Unknown fields"):
        normalize_update({"created_by": "spoofed@plotline.so"})


def test_name_is_required_and_limited():
    with pytest.raises(ValidationError, match="name is required"):
        normalize_create({"name": "  "})
    with pytest.raises(ValidationError, match="200 characters"):
        normalize_create({"name": "a" * 201})


def test_create_initializes_onboarding_plan_and_work_items():
    result = normalize_create({
        "name": "Acme",
        "platforms": ["ios", "android", "ios"],
        "environments": ["staging", "production"],
        "stakeholders": ["Owner", "Engineer"],
    })
    assert result["platforms"] == ["ios", "android"]
    assert result["environments"] == ["staging", "production"]
    assert result["completion_percentage"] == 0
    assert result["work_items"] == []


def test_update_validates_completion_percentage():
    assert normalize_update({"completion_percentage": 75})["completion_percentage"] == 75
    with pytest.raises(ValidationError, match="integer from 0 to 100"):
        normalize_update({"completion_percentage": 101})


def test_work_item_normalization():
    item = normalize_work_item({
        "type": "risk",
        "title": " Customer dependency ",
        "severity": "high",
        "owner_email": "Owner@Plotline.so",
        "due_at": "2026-08-15",
    })
    assert item["title"] == "Customer dependency"
    assert item["severity"] == "high"
    assert item["status"] == "not_started"
    assert item["owner_email"] == "owner@plotline.so"


@pytest.mark.parametrize("data", [
    {"type": "unknown", "title": "Invalid"},
    {"type": "task", "title": ""},
    {"type": "risk", "title": "Risk", "severity": "urgent"},
])
def test_work_item_rejects_invalid_fields(data):
    with pytest.raises(ValidationError):
        normalize_work_item(data)
