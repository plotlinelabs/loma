from datetime import datetime, timedelta, timezone

import pytest

from integration_hub.models import (
    ValidationError, calculate_account_health, normalize_activity, normalize_create,
    normalize_project, normalize_source_link, normalize_update, normalize_work_item,
    validate_status_transition,
)
from integration_hub.service import AccountService


def test_create_normalizes_manual_onboarding_fields():
    result = normalize_create({
        "name": " Acme ",
        "owner_email": "Owner@Example.com",
        "target_go_live_at": "2026-08-01",
    })
    assert result["name"] == "Acme"
    assert result["owner_email"] == "owner@example.com"
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
        normalize_update({"created_by": "spoofed@example.com"})


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
    assert "work_items" not in result
    assert "activities" not in result
    assert "source_links" not in result


def test_update_validates_completion_percentage():
    assert normalize_update({"completion_percentage": 75})["completion_percentage"] == 75
    with pytest.raises(ValidationError, match="integer from 0 to 100"):
        normalize_update({"completion_percentage": 101})


def test_work_item_normalization():
    item = normalize_work_item({
        "type": "risk",
        "title": " Customer dependency ",
        "severity": "high",
        "owner_email": "Owner@Example.com",
        "due_at": "2026-08-15",
    })
    assert item["title"] == "Customer dependency"
    assert item["severity"] == "high"
    assert item["status"] == "open"
    assert item["owner_email"] == "owner@example.com"


@pytest.mark.parametrize("data", [
    {"type": "unknown", "title": "Invalid"},
    {"type": "task", "title": ""},
    {"type": "risk", "title": "Risk", "severity": "urgent"},
])
def test_work_item_rejects_invalid_fields(data):
    with pytest.raises(ValidationError):
        normalize_work_item(data)


def test_activity_normalization():
    assert normalize_activity({"type": "decision", "message": " Ship Friday "}) == {
        "type": "decision", "message": "Ship Friday",
    }
    with pytest.raises(ValidationError):
        normalize_activity({"type": "meeting", "message": "Invalid"})


def test_source_link_normalization():
    link = normalize_source_link({
        "type": "grain", "title": "Kickoff", "url": "https://grain.com/recording",
    })
    assert link["type"] == "grain"
    with pytest.raises(ValidationError, match="http"):
        normalize_source_link({"type": "slack", "title": "Thread", "url": "slack://thread"})


def test_calculated_health_prioritizes_unresolved_blockers():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = calculate_account_health({
        "completion_percentage": 50,
        "work_items": [{
            "type": "blocker", "status": "in_progress", "severity": "high",
            "due_at": now - timedelta(days=1), "escalated": False,
        }],
    }, now)
    assert result["calculated_health"] == "blocked"
    assert result["effective_health"] == "blocked"
    assert result["overdue_count"] == 1
    assert result["open_blocker_count"] == 1


def test_manual_health_override_preserves_calculated_explanation():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = calculate_account_health({
        "health": "on_track",
        "health_override_enabled": True,
        "completion_percentage": 50,
        "target_go_live_at": now - timedelta(days=1),
        "work_items": [],
    }, now)
    assert result["calculated_health"] == "at_risk"
    assert result["effective_health"] == "on_track"
    assert "Target go-live date has passed" in result["calculated_health_reasons"]


def test_calculated_health_accepts_naive_mongodb_datetimes():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = calculate_account_health({
        "completion_percentage": 50,
        "target_go_live_at": datetime(2026, 7, 31),
        "work_items": [{
            "type": "task",
            "status": "in_progress",
            "due_at": datetime(2026, 7, 30),
        }],
    }, now)
    assert result["calculated_health"] == "at_risk"
    assert result["overdue_count"] == 1


@pytest.mark.asyncio
async def test_action_center_accepts_naive_mongodb_datetimes():
    class Repository:
        async def list_actions(self, actor):
            assert actor == "owner@example.com"
            return ([{
                "resource_id": "item_1", "type": "task", "title": "Ship SDK",
                "status": "in_progress", "owner_email": actor,
                "due_at": datetime(2020, 7, 30),
                "account": {"account_id": "acc_1", "name": "Acme"},
            }], [])

    actions, attention = await AccountService(Repository()).list_actions("owner@example.com")
    assert actions[0]["is_overdue"] is True
    assert actions[0]["item_id"] == "item_1"
    assert attention == []


def test_health_override_flag_requires_boolean():
    with pytest.raises(ValidationError, match="boolean"):
        normalize_update({"health_override_enabled": "yes"})


@pytest.mark.parametrize("field,payload", [
    ("health_override_enabled", {"name": "Acme", "health_override_enabled": "false"}),
    ("escalated", {"type": "risk", "title": "Dependency", "escalated": "false"}),
])
def test_create_boolean_fields_reject_strings(field, payload):
    normalizer = normalize_create if "name" in payload else normalize_work_item
    with pytest.raises(ValidationError, match=f"{field} must be a boolean"):
        normalizer(payload)


def test_project_and_playbook_fields_are_normalized():
    project = normalize_project({
        "name": " Production rollout ",
        "owner_email": "Owner@Example.com",
        "playbook": "mobile_sdk",
    })
    assert project["name"] == "Production rollout"
    assert project["owner_email"] == "owner@example.com"
    assert project["status"] == "active"
    assert project["playbook"] == "mobile_sdk"


def test_invalid_project_status_is_rejected():
    with pytest.raises(ValidationError, match="status is invalid"):
        normalize_project({"name": "Rollout", "status": "unknown"})


def test_account_lifecycle_transitions_are_explicit():
    validate_status_transition("active", "archived")
    validate_status_transition("archived", "active")
    with pytest.raises(ValidationError, match="Cannot transition"):
        validate_status_transition("archived", "inactive")


def test_work_item_supports_project_dependencies():
    item = normalize_work_item({
        "type": "task",
        "title": "Validate events",
        "project_id": "project_123",
        "depends_on": ["item_1", "item_2"],
    })
    assert item["project_id"] == "project_123"
    assert item["depends_on"] == ["item_1", "item_2"]


def test_resource_lifecycles_match_phase_one_contract():
    assert normalize_work_item({"type": "task", "title": "T"})["status"] == "todo"
    assert normalize_work_item({"type": "milestone", "title": "M"})["status"] == "pending"
    assert normalize_work_item({"type": "risk", "title": "R"})["status"] == "open"
    assert normalize_project({"name": "P"})["status"] == "active"
    with pytest.raises(ValidationError):
        normalize_project({"name": "P", "status": "planned"})


def test_distinct_lifecycle_transitions_and_reopen_rules():
    AccountService._validate_item_transition("task", "completed", "in_progress")
    AccountService._validate_item_transition("milestone", "achieved", "in_progress")
    AccountService._validate_item_transition("risk", "resolved", "open")
    with pytest.raises(ValidationError):
        AccountService._validate_item_transition("task", "todo", "completed")
