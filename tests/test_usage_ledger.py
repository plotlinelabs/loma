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
