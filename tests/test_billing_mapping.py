import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from api import billing_mapping_routes
from api import dashboard_db
from api.billing_mapping_routes import (
    _build_view,
    _contract_account_id,
    _contract_cache,
    _expected_account_id,
    _items,
    _load_contract,
    _upstream_payload,
    classify_product,
    handle_list_billing_mappings,
)


# --------------------------------------------------------------------------
# Fake Motor collections: find(...)[.sort(...)].to_list(...)
# --------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return self

    async def to_list(self, length=None):
        return self._docs if length is None else self._docs[:length]


class FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query=None, projection=None):
        query = query or {}
        matched = [doc for doc in self.docs if self._matches(doc, query)]
        return FakeCursor(matched)

    async def find_one(self, query=None, projection=None):
        for doc in self.find(query)._docs:
            return doc
        return None

    @staticmethod
    def _matches(doc, query):
        for key, condition in query.items():
            value = doc.get(key)
            if isinstance(condition, dict) and "$in" in condition:
                if isinstance(value, list):
                    if not any(item in condition["$in"] for item in value):
                        return False
                elif value not in condition["$in"]:
                    return False
            elif isinstance(condition, dict):
                continue
            elif isinstance(value, list):
                if condition not in value:
                    return False
            elif value != condition:
                return False
        return True


ORG_A = ObjectId()
ORG_B = ObjectId()
PROD_A1 = ObjectId()
PROD_A2 = ObjectId()
PROD_B1 = ObjectId()
PROD_DELETED = ObjectId()


def build_fake_dbs():
    """Two orgs. Org A owns two products plus one dangling reference."""
    dashboard = SimpleNamespace(
        orgs=FakeCollection([
            {"_id": ORG_A, "name": "Acme", "products": [PROD_A1, PROD_A2, PROD_DELETED],
             "shouldDisableDashboard": False},
            {"_id": ORG_B, "name": "Globex", "products": [PROD_B1],
             "shouldDisableDashboard": True},
        ]),
        products=FakeCollection([
            {"_id": PROD_A1, "name": "Acme Prod", "billingId": "ctr_1"},
            {"_id": PROD_A2, "name": "Acme Dev", "billingId": "ctr_2"},
            {"_id": PROD_B1, "name": "Globex Prod", "billingId": "ctr_1"},
        ]),
    )
    loma = SimpleNamespace(billing_account_mappings=FakeCollection([]))
    return dashboard, loma


CONTRACTS = {
    "ctr_1": {"id": "ctr_1", "name": "Acme Annual", "status": "ACTIVE", "accountId": "acct_1"},
    "ctr_2": {"id": "ctr_2", "name": "Acme Old", "status": "FINISHED", "accountId": "acct_1"},
}
ACCOUNTS = {"acct_1": {"id": "acct_1", "name": "Acme Inc"}}


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    _contract_cache.clear()
    billing_mapping_routes._view_cache = None
    monkeypatch.setattr(
        billing_mapping_routes, "get_contract",
        AsyncMock(side_effect=lambda cid: {"data": CONTRACTS.get(cid)}),
    )
    monkeypatch.setattr(
        billing_mapping_routes, "get_account",
        AsyncMock(side_effect=lambda aid: {"data": ACCOUNTS.get(aid)}),
    )
    yield
    _contract_cache.clear()
    billing_mapping_routes._view_cache = None


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
def test_classify_product_states():
    active = {"status": "ACTIVE", "accountId": "acct_1"}
    assert classify_product(None, None, "acct_1") == "contract_missing"
    assert classify_product("ctr_1", None, "acct_1") == "invalid_contract"
    assert classify_product("ctr_1", {**active, "status": "FINISHED"}, "acct_1") == "inactive_contract"
    assert classify_product("ctr_1", active, "acct_2") == "account_mismatch"
    assert classify_product("ctr_1", active, "acct_1") == "correctly_linked"


def test_unknown_expected_account_is_not_a_mismatch():
    """With no expected account there is nothing to disagree with."""
    active = {"status": "ACTIVE", "accountId": "acct_1"}
    assert classify_product("ctr_1", active, None) == "correctly_linked"


def test_upstream_failure_is_unknown_not_invalid():
    assert classify_product("ctr_1", None, "acct_1", lookup_failed=True) == "unknown"


def test_expected_account_prefers_override_then_majority():
    contracts = [
        {"accountId": "acct_1"}, {"accountId": "acct_1"}, {"accountId": "acct_2"},
    ]
    assert _expected_account_id("acct_9", contracts) == ("acct_9", "override")
    assert _expected_account_id(None, contracts) == ("acct_1", "derived")
    assert _expected_account_id(None, []) == (None, "none")


def test_nested_account_and_list_response_shapes():
    assert _contract_account_id({"account": {"id": "acct_1"}}) == "acct_1"
    assert _items({"data": {"content": [{"id": "ctr_1"}]}}) == [{"id": "ctr_1"}]


def test_upstream_payload_distinguishes_errors_from_empty_results():
    assert _upstream_payload({"error": "timeout"}) == (None, "timeout")
    assert _upstream_payload({"data": {"id": "acct_1"}}) == ({"id": "acct_1"}, None)
    assert _upstream_payload({"data": None}) == (None, None)


# --------------------------------------------------------------------------
# The join
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_view_joins_products_through_the_org_array():
    """Products are owned via orgs.products[]; products.orgId does not exist."""
    dashboard, loma = build_fake_dbs()
    view = await _build_view(dashboard, loma)
    acme = next(org for org in view["organizations"] if org["name"] == "Acme")
    assert [p["name"] for p in acme["products"]] == ["Acme Prod", "Acme Dev"]


@pytest.mark.asyncio
async def test_view_tolerates_dangling_product_references():
    dashboard, loma = build_fake_dbs()
    view = await _build_view(dashboard, loma)
    acme = next(org for org in view["organizations"] if org["name"] == "Acme")
    assert acme["danglingProductRefs"] == 1


@pytest.mark.asyncio
async def test_view_reads_should_disable_dashboard_not_is_blocked():
    dashboard, loma = build_fake_dbs()
    view = await _build_view(dashboard, loma)
    by_name = {org["name"]: org for org in view["organizations"]}
    assert by_name["Acme"]["dashboardDisabled"] is False
    assert by_name["Globex"]["dashboardDisabled"] is True


@pytest.mark.asyncio
async def test_account_name_is_derived_from_the_contract():
    """Operators never have to type an account id for the common case."""
    dashboard, loma = build_fake_dbs()
    view = await _build_view(dashboard, loma)
    acme = next(org for org in view["organizations"] if org["name"] == "Acme")
    assert acme["accountId"] == "acct_1"
    assert acme["accountName"] == "Acme Inc"
    assert acme["accountSource"] == "derived"


@pytest.mark.asyncio
async def test_contract_shared_across_organizations_is_flagged():
    dashboard, loma = build_fake_dbs()
    view = await _build_view(dashboard, loma)
    acme = next(org for org in view["organizations"] if org["name"] == "Acme")
    shared = next(p for p in acme["products"] if p["billingId"] == "ctr_1")
    assert shared["sharedWithOrganizations"] == ["Globex"]
    unshared = next(p for p in acme["products"] if p["billingId"] == "ctr_2")
    assert unshared["sharedWithOrganizations"] == []


@pytest.mark.asyncio
async def test_inactive_contract_is_classified_per_product():
    dashboard, loma = build_fake_dbs()
    view = await _build_view(dashboard, loma)
    acme = next(org for org in view["organizations"] if org["name"] == "Acme")
    assert acme["summary"]["correctly_linked"] == 1
    assert acme["summary"]["inactive_contract"] == 1


# --------------------------------------------------------------------------
# Handler: pagination total must agree with the rows returned
# --------------------------------------------------------------------------
def fake_request(**query):
    return SimpleNamespace(query=query, match_info={})


async def call_list(monkeypatch, **query):
    dashboard, loma = build_fake_dbs()
    monkeypatch.setattr(billing_mapping_routes, "require_operator_or_above", lambda r: None)
    monkeypatch.setattr(billing_mapping_routes, "get_dashboard_db", lambda: dashboard)
    monkeypatch.setattr(billing_mapping_routes, "get_db", lambda: loma)
    response = await handle_list_billing_mappings(fake_request(**query))
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_total_matches_filtered_rows(monkeypatch):
    """The header count is derived from the same filtered list the page slices."""
    body = await call_list(monkeypatch, status="inactive_contract")
    assert body["pagination"]["total"] == len(body["organizations"]) == 1
    assert body["organizations"][0]["name"] == "Acme"


@pytest.mark.asyncio
async def test_unfiltered_total_counts_every_org(monkeypatch):
    body = await call_list(monkeypatch)
    assert body["pagination"]["total"] == 2
    assert body["totals"]["correctly_linked"] == 2
    assert body["totals"]["inactive_contract"] == 1


@pytest.mark.asyncio
async def test_search_filters_by_org_and_account_name(monkeypatch):
    body = await call_list(monkeypatch, q="globex")
    assert [org["name"] for org in body["organizations"]] == ["Globex"]


@pytest.mark.asyncio
async def test_invalid_status_filter_is_rejected(monkeypatch):
    body = await call_list(monkeypatch, status="not_a_status")
    assert "Invalid status filter" in body["error"]


# --------------------------------------------------------------------------
# Optional dashboard connection
# --------------------------------------------------------------------------
def test_dashboard_db_accepts_existing_dashboard_uri(monkeypatch):
    monkeypatch.delenv("DASHBOARD_MONGODB_URI", raising=False)
    monkeypatch.setenv("MONGODB_DASHBOARD_URI", "mongodb://localhost:27017/app")
    assert dashboard_db._dashboard_mongodb_uri() == "mongodb://localhost:27017/app"

    monkeypatch.setenv("DASHBOARD_MONGODB_URI", "mongodb://override:27017/app")
    assert dashboard_db._dashboard_mongodb_uri() == "mongodb://override:27017/app"


@pytest.mark.asyncio
async def test_dashboard_db_connection_failure_is_optional(monkeypatch):
    client = MagicMock()
    database = MagicMock()
    database.command = AsyncMock(side_effect=ConnectionError("unreachable"))
    client.__getitem__.return_value = database
    monkeypatch.setenv("DASHBOARD_MONGODB_URI", "mongodb://unreachable:27017/app")
    monkeypatch.setattr(dashboard_db, "AsyncIOMotorClient", lambda *a, **k: client)

    await dashboard_db.init_dashboard_db()

    assert dashboard_db.get_dashboard_db() is None
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_contract_cache_is_bounded(monkeypatch):
    _contract_cache.clear()
    monkeypatch.setattr(billing_mapping_routes, "_CONTRACT_CACHE_MAX_ENTRIES", 2)
    semaphore = asyncio.Semaphore(1)

    await _load_contract("ctr_1", semaphore)
    await _load_contract("ctr_2", semaphore)
    await _load_contract("ctr_3", semaphore)

    assert list(_contract_cache) == ["ctr_2", "ctr_3"]


@pytest.mark.asyncio
async def test_failed_contract_lookup_is_recorded(monkeypatch):
    monkeypatch.setattr(
        billing_mapping_routes, "get_contract",
        AsyncMock(side_effect=lambda cid: {"error": "boom"}),
    )
    errored: set[str] = set()
    result = await _load_contract("ctr_1", asyncio.Semaphore(1), errored=errored)
    assert result is None and errored == {"ctr_1"}
