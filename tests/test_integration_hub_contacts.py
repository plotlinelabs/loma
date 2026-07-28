import pytest

from integration_hub.models import ValidationError, normalize_contact, normalize_create, normalize_update


def test_client_email_domains_are_normalized_and_internal_is_reserved(monkeypatch):
    monkeypatch.setenv("INTERNAL_EMAIL_DOMAIN", "internal.example")
    account = normalize_create({"name": "Acme", "client_email_domains": ["@ACME.com", "acme.com"]})
    assert account["client_email_domains"] == ["acme.com"]
    with pytest.raises(ValidationError):
        normalize_update({"client_email_domains": ["internal.example"]})


def test_contact_supports_pilot_role_and_dashboard_access():
    contact = normalize_contact({
        "name": "Customer owner",
        "email": "owner@acme.com",
        "role": "Product lead",
        "role_description": "Owns pilot success criteria",
        "dashboard_access": "Admin",
        "organization_ids": ["org-1", "org-2"],
        "access_url": "https://app.example.com",
    })
    assert contact["role_description"] == "Owns pilot success criteria"
    assert contact["organization_ids"] == ["org-1", "org-2"]
