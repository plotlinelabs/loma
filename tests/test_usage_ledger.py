"""Usage ledger: per-run attribution, resume accumulation, idempotent backfill,
and /api/usage/me windows computed on run time rather than chat start."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from aiohttp.test_utils import make_mocked_request
from mongomock_motor import AsyncMongoMockClient

from api import my_usage_routes
from observability.observer import ConversationObserver
from observability.usage_ledger import (
    COLLECTION,
    backfill_usage_events,
    build_usage_event,
    record_usage_event,
)

USER = "me@example.com"
USAGE = {"input_tokens": 100, "output_tokens": 10,
         "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50}


@pytest.fixture
def db():
    return AsyncMongoMockClient()["loma_test"]


async def _seed_conversation(db, cid="c1", started_at=None, cost=None, **extra):
    await db.conversations.insert_one({
        "conversation_id": cid,
        "source": "dashboard",
        "started_at": started_at or datetime.now(timezone.utc),
        "metadata": {"user_name": USER},
        "model": "m",
        "total_turns": 0,
        "cost": cost,
        "title": f"chat {cid}",
        "status": "completed",
        **extra,
    })


@pytest.mark.asyncio
async def test_resume_with_zero_turns_accumulates_instead_of_overwriting(db):
    await _seed_conversation(db)
    first = ConversationObserver(db, {}, "c1")
    await first.record_usage(USAGE, 1.5)
    # Resumed run on a chat whose total_turns is still 0 -> turn_offset 0.
    second = ConversationObserver(db, {}, "c1")
    assert second.turn_offset == 0
    await second.record_usage(USAGE, 0.5)

    conv = await db.conversations.find_one({"conversation_id": "c1"})
    assert conv["cost"]["total_cost_usd"] == pytest.approx(2.0)
    assert conv["cost"]["input_tokens"] == 200
    assert conv["cost"]["cache_read_tokens"] == 2000
    assert conv["cost"]["confidence_cost_usd"] == 0

    events = await db[COLLECTION].find({"conversation_id": "c1"}).to_list(None)
    assert len(events) == 2
    assert {e["user_email"] for e in events} == {USER}
    assert sum(e["cost_usd"] for e in events) == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_record_usage_event_is_idempotent(db):
    event = build_usage_event(event_id="x", conversation_id="c1", at=datetime.now(timezone.utc),
                              user_email=USER, source="dashboard", model="m", cost_usd=1)
    assert await record_usage_event(db, event) is True
    assert await record_usage_event(db, dict(event)) is False
    assert await db[COLLECTION].count_documents({}) == 1


@pytest.mark.asyncio
async def test_backfill_records_only_pre_ledger_remainder_once(db):
    started = datetime.now(timezone.utc) - timedelta(days=3)
    await _seed_conversation(db, started_at=started, cost={
        "input_tokens": 500, "output_tokens": 50, "cache_read_tokens": 0,
        "cache_creation_tokens": 0, "total_cost_usd": 5.0,
    })
    # A post-deploy run already wrote $2 of it to the ledger.
    await record_usage_event(db, build_usage_event(
        event_id="live", conversation_id="c1", at=datetime.now(timezone.utc),
        user_email=USER, source="dashboard", model="m",
        input_tokens=200, output_tokens=20, cost_usd=2.0))

    assert await backfill_usage_events(db) == 1
    approx = await db[COLLECTION].find_one({"event_id": "backfill:c1"})
    assert approx["approx"] is True
    assert approx["cost_usd"] == pytest.approx(3.0)
    assert approx["input_tokens"] == 300
    assert approx["at"].replace(tzinfo=timezone.utc) == started.replace(microsecond=approx["at"].microsecond)
    conv = await db.conversations.find_one({"conversation_id": "c1"})
    assert conv["usage_ledger_backfilled"] is True

    # Re-running (restart / another replica) adds nothing.
    await db.conversations.update_one({"conversation_id": "c1"},
                                      {"$unset": {"usage_ledger_backfilled": ""}})
    assert await backfill_usage_events(db) == 0
    assert await db[COLLECTION].count_documents({"conversation_id": "c1"}) == 2


@pytest.mark.asyncio
async def test_backfill_skips_conversation_mid_write(db):
    # Ledger ahead of the conversation total: a run is between its two writes.
    await _seed_conversation(db, cost={"total_cost_usd": 1.0, "input_tokens": 10})
    await record_usage_event(db, build_usage_event(
        event_id="live", conversation_id="c1", at=datetime.now(timezone.utc),
        user_email=USER, source="dashboard", model="m", input_tokens=20, cost_usd=2.0))
    assert await backfill_usage_events(db) == 0
    conv = await db.conversations.find_one({"conversation_id": "c1"})
    assert "usage_ledger_backfilled" not in conv


async def _get_usage(db, **query):
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    request = make_mocked_request("GET", f"/api/usage/me?{qs}")
    with patch.object(my_usage_routes, "get_db", return_value=db), \
            patch.object(my_usage_routes, "get_user_email", return_value=USER):
        response = await my_usage_routes.handle_my_usage(request)
    assert response.status == 200
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_my_usage_counts_spend_on_the_day_it_happened(db):
    now = datetime.now(timezone.utc)
    # Chat started 3 days ago; one run then, one run today.
    await _seed_conversation(db, started_at=now - timedelta(days=3))
    for event_id, at, cost in (("old", now - timedelta(days=3), 4.0), ("new", now, 1.0)):
        await record_usage_event(db, build_usage_event(
            event_id=event_id, conversation_id="c1", at=at, user_email=USER,
            source="dashboard", model="m", input_tokens=10, cost_usd=cost))
    # Someone else's spend and a flow run never show up.
    await record_usage_event(db, build_usage_event(
        event_id="other", conversation_id="c2", at=now, user_email="x@example.com",
        source="dashboard", model="m", cost_usd=9.0))
    await record_usage_event(db, build_usage_event(
        event_id="flow", conversation_id="c3", at=now, user_email=USER,
        source="flow", model="m", cost_usd=9.0))

    since_today = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    today = await _get_usage(db, since=since_today)
    assert today["totals"]["total_cost_usd"] == pytest.approx(1.0)
    assert today["totals"]["conversations"] == 1
    assert today["top_chats"][0]["conversation_id"] == "c1"
    assert today["top_chats"][0]["total_cost_usd"] == pytest.approx(1.0)
    assert today["top_chats"][0]["title"] == "chat c1"
    assert today["includes_approximate"] is False

    week = await _get_usage(db, days=7)
    assert week["totals"]["total_cost_usd"] == pytest.approx(5.0)
    assert week["totals"]["conversations"] == 1
    assert len(week["daily"]) == 2
    assert [d["total_cost_usd"] for d in week["daily"]] == pytest.approx([4.0, 1.0])


@pytest.mark.asyncio
async def test_record_usage_stores_run_model_and_unknown_cost(db):
    # Chat created on one model, then switched: the ledger keeps what each run used.
    await _seed_conversation(db, model="anthropic/claude-a")
    await ConversationObserver(db, {}, "c1").record_usage(
        USAGE, 1.0, model="anthropic/claude-b", runtime="claude")
    # Codex reports tokens but no price.
    await ConversationObserver(db, {}, "c1").record_usage(
        USAGE, None, model="codex/gpt-x", runtime="codex")
    # No model passed: falls back to the conversation's model.
    await ConversationObserver(db, {}, "c1").record_usage(USAGE, 0.5)

    events = await db[COLLECTION].find({"conversation_id": "c1"}).sort("at", 1).to_list(None)
    assert [(e["model"], e["runtime"], e["cost_known"]) for e in events] == [
        ("anthropic/claude-b", "claude", True),
        ("codex/gpt-x", "codex", False),
        ("anthropic/claude-a", "", True),
    ]
    assert events[1]["cost_usd"] == 0


@pytest.mark.asyncio
async def test_backfill_marks_legacy_codex_zero_cost_as_unknown(db):
    await _seed_conversation(db, cid="cx", model="codex/gpt-x", cost={
        "input_tokens": 100, "output_tokens": 10, "total_cost_usd": 0})
    await _seed_conversation(db, cid="cl", model="anthropic/claude-a", cost={
        "input_tokens": 100, "output_tokens": 10, "total_cost_usd": 2.0})
    assert await backfill_usage_events(db) == 2
    cx = await db[COLLECTION].find_one({"event_id": "backfill:cx"})
    cl = await db[COLLECTION].find_one({"event_id": "backfill:cl"})
    assert cx["cost_known"] is False
    assert cl["cost_known"] is True


async def _event(db, event_id, cid, cost, model="m", at=None, cost_known=True, **kw):
    await record_usage_event(db, build_usage_event(
        event_id=event_id, conversation_id=cid, at=at or datetime.now(timezone.utc),
        user_email=USER, source="dashboard", model=model, input_tokens=10,
        output_tokens=1, cost_usd=cost, cost_known=cost_known, **kw))


@pytest.mark.asyncio
async def test_my_usage_by_model_and_paginated_chat_list(db):
    for cid in ("a", "b", "c"):
        await _seed_conversation(db, cid=cid)
    await _event(db, "a1", "a", 1.0, model="anthropic/claude-x")
    await _event(db, "a2", "a", 2.0, model="opencode/y")
    await _event(db, "b1", "b", 5.0, model="anthropic/claude-x")
    await _event(db, "c1", "c", 0.0, model="codex/z", cost_known=False)
    await _event(db, "c2", "c", 0.0, model="")  # legacy row with no model

    body = await _get_usage(db, days=7)
    assert body["totals"]["runs"] == 5
    assert body["totals"]["unpriced_runs"] == 1
    assert body["totals"]["unpriced_tokens"] == 11
    models = {m["model"]: m for m in body["by_model"]}
    assert models["anthropic/claude-x"]["total_cost_usd"] == pytest.approx(6.0)
    assert models["anthropic/claude-x"]["conversations"] == 2
    assert models["codex/z"]["unpriced_runs"] == 1
    assert "unknown" in models
    assert body["by_model"][0]["model"] == "anthropic/claude-x"

    # Costliest first, every chat (not just 5), with the models each used.
    assert [c["conversation_id"] for c in body["chats"]] == ["b", "a", "c"]
    assert body["chats_total"] == 3
    a = body["chats"][1]
    assert a["runs"] == 2 and a["models"] == ["anthropic/claude-x", "opencode/y"]
    assert body["top_chats"][0]["conversation_id"] == "b"

    cheap = await _get_usage(db, days=7, sort="cost_asc", limit=2, offset=1)
    assert [c["conversation_id"] for c in cheap["chats"]] == ["a", "b"]
    assert cheap["chats_total"] == 3
    assert cheap["top_chats"] == []


@pytest.mark.asyncio
async def test_my_usage_rejects_bad_sort(db):
    request = make_mocked_request("GET", "/api/usage/me?sort=drop")
    with patch.object(my_usage_routes, "get_db", return_value=db), \
            patch.object(my_usage_routes, "get_user_email", return_value=USER):
        response = await my_usage_routes.handle_my_usage(request)
    assert response.status == 400


@pytest.mark.asyncio
async def test_chat_runs_are_scoped_to_the_caller(db):
    await _seed_conversation(db)
    now = datetime.now(timezone.utc)
    await _event(db, "r1", "c1", 1.0, model="anthropic/claude-x", at=now - timedelta(hours=2),
                 runtime="claude")
    await _event(db, "r2", "c1", 0.0, model="codex/z", at=now, cost_known=False, runtime="codex")
    await record_usage_event(db, build_usage_event(
        event_id="other", conversation_id="c1", at=now, user_email="x@example.com",
        source="dashboard", model="m", cost_usd=9.0))

    request = make_mocked_request(
        "GET", "/api/usage/me/chats/c1/runs?days=7", match_info={"conversation_id": "c1"})
    with patch.object(my_usage_routes, "get_db", return_value=db), \
            patch.object(my_usage_routes, "get_user_email", return_value=USER):
        response = await my_usage_routes.handle_my_chat_runs(request)
    runs = json.loads(response.body)["runs"]
    assert [(r["model"], r["cost_known"], r["runtime"]) for r in runs] == [
        ("codex/z", False, "codex"), ("anthropic/claude-x", True, "claude")]
