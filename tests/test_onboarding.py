"""Tests for the onboarding tracker service and routes."""

import copy
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
                return copy.deepcopy(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    async def update_one(self, query, update, upsert=False):
        target_doc = next((d for d in self.docs if _match(d, query)), None)
        if target_doc is None:
            if not upsert:
                return
            target_doc = dict(query)
            self.docs.append(target_doc)
        for path, value in update.get("$set", {}).items():
            parts = path.split(".")
            target = target_doc
            for p in parts[:-1]:
                target = target.setdefault(p, {})
            target[parts[-1]] = copy.deepcopy(value)

    def find(self, query=None, projection=None):
        docs = [copy.deepcopy(d) for d in self.docs if _match(d, query or {})]
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
    db.onboarding_settings_history = FakeCollection()
    return db


def cfg():
    return svc._resolve_options(copy.deepcopy(svc.DEFAULT_CONFIG))


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
async def test_rejects_unknown_field_stage_and_module():
    db = make_db()
    rec = await svc.create_record(db, {"name": "X"}, actor="a")
    with pytest.raises(ValueError):
        await svc.apply_changes(db, rec, {"bogus": 1}, actor="a")
    with pytest.raises(ValueError):
        await svc.apply_changes(db, rec, {"stage": "nope"}, actor="a")
    with pytest.raises(ValueError):
        await svc.apply_changes(db, rec, {"first_live": "5th May"}, actor="a")
    with pytest.raises(ValueError, match="unknown module"):
        await svc.apply_changes(db, rec, {"modules_paid": "Widgets,Hologram"}, actor="a")
    _, applied, _ = await svc.apply_changes(db, rec, {"modules_paid": "Widgets, Stories"}, actor="a")
    assert applied[0]["new"] == ["Widgets", "Stories"]


@pytest.mark.asyncio
async def test_find_record_org_match_only_when_unique():
    db = make_db()
    await svc.create_record(db, {"name": "BharatPe - Merchant", "fields": {"org_id": "o1"}}, actor="a")
    await svc.create_record(db, {"name": "BharatPe - 12% Club", "fields": {"org_id": "o1"}}, actor="a")
    assert await svc.find_record(db, org_id="o1") is None
    found = await svc.find_record(db, org_id="o1", name="bharatpe - 12% club")
    assert found["name"] == "BharatPe - 12% Club"


def test_derive_days_and_flags():
    today = date(2026, 10, 2)
    rec = {"stage": "pilot", "fields": {
        "dev_provisioned": "2025-06-01", "first_live": "2025-09-14", "client_blocked_days": 74,
        "next_step_due": (today - timedelta(days=1)).isoformat(),
        "pilot_end": (today + timedelta(days=10)).isoformat(), "blocker_owner": "Client eng"}}
    d = svc.derive(rec, cfg(), today)
    assert d["days_to_live_gross"] == 105
    assert d["days_to_live_net"] == 31
    assert d["pilot_days_left"] == 10
    assert {"overdue", "blocked", "pilot_ending"} <= set(d["flags"])
    rec["stage"] = "closed"
    assert svc.derive(rec, cfg(), today)["flags"] == []


def test_first_campaign_needs_min_users_and_drives_suggestion():
    today = date(2026, 10, 2)
    rec = {"stage": "live", "fields": {"first_live": "2026-09-01",
                                        "first_campaign_live": "2026-09-05",
                                        "first_campaign_users": 40}}
    d = svc.derive(rec, cfg(), today)
    # 40 users is a test campaign: not a first campaign, so the app is idle.
    assert d["first_campaign_qualified"] is False
    assert d["days_live_without_campaign"] == 31
    assert "idle" in d["flags"]
    assert d["suggested_stage"] is None

    rec["fields"]["first_campaign_users"] = 100
    d = svc.derive(rec, cfg(), today)
    assert d["first_campaign_qualified"] is True
    assert d["days_live_to_first_campaign"] == 4
    assert "idle" not in d["flags"]
    assert d["suggested_stage"] == "first_campaign"

    rec["fields"]["live_campaigns"] = 3
    assert svc.derive(rec, cfg(), today)["suggested_stage"] == "pilot"
    # Never suggests a step back.
    rec["stage"] = "pilot"
    assert svc.derive(rec, cfg(), today)["suggested_stage"] is None
    # The threshold is a template rule.
    c = cfg()
    c["rules"]["first_campaign_min_users"] = 500
    rec["stage"] = "live"
    assert svc.derive(rec, c, today)["first_campaign_qualified"] is False


def test_module_gaps_and_flags():
    today = date(2026, 10, 2)
    rec = {"stage": "first_campaign", "fields": {
        "modules_enabled": ["Nudges", "Widgets", "Luck games"],
        "modules_integrated": ["Nudges", "Widgets"],
        "modules_in_use": ["Nudges"]}}
    d = svc.derive(rec, cfg(), today)
    # Nothing ticked as paid yet: no paid-based gaps, only integrated-not-used.
    assert d["module_gaps"]["paid_not_integrated"] == []
    assert d["module_gaps"]["enabled_not_paid"] == []
    assert d["module_gaps"]["integrated_not_used"] == ["Widgets"]
    assert "modules_paid" in d["missing_required"]

    rec["fields"]["modules_paid"] = ["Nudges", "Widgets", "Stories"]
    d = svc.derive(rec, cfg(), today)
    assert d["module_gaps"]["paid_not_integrated"] == ["Stories"]
    assert d["module_gaps"]["enabled_not_paid"] == ["Luck games"]
    assert "Push" in d["module_gaps"]["upsell"]
    # Upsell is reported per group: nothing paid in these groups, In-app is covered.
    assert "In-app campaigns" not in d["module_gaps"]["upsell_groups"]
    assert {"Outside-app campaigns", "Gamification", "Journeys"} <= set(
        d["module_gaps"]["upsell_groups"])
    assert "Core SDK" not in d["module_gaps"]["upsell_groups"]
    assert {"paid_gap", "unpaid_enabled"} <= set(d["flags"])
    # A paid module not yet integrated is expected before the SDK is live.
    rec["stage"] = "sdk"
    assert "paid_gap" not in svc.derive(rec, cfg(), today)["flags"]


def test_integration_items_scope_done_and_pending():
    today = date(2026, 10, 2)
    rec = {"stage": "sdk", "fields": {"modules_paid": ["Nudges", "Push", "Widgets"]}}
    d = svc.derive(rec, cfg(), today)
    # Nothing recorded yet: needed is known, but nothing is reported as pending.
    assert d["integration"]["tracked"] is False and d["integration"]["pending"] == []
    assert {"SDK init", "Push credentials (FCM / APNs)", "Widget placeholders"} <= set(
        d["integration"]["needed"])
    # Optional core items are only needed when ticked In scope.
    assert "Backend events API" not in d["integration"]["needed"]

    rec["fields"]["integration_scope"] = ["Backend events API"]
    rec["fields"]["integration_done"] = ["SDK init", "User identify", "Front-end events",
                                         "User attributes", "Widget placeholders"]
    d = svc.derive(rec, cfg(), today)
    assert d["integration"]["pending"] == ["Backend events API", "Push credentials (FCM / APNs)"]
    assert d["integration"]["done_count"] == 5
    assert d["integration"]["module_setup_pending"] == {"Push": ["Push credentials (FCM / APNs)"]}
    # Open integration work is normal before go-live, and a flag once live.
    assert "integration_gap" not in d["flags"]
    rec["stage"] = "live"
    assert "integration_gap" in svc.derive(rec, cfg(), today)["flags"]
    rec["stage"] = "handed_over"
    assert "integration_gap" not in svc.derive(rec, cfg(), today)["flags"]


@pytest.mark.asyncio
async def test_catalogue_groups_items_and_bundles_validate():
    db = make_db()
    base = await svc.get_config(db)
    scope = next(f for f in base["fields"] if f["key"] == "integration_scope")
    assert scope["options_from"] == "integration_items" and "SDK init" in scope["options"]
    # Every default module sits in a sellable group and only requires real items.
    sellable = {g["key"] for g in base["module_groups"] if g["sellable"]}
    items = {i["key"] for i in base["integration_items"]}
    assert all(m["group"] in sellable and set(m["requires"]) <= items for m in base["modules"])

    body = copy.deepcopy(base)
    body["module_groups"].append({"key": "payments", "label": "Payments", "sellable": True})
    body["integration_items"].append({"key": "pay_sdk", "label": "Payments SDK", "group": "payments"})
    body["modules"].append({"key": "checkout", "label": "Checkout", "group": "payments",
                            "requires": ["pay_sdk"]})
    body["bundles"].append({"key": "pay", "label": "Payments pack", "modules": ["checkout"]})
    saved, changes = await svc.save_template(db, body, actor="a")
    assert {"Added module group 'Payments'", "Added integration item 'Payments SDK'",
            "Added module 'Checkout'", "Added bundle 'Payments pack'"} <= set(changes)
    rec = await svc.create_record(db, {"name": "X"}, actor="a")
    _, applied, _ = await svc.apply_changes(
        db, rec, {"modules_paid": "Checkout", "integration_done": "Payments SDK"}, actor="a")
    assert len(applied) == 2
    with pytest.raises(ValueError, match="unknown integration item"):
        await svc.apply_changes(db, rec, {"integration_done": "Teleporter"}, actor="a")

    bad = copy.deepcopy(saved)
    bad["modules"].append({"key": "m1", "label": "Lost", "group": "nowhere"})
    bad["modules"].append({"key": "m2", "label": "In core", "group": "core"})
    bad["modules"].append({"key": "m3", "label": "A, B", "group": "in_app", "requires": ["ghost"]})
    bad["bundles"].append({"key": "b1", "label": "Broken", "modules": ["ghost_module"]})
    with pytest.raises(ValueError) as exc:
        await svc.save_template(db, bad, actor="a")
    msg = str(exc.value)
    assert "unknown group 'nowhere'" in msg and "only holds integration items" in msg
    assert "cannot contain a comma" in msg and "unknown integration item(s) ghost" in msg
    assert "unknown module(s) ghost_module" in msg


@pytest.mark.asyncio
async def test_save_template_validates_versions_and_applies():
    db = make_db()
    await svc.create_record(db, {"name": "Iku", "stage": "kickoff"}, actor="a")
    base = await svc.get_config(db)

    body = copy.deepcopy(base)
    body["stages"][1]["label"] = "Kickoff call done"
    body["modules"].append({"key": "voice_ai", "label": "Voice AI", "group": "voice_ai"})
    body["fields"].append({"key": "csm", "label": "CSM", "type": "person", "section": "People"})
    body["rules"]["first_campaign_min_users"] = 250
    saved, changes = await svc.save_template(db, body, actor="vamsi@x.com")
    assert saved["version"] == 1
    assert "Edited stage 'Kickoff call done'" in changes
    assert "Added module 'Voice AI'" in changes
    assert "Added field 'CSM'" in changes
    assert any(c.startswith("Rule first_campaign_min_users") for c in changes)
    # The new module is immediately a valid option on all four layers.
    paid = next(f for f in saved["fields"] if f["key"] == "modules_paid")
    assert "Voice AI" in paid["options"]
    assert len(db.onboarding_settings_history.docs) == 1

    rec = await svc.find_record(db, name="Iku")
    _, applied, _ = await svc.apply_changes(db, rec, {"csm": "x@y.com", "modules_paid": "Voice AI"},
                                            actor="a")
    assert len(applied) == 2

    # Saving the same template again is a no-op: no new version.
    again, changes = await svc.save_template(db, copy.deepcopy(saved), actor="vamsi@x.com")
    assert changes == [] and again["version"] == 1

    # Removing a stage that still has records is refused.
    bad = copy.deepcopy(saved)
    bad["stages"] = [s for s in bad["stages"] if s["key"] != "kickoff"]
    with pytest.raises(ValueError, match="still has 1 record"):
        await svc.save_template(db, bad, actor="a")

    bad = copy.deepcopy(saved)
    bad["fields"].append({"key": "owner", "label": "Dup", "type": "text"})
    bad["fields"].append({"key": "tier", "label": "Tier", "type": "select", "options": []})
    bad["stages"][2]["milestone"] = "sdk_live"
    with pytest.raises(ValueError) as exc:
        await svc.save_template(db, bad, actor="a")
    msg = str(exc.value)
    assert "used twice" in msg and "at least one option" in msg and "more than one stage" in msg


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

        db.onboarding_settings.docs.append({"_id": "config", "edit_min_role": "operator",
                                            "template_min_role": "admin"})
        resp = await routes.handle_config(FakeRequest())
        assert json.loads(resp.body)["can_edit"] is False
        assert json.loads(resp.body)["can_edit_template"] is False
        with pytest.raises(Exception) as exc:
            await routes.handle_update(FakeRequest(record_id=rid, body={"changes": {"stage": "qa"}}))
        assert getattr(exc.value, "status", None) == 403
        with pytest.raises(Exception) as exc:
            await routes.handle_save_config(FakeRequest(role="operator", body={}))
        assert getattr(exc.value, "status", None) == 403


@pytest.mark.asyncio
async def test_route_save_config_returns_errors_and_changes():
    db = make_db()
    with patch.object(routes, "get_db", return_value=db):
        resp = await routes.handle_save_config(FakeRequest(body={"stages": [], "fields": []}))
        assert resp.status == 400
        cfg_now = json.loads((await routes.handle_config(FakeRequest())).body)
        cfg_now["rules"]["idle_days"] = 21
        resp = await routes.handle_save_config(FakeRequest(body=cfg_now))
        assert resp.status == 200
        assert json.loads(resp.body)["changes"] == ["Rule idle_days: 14 -> 21"]
        hist = json.loads((await routes.handle_config_history(FakeRequest())).body)["history"]
        assert hist[0]["version"] == 1 and hist[0]["by"] == "u@x.com"


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
