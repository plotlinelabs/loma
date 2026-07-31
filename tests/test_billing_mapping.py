import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError

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


class _FakeLeaseCollection:
    """Minimal in-memory stand-in that enforces the Mongo semantics the lease relies
    on: a unique ``_id`` (upsert on an existing doc that the filter did not match
    raises ``DuplicateKeyError``) plus the conditional expiry ``$or``. This proves the
    actual mutual-exclusion guarantee, not just the Python return-value mapping."""

    def __init__(self):
        self.doc = None

    def _matches(self, filt):
        if self.doc is None or self.doc.get("_id") != filt.get("_id"):
            return False
        if "owner" in filt and self.doc.get("owner") != filt["owner"]:
            return False
        if "$or" in filt:
            now = filt["$or"][0]["lease_expires_at"]["$lte"]
            expires = self.doc.get("lease_expires_at")
            if not ("lease_expires_at" not in self.doc or (expires is not None and expires <= now)):
                return False
        return True

    async def update_one(self, filt, update, upsert=False):
        if self._matches(filt):
            self.doc.update(update.get("$set", {}))
            return MagicMock(modified_count=1, matched_count=1, upserted_id=None)
        if upsert:
            if self.doc is not None:
                raise DuplicateKeyError("dup _id")
            self.doc = {"_id": filt["_id"]}
            self.doc.update(update.get("$set", {}))
            return MagicMock(modified_count=0, matched_count=0, upserted_id=filt["_id"])
        return MagicMock(modified_count=0, matched_count=0, upserted_id=None)


def test_worklist_statuses_expose_unknown_for_filtering():
    # A product whose upstream lookup failed is seeded "unknown"; operators must be
    # able to filter for it so a genuinely broken product cannot silently vanish.
    assert "unknown" in billing_mapping_routes._WORKLIST_STATUSES


@pytest.mark.asyncio
async def test_reconciliation_lease_is_mutually_exclusive_over_fake_mongo(monkeypatch):
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    leases = _FakeLeaseCollection()
    database = MagicMock(billing_reconciliation_leases=leases)

    monkeypatch.setattr(billing_mapping_routes, "_RECONCILIATION_OWNER", "owner_a")
    assert await billing_mapping_routes._acquire_reconciliation_lease(database, t0) is True

    # A second instance cannot take an unexpired lease held by owner_a.
    monkeypatch.setattr(billing_mapping_routes, "_RECONCILIATION_OWNER", "owner_b")
    assert await billing_mapping_routes._acquire_reconciliation_lease(
        database, t0 + timedelta(minutes=1)
    ) is False

    # After the TTL expires, exactly one other instance takes over.
    later = t0 + timedelta(minutes=billing_mapping_routes._RECONCILIATION_LEASE_MINUTES + 1)
    assert await billing_mapping_routes._acquire_reconciliation_lease(database, later) is True
    assert leases.doc["owner"] == "owner_b"

    # The evicted owner can no longer renew a lease it lost; the new owner can.
    monkeypatch.setattr(billing_mapping_routes, "_RECONCILIATION_OWNER", "owner_a")
    assert await billing_mapping_routes._renew_reconciliation_lease(database, later) is False
    monkeypatch.setattr(billing_mapping_routes, "_RECONCILIATION_OWNER", "owner_b")
    assert await billing_mapping_routes._renew_reconciliation_lease(database, later) is True


@pytest.mark.asyncio
async def test_reconcile_all_statuses_stamps_forward_and_cleans_from_run_start(monkeypatch):
    captured = {}

    def cursor(batches):
        source = iter(batches)
        handle = MagicMock()

        async def to_list(_size):
            try:
                return next(source)
            except StopIteration:
                return []

        handle.to_list = to_list
        return handle

    plotline = MagicMock()
    plotline.orgs.find.return_value.sort.return_value = cursor([[{"_id": "o1"}], []])
    loma = MagicMock()
    loma.billing_product_statuses.create_index = AsyncMock()
    loma.billing_product_statuses.delete_many = AsyncMock()
    loma.billing_reconciliation_state.find_one = AsyncMock(return_value=None)
    loma.billing_reconciliation_state.update_one = AsyncMock()
    loma.billing_reconciliation_leases.delete_one = AsyncMock()

    monkeypatch.setattr(billing_mapping_routes, "get_plotline_db", lambda: plotline)
    monkeypatch.setattr(billing_mapping_routes, "get_db", lambda: loma)
    monkeypatch.setattr(
        billing_mapping_routes, "_acquire_reconciliation_lease", AsyncMock(return_value=True)
    )
    renew = AsyncMock(return_value=True)
    monkeypatch.setattr(billing_mapping_routes, "_renew_reconciliation_lease", renew)

    async def fake_batch(_pl, _lo, _docs, run_id, checked_at):
        captured["checked_at"] = checked_at
        captured["run_id"] = run_id

    monkeypatch.setattr(billing_mapping_routes, "_reconcile_org_batch", fake_batch)

    await billing_mapping_routes._reconcile_all_statuses()

    # The lease is renewed before the batch runs, and cleanup deletes only rows older
    # than the run start while the batch is stamped at/after that start (forward-only).
    renew.assert_awaited()
    run_start = loma.billing_product_statuses.delete_many.await_args.args[0]["checked_at"]["$lt"]
    assert captured["checked_at"] >= run_start


@pytest.mark.asyncio
async def test_monetizenow_missing_config_returns_error_not_raise(monkeypatch):
    # Missing MONETIZE_NOW_* must degrade to the {"error": ...} contract every caller
    # handles, never raise and surface as an opaque 500 on the billing page load.
    from tools import monetize_now

    monkeypatch.delenv("MONETIZE_NOW_API_KEY", raising=False)
    monkeypatch.delenv("MONETIZE_NOW_BASE_URL", raising=False)

    result = await monetize_now.get_contract("ctr_1")
    assert isinstance(result, dict) and result.get("error")


@pytest.mark.asyncio
async def test_load_contract_tolerates_tool_exception(monkeypatch):
    _contract_cache.clear()

    async def boom(_contract_id):
        raise RuntimeError("MONETIZE_NOW_API_KEY not set")

    monkeypatch.setattr(billing_mapping_routes, "get_contract", boom)
    errored: set[str] = set()

    # A raising tool must not propagate: the contract loader records the failure and
    # returns None so the page classifies conservatively instead of 500-ing.
    result = await _load_contract("ctr_1", asyncio.Semaphore(1), errored=errored)
    assert result is None
    assert "ctr_1" in errored


@pytest.mark.asyncio
async def test_plotline_db_status_reports_missing_env(monkeypatch):
    monkeypatch.delenv("PLOTLINE_MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DASHBOARD_URI", raising=False)
    await plotline_db.init_plotline_db()
    assert plotline_db.get_plotline_db_status() == "env-missing"


@pytest.mark.asyncio
async def test_reconcile_releases_lease_when_state_write_fails(monkeypatch):
    # Even if the very first state write throws, the lease must be released (finally)
    # and the exception must not propagate out to kill the reconciliation task.
    plotline = MagicMock()
    loma = MagicMock()
    loma.billing_reconciliation_state.find_one = AsyncMock(return_value=None)
    loma.billing_reconciliation_state.update_one = AsyncMock(side_effect=RuntimeError("db blip"))
    loma.billing_reconciliation_leases.delete_one = AsyncMock()

    monkeypatch.setattr(billing_mapping_routes, "get_plotline_db", lambda: plotline)
    monkeypatch.setattr(billing_mapping_routes, "get_db", lambda: loma)
    monkeypatch.setattr(
        billing_mapping_routes, "_acquire_reconciliation_lease", AsyncMock(return_value=True)
    )

    await billing_mapping_routes._reconcile_all_statuses()  # must not raise

    loma.billing_reconciliation_leases.delete_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconciliation_loop_survives_a_failed_cycle(monkeypatch):
    # One crashing sweep must not terminate the loop: the next tick still runs.
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")

    sleeps = {"n": 0}

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(billing_mapping_routes, "_reconcile_all_statuses", flaky)
    monkeypatch.setattr(billing_mapping_routes.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(billing_mapping_routes, "get_db", lambda: None)

    gen = billing_mapping_routes._reconciliation_context(MagicMock())
    await gen.__anext__()  # startup: spawns the loop task
    task = [t for t in asyncio.all_tasks() if t.get_name() == "billing-status-reconciliation"][0]
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["n"] >= 2  # ran again after the first cycle raised
