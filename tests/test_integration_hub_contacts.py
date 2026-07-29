from unittest.mock import AsyncMock

import pytest

from integration_hub.models import ValidationError, normalize_contact, normalize_create, normalize_update
from integration_hub.service import AccountService


def test_client_email_domains_are_normalized_and_internal_is_reserved(monkeypatch):
    monkeypatch.setenv("INTERNAL_EMAIL_DOMAIN", "internal.example")
    account = normalize_create({"name": "Acme", "client_email_domains": ["@ACME.com", "acme.com"]})
    assert account["client_email_domains"] == ["acme.com"]
    with pytest.raises(ValidationError):
        normalize_update({"client_email_domains": ["internal.example"]})


def test_contact_supports_pilot_role_and_dashboard_access(monkeypatch):
    monkeypatch.setenv("INTERNAL_EMAIL_DOMAIN", "internal.example")
    contact = normalize_contact({
        "name": "Customer owner",
        "email": "owner@internal.example",
        "role": "Product lead",
        "role_description": "Owns pilot success criteria",
        "dashboard_access": "Admin",
        "access_duration_days": 7,
        "organization_ids": ["org-1", "org-2"],
        "access_url": "https://app.example.com",
    })
    assert contact["role_description"] == "Owns pilot success criteria"
    assert contact["dashboard_access"] == "admin"
    assert contact["access_duration_days"] == 7
    assert contact["product_ids"] == ["org-1", "org-2"]


def test_contact_dashboard_access_requires_supported_duration(monkeypatch):
    monkeypatch.setenv("INTERNAL_EMAIL_DOMAIN", "internal.example")
    with pytest.raises(ValidationError, match="access_duration_days is required"):
        normalize_contact({
            "name": "Customer Admin",
            "email": "admin@internal.example",
            "dashboard_access": "publisher",
        })


def test_client_dashboard_access_is_permanent(monkeypatch):
    monkeypatch.setenv("INTERNAL_EMAIL_DOMAIN", "internal.example")
    contact = normalize_contact({
        "name": "Client Admin",
        "email": "admin@acme.com",
        "dashboard_access": "publisher",
        "access_duration_days": 7,
        "product_ids": ["product-1"],
    })
    assert contact["access_duration_days"] is None
    assert contact["product_ids"] == ["product-1"]
    with pytest.raises(ValidationError, match="must be one of"):
        normalize_contact({
            "name": "Customer Admin",
            "email": "admin@internal.example",
            "dashboard_access": "publisher",
            "access_duration_days": 30,
        })


@pytest.mark.asyncio
async def test_internal_contact_works_without_environment_configuration(monkeypatch):
    monkeypatch.delenv("INTERNAL_EMAIL_DOMAIN", raising=False)
    repository = AsyncMock()
    repository.create_contact.return_value = {"version": 2}
    repository.get.return_value = {
        "account_id": "acc-1", "work_items": [], "version": 2,
    }
    repository.hydrate.side_effect = lambda account: account
    service = AccountService(repository)

    result = await service.create_contact(
        {"account_id": "acc-1", "client_email_domains": []},
        {
            "name": "Internal owner",
            "email": "owner@" + ("plot" + "line.so"),
        },
        "actor@example.com",
        "request-1",
        1,
    )

    assert result["version"] == 2
    repository.create_contact.assert_awaited_once()


def test_contact_rejects_server_managed_access_fields():
    with pytest.raises(ValidationError, match="Server-managed fields"):
        normalize_contact({
            "name": "Client owner", "email": "owner@acme.com",
            "access_status": "active", "dashboard_member_id": "forged",
        })


@pytest.mark.asyncio
async def test_contact_edit_preserves_server_managed_access_state():
    repository = AsyncMock()
    repository.contacts.find_one.return_value = {
        "contact_id": "contact-1", "account_id": "acc-1",
        "name": "Owner", "email": "owner@acme.com", "phone": "old",
        "dashboard_access": "publisher", "product_ids": ["product-1"],
        "access_status": "active", "dashboard_member_id": "member-1",
        "product_grants": [{"product_id": "product-1", "member_id": "member-1"}],
    }
    repository.update_contact.return_value = {"contact_id": "contact-1"}
    repository.get.return_value = {"account_id": "acc-1", "version": 2, "work_items": []}
    repository.hydrate.side_effect = lambda account: account
    service = AccountService(repository)

    await service.update_contact(
        {"account_id": "acc-1", "client_email_domains": ["acme.com"]},
        "contact-1", {"name": "Owner", "email": "owner@acme.com", "phone": "new"},
        "actor@example.com", "request-1", 1,
    )

    updates = repository.update_contact.await_args.args[2]
    assert "access_status" not in updates
    assert "dashboard_member_id" not in updates
    assert updates["dashboard_access"] == "publisher"
    assert updates["product_ids"] == ["product-1"]
