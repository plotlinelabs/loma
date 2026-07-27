from datetime import timezone

import pytest

from integration_hub.models import ValidationError, normalize_interaction
from integration_hub.service import AccountService


def test_normalize_interaction_contract():
    result = normalize_interaction({
        "source": "slack",
        "tenant_id": "T123",
        "source_id": "thread-1",
        "occurred_at": "2026-07-27T10:00:00Z",
        "direction": "customer_to_plotline",
        "conversation_state": "waiting_on_plotline",
        "summary": "Customer asked for event validation.",
        "requires_response": True,
        "meaningful_contact": True,
        "confidence": 0.94,
    })
    assert result["occurred_at"].tzinfo == timezone.utc
    assert result["classifier_version"] == "rules-v1"
    assert result["requires_response"] is True


@pytest.mark.parametrize("field,value", [
    ("source", "email"),
    ("direction", "outbound"),
    ("conversation_state", "open"),
    ("confidence", 1.1),
])
def test_normalize_interaction_rejects_invalid_enums(field, value):
    payload = {
        "source": "slack",
        "tenant_id": "T123",
        "source_id": "thread-1",
        "occurred_at": "2026-07-27T10:00:00Z",
        "direction": "customer_to_plotline",
        "conversation_state": "monitoring",
        "summary": "Update",
        "confidence": 0.8,
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        normalize_interaction(payload)


class InteractionRepository:
    def __init__(self):
        self.rows = []
        self.interactions = self

    async def create_interaction(self, interaction, audit):
        if any(
            (row["source"], row["tenant_id"], row["source_id"])
            == (interaction["source"], interaction["tenant_id"], interaction["source_id"])
            for row in self.rows
        ):
            return False
        self.rows.append(interaction)
        return True

    async def find_one(self, query):
        return next(row for row in self.rows if all(row.get(k) == v for k, v in query.items()))


@pytest.mark.asyncio
async def test_interaction_ingestion_is_deduplicated():
    repository = InteractionRepository()
    service = AccountService(repository)
    account = {"account_id": "acc_1"}
    payload = {
        "source": "pylon",
        "tenant_id": "workspace",
        "source_id": "issue-1",
        "occurred_at": "2026-07-27T10:00:00Z",
        "direction": "customer_to_plotline",
        "conversation_state": "waiting_on_plotline",
        "summary": "Customer needs a response.",
        "requires_response": True,
    }
    first, created = await service.ingest_interaction(account, payload, "owner@example.com", "req-1")
    second, replay_created = await service.ingest_interaction(account, payload, "owner@example.com", "req-2")
    assert created is True
    assert replay_created is False
    assert second["interaction_id"] == first["interaction_id"]
    assert len(repository.rows) == 1
