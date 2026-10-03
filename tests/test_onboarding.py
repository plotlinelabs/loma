"""Tests for the onboarding tracker service and routes."""

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api import onboarding_routes as routes
from observability import onboarding as svc


class FakeCollection:
    """Minimal in-memory stand-in for the motor collections we use."""

    def __init__(self):
        self.docs = []

    async def find_one(self, query, projection=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update):
        for d in self.docs:
            if _match(d, query):
                for path, value in update.get("$set", {}).items():
                    parts = path.split(".")
                    target = d
                    for p in parts[:-1]:
                        target = target.setdefault(p, {})
                    target[parts[-1]] = value
                return

    def find(self, query=None, projection=None):
        docs = [dict(d) for d in self.docs if _match(d, query or {})]
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.to_list = AsyncMock(return_value=docs)
        return cursor


def _get(doc, path):
    for part in path.split("."):
        if not isinstance(doc, dict):
            return None
        doc = doc.get(part)
    return doc


def _match(doc, query):
    for key, cond in query.items():
        value = _get(doc, key)
        if isinstance(cond, dict) and "$ne" in cond:
            if value == cond["$ne"]:
                return False
        elif value != cond:
            return False
    return True


def make_db():
    db = MagicMock()
    db.onboarding_records = FakeCollection()
    db.onboarding_events = FakeCollection()
    db.onboarding_settings = FakeCollection()
    return db


@pytest.mark.asyncio
async def test_create_and_human_value_protected_from_sync():
    db = make_db()
    rec = await svc.create_record(db, {"name": "Careem - Pay", "account": "Careem", "stage": "sdk",
                                       "fields": {"org_id": "org1", "arr": "48000"}},
                                  actor="a@x.com")
    assert rec["stage"] == "sdk"
    assert rec["fields"]["arr"] == 48000
    assert rec["meta"]["arr"]["source"] == "human"

    _, applied, skipped = await svc.apply_changes(
        db, rec, {"arr": 50000, "first_live": "2026-01-05"}, actor="bot", source="hubspot")
    assert skipped == ["arr"]
    assert [c["field"] for c in applied] == ["first_live"]

    updated, applied, _ = await svc.apply_changes(
        db, rec, {"arr": 50000}, actor="bot", source="hubspot", force=True)
    assert updated["fields"]["arr"] == 50000
    assert len(db.onboarding_events.docs) == 3


@pytest.mark.asyncio
async def test_rejects_unknown_field_and_stage():
    db = make_db()
    rec = await svc.create_record(db, {"name": "X"}, actor="a")
    with pytest.raises(ValueError):
        await svc.apply_changes(db, rec, {"bogus": 1}, actor="a")
    with pytest.raises(ValueError):
        await svc.apply_changes(db, rec, {"stage": "nope"}, actor="a")
    with pytest.raises(ValueError):
        await svc.apply_changes(db, rec, {"first_live": "5th May"}, actor="a")


@pytest.mark.asyncio
async def test_find_record_org_match_only_when_unique():
    db = make_db()
    await svc.create_record(db, {"name": "BharatPe - Merchant", "fields": {"org_id": "o1"}}, actor="a")
    await svc.create_record(db, {"name": "BharatPe - 12% Club", "fields": {"org_id": "o1"}}, actor="a")
    assert await svc.find_record(db, org_id="o1") is None
    found = await svc.find_record(db, org_id="o1", name="bharatpe - 12% club")
    assert found["name"] == "BharatPe - 12% Club"


def test_derive_days_and_flags():
    cfg = svc.DEFAULT_CONFIG
    today = date(2026, 10, 2)
    rec = {"stage": "pilot", "fields": {
        "dev_provisioned": "2025-06-01", "first_live": "2025-09-14", "client_blocked_days": 74,
        "next_step_due": (today - timedelta(days=1)).isoformat(),
        "pilot_end": (today + timedelta(days=10)).isoformat(), "blocker_owner": "Client eng"}}
    d = svc.derive(rec, cfg, today)
    assert d["days_to_live_gross"] == 105
    assert d["days_to_live_net"] == 31
    assert d["pilot_days_left"] == 10
    assert {"overdue", "blocked", "pilot_ending"} <= set(d["flags"])
    rec["stage"] = "closed"
    assert svc.derive(rec, cfg, today)["flags"] == []


class FakeRequest(dict):
    def __init__(self, *, user_email="u@x.com", role="chatter", body=None, record_id="r"):
        super().__init__(user_email=user_email, system_role=role)
        self.match_info = {"record_id": record_id}
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_routes_create_update_and_edit_gate():
    db = make_db()
    with patch.object(routes, "get_db", return_value=db):
        resp = await routes.handle_create(FakeRequest(body={"name": "Iku", "stage": "kickoff"}))
        assert resp.status == 201
        rid = json.loads(resp.body)["record"]["record_id"]

        resp = await routes.handle_update(FakeRequest(record_id=rid, body={
            "changes": {"stage": "sdk", "next_step": "Share PN docs"}}))
        assert resp.status == 200
        assert json.loads(resp.body)["record"]["stage"] == "sdk"

        resp = await routes.handle_get(FakeRequest(record_id=rid))
        body = json.loads(resp.body)
        assert len(body["events"]) == 2

        db.onboarding_settings.docs.append({"_id": "config", "edit_min_role": "operator"})
        resp = await routes.handle_config(FakeRequest())
        assert json.loads(resp.body)["can_edit"] is False
        with pytest.raises(Exception) as exc:
            await routes.handle_update(FakeRequest(record_id=rid, body={"changes": {"stage": "qa"}}))
        assert getattr(exc.value, "status", None) == 403


@pytest.mark.asyncio
async def test_routes_require_auth():
    with patch.object(routes, "get_db", return_value=make_db()):
        with pytest.raises(Exception) as exc:
            await routes.handle_list(FakeRequest(user_email=""))
    assert getattr(exc.value, "status", None) == 401


@pytest.mark.asyncio
async def test_noop_sync_does_not_reset_staleness():
    db = make_db()
    rec = await svc.create_record(db, {"name": "Iku", "fields": {"org_id": "o1"}}, actor="a@x.com")
    before = (await svc.get_record(db, rec["record_id"]))["updated_at"]
    events = len(db.onboarding_events.docs)

    # Automated sync with a note but no real change: nothing logged, no bump.
    await svc.apply_changes(db, rec, {"org_id": "o1"}, actor="bot", source="mongodb", note="daily sync")
    assert len(db.onboarding_events.docs) == events
    assert (await svc.get_record(db, rec["record_id"]))["updated_at"] == before

    # Human note-only save: logged as a comment, still no bump.
    await svc.apply_changes(db, rec, {}, actor="a@x.com", note="Called client, waiting on build")
    assert len(db.onboarding_events.docs) == events + 1
    assert (await svc.get_record(db, rec["record_id"]))["updated_at"] == before
