"""Tests for the per-PR self-review lock (webhooks/self_review_lock.py).

Unit tests drive the lock against a Mongo double. The integration test at the
bottom runs against a real MongoDB when LOMA_TEST_MONGODB_URI is set (it uses
a throwaway database and drops it) — the atomic upsert-vs-unique-index
semantics are exactly the kind of thing a mock cannot prove.
"""

import asyncio
import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

from webhooks.self_review_lock import (
    COLLECTION,
    LOCK_HEARTBEAT_SECONDS,
    LOCK_HEARTBEAT_WRITE_TIMEOUT_SECONDS,
    LOCK_STALE_SECONDS,
    SelfReviewLock,
)

REPO = "example-org/example-repo"


def _db(update_one=None, matched_count=0):
    locks = MagicMock()
    locks.update_one = update_one or AsyncMock(return_value=MagicMock(matched_count=matched_count))
    locks.find_one_and_delete = AsyncMock(return_value=None)
    locks.find_one = AsyncMock(return_value={"conversation_id": "other", "head_sha": "a" * 40})
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=locks)
    return db, locks


class TestAcquireRelease:
    @pytest.mark.asyncio
    async def test_acquire_upserts_atomically_and_starts_heartbeat(self):
        db, locks = _db()
        lock = SelfReviewLock(db, REPO, 42, "conv-1", "h" * 40)
        assert await lock.acquire() is True
        assert lock.held is True
        assert lock._heartbeat_task is not None and not lock._heartbeat_task.done()
        db.__getitem__.assert_called_with(COLLECTION)
        filt, update = locks.update_one.call_args.args
        assert filt["repo_full_name"] == REPO and filt["pr_number"] == 42
        stale_cutoff = filt["last_heartbeat"]["$lt"]
        assert datetime.now(timezone.utc) - stale_cutoff >= timedelta(seconds=LOCK_STALE_SECONDS - 1)
        assert update["$set"]["conversation_id"] == "conv-1"
        assert locks.update_one.call_args.kwargs["upsert"] is True
        await lock.release()
        assert lock.held is False
        assert lock._heartbeat_task is None
        locks.find_one_and_delete.assert_awaited_once_with(
            {"repo_full_name": REPO, "pr_number": 42, "conversation_id": "conv-1"}
        )

    @pytest.mark.asyncio
    async def test_live_holder_wins_and_loser_does_not_release(self):
        db, locks = _db(update_one=AsyncMock(side_effect=DuplicateKeyError("dup")))
        lock = SelfReviewLock(db, REPO, 42, "conv-2", "n" * 40)
        assert await lock.acquire() is False
        assert lock.held is False
        assert lock.holder["conversation_id"] == "other"
        assert lock._heartbeat_task is None
        await lock.release()  # no-op for a loser
        locks.find_one_and_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_holder_is_taken_over(self):
        # A crashed run left a doc whose heartbeat is older than the window:
        # the upsert filter matches it (matched_count=1) and we take over.
        db, locks = _db(matched_count=1)
        lock = SelfReviewLock(db, REPO, 42, "conv-3", "n" * 40)
        assert await lock.acquire() is True
        assert lock.held is True
        await lock.release()

    @pytest.mark.asyncio
    async def test_mongo_error_fails_open(self):
        db, locks = _db(update_one=AsyncMock(side_effect=RuntimeError("mongo down")))
        lock = SelfReviewLock(db, REPO, 42, "conv-4", "n" * 40)
        assert await lock.acquire() is True   # a missed review is worse than a duplicate one
        assert lock.held is False             # …but we own nothing to release
        await lock.release()
        locks.find_one_and_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_failure_never_raises(self):
        db, locks = _db()
        locks.find_one_and_delete = AsyncMock(side_effect=RuntimeError("mongo down"))
        lock = SelfReviewLock(db, REPO, 42, "conv-5", "n" * 40)
        assert await lock.acquire() is True
        await lock.release()  # must not raise
        assert lock.held is False

    @pytest.mark.asyncio
    async def test_heartbeat_is_scoped_to_our_conversation(self, monkeypatch):
        monkeypatch.setattr("webhooks.self_review_lock.LOCK_HEARTBEAT_SECONDS", 0.01)
        db, locks = _db()
        lock = SelfReviewLock(db, REPO, 42, "conv-6", "n" * 40)
        await lock.acquire()
        await asyncio.sleep(0.05)
        await lock.release()
        beats = [c for c in locks.update_one.call_args_list[1:]]
        assert beats, "heartbeat never fired"
        for call in beats:
            assert call.args[0]["conversation_id"] == "conv-6"  # never revives a taken-over lock
            assert "last_heartbeat" in call.args[1]["$set"]


    @pytest.mark.asyncio
    async def test_heartbeat_survives_transient_errors(self, monkeypatch):
        # One failed beat (Mongo blip, primary election) must not kill the
        # loop: a silently dead heartbeat lets the lock go stale mid-run, the
        # next `synchronize` takes it over, and two live reviewers race —
        # exactly what the lock exists to prevent.
        monkeypatch.setattr("webhooks.self_review_lock.LOCK_HEARTBEAT_SECONDS", 0.01)
        db, locks = _db()
        calls = {"n": 0}

        async def _update_one(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 2:  # first beat after the claim fails
                raise RuntimeError("mongo blip")
            return MagicMock(matched_count=0)

        locks.update_one = _update_one
        lock = SelfReviewLock(db, REPO, 42, "conv-7", "n" * 40)
        assert await lock.acquire() is True
        await asyncio.sleep(0.08)
        assert not lock._heartbeat_task.done(), "heartbeat loop died on a transient error"
        assert calls["n"] >= 4  # claim + failed beat + at least two more beats
        await lock.release()
        assert lock._heartbeat_task is None

    @pytest.mark.asyncio
    async def test_heartbeat_write_is_bounded(self, monkeypatch):
        # A hung Mongo write must not block the loop for the full server-selection
        # timeout: wait_for abandons it and the loop retries on the next tick, so
        # a slow primary cannot starve the heartbeat and let the lock go stale.
        monkeypatch.setattr("webhooks.self_review_lock.LOCK_HEARTBEAT_SECONDS", 0.01)
        monkeypatch.setattr("webhooks.self_review_lock.LOCK_HEARTBEAT_WRITE_TIMEOUT_SECONDS", 0.02)
        attempts = {"n": 0}

        async def _hang(*_a, **_k):
            attempts["n"] += 1
            await asyncio.sleep(10)  # never completes; wait_for must abandon it

        db, locks = _db()
        locks.update_one = AsyncMock(side_effect=_hang)
        lock = SelfReviewLock(db, REPO, 42, "conv-hb", "h" * 40)
        task = asyncio.create_task(lock._heartbeat_loop())
        await asyncio.sleep(0.15)
        assert not task.done(), "loop died instead of bounding the hung write"
        assert attempts["n"] >= 2, "each hung write should be abandoned and retried"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_request_rerun_flags_the_live_holder_only(self):
        # An explicit re-review that lost the claim asks the holder for one
        # more run instead of racing it. The flag is a plain update on the
        # (repo, pr) key — no upsert, so a vanished holder is NOT resurrected.
        db, locks = _db(update_one=AsyncMock(side_effect=DuplicateKeyError("dup")))
        lock = SelfReviewLock(db, REPO, 42, "conv-8", "n" * 40)
        assert await lock.acquire() is False
        locks.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
        assert await lock.request_rerun() is True
        filt, update = locks.update_one.call_args.args
        assert filt["repo_full_name"] == REPO and filt["pr_number"] == 42
        # Only a LIVE holder is flagged. A crashed holder (stale heartbeat)
        # never reads the flag, so "not flagged" sends the caller to acquire(),
        # which takes the stale doc over and IS the requested fresh run.
        live_bound = filt["last_heartbeat"]["$gte"]
        assert datetime.now(timezone.utc) - live_bound >= timedelta(seconds=LOCK_STALE_SECONDS - 1)
        assert "upsert" not in locks.update_one.call_args.kwargs
        assert update["$set"]["rerun_requested"] is True
        assert update["$set"]["rerun_requested_by"] == "conv-8"
        # Holder finished between the claim and the flag → nothing to flag
        locks.update_one = AsyncMock(return_value=MagicMock(matched_count=0))
        assert await lock.request_rerun() is False
        # Mongo error → never raises, reports "not flagged"
        locks.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        assert await lock.request_rerun() is False

    @pytest.mark.asyncio
    async def test_release_reports_rerun_requested_and_a_claim_resets_it(self):
        db, locks = _db()
        locks.find_one_and_delete = AsyncMock(
            return_value={"conversation_id": "conv-9", "rerun_requested": True}
        )
        lock = SelfReviewLock(db, REPO, 42, "conv-9", "n" * 40)
        assert await lock.acquire() is True
        # A takeover of a stale doc clears the dead holder's flag: this run IS
        # the fresh review that flag asked for.
        assert locks.update_one.call_args.args[1]["$set"]["rerun_requested"] is False
        assert lock.rerun_requested is False
        await lock.release()
        assert lock.rerun_requested is True
        released_filter = locks.find_one_and_delete.call_args.args[0]
        assert released_filter["conversation_id"] == "conv-9"

        db2, _ = _db()
        lock2 = SelfReviewLock(db2, REPO, 42, "conv-10", "n" * 40)
        await lock2.acquire()
        await lock2.release()
        assert lock2.rerun_requested is False

    @pytest.mark.asyncio
    async def test_release_retries_once_then_reports_a_possibly_lost_rerun(self, monkeypatch):
        monkeypatch.setattr("webhooks.self_review_lock.RELEASE_RETRY_SECONDS", 0)
        # One blip: the retry lands and recovers the queued flag.
        db, locks = _db()
        locks.find_one_and_delete = AsyncMock(
            side_effect=[RuntimeError("blip"), {"conversation_id": "conv-11", "rerun_requested": True}]
        )
        lock = SelfReviewLock(db, REPO, 42, "conv-11", "n" * 40)
        assert await lock.acquire() is True
        await lock.release()
        assert locks.find_one_and_delete.await_count == 2
        assert lock.rerun_requested is True

        # Mongo down for both attempts: never raises, but says loudly that a
        # queued re-review (promised on the PR) may have been dropped.
        db, locks = _db()
        locks.find_one_and_delete = AsyncMock(side_effect=RuntimeError("mongo down"))
        lock = SelfReviewLock(db, REPO, 42, "conv-12", "n" * 40)
        assert await lock.acquire() is True
        with patch("webhooks.self_review_lock.logger") as logger_mock:
            await lock.release()
        assert locks.find_one_and_delete.await_count == 2
        assert lock.held is False and lock.rerun_requested is False
        assert "may have been lost" in logger_mock.error.call_args.args[0]

    def test_lock_cadence_is_not_derived_from_the_observer_heartbeat(self):
        # Tuning how often conversations heartbeat for the deploy drain must
        # not silently change how long a crashed run can block a PR.
        source = Path("webhooks/self_review_lock.py").read_text()
        assert "from observability.observer import" not in source
        # Stale window is THREE intervals (not two): with a bounded heartbeat
        # write, one failed beat cannot push the next good beat past the cutoff.
        assert LOCK_HEARTBEAT_SECONDS == 30
        assert LOCK_STALE_SECONDS == 90
        assert LOCK_STALE_SECONDS == LOCK_HEARTBEAT_SECONDS * 3
        # Worst-case gap between good beats (sleep + bounded failed write + sleep)
        # must stay inside the stale window.
        assert (
            LOCK_HEARTBEAT_SECONDS + LOCK_HEARTBEAT_WRITE_TIMEOUT_SECONDS
            + LOCK_HEARTBEAT_SECONDS
        ) < LOCK_STALE_SECONDS


MONGO_URI = os.environ.get("LOMA_TEST_MONGODB_URI", "")


@pytest.mark.skipif(not MONGO_URI, reason="LOMA_TEST_MONGODB_URI not set")
@pytest.mark.asyncio
async def test_lock_is_atomic_against_real_mongo():
    """Two runs racing for one PR: exactly one wins; a stale holder is taken over."""
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[f"loma_test_lock_{uuid.uuid4().hex[:8]}"]
    try:
        await db[COLLECTION].create_index([("repo_full_name", 1), ("pr_number", 1)], unique=True)

        # Race: N claims for the same PR in the same event-loop tick
        locks = [SelfReviewLock(db, REPO, 7, f"conv-{i}", f"{i:040d}") for i in range(8)]
        results = await asyncio.gather(*(lock.acquire() for lock in locks))
        assert results.count(True) == 1, results
        winner = locks[results.index(True)]
        assert await db[COLLECTION].count_documents({"repo_full_name": REPO, "pr_number": 7}) == 1

        # A different PR is independent
        other = SelfReviewLock(db, REPO, 8, "conv-other", "o" * 40)
        assert await other.acquire() is True

        # Live holder keeps winning; release hands over
        late = SelfReviewLock(db, REPO, 7, "conv-late", "l" * 40)
        assert await late.acquire() is False
        # A forced re-review that lost the claim flags the holder...
        assert await late.request_rerun() is True
        await winner.release()
        assert winner.rerun_requested is True  # ...who sees it on release
        assert await late.acquire() is True
        # A fresh claim never inherits the flag
        assert (await db[COLLECTION].find_one({"repo_full_name": REPO, "pr_number": 7}))["rerun_requested"] is False

        # Crashed holder: heartbeat stops (simulate by back-dating), next run takes over
        late._heartbeat_task.cancel()
        await db[COLLECTION].update_one(
            {"repo_full_name": REPO, "pr_number": 7},
            {"$set": {"last_heartbeat": datetime.now(timezone.utc) - timedelta(seconds=LOCK_STALE_SECONDS + 5)}},
        )
        takeover = SelfReviewLock(db, REPO, 7, "conv-takeover", "t" * 40)
        assert await takeover.acquire() is True
        doc = await db[COLLECTION].find_one({"repo_full_name": REPO, "pr_number": 7})
        assert doc["conversation_id"] == "conv-takeover"
        # The stalled previous holder's release must NOT delete the new owner's lock
        late.held = True
        await late.release()
        assert (await db[COLLECTION].find_one({"repo_full_name": REPO, "pr_number": 7}))["conversation_id"] == "conv-takeover"
        await takeover.release()
        await other.release()
        assert await db[COLLECTION].count_documents({}) == 0
    finally:
        await client.drop_database(db.name)
        client.close()
