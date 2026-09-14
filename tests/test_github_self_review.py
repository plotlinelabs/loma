"""Tests for fresh-context self-review of agent-authored PRs (webhooks/github.py).

Agent-created draft PRs (implement-ticket conversations, Linear webhook flows,
utils/github_pr.py) must be routed into a clean-context self-review instead of
being skipped, while other bots' PRs and human draft PRs keep the old behavior.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

from webhooks.github import (
    AGENT_GITHUB_LOGIN,
    AGENT_PR_LABEL,
    _process_pr_review,
    _rerun_self_review_if_head_moved,
    handle_github_webhook,
)

SECRET = "test-github-secret"


def _signed(body: dict) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, sig


def _pr_event(
    author: str, draft: bool = True, action: str = "opened", labels: list[str] | None = None,
) -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": 42,
            "title": "feat: something",
            "html_url": "https://github.com/example-org/example-repo/pull/42",
            "draft": draft,
            "user": {"login": author},
            "labels": [{"name": name} for name in (labels or [])],
            "base": {"sha": "b" * 40, "ref": "main"},
            "head": {"sha": "h" * 40, "ref": "feat/something"},
        },
        "repository": {"name": "example-repo", "full_name": "example-org/example-repo"},
    }


def _request(raw_body: bytes, signature: str, event: str = "pull_request") -> MagicMock:
    request = MagicMock()
    request.read = AsyncMock(return_value=raw_body)
    request.headers = {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "test-delivery",
    }
    return request


class TestSelfReviewRouting:
    @pytest.mark.asyncio
    async def test_agent_draft_pr_triggers_self_review(self):
        raw, sig = _signed(_pr_event(AGENT_GITHUB_LOGIN, draft=True, action="opened"))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", True), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        body = json.loads(response.body)
        assert body["status"] == "accepted"
        assert body["mode"] == "self_review"
        review_mock.assert_called_once()
        assert review_mock.call_args.kwargs["self_review"] is True
        assert review_mock.call_args.kwargs["pr_author"] == AGENT_GITHUB_LOGIN

    @pytest.mark.asyncio
    async def test_agent_pr_synchronize_re_reviews(self):
        raw, sig = _signed(_pr_event(AGENT_GITHUB_LOGIN, draft=True, action="synchronize"))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", True), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        body = json.loads(response.body)
        assert body["status"] == "accepted"
        assert body["mode"] == "self_review"
        assert review_mock.call_args.kwargs["self_review"] is True

    @pytest.mark.asyncio
    async def test_agent_pr_skipped_when_self_review_disabled(self):
        raw, sig = _signed(_pr_event(AGENT_GITHUB_LOGIN, draft=True))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", False), \
             patch("webhooks.github.get_db", return_value=None), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github.post_self_review_followup", new_callable=AsyncMock) as followup_mock, \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
            await asyncio.sleep(0)  # let the fire-and-forget follow-up task run
        body = json.loads(response.body)
        assert body == {"status": "ignored", "reason": "self_review_disabled"}
        review_mock.assert_not_called()
        # Stage 1 promised a verdict; the disabled deploy must answer that promise.
        followup_mock.assert_awaited_once()
        kwargs = followup_mock.call_args.kwargs
        assert kwargs["disabled"] is True
        assert kwargs["pr_number"] == 42
        assert kwargs["repo_full_name"] == "example-org/example-repo"

    @pytest.mark.asyncio
    async def test_agent_pr_label_routes_to_self_review_when_login_mismatches(self):
        # Misconfigured AGENT_GITHUB_LOGIN: author differs, but the PR carries the
        # "Agent PR" label → still self-review (and a warning is logged).
        raw, sig = _signed(_pr_event("some-other-login", draft=True, action="synchronize",
                                     labels=[AGENT_PR_LABEL, "preview"]))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", True), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github.logger") as logger_mock, \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        body = json.loads(response.body)
        assert body["status"] == "accepted"
        assert body["mode"] == "self_review"
        assert review_mock.call_args.kwargs["self_review"] is True
        assert any("AGENT_GITHUB_LOGIN" in str(c) for c in logger_mock.warning.call_args_list)

    @pytest.mark.asyncio
    async def test_agent_pr_label_does_not_override_other_bots(self):
        raw, sig = _signed(_pr_event("dependabot[bot]", draft=False, labels=[AGENT_PR_LABEL]))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", True), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        assert json.loads(response.body) == {"status": "ignored", "reason": "bot_pr"}
        review_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_bot_pr_still_skipped(self):
        raw, sig = _signed(_pr_event("dependabot[bot]", draft=False))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", True), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        body = json.loads(response.body)
        assert body == {"status": "ignored", "reason": "bot_pr"}
        review_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_draft_pr_still_skipped(self):
        raw, sig = _signed(_pr_event("some-human", draft=True))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", True), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        body = json.loads(response.body)
        assert body == {"status": "ignored", "reason": "draft_pr"}
        review_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_ready_pr_gets_normal_review(self):
        raw, sig = _signed(_pr_event("some-human", draft=False))
        request = _request(raw, sig)
        with patch("webhooks.github.GITHUB_WEBHOOK_SECRET", SECRET), \
             patch("webhooks.github.SELF_REVIEW_ENABLED", True), \
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        body = json.loads(response.body)
        assert body["status"] == "accepted"
        assert body["mode"] == "review"
        assert review_mock.call_args.kwargs["self_review"] is False


class TestSelfReviewPromptSource:
    """Source-level assertions (same style as test_github_webhook_prompt.py)."""

    def setup_method(self):
        self.source = Path("webhooks/github.py").read_text()

    def test_self_review_forces_comment_event(self):
        assert "GitHub rejects APPROVE/REQUEST_CHANGES" in self.source
        assert "event: `COMMENT` — ALWAYS" in self.source

    def test_self_review_has_verdict_lines(self):
        assert "Self-review: no blocking issues found" in self.source
        assert "blocking issue(s) found — address before human review" in self.source

    def test_self_review_mode_header_is_fresh_context(self):
        assert "Mode: SELF-REVIEW (fresh context)" in self.source
        assert "NO memory of writing this" in self.source

    def test_normal_review_path_preserved(self):
        assert "`APPROVE` if **no**" in self.source
        assert "`REQUEST_CHANGES` if any" in self.source

    def test_rereview_call_sites_propagate_self_review(self):
        # Both explicit re-review call sites must keep agent PRs in self-review mode
        assert self.source.count(
            'self_review=pr_details.get("user", {}).get("login", "") == AGENT_GITHUB_LOGIN'
        ) == 2

    def test_trigger_type_distinguishes_self_review(self):
        assert '"pr_self_review" if self_review else "pr_review"' in self.source


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _review_kwargs(**overrides) -> dict:
    base = dict(
        repo_owner="example-org", repo_name="example-repo",
        repo_full_name="example-org/example-repo", pr_number=42,
        pr_title="feat: something", pr_url="https://github.com/example-org/example-repo/pull/42",
        base_sha="b" * 40, head_sha="h" * 40, base_branch="main", head_branch="feat/something",
        action="opened", pr_author=AGENT_GITHUB_LOGIN, conversation_id="conv-1",
        self_review=True,
    )
    base.update(overrides)
    return base


def _lock_db(update_one=None):
    """db double: SHA dedup finds nothing; db["pr_self_review_locks"] is `locks`."""
    locks = MagicMock()
    result = MagicMock(matched_count=0, upserted_id="new")
    locks.update_one = update_one or AsyncMock(return_value=result)
    locks.delete_one = AsyncMock()
    locks.find_one = AsyncMock(return_value=None)
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=locks)
    db.conversations.find_one = AsyncMock(return_value=None)
    db.conversations.insert_one = AsyncMock()
    db.conversations.update_one = AsyncMock()
    return db, locks


def _agent_stream(*chunks: str):
    async def _gen(**_kwargs):
        for chunk in chunks:
            yield chunk
    return _gen


class TestSelfReviewPipeline:
    """Behavioural tests of _process_pr_review's finally-block for self-reviews,
    with the agent and GitHub calls stubbed."""

    def _patches(self, reviews, stream=None, pr_details=None, reviews_before=None):
        # get_pr_reviews is called twice per self-review: once BEFORE the agent
        # (ID snapshot) and once AFTER (verdict lookup). Model both.
        calls = {"n": 0}

        async def _reviews(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                if isinstance(reviews_before, Exception):
                    raise reviews_before
                return list(reviews_before or [])
            return reviews

        return [
            patch("webhooks.github.get_db", return_value=None),
            patch("webhooks.github._get_pr_stats", new_callable=AsyncMock, return_value={}),
            patch("webhooks.github.get_agent_review_threads", new_callable=AsyncMock, return_value=[]),
            patch("webhooks.github._minimize_previous_agent_comments", new_callable=AsyncMock, return_value=0),
            patch("webhooks.github._create_pr_comment", new_callable=AsyncMock, return_value=1001),
            patch("webhooks.github._create_check_run", new_callable=AsyncMock, return_value=2002),
            patch("webhooks.github.stream_agent", new=stream or _agent_stream("posted review")),
            patch("webhooks.github._delete_pr_comment", new_callable=AsyncMock),
            patch("webhooks.github._update_pr_comment", new_callable=AsyncMock),
            patch("webhooks.github._update_check_run", new_callable=AsyncMock),
            patch("webhooks.github.get_pr_reviews", new_callable=AsyncMock, side_effect=_reviews),
            patch("webhooks.github.post_self_review_followup", new_callable=AsyncMock, return_value=True),
            patch("webhooks.github._get_pr_details", new_callable=AsyncMock, return_value=pr_details),
        ]

    async def _run(self, reviews, stream=None, pr_details=None, reviews_before=None, **kwargs):
        patches = self._patches(reviews, stream=stream, pr_details=pr_details,
                                reviews_before=reviews_before)
        mocks = {}
        for p in patches:
            mocks[p.attribute] = p.start()
        try:
            await _process_pr_review(**_review_kwargs(**kwargs))
        finally:
            for p in patches:
                p.stop()
        return mocks

    @pytest.mark.asyncio
    async def test_verdict_from_this_run_is_threaded_back(self):
        fresh = _ts(datetime.now(timezone.utc) + timedelta(minutes=2))
        reviews = [{"id": "R2", "author": AGENT_GITHUB_LOGIN, "created_at": fresh,
                    "body": "✅ Self-review: no blocking issues found — ready for human review"}]
        mocks = await self._run(reviews, reviews_before=[])

        followup = mocks["post_self_review_followup"].call_args.kwargs
        assert followup["succeeded"] is True
        assert followup["verdict"].startswith("✅ Self-review: no blocking")
        check = mocks["_update_check_run"].call_args.kwargs
        assert check["conclusion"] == "success"
        mocks["_delete_pr_comment"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stale_previous_verdict_is_not_reported_as_fresh(self):
        # Agent finished cleanly but posted nothing; the only review on the PR is
        # from the previous push. Must surface as incomplete, not "complete".
        stale = _ts(datetime.now(timezone.utc) - timedelta(hours=1))
        reviews = [{"id": "R1", "author": AGENT_GITHUB_LOGIN, "created_at": stale,
                    "body": "✅ Self-review: no blocking issues found (previous push)"}]
        mocks = await self._run(reviews, reviews_before=reviews, action="synchronize")

        followup = mocks["post_self_review_followup"].call_args.kwargs
        assert followup["succeeded"] is True
        assert followup["verdict"] is None
        check = mocks["_update_check_run"].call_args.kwargs
        assert check["conclusion"] == "failure"
        assert "Incomplete" in check["title"]
        # Status comment is turned into a handoff, not deleted
        mocks["_delete_pr_comment"].assert_not_awaited()
        mocks["_update_pr_comment"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_error_posts_failed_followup(self):
        mocks = await self._run([], stream=_agent_stream("Sorry, I encountered an error: boom"))
        followup = mocks["post_self_review_followup"].call_args.kwargs
        assert followup["succeeded"] is False
        assert followup["verdict"] is None
        assert mocks["_update_check_run"].call_args.kwargs["conclusion"] == "failure"
        # Only the pre-run snapshot; no verdict lookup after a failed run
        assert mocks["get_pr_reviews"].await_count == 1

    @pytest.mark.asyncio
    async def test_coalesced_rerun_never_reports_previous_runs_verdict(self):
        # Regression for the coalescing hole: run A posted its review, its
        # finally scheduled run B on the new head ~10s later, and B's agent
        # posted nothing. A's review is INSIDE any timestamp skew window, so
        # only the ID snapshot can reject it.
        seconds_ago = _ts(datetime.now(timezone.utc) - timedelta(seconds=10))
        previous = [{"id": "R-runA", "author": AGENT_GITHUB_LOGIN, "created_at": seconds_ago,
                     "body": "✅ Self-review: no blocking issues found (run A, old head)"}]
        mocks = await self._run(previous, reviews_before=previous, action="synchronize_coalesced")

        followup = mocks["post_self_review_followup"].call_args.kwargs
        assert followup["verdict"] is None
        assert mocks["_update_check_run"].call_args.kwargs["conclusion"] == "failure"
        mocks["_delete_pr_comment"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_review_stamped_with_moved_head_still_counts_for_this_run(self):
        # GitHub stamps a review with the head at submission time. If a push
        # landed mid-review, this run's review carries the NEW head sha, so
        # commit_oid must not be used to reject it — only the ID snapshot.
        fresh = _ts(datetime.now(timezone.utc) + timedelta(minutes=1))
        reviews = [{"id": "R-new", "author": AGENT_GITHUB_LOGIN, "created_at": fresh,
                    "commit_oid": "n" * 40,  # != head_sha "h"*40 being reviewed
                    "body": "🔴 Self-review: 1 blocking issue(s) found — address before human review"}]
        mocks = await self._run(reviews, reviews_before=[])
        assert mocks["post_self_review_followup"].call_args.kwargs["verdict"].startswith("🔴")
        assert mocks["_update_check_run"].call_args.kwargs["conclusion"] == "success"

    @pytest.mark.asyncio
    async def test_snapshot_failure_falls_back_to_timestamp_scoping(self):
        fresh = _ts(datetime.now(timezone.utc) + timedelta(minutes=2))
        old = _ts(datetime.now(timezone.utc) - timedelta(hours=1))
        reviews = [
            {"id": "R1", "author": AGENT_GITHUB_LOGIN, "created_at": old,
             "body": "✅ Self-review: stale"},
            {"id": "R2", "author": AGENT_GITHUB_LOGIN, "created_at": fresh,
             "body": "✅ Self-review: no blocking issues found"},
        ]
        mocks = await self._run(reviews, reviews_before=RuntimeError("graphql down"))
        verdict = mocks["post_self_review_followup"].call_args.kwargs["verdict"]
        assert verdict == "✅ Self-review: no blocking issues found"

    @pytest.mark.asyncio
    async def test_followup_failure_never_masks_review_result(self):
        fresh = _ts(datetime.now(timezone.utc) + timedelta(minutes=1))
        reviews = [{"author": AGENT_GITHUB_LOGIN, "created_at": fresh,
                    "body": "✅ Self-review: no blocking issues found"}]
        patches = self._patches(reviews)
        for p in patches:
            p.start()
        try:
            with patch("webhooks.github.post_self_review_followup",
                       new_callable=AsyncMock, side_effect=RuntimeError("slack down")):
                await _process_pr_review(**_review_kwargs())  # must not raise
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_held_lock_skips_run_before_any_github_work(self):
        # Two `synchronize` events seconds apart: the second must lose the lock
        # at the very top of the pipeline — before _get_pr_stats & co, which is
        # the multi-second window a conversation-based check could not cover.
        db, locks = _lock_db(update_one=AsyncMock(side_effect=DuplicateKeyError("dup")))
        locks.find_one = AsyncMock(return_value={"conversation_id": "running-1", "head_sha": "a" * 40})
        with patch("webhooks.github.get_db", return_value=db), \
             patch("webhooks.github._get_pr_stats", new_callable=AsyncMock) as stats_mock, \
             patch("webhooks.github._get_pr_details", new_callable=AsyncMock) as details_mock:
            await _process_pr_review(**_review_kwargs(action="synchronize", head_sha="n" * 40))
        stats_mock.assert_not_awaited()      # skipped before doing any work
        details_mock.assert_not_awaited()    # a loser never schedules a re-run
        locks.delete_one.assert_not_awaited()  # and never releases someone else's lock
        claim = locks.update_one.call_args
        assert claim.kwargs["upsert"] is True
        assert "$lt" in claim.args[0]["last_heartbeat"]  # stale holders are taken over

    @pytest.mark.asyncio
    async def test_lock_holder_releases_then_checks_for_newer_head(self):
        db, locks = _lock_db()
        order: list[str] = []
        locks.delete_one = AsyncMock(side_effect=lambda *a, **k: order.append("release"))

        async def _details(*_a, **_k):
            order.append("head_check")
            return None

        patches = self._patches([{"id": "R2", "author": AGENT_GITHUB_LOGIN,
                                  "created_at": _ts(datetime.now(timezone.utc)),
                                  "body": "✅ Self-review: no blocking issues found"}],
                                reviews_before=[])
        patches[0] = patch("webhooks.github.get_db", return_value=db)
        patches[-1] = patch("webhooks.github._get_pr_details", new_callable=AsyncMock, side_effect=_details)
        for p in patches:
            p.start()
        try:
            await _process_pr_review(**_review_kwargs())
        finally:
            for p in patches:
                p.stop()
        assert order == ["release", "head_check"]  # release first so the re-run can claim it
        release_filter = locks.delete_one.call_args.args[0]
        assert release_filter["conversation_id"] == "conv-1"

    @pytest.mark.asyncio
    async def test_lock_released_even_when_prework_raises(self):
        db, locks = _lock_db()
        with patch("webhooks.github.get_db", return_value=db), \
             patch("webhooks.github._get_pr_stats", new_callable=AsyncMock, side_effect=RuntimeError("github down")), \
             patch("webhooks.github._get_pr_details", new_callable=AsyncMock, return_value=None):
            with pytest.raises(RuntimeError):
                await _process_pr_review(**_review_kwargs())
        locks.delete_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_review_proceeds_when_lock_is_held(self):
        db, locks = _lock_db(update_one=AsyncMock(side_effect=DuplicateKeyError("dup")))
        locks.find_one = AsyncMock(return_value={"conversation_id": "running-1"})
        with patch("webhooks.github.get_db", return_value=db), \
             patch("webhooks.github._get_pr_stats", new_callable=AsyncMock, side_effect=RuntimeError("stop here")), \
             patch("webhooks.github._get_pr_details", new_callable=AsyncMock) as details_mock:
            with pytest.raises(RuntimeError):
                await _process_pr_review(**_review_kwargs(force_review=True))
        details_mock.assert_not_awaited()  # not the holder → no re-run duty
        locks.delete_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_only_applies_to_self_reviews(self):
        db, locks = _lock_db()
        with patch("webhooks.github.get_db", return_value=db), \
             patch("webhooks.github._get_pr_stats", new_callable=AsyncMock, side_effect=RuntimeError("stop here")):
            with pytest.raises(RuntimeError):
                await _process_pr_review(**_review_kwargs(self_review=False, pr_author="human"))
        assert db.conversations.find_one.await_count == 1  # only the SHA dedup query
        locks.update_one.assert_not_awaited()


class TestCoalescedRerun:
    @pytest.mark.asyncio
    async def test_reruns_on_newest_head_when_it_moved(self):
        latest = {
            "state": "open", "title": "feat: something", "html_url": "u",
            "base": {"sha": "b" * 40, "ref": "main"},
            "head": {"sha": "n" * 40, "ref": "feat/something"},
            "user": {"login": AGENT_GITHUB_LOGIN},
        }
        with patch("webhooks.github._get_pr_details", new_callable=AsyncMock, return_value=latest), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            scheduled = await _rerun_self_review_if_head_moved(
                repo_owner="example-org", repo_name="example-repo",
                repo_full_name="example-org/example-repo", pr_number=42,
                reviewed_head_sha="h" * 40,
            )
            await asyncio.sleep(0)
        assert scheduled is True
        kwargs = review_mock.call_args.kwargs
        assert kwargs["head_sha"] == "n" * 40
        assert kwargs["self_review"] is True
        assert kwargs["action"] == "synchronize_coalesced"

    @pytest.mark.asyncio
    async def test_no_rerun_when_head_unchanged_or_pr_closed(self):
        same = {"state": "open", "head": {"sha": "h" * 40}}
        closed = {"state": "closed", "head": {"sha": "n" * 40}}
        for details in (same, closed, None):
            with patch("webhooks.github._get_pr_details", new_callable=AsyncMock, return_value=details), \
                 patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
                assert await _rerun_self_review_if_head_moved(
                    repo_owner="example-org", repo_name="example-repo",
                    repo_full_name="example-org/example-repo", pr_number=42,
                    reviewed_head_sha="h" * 40,
                ) is False
            review_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_lookup_failure_never_raises(self):
        with patch("webhooks.github._get_pr_details", new_callable=AsyncMock, side_effect=RuntimeError("api")):
            assert await _rerun_self_review_if_head_moved(
                repo_owner="o", repo_name="r", repo_full_name="o/r", pr_number=1,
                reviewed_head_sha="h" * 40,
            ) is False
