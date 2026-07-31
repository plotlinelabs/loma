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
