"""Per-PR lock for fresh-context self-reviews.

Why a dedicated lock doc instead of looking for a `status: running`
conversation: the conversation doc is only inserted by `observer.start()`,
which happens AFTER several seconds of GitHub round-trips (`_get_pr_stats`,
`get_agent_review_threads`, `_minimize_previous_agent_comments`, status
comment, check run). Agent flows push one commit per `push_files` call, so
`synchronize` events arrive seconds apart — two of them can both pass a
conversation-based check inside that window and run N parallel reviewers.

The lock is claimed as the very first thing `_process_pr_review` does, in a
single atomic Mongo op, and carries its own heartbeat so a crashed holder
(deploy restart, OOM) never blocks the PR forever: a lock whose heartbeat is
older than ``LOCK_STALE_SECONDS`` is taken over by the next run.

An explicit re-review (`/rereview`, `@mention`) that arrives while a run is
in flight does not start a second reviewer: it flags the held lock with
``request_rerun()`` and the holder honours the flag when it releases, by
scheduling one fresh run on the newest head. See
``webhooks.github._process_pr_review``.

Requires the unique ``(repo_full_name, pr_number)`` index created in
``observability.db.ensure_indexes`` — the upsert relies on it to reject a
second live holder.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

COLLECTION = "pr_self_review_locks"

# Lock cadence. Numerically the same as the observer's conversation heartbeat
# (observability.observer.HEARTBEAT_INTERVAL_SECONDS = 30) and the "two missed
# beats" stale window used by api.drain / recovery, but deliberately NOT
# derived from them: the lock's semantics ("how long after a crash may a PR
# stay unreviewable") must not silently change when someone tunes how often
# conversations heartbeat for the deploy drain.
LOCK_HEARTBEAT_SECONDS = 30
LOCK_STALE_SECONDS = LOCK_HEARTBEAT_SECONDS * 2

# `release()` retries the delete once after this delay before giving up. A
# failed release does not just leave the lock to expire: it also drops a
# queued re-review flag the holder owes someone (see `release`).
RELEASE_RETRY_SECONDS = 1.0


class SelfReviewLock:
    """Claim exclusive self-review ownership of one PR for the duration of a run.

    Usage::

        lock = SelfReviewLock(db, repo_full_name, pr_number, conversation_id, head_sha)
        if not await lock.acquire():
            return  # another live run owns this PR; it will re-run on the newest head
        try:
            ...
        finally:
            await lock.release()
            if lock.rerun_requested:
                ...  # someone asked for a re-review while we held the lock

    ``acquire``, ``release`` and ``request_rerun`` never raise.
    """

    def __init__(
        self,
        db,
        repo_full_name: str,
        pr_number: int,
        conversation_id: str,
        head_sha: str = "",
    ):
        self.db = db
        self.repo_full_name = repo_full_name
        self.pr_number = pr_number
        self.conversation_id = conversation_id
        self.head_sha = head_sha
        self.held = False
        self.holder: dict | None = None  # populated when acquire() loses
        # Set by release(): True if a forced re-review was requested while we
        # held the lock (see request_rerun). The holder owes that requester a
        # fresh run on the newest head.
        self.rerun_requested = False
        self._heartbeat_task: asyncio.Task | None = None

    @property
    def _key(self) -> dict:
        return {"repo_full_name": self.repo_full_name, "pr_number": self.pr_number}

    async def acquire(self) -> bool:
        """Atomically claim the lock. Returns False if a live run already holds it.

        One upsert does all the work:
          - no doc            → inserted, we hold it
          - stale doc         → filter matches, we take it over
          - live doc          → filter misses, insert hits the unique index → lose
        """
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(seconds=LOCK_STALE_SECONDS)
        claim = {
            "conversation_id": self.conversation_id,
            "head_sha": self.head_sha,
            "acquired_at": now,
            "last_heartbeat": now,
            # A stale doc we take over may carry a previous holder's flag; the
            # request was aimed at a run that is now dead, and THIS run is the
            # fresh review it asked for.
            "rerun_requested": False,
            "rerun_requested_by": None,
        }
        try:
            result = await self.db[COLLECTION].update_one(
                {**self._key, "last_heartbeat": {"$lt": stale_cutoff}},
                {"$set": claim},
                upsert=True,
            )
        except DuplicateKeyError:
            try:
                self.holder = await self.db[COLLECTION].find_one(self._key)
            except Exception:
                self.holder = None
            logger.info(
                "[SELF-REVIEW-LOCK] %s#%d is held by a live run (conversation %s, head %s) "
                "— %s skipped; the holder re-runs on the newest head when it finishes",
                self.repo_full_name, self.pr_number,
                (self.holder or {}).get("conversation_id"),
                str((self.holder or {}).get("head_sha", ""))[:7],
                self.head_sha[:7],
            )
            return False
        except Exception:
            # Mongo unavailable: fail open. A duplicate review is a nuisance; a
            # PR that never gets reviewed is a silent gap.
            logger.exception(
                "[SELF-REVIEW-LOCK] Could not claim lock for %s#%d — proceeding unlocked",
                self.repo_full_name, self.pr_number,
            )
            return True

        if getattr(result, "matched_count", 0):
            logger.warning(
                "[SELF-REVIEW-LOCK] Took over a stale self-review lock on %s#%d "
                "(previous holder's heartbeat older than %ds — crashed run?)",
                self.repo_full_name, self.pr_number, LOCK_STALE_SECONDS,
            )
        self.held = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return True

    async def request_rerun(self) -> bool:
        """Ask the live holder to run one more self-review when it finishes.

        Used by an explicit re-review that lost ``acquire``: instead of running
        a second reviewer in parallel (both minimizing each other's comments,
        two verdict follow-ups in one thread), it flags the holder's lock. The
        holder reads the flag in ``release`` and schedules a forced run on the
        newest head. Returns False if no LIVE lock exists any more (the holder
        finished, or its heartbeat is older than ``LOCK_STALE_SECONDS`` — a
        crashed holder will never read the flag) — the caller should then
        claim the lock itself, which takes over a stale doc.
        """
        stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=LOCK_STALE_SECONDS)
        try:
            result = await self.db[COLLECTION].update_one(
                {**self._key, "last_heartbeat": {"$gte": stale_cutoff}},
                {"$set": {
                    "rerun_requested": True,
                    "rerun_requested_by": self.conversation_id,
                    "rerun_requested_at": datetime.now(timezone.utc),
                }},
            )
        except Exception:
            logger.exception(
                "[SELF-REVIEW-LOCK] Could not flag the in-flight self-review of %s#%d "
                "for a re-run", self.repo_full_name, self.pr_number,
            )
            return False
        flagged = bool(getattr(result, "matched_count", 0))
        if flagged:
            logger.info(
                "[SELF-REVIEW-LOCK] Re-review of %s#%d queued behind the in-flight run "
                "(conversation %s)", self.repo_full_name, self.pr_number,
                (self.holder or {}).get("conversation_id"),
            )
        return flagged

    async def release(self) -> None:
        """Drop the lock if we hold it. Safe to call when acquire() lost or was never called.

        Sets ``rerun_requested`` from the released doc so the holder can honour
        a re-review that was requested while it ran.
        """
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if not self.held:
            return
        self.held = False
        # Filtered by conversation_id so we never delete a lock that was
        # taken over from us while we were stalled. find_one_and_delete so
        # the flag read and the release are one atomic step: a
        # request_rerun that lands after this cannot be lost, it simply
        # finds no lock and claims one itself.
        release_filter = {**self._key, "conversation_id": self.conversation_id}
        for attempt in (1, 2):
            try:
                released = await self.db[COLLECTION].find_one_and_delete(release_filter)
                self.rerun_requested = bool((released or {}).get("rerun_requested"))
                return
            except Exception as e:
                if attempt == 1:
                    logger.warning(
                        "[SELF-REVIEW-LOCK] Failed to release lock for %s#%d (conversation %s), "
                        "retrying once in %ss: %s",
                        self.repo_full_name, self.pr_number, self.conversation_id,
                        RELEASE_RETRY_SECONDS, e,
                    )
                    await asyncio.sleep(RELEASE_RETRY_SECONDS)
        # Both attempts failed. The doc expires after LOCK_STALE_SECONDS without
        # a heartbeat, but a re-review that was queued on it (`rerun_requested`)
        # is LOST: this run never read the flag, and the next claim resets it.
        # The PR may carry a "a fresh one will start" promise nobody will keep.
        logger.error(
            "[SELF-REVIEW-LOCK] Could not release lock for %s#%d (conversation %s) after "
            "2 attempts; it expires after %ds without a heartbeat. A re-review queued "
            "behind this run may have been lost — if one was promised on the PR, "
            "trigger it manually with a /rereview.",
            self.repo_full_name, self.pr_number, self.conversation_id, LOCK_STALE_SECONDS,
        )

    async def _heartbeat_loop(self) -> None:
        # One failed beat (Mongo blip, primary election) must NOT end the loop:
        # the run keeps going for minutes, the lock would go stale after
        # LOCK_STALE_SECONDS, the next `synchronize` would take it over, and we
        # would be back to two live reviewers — the exact race this lock exists
        # to prevent. Log and keep beating; only cancellation ends the loop.
        while True:
            try:
                await asyncio.sleep(LOCK_HEARTBEAT_SECONDS)
                await self.db[COLLECTION].update_one(
                    {**self._key, "conversation_id": self.conversation_id},
                    {"$set": {"last_heartbeat": datetime.now(timezone.utc)}},
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(
                    "[SELF-REVIEW-LOCK] Heartbeat failed for %s#%d (will retry in %ss): %s",
                    self.repo_full_name, self.pr_number, LOCK_HEARTBEAT_SECONDS, e,
                )
