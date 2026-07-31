import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.billing_mapping_routes import (
    _contract_account_id,
    _contract_cache,
    _items,
    _load_contract,
    _store_status_snapshots,
    _upstream_payload,
    classify_product,
)
from api import billing_mapping_routes
from api import plotline_db


def test_classify_product_states():
    active = {"status": "ACTIVE", "accountId": "acct_1"}
    assert classify_product(None, None, "acct_1") == "contract_missing"
    assert classify_product("ctr_1", None, "acct_1") == "invalid_contract"
    assert classify_product("ctr_1", {**active, "status": "FINISHED"}, "acct_1") == "inactive_contract"
    assert classify_product("ctr_1", active, None) == "account_not_linked"
    assert classify_product("ctr_1", active, "acct_2") == "account_mismatch"
    assert classify_product("ctr_1", active, "acct_1") == "correctly_linked"


def test_nested_account_and_list_response_shapes():
    assert _contract_account_id({"account": {"id": "acct_1"}}) == "acct_1"
    assert _items({"data": {"content": [{"id": "ctr_1"}]}}) == [{"id": "ctr_1"}]


def test_plotline_db_accepts_existing_dashboard_uri(monkeypatch):
    monkeypatch.delenv("PLOTLINE_MONGODB_URI", raising=False)
    monkeypatch.setenv("MONGODB_DASHBOARD_URI", "mongodb://localhost:27017/plotline")
    assert plotline_db._dashboard_mongodb_uri() == "mongodb://localhost:27017/plotline"

    monkeypatch.setenv("PLOTLINE_MONGODB_URI", "mongodb://override:27017/plotline")
    assert plotline_db._dashboard_mongodb_uri() == "mongodb://override:27017/plotline"


def test_upstream_payload_distinguishes_errors_from_empty_results():
    assert _upstream_payload({"error": "timeout"}) == (None, "timeout")
    assert _upstream_payload({"data": {"id": "acct_1"}}) == ({"id": "acct_1"}, None)
    assert _upstream_payload({"data": None}) == (None, None)


@pytest.mark.asyncio
async def test_plotline_db_connection_failure_is_optional(monkeypatch):
    client = MagicMock()
    database = MagicMock()
    database.command = AsyncMock(side_effect=ConnectionError("unreachable"))
    client.__getitem__.return_value = database
    monkeypatch.setenv("PLOTLINE_MONGODB_URI", "mongodb://unreachable:27017/plotline")
    monkeypatch.setattr(plotline_db, "AsyncIOMotorClient", lambda *args, **kwargs: client)

    await plotline_db.init_plotline_db()

    assert plotline_db.get_plotline_db() is None
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_contract_cache_is_bounded(monkeypatch):
    _contract_cache.clear()
    monkeypatch.setattr(billing_mapping_routes, "_CONTRACT_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(
        billing_mapping_routes,
        "get_contract",
        AsyncMock(side_effect=lambda contract_id: {"data": {"id": contract_id}}),
    )
    semaphore = asyncio.Semaphore(1)

    await _load_contract("ctr_1", semaphore)
    await _load_contract("ctr_2", semaphore)
    await _load_contract("ctr_3", semaphore)

    assert list(_contract_cache) == ["ctr_2", "ctr_3"]


@pytest.mark.asyncio
async def test_status_snapshots_are_persisted_per_product():
    collection = MagicMock()
    collection.bulk_write = AsyncMock()
    database = MagicMock(billing_product_statuses=collection)

    await _store_status_snapshots(database, [{
        "id": "org_1",
        "products": [
            {"id": "product_1", "status": "invalid_contract"},
            {"id": "product_2", "status": "correctly_linked"},
        ],
    }])

    collection.bulk_write.assert_awaited_once()
    operations = collection.bulk_write.await_args.args[0]
    assert len(operations) == 2
    assert operations[0]._filter == {"product_id": "product_1"}
    assert operations[0]._doc["$set"]["organization_id"] == "org_1"
    assert operations[0]._doc["$set"]["status"] == "invalid_contract"
    assert collection.bulk_write.await_args.kwargs == {"ordered": False}


@pytest.mark.asyncio
async def test_snapshot_indexes_cover_worklist_queries():
    collection = MagicMock()
    collection.create_index = AsyncMock()
    database = MagicMock(billing_product_statuses=collection)

    await billing_mapping_routes._ensure_snapshot_indexes(database)

    assert collection.create_index.await_args_list[0].args == ("product_id",)
    assert collection.create_index.await_args_list[0].kwargs == {"unique": True}
    assert collection.create_index.await_args_list[1].args == ([
        ("status", 1), ("organization_id", 1)
    ],)


def test_snapshot_operation_preserves_status_when_upstream_failed():
    from datetime import datetime, timezone

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    healthy = billing_mapping_routes._snapshot_operation(
        "p1", "o1", now, status="correctly_linked", run_id="r1"
    )
    assert healthy._filter == {"product_id": "p1"}
    assert healthy._doc["$set"]["status"] == "correctly_linked"
    assert healthy._doc["$set"]["run_id"] == "r1"
    assert "$setOnInsert" not in healthy._doc

    # Upstream failure: never overwrite a known status; only seed a neutral
    # "unknown" on insert, but still stamp run_id so the sweep keeps the row.
    errored = billing_mapping_routes._snapshot_operation("p1", "o1", now, status=None, run_id="r1")
    assert "status" not in errored._doc["$set"]
    assert errored._doc["$setOnInsert"] == {"status": "unknown"}
    assert errored._doc["$set"]["run_id"] == "r1"


@pytest.mark.asyncio
async def test_load_contracts_reports_upstream_errors(monkeypatch):
    _contract_cache.clear()

    async def fake_get_contract(contract_id):
        if contract_id == "ctr_bad":
            return {"error": "timeout"}
        return {"data": {"id": contract_id}}

    monkeypatch.setattr(billing_mapping_routes, "get_contract", fake_get_contract)

    contract_by_id, errored = await billing_mapping_routes._load_contracts(["ctr_ok", "ctr_bad"])

    assert contract_by_id == {"ctr_ok": {"id": "ctr_ok"}, "ctr_bad": None}
    assert errored == {"ctr_bad"}


@pytest.mark.asyncio
async def test_acquire_reconciliation_lease_grants_and_denies():
    from datetime import datetime, timezone

    from pymongo.errors import DuplicateKeyError

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    leases = MagicMock()
    database = MagicMock(billing_reconciliation_leases=leases)

    leases.update_one = AsyncMock(return_value=MagicMock(modified_count=1, upserted_id=None))
    assert await billing_mapping_routes._acquire_reconciliation_lease(database, now) is True

    leases.update_one = AsyncMock(return_value=MagicMock(modified_count=0, upserted_id="status_snapshot"))
    assert await billing_mapping_routes._acquire_reconciliation_lease(database, now) is True

    # Another unexpired lease already exists: no match, no takeover, no insert.
    leases.update_one = AsyncMock(return_value=MagicMock(modified_count=0, upserted_id=None))
    assert await billing_mapping_routes._acquire_reconciliation_lease(database, now) is False

    # Concurrent insert lost the race on the unique _id.
    leases.update_one = AsyncMock(side_effect=DuplicateKeyError("dup"))
    assert await billing_mapping_routes._acquire_reconciliation_lease(database, now) is False


@pytest.mark.asyncio
async def test_reconcile_org_batch_tolerates_upstream_errors(monkeypatch):
    _contract_cache.clear()
    from datetime import datetime, timezone

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    async def fake_get_contract(contract_id):
        if contract_id == "ctr_bad":
            return {"error": "timeout"}
        return {"data": {"id": contract_id, "status": "ACTIVE", "account": {"id": "acct_1"}}}

    monkeypatch.setattr(billing_mapping_routes, "get_contract", fake_get_contract)

    def cursor(docs):
        result = MagicMock()
        result.to_list = AsyncMock(return_value=docs)
        return result

    plotline_db = MagicMock()
    plotline_db.products.find.return_value = cursor([
        {"_id": "p_ok", "orgId": "o1", "billingId": "ctr_ok"},
        {"_id": "p_bad", "orgId": "o1", "billingId": "ctr_bad"},
    ])
    loma_db = MagicMock()
    loma_db.billing_account_mappings.find.return_value = cursor([
        {"organization_id": "o1", "monetize_now_account_id": "acct_1"},
    ])
    loma_db.billing_product_statuses.bulk_write = AsyncMock()

    # One failing contract must not raise or abort the whole batch.
    await billing_mapping_routes._reconcile_org_batch(
        plotline_db, loma_db, [{"_id": "o1"}], "run_1", now
    )

    loma_db.billing_product_statuses.bulk_write.assert_awaited_once()
    operations = loma_db.billing_product_statuses.bulk_write.await_args.args[0]
    by_product = {op._filter["product_id"]: op._doc for op in operations}
    assert by_product["p_ok"]["$set"]["status"] == "correctly_linked"
    assert by_product["p_ok"]["$set"]["run_id"] == "run_1"
    assert "status" not in by_product["p_bad"]["$set"]
    assert by_product["p_bad"]["$setOnInsert"] == {"status": "unknown"}
    assert by_product["p_bad"]["$set"]["run_id"] == "run_1"
