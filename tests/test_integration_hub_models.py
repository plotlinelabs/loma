import pytest

from integration_hub.models import ValidationError, normalize_create, normalize_update


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
