from datetime import timezone
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from integration_hub.models import ValidationError, normalize_interaction
from integration_hub.repository import AccountRepository
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


def test_sync_mapping_is_limited_to_pull_only_sources():
    from integration_hub.models import normalize_source_mapping, ValidationError
    assert normalize_source_mapping({"source": "slack", "tenant_id": "T1", "external_id": "C1"})["status"] == "active"
    with pytest.raises(ValidationError):
        normalize_source_mapping({"source": "linear", "tenant_id": "T1", "external_id": "ENG"})
    with pytest.raises(ValidationError):
        normalize_source_mapping({"source": "slack", "tenant_id": "T1", "external_id": "C1", "config": {"write": True}})


@pytest.mark.asyncio
async def test_pull_dispatches_only_registered_readers(monkeypatch):
    from integration_hub import read_only_sync
    called = []
    async def reader(mapping, actor):
        called.append((mapping["external_id"], actor))
        return []
    monkeypatch.setitem(read_only_sync.READERS, "slack", reader)
    result = await read_only_sync.pull({"source": "slack", "external_id": "C1"}, "user@example.com")
    assert result == []
    assert called == [("C1", "user@example.com")]
    assert not any(name.startswith(("send", "update", "create", "reply", "post")) for name in read_only_sync.READERS)


@pytest.mark.asyncio
async def test_sync_deduplicates_pulled_records(monkeypatch):
    from integration_hub.service import AccountService
    service = AccountService(None)
    records = [{"source": "slack", "tenant_id": "T", "source_id": "1"}]
    async def fake_pull(mapping, actor): return records
    monkeypatch.setattr("integration_hub.read_only_sync.pull", fake_pull)
    seen = []
    async def ingest(account, record, actor, request_id):
        seen.append(record)
        return record, len(seen) == 1
    service.ingest_interaction = ingest
    class Repo:
        async def update_sync_result(self, *args, **kwargs): return kwargs
    service.repository = Repo()
    updated, created, total = await service.sync_source(
        {"account_id": "a"}, {"mapping_id": "m", "source": "slack"}, "u", "r"
    )
    assert (created, total) == (1, 1)
    assert updated["status"] == "succeeded"


@pytest.mark.asyncio
async def test_slack_sync_uses_full_precision_ts_as_external_id(monkeypatch):
    from integration_hub import read_only_sync
    monkeypatch.setattr("tools.slack_reader.read_history", lambda *args, **kwargs: {
        "channel_id": "C1",
        "messages": [
            {"ts": "1785169528.000101", "timestamp": "2026-07-27 10:00 UTC",
             "user": "A", "user_id": "U1", "text": "First"},
            {"ts": "1785169528.000102", "timestamp": "2026-07-27 10:00 UTC",
             "user": "B", "user_id": "U2", "text": "Second"},
        ],
    })
    rows = await read_only_sync._slack({
        "external_id": "C1", "tenant_id": "T1", "config": {},
    }, "user@example.com")
    assert [row["source_id"] for row in rows] == [
        "C1:1785169528.000101", "C1:1785169528.000102",
    ]


@pytest.mark.asyncio
async def test_interaction_deduplication_is_account_scoped():
    class Repository(InteractionRepository):
        async def create_interaction(self, interaction, audit):
            identity = ("account_id", "source", "tenant_id", "source_id")
            if any(tuple(row[key] for key in identity) ==
                   tuple(interaction[key] for key in identity) for row in self.rows):
                return False
            self.rows.append(interaction)
            return True

    repository = Repository()
    service = AccountService(repository)
    payload = {
        "source": "slack", "tenant_id": "T1", "source_id": "C1:1.1",
        "occurred_at": "2026-07-27T10:00:00Z", "direction": "internal",
        "conversation_state": "monitoring", "summary": "Shared-channel update",
    }
    _, first = await service.ingest_interaction(
        {"account_id": "acc_1"}, payload, "owner@example.com", "r1"
    )
    _, second = await service.ingest_interaction(
        {"account_id": "acc_2"}, payload, "owner@example.com", "r2"
    )
    assert first is True and second is True


@pytest.mark.asyncio
async def test_repository_handles_duplicate_after_transaction_aborts():
    """A duplicate aborts MongoDB's transaction and must escape its callback.

    Catching it inside the callback causes the driver to commit an aborted
    transaction and report NoSuchTransaction to the sync worker.
    """
    repository = AccountRepository.__new__(AccountRepository)
    repository._transaction = AsyncMock(side_effect=DuplicateKeyError("duplicate"))

    created = await repository.create_interaction(
        {"account_id": "acc_1"}, {"audit_id": "audit_1"}
    )

    assert created is False
    repository._transaction.assert_awaited_once()


def test_connector_analysis_marks_customer_issues_as_waiting():
    from integration_hub.read_only_sync import _interaction
    row = _interaction(
        "slack", "T1", "1.1", "2026-07-27T10:00:00Z",
        "Customer: SDK initialization is failing, can you help?",
        direction="customer_to_plotline",
    )
    assert row["classification"] == "reported_issue"
    assert row["requires_response"] is True
    assert row["conversation_state"] == "waiting_on_plotline"
    assert row["evidence"]["source_id"] == "1.1"


@pytest.mark.asyncio
async def test_pylon_customer_discovery_groups_issues_by_stable_account_id(monkeypatch):
    from integration_hub import read_only_sync
    async def search_accounts(query, limit):
        assert query == "acm"
        assert limit == 50
        return {"accounts": [{
            "customer_id": "account-1", "name": "Acme",
            "domains": ["acme.test"],
        }]}
    monkeypatch.setattr("tools.pylon.search_accounts", search_accounts)
    rows = await read_only_sync.discover_pylon_customers("acm")
    assert rows == [{
        "customer_id": "account-1", "name": "Acme",
        "domains": ["acme.test"], "issue_count": None, "preview_issues": [],
    }]


@pytest.mark.asyncio
async def test_pylon_issue_page_is_account_scoped_and_cursor_paginated(monkeypatch):
    captured = {}

    async def api_post(path, body):
        captured.update(path=path, body=body)
        return {
            "data": [{
                "id": "issue-1", "title": "SDK issue", "state": "waiting_on_you",
                "number": 2850,
                "assignee_user": {"id": "user-1", "name": "Vamsi"},
                "updated_at": "2026-07-28T10:00:00Z",
                "account": {"id": "account-1"},
                "link": "https://app.usepylon.com/issues?issueNumber=2850",
            }],
            "pagination": {"has_next_page": True, "cursor": "next-page"},
        }

    monkeypatch.setattr("tools.pylon._api_post", api_post)
    from tools.pylon import list_account_issues_page
    result = await list_account_issues_page(
        "account-1", limit=25, cursor="current-page",
        state="waiting_on_you", query="SDK",
    )

    assert captured["path"] == "/issues/search"
    assert captured["body"]["cursor"] == "current-page"
    assert captured["body"]["limit"] == 25
    assert captured["body"]["filter"]["operator"] == "and"
    filters = captured["body"]["filter"]["subfilters"]
    assert {"field": "account_id", "operator": "equals", "value": "account-1"} in filters
    assert result["issues"][0]["id"] == "issue-1"
    assert result["issues"][0]["assignee"] == {"id": "user-1", "name": "Vamsi"}
    assert result["issues"][0]["url"] == (
        "https://app.usepylon.com/support/issues/views/"
        "ab8a8a4e-a550-4c1b-9479-c00066f233cb?issueNumber=2850"
    )
    assert result["pagination"]["next_cursor"] == "next-page"


@pytest.mark.asyncio
async def test_pylon_issue_page_caps_external_page_size(monkeypatch):
    async def api_post(_path, body):
        assert body["limit"] == 50
        return {"data": [], "pagination": {"has_next_page": False}}

    monkeypatch.setattr("tools.pylon._api_post", api_post)
    from tools.pylon import list_account_issues_page
    result = await list_account_issues_page("account-1", limit=500)
    assert result["pagination"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_pylon_issue_page_supports_combined_status_filter(monkeypatch):
    async def api_post(_path, body):
        state_filter = next(
            item for item in body["filter"]["subfilters"]
            if item["field"] == "state"
        )
        assert state_filter["value"] == [
            "waiting_on_customer", "closed", "resolved",
        ]
        return {"data": [], "pagination": {"has_next_page": False}}

    monkeypatch.setattr("tools.pylon._api_post", api_post)
    from tools.pylon import list_account_issues_page
    await list_account_issues_page(
        "account-1", state="waiting_on_customer,closed,resolved"
    )


@pytest.mark.asyncio
async def test_pylon_issue_page_uses_equals_for_single_status(monkeypatch):
    async def api_post(_path, body):
        state_filter = next(
            item for item in body["filter"]["subfilters"] if item["field"] == "state"
        )
        assert state_filter == {
            "field": "state", "operator": "equals", "value": "waiting_on_customer",
        }
        return {"data": [], "pagination": {"has_next_page": False}}
    monkeypatch.setattr("tools.pylon._api_post", api_post)
    from tools.pylon import list_account_issues_page
    await list_account_issues_page("account-1", state="waiting_on_customer")


def test_pylon_message_normalization_returns_real_safe_content():
    from tools.pylon import normalize_message

    message = normalize_message({
        "id": "message-1",
        "message_html": "<p>Hello <strong>team</strong>,</p><p>SDK is failing.</p>",
        "timestamp": "2026-07-28T10:00:00Z",
        "author": {"name": "Customer"},
        "source": "email",
        "is_private": False,
    })

    assert message == {
        "id": "message-1",
        "body": "Hello team,\nSDK is failing.",
        "author": "Customer",
        "timestamp": "2026-07-28T10:00:00Z",
        "source": "email",
        "is_private": False,
    }


def test_pylon_message_normalization_marks_internal_notes_private():
    from tools.pylon import normalize_message
    assert normalize_message({
        "id": "note-1", "body": "Agent-only context", "source": "internal_note",
    })["is_private"] is True


@pytest.mark.asyncio
async def test_pylon_issue_page_uses_plain_filter_for_account_only(monkeypatch):
    async def api_post(_path, body):
        assert body["filter"] == {
            "field": "account_id",
            "operator": "equals",
            "value": "account-1",
        }
        return {"data": [], "pagination": {"has_next_page": False}}

    monkeypatch.setattr("tools.pylon._api_post", api_post)
    from tools.pylon import list_account_issues_page
    await list_account_issues_page("account-1")


@pytest.mark.asyncio
async def test_pylon_customer_mapping_imports_all_issue_threads_read_only(monkeypatch):
    from integration_hub import read_only_sync
    async def list_issues(**_kwargs):
        return {"issues": [
            {"id": "i1", "customer": "Acme", "customer_id": "account-1"},
            {"id": "i2", "customer": "Other", "customer_id": "account-2"},
        ]}
    async def get_issue(issue_id):
        return {"data": {"id": issue_id, "title": "SDK issue", "state": "open"}}
    async def get_messages(issue_id):
        return {"data": [
            {"id": f"{issue_id}-m1", "created_at": "2026-07-27T10:00:00Z",
             "source": "customer", "body": "Can you help?"},
            {"id": f"{issue_id}-m2", "created_at": "2026-07-27T11:00:00Z",
             "source": "agent", "body": "We are checking."},
        ]}
    monkeypatch.setattr("tools.pylon.list_issues", list_issues)
    monkeypatch.setattr("tools.pylon.get_issue", get_issue)
    monkeypatch.setattr("tools.pylon.get_messages", get_messages)
    rows = await read_only_sync._pylon({
        "external_id": "account-1", "tenant_id": "pylon", "config": {"customer_name": "Acme"},
    }, "user@example.com")
    assert [row["source_id"] for row in rows] == ["i1-m1", "i1-m2"]
    assert rows[-1]["conversation_state"] == "waiting_on_customer"
    assert all(row["conversation_id"] == "i1" for row in rows)
