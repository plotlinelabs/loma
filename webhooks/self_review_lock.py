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
older than ``LOCK_STALE_SECONDS`` is taken over by the next run, mirroring
``api.drain.running_query`` / ``recovery.HEARTBEAT_STALE_SECONDS``.

Requires the unique ``(repo_full_name, pr_number)`` index created in
``observability.db.ensure_indexes`` — the upsert relies on it to reject a
second live holder.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from observability.observer import HEARTBEAT_INTERVAL_SECONDS

logger = logging.getLogger(__name__)

COLLECTION = "pr_self_review_locks"

LOCK_HEARTBEAT_SECONDS = HEARTBEAT_INTERVAL_SECONDS
# Same window the drain endpoint and the recovery sweeper use for "genuinely
# running": two missed heartbeats means the holder is gone.
LOCK_STALE_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 2


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

    ``acquire`` never raises; ``release`` never raises.
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

    async def release(self) -> None:
        """Drop the lock if we hold it. Safe to call when acquire() lost or was never called."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if not self.held:
            return
        self.held = False
        try:
            # Filtered by conversation_id so we never delete a lock that was
            # taken over from us while we were stalled.
            await self.db[COLLECTION].delete_one(
                {**self._key, "conversation_id": self.conversation_id}
            )
        except Exception:
            logger.warning(
                "[SELF-REVIEW-LOCK] Failed to release lock for %s#%d (conversation %s); "
                "it expires after %ds without a heartbeat",
                self.repo_full_name, self.pr_number, self.conversation_id, LOCK_STALE_SECONDS,
            )

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(LOCK_HEARTBEAT_SECONDS)
                await self.db[COLLECTION].update_one(
                    {**self._key, "conversation_id": self.conversation_id},
                    {"$set": {"last_heartbeat": datetime.now(timezone.utc)}},
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(
                "[SELF-REVIEW-LOCK] Heartbeat failed for %s#%d: %s",
                self.repo_full_name, self.pr_number, e,
            )
