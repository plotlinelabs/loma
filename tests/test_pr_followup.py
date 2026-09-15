"""Tests for the two-stage self-review notification (utils/pr_followup.py).

Stage 1: PR-creating flows register where the PR was announced.
Stage 2: the self-review pipeline threads the verdict back to that target.
"""

import importlib
import importlib.util
import json
import os
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.pr_followup import (
    COLLECTION,
    REREVIEW_COMMAND,
    VERDICT_MAX_CHARS,
    SelfReviewLookup,
    _build_messages,
    _escape_slack,
    _normalize_repo,
    extract_self_review_verdict,
    find_self_review,
    get_pr_notification_target,
    mark_self_review_disabled,
    post_self_review_followup,
    register_pr_notification_target,
)

REPO = "example-org/example-repo"
PR_URL = f"https://github.com/{REPO}/pull/42"


def _fake_db(existing_record=None):
    """A minimal db double exposing db[COLLECTION].update_one/find_one."""
    collection = MagicMock()
    collection.update_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=existing_record)
    # The disabled-notice claim: by default this event wins it.
    collection.find_one_and_update = AsyncMock(return_value=existing_record)
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db, collection


class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_slack_target_upserts(self):
        db, collection = _fake_db()
        target = {"type": "slack", "channel": "C123", "thread_ts": "1700000000.1"}
        doc = await register_pr_notification_target(db, REPO, 42, target)
        assert doc["target"] == target
        collection.update_one.assert_awaited_once()
        args, kwargs = collection.update_one.call_args
        assert args[0] == {"repo_full_name": REPO, "pr_number": 42}
        assert kwargs.get("upsert") is True

    @pytest.mark.asyncio
    async def test_register_linear_and_loma_targets(self):
        db, _ = _fake_db()
        await register_pr_notification_target(
            db, REPO, 42, {"type": "linear", "issue_id": "uuid-1"}
        )
        await register_pr_notification_target(
            db, REPO, 42, {"type": "loma", "user_email": "a@b.co", "conversation_id": "c1"}
        )

    @pytest.mark.asyncio
    async def test_register_rejects_bad_targets(self):
        db, collection = _fake_db()
        with pytest.raises(ValueError):
            await register_pr_notification_target(db, REPO, 42, {"type": "carrier-pigeon"})
        with pytest.raises(ValueError):
            # slack without thread_ts
            await register_pr_notification_target(
                db, REPO, 42, {"type": "slack", "channel": "C123"}
            )
        with pytest.raises(ValueError):
            await register_pr_notification_target(
                db, "not-a-full-name", 42, {"type": "linear", "issue_id": "u"}
            )
        with pytest.raises(ValueError):
            await register_pr_notification_target(
                db, REPO, 0, {"type": "linear", "issue_id": "u"}
            )
        collection.update_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_target_roundtrip(self):
        record = {"repo_full_name": REPO, "pr_number": 42, "target": {"type": "linear"}}
        db, collection = _fake_db(existing_record=record)
        assert await get_pr_notification_target(db, REPO, 42) == record
        collection.find_one.assert_awaited_once_with(
            {"repo_full_name": REPO, "pr_number": 42}
        )

    def test_normalize_repo_lowercases_and_strips(self):
        assert _normalize_repo("ExampleOrg/Repo") == "exampleorg/repo"
        assert _normalize_repo("  Org/Name  ") == "org/name"
        assert _normalize_repo("") == ""

    @pytest.mark.asyncio
    async def test_repo_full_name_is_case_normalized_on_both_paths(self):
        # GitHub repo names are case-insensitive but Mongo equality is not; a
        # `ExampleOrg/Repo` registration must match a `exampleorg/repo`
        # webhook lookup or the follow-up is silently dropped.
        db, collection = _fake_db()
        await register_pr_notification_target(
            db, "ExampleOrg/Repo", 7, {"type": "linear", "issue_id": "u"}
        )
        stored_key = collection.update_one.call_args_list[0].args[0]
        assert stored_key["repo_full_name"] == "exampleorg/repo"
        await get_pr_notification_target(db, "EXAMPLEORG/REPO", 7)
        # First read is the exact (normalised) key; the fake returns None so a
        # case-insensitive fallback read follows — see the dedicated test.
        assert collection.find_one.call_args_list[0].args[0]["repo_full_name"] == "exampleorg/repo"

    @pytest.mark.asyncio
    async def test_mark_self_review_disabled_upserts_marker(self):
        db, collection = _fake_db()
        await mark_self_review_disabled(db, "Org/Repo", 9, PR_URL)
        args, kwargs = collection.update_one.call_args
        assert args[0] == {"repo_full_name": "org/repo", "pr_number": 9}
        assert args[1]["$set"]["disabled_pending"] is True
        assert args[1]["$set"]["disabled_pending_pr_url"] == PR_URL
        assert kwargs.get("upsert") is True

    @pytest.mark.asyncio
    async def test_register_delivers_pending_disabled_notice(self):
        # The webhook set `disabled_pending` on `opened` before a target had
        # registered (single-push PR, no later synchronize). Registration must
        # now answer the Stage-1 promise and clear the marker.
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "loma", "user_email": "a@b.co"},
            "registered_at": datetime.now(timezone.utc),
            "disabled_pending": True,
            "disabled_pending_pr_url": PR_URL,
        }
        db, collection = _fake_db(existing_record=record)
        with patch("utils.pr_followup._dispatch_loma", new_callable=AsyncMock, return_value=True) as loma:
            doc = await register_pr_notification_target(
                db, REPO, 42, {"type": "loma", "user_email": "a@b.co"}
            )
        loma.assert_awaited_once()
        unsets = [c for c in collection.update_one.call_args_list
                  if isinstance(c.args[1], dict) and "$unset" in c.args[1]]
        assert any("disabled_pending" in c.args[1]["$unset"] for c in unsets)
        assert doc["disabled_notice"] == "delivered"

    @pytest.mark.asyncio
    async def test_register_reports_an_undelivered_pending_notice_and_keeps_the_marker(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "loma", "user_email": "a@b.co"},
            "registered_at": datetime.now(timezone.utc),
            "disabled_pending": True,
            "disabled_pending_pr_url": PR_URL,
        }
        db, collection = _fake_db(existing_record=record)
        with patch("utils.pr_followup._dispatch_loma", new_callable=AsyncMock, return_value=False):
            doc = await register_pr_notification_target(
                db, REPO, 42, {"type": "loma", "user_email": "a@b.co"}
            )
        assert doc["disabled_notice"] == "failed"
        unsets = [c for c in collection.update_one.call_args_list
                  if isinstance(c.args[1], dict) and "$unset" in c.args[1]]
        assert not any("disabled_pending" in c.args[1]["$unset"] for c in unsets)

    @pytest.mark.asyncio
    async def test_stale_disabled_marker_is_cleared_once_the_registration_was_told(self):
        # The webhook re-arms `disabled_pending` on every reviewable event. After
        # the notice went out for this registration, a later `synchronize` must
        # clear the re-armed marker; otherwise flipping the deploy to enabled and
        # re-registering would replay a spurious "self-review skipped" notice.
        registered_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        told = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "loma", "user_email": "a@b.co"},
            "registered_at": registered_at,
            "last_followup_disabled": True,
            "last_followup_at": registered_at + timedelta(minutes=1),
            "disabled_pending": True, "disabled_pending_pr_url": PR_URL,
        }
        # (a) pre-check says "already told"
        db, collection = _fake_db(existing_record=told)
        with patch("utils.pr_followup._dispatch_loma", new_callable=AsyncMock) as loma:
            assert await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False, disabled=True,
            ) is False
        loma.assert_not_awaited()
        assert collection.update_one.call_args.args[1] == \
            {"$unset": {"disabled_pending": "", "disabled_pending_pr_url": ""}}
        # (b) lost the atomic claim to a concurrent event
        fresh = {k: v for k, v in told.items() if not k.startswith("last_followup")}
        db, collection = _fake_db(existing_record=fresh)
        collection.find_one_and_update = AsyncMock(return_value=None)
        assert await post_self_review_followup(
            db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
            verdict=None, succeeded=False, disabled=True,
        ) is False
        assert collection.update_one.call_args.args[1] == \
            {"$unset": {"disabled_pending": "", "disabled_pending_pr_url": ""}}
        # (c) delivered now → cleared too, and the delivery record still written
        db, collection = _fake_db(existing_record=fresh)
        with patch("utils.pr_followup._dispatch_loma", new_callable=AsyncMock, return_value=True):
            assert await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False, disabled=True,
            ) is True
        updates = [c.args[1] for c in collection.update_one.call_args_list]
        assert {"$unset": {"disabled_pending": "", "disabled_pending_pr_url": ""}} in updates
        assert any("$set" in u and u["$set"].get("last_followup_disabled") is True for u in updates)
        # (d) no marker on the record → nothing to clear, no extra write
        db, collection = _fake_db(existing_record={k: v for k, v in told.items()
                                                   if not k.startswith("disabled_pending")})
        assert await post_self_review_followup(
            db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
            verdict=None, succeeded=False, disabled=True,
        ) is False
        collection.update_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_target_falls_back_to_a_case_insensitive_read(self):
        # A doc registered before keys were lowercased must stay reachable
        # during the deploy window (init_observability folds them at boot).
        legacy = {"repo_full_name": "Example-Org/Example-Repo", "pr_number": 42,
                  "target": {"type": "linear", "issue_id": "u"}}
        db, collection = _fake_db()
        collection.find_one = AsyncMock(side_effect=[None, legacy])
        assert await get_pr_notification_target(db, "example-org/example-repo", 42) == legacy
        fallback_filter = collection.find_one.call_args_list[1].args[0]
        assert fallback_filter["repo_full_name"]["$options"] == "i"
        assert fallback_filter["repo_full_name"]["$regex"] == "^example\\-org/example\\-repo$"
        # Exact hit → no second read
        db, collection = _fake_db(existing_record=legacy)
        await get_pr_notification_target(db, REPO, 42)
        collection.find_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_without_pending_marker_delivers_nothing(self):
        db, collection = _fake_db(existing_record=None)
        with patch("utils.pr_followup.post_self_review_followup", new_callable=AsyncMock) as pf:
            await register_pr_notification_target(
                db, REPO, 42, {"type": "linear", "issue_id": "u"}
            )
        pf.assert_not_awaited()


class TestVerdictExtraction:
    def test_picks_latest_agent_verdict_line(self):
        reviews = [
            {"author": "loma-insights", "body": "🔴 Self-review: 2 blocking issue(s) found\n\ndetails"},
            {"author": "some-human", "body": "✅ Self-review: fake, wrong author"},
            {"author": "loma-insights", "body": "✅ Self-review: no blocking issues found — ready for human review\n\n<!-- loma-agent-review -->"},
        ]
        verdict = extract_self_review_verdict(reviews, "loma-insights")
        assert verdict.startswith("✅ Self-review: no blocking issues")

    def test_ignores_non_agent_reviews_and_handles_empty(self):
        assert extract_self_review_verdict([], "loma-insights") is None
        assert extract_self_review_verdict(None, "loma-insights") is None
        reviews = [{"author": "human", "body": "✅ Self-review: nope"}]
        assert extract_self_review_verdict(reviews, "loma-insights") is None

    def test_pending_reviews_never_count_as_posted(self):
        # GitHub returns an unsubmitted (PENDING) review to its author — the
        # very token this pipeline queries with. An agent that opened one and
        # died before `submit_pending` must not register as "review posted":
        # nobody but the bot can see that review.
        reviews = [{"id": "R-pending", "author": "loma-insights", "state": "PENDING",
                    "body": "✅ Self-review: no blocking issues found (never submitted)"}]
        assert find_self_review(reviews, "loma-insights", exclude_review_ids=set()) == (
            SelfReviewLookup(review_found=False, verdict=None)
        )
        assert extract_self_review_verdict(reviews, "loma-insights") is None
        reviews.append({"id": "R-sub", "author": "loma-insights", "state": "COMMENTED",
                        "body": "🔴 Self-review: 1 blocking issue(s) found"})
        lookup = find_self_review(reviews, "loma-insights", exclude_review_ids=set())
        assert lookup.review_found is True and lookup.verdict.startswith("🔴")

    def test_falls_back_to_older_review_when_latest_has_no_verdict(self):
        reviews = [
            {"author": "loma-insights", "body": "🔴 Self-review: 1 blocking issue(s) found"},
            {"author": "loma-insights", "body": "just a comment reply, no verdict"},
        ]
        verdict = extract_self_review_verdict(reviews, "loma-insights")
        assert verdict.startswith("🔴 Self-review:")

    def test_started_at_scopes_verdict_to_this_run(self):
        # Regression: a run whose agent finished WITHOUT posting must not report
        # the previous push's verdict as fresh.
        started_at = datetime.now(timezone.utc)
        old = (started_at - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        reviews = [
            {"author": "loma-insights", "created_at": old,
             "body": "✅ Self-review: no blocking issues found (from the PREVIOUS push)"},
        ]
        assert extract_self_review_verdict(reviews, "loma-insights", started_at=started_at) is None
        # Without started_at the legacy behaviour (latest agent verdict) is kept
        assert extract_self_review_verdict(reviews, "loma-insights") is not None

    def test_started_at_accepts_review_from_this_run(self):
        started_at = datetime.now(timezone.utc)
        fresh = (started_at + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (started_at - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        reviews = [
            {"author": "loma-insights", "created_at": old,
             "body": "✅ Self-review: stale verdict"},
            {"author": "loma-insights", "created_at": fresh,
             "body": "🔴 Self-review: 2 blocking issue(s) found — address before human review"},
        ]
        verdict = extract_self_review_verdict(reviews, "loma-insights", started_at=started_at)
        assert verdict.startswith("🔴 Self-review: 2 blocking")

    def test_started_at_tolerates_small_clock_skew(self):
        # A review stamped a few seconds BEFORE started_at (host clock ahead of
        # GitHub) must still count as this run's review.
        started_at = datetime.now(timezone.utc)
        skewed = (started_at - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        reviews = [{"author": "loma-insights", "created_at": skewed,
                    "body": "✅ Self-review: no blocking issues found"}]
        assert extract_self_review_verdict(reviews, "loma-insights", started_at=started_at)

    def test_started_at_skips_reviews_with_unparseable_timestamp(self):
        started_at = datetime.now(timezone.utc)
        reviews = [{"author": "loma-insights", "created_at": "garbage",
                    "body": "✅ Self-review: no blocking issues found"}]
        assert extract_self_review_verdict(reviews, "loma-insights", started_at=started_at) is None

    def test_exclude_review_ids_is_structural_not_temporal(self):
        # The coalescing case: the previous run's review is 10s old — inside
        # the skew window — so only the ID snapshot can reject it.
        now = datetime.now(timezone.utc)
        ten_s_ago = (now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        previous = {"id": "R1", "author": "loma-insights", "created_at": ten_s_ago,
                    "body": "✅ Self-review: no blocking issues found (previous run)"}
        # Timestamp scoping alone would (wrongly) accept it…
        assert extract_self_review_verdict([previous], "loma-insights", started_at=now)
        # …the ID snapshot rejects it.
        assert extract_self_review_verdict([previous], "loma-insights", exclude_review_ids={"R1"}) is None
        # A review that was not in the snapshot is this run's, whatever its timestamp
        fresh = {"id": "R2", "author": "loma-insights", "created_at": ten_s_ago,
                 "body": "🔴 Self-review: 1 blocking issue(s) found"}
        assert extract_self_review_verdict(
            [previous, fresh], "loma-insights", exclude_review_ids={"R1"}
        ).startswith("🔴")

    def test_exclude_review_ids_skips_reviews_without_id(self):
        reviews = [{"author": "loma-insights", "body": "✅ Self-review: no id, cannot be proven new"}]
        assert extract_self_review_verdict(reviews, "loma-insights", exclude_review_ids=set()) is None
        # An empty snapshot (no prior agent reviews) still accepts an ID'd review
        reviews = [{"id": "R1", "author": "loma-insights", "body": "✅ Self-review: first ever"}]
        assert extract_self_review_verdict(reviews, "loma-insights", exclude_review_ids=set())


    def test_verdict_line_is_anchored_and_capped(self):
        # The review body is agent-controlled and the verdict line is relayed
        # verbatim to Slack / Linear / the inbox: only a line that STARTS with
        # the verdict counts (markdown emphasis tolerated), and it is capped so
        # a runaway line cannot bloat the notification.
        buried = [{"id": "R1", "author": "loma-insights",
                   "body": "Notes: the earlier Self-review: line was wrong.\n\nmore prose"}]
        assert extract_self_review_verdict(buried, "loma-insights") is None
        bold = [{"id": "R2", "author": "loma-insights",
                 "body": "**✅ Self-review: no blocking issues found**\n\ndetails"}]
        assert extract_self_review_verdict(bold, "loma-insights") == "✅ Self-review: no blocking issues found"
        long = [{"id": "R3", "author": "loma-insights", "body": "🔴 Self-review: " + "x" * 1000}]
        assert len(extract_self_review_verdict(long, "loma-insights")) == VERDICT_MAX_CHARS

    def test_find_self_review_distinguishes_nothing_posted_from_no_verdict_line(self):
        # "The agent posted nothing" and "the agent posted a review without
        # the verdict line" need different follow-up copy; the lookup must
        # keep them apart while applying the same run scoping.
        assert find_self_review([], "loma-insights", exclude_review_ids=set()) == \
            SelfReviewLookup(review_found=False, verdict=None)
        verdictless = [{"id": "R9", "author": "loma-insights", "body": "Looks fine.\n- nit"}]
        assert find_self_review(verdictless, "loma-insights", exclude_review_ids=set()) == \
            SelfReviewLookup(review_found=True, verdict=None)
        # A previous run's review (excluded) does not count as "found"
        assert find_self_review(verdictless, "loma-insights", exclude_review_ids={"R9"}).review_found is False
        # Another user's review never counts
        assert find_self_review([{"id": "R1", "author": "human", "body": "✅ Self-review: x"}],
                                "loma-insights", exclude_review_ids=set()).review_found is False
        # Verdict comes from the newest this-run review that has one
        both = verdictless + [{"id": "R10", "author": "loma-insights", "body": "✅ Self-review: ok"}]
        assert find_self_review(both, "loma-insights", exclude_review_ids=set()) == \
            SelfReviewLookup(review_found=True, verdict="✅ Self-review: ok")
        assert extract_self_review_verdict(both, "loma-insights", exclude_review_ids=set()) == "✅ Self-review: ok"

    def test_quoted_or_non_first_line_verdict_is_not_promoted(self):
        # The prompt requires the body to START with the verdict; only the first
        # non-empty line counts. A blockquoted prior verdict (a re-run quoting
        # the previous run while explaining what changed) or a fenced template
        # line must never override the real verdict — the line is relayed
        # verbatim, so a red verdict must not be turned green by quoted text.
        quoted = [{"id": "R1", "author": "loma-insights",
                   "body": "> ✅ Self-review: no blocking issues (quoting the previous run)"
                           "\n\n🔴 Self-review: 2 blocking issue(s)"}]
        lk = find_self_review(quoted, "loma-insights", exclude_review_ids=set())
        # Either candidate login counts (label-backstop cases); unknown ones do not.
        assert find_self_review(quoted, {"human-dev", "loma-insights"}, exclude_review_ids=set()) == lk
        assert find_self_review(quoted, {"human-dev", ""}, exclude_review_ids=set()).review_found is False
        assert lk.review_found is True and lk.verdict is None
        later = [{"id": "R2", "author": "loma-insights",
                  "body": "Here is my review.\n\n✅ Self-review: ok"}]
        assert extract_self_review_verdict(later, "loma-insights", exclude_review_ids=set()) is None
        fenced = [{"id": "R3", "author": "loma-insights",
                   "body": "```\n✅ Self-review: template line\n```\n\n🔴 Self-review: real"}]
        assert extract_self_review_verdict(fenced, "loma-insights", exclude_review_ids=set()) is None
        # A genuine first-line verdict still works when a `>` appears LATER.
        ok = [{"id": "R4", "author": "loma-insights",
               "body": "✅ Self-review: clean\n\n> quoting something else"}]
        assert extract_self_review_verdict(ok, "loma-insights", exclude_review_ids=set()) == "✅ Self-review: clean"


class TestMessageOutcomes:
    def test_retry_instruction_uses_mention_form(self):
        # A bare `/rereview` is ignored by the issue_comment webhook (it only
        # dispatches comments mentioning the agent) — the copy must not send
        # humans down a dead recovery path.
        assert REREVIEW_COMMAND == "@loma-agent /rereview"
        for succeeded in (True, False):
            _, body, slack = _build_messages(42, PR_URL, None, succeeded)
            assert REREVIEW_COMMAND in body and REREVIEW_COMMAND in slack
            assert " `/rereview`" not in body and " `/rereview`" not in slack

    def test_success_without_verdict_is_reported_as_incomplete(self):
        title, body, slack = _build_messages(42, PR_URL, None, True)
        assert "incomplete" in title.lower()
        assert "no review from this run was found" in body
        assert "unreviewed" in slack
        # The old copy claimed a review was posted — it must be gone.
        assert "review posted" not in body and "review posted" not in slack

    def test_review_posted_without_verdict_points_at_the_review(self):
        title, body, slack = _build_messages(42, PR_URL, None, True, review_posted=True)
        assert "without a verdict" in title.lower()
        assert "Read the review on the PR directly" in body
        assert "Read the review on the PR directly" in slack
        assert "unreviewed" not in slack  # a review exists — do not call the PR unreviewed
        assert REREVIEW_COMMAND not in body and REREVIEW_COMMAND not in slack  # nothing to retry

    def test_success_with_verdict(self):
        title, body, slack = _build_messages(42, PR_URL, "✅ Self-review: ok", True)
        assert "complete" in title.lower()
        assert "✅ Self-review: ok" in body and "✅ Self-review: ok" in slack

    def test_disabled_outcome(self):
        title, body, slack = _build_messages(42, PR_URL, None, False, disabled=True)
        assert "skipped" in title.lower()
        assert "LOMA_ENABLE_SELF_REVIEW" in body
        assert "disabled" in slack and "unreviewed" in slack

    def test_verdict_unknown_outcome(self):
        # A transient GitHub failure during the verdict lookup means we do not
        # know the outcome — it must NOT be reported as "unreviewed".
        title, body, slack = _build_messages(42, PR_URL, None, True, verdict_unknown=True)
        assert "unknown" in title.lower()
        assert "could not read" in body.lower()
        assert REREVIEW_COMMAND in body and REREVIEW_COMMAND in slack
        # Not the "finished without posting / unreviewed" copy.
        assert "no review from this run was found" not in body

    def test_verdict_is_slack_escaped(self):
        # The verdict is agent-controlled; Slack control chars must be escaped so
        # it cannot @-mention the channel or inject a link from the bot.
        assert _escape_slack("<!channel> & <@U1> <http://x|y>") == \
            "&lt;!channel&gt; &amp; &lt;@U1&gt; &lt;http://x|y&gt;"
        verdict = "✅ Self-review: ok <!channel>"
        _, body, slack = _build_messages(42, PR_URL, verdict, True)
        assert "<!channel>" not in slack
        assert "&lt;!channel&gt;" in slack


class TestFollowupDispatch:
    @pytest.mark.asyncio
    async def test_no_db_or_no_target_returns_false(self):
        assert await post_self_review_followup(
            None, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
            verdict=None, succeeded=True,
        ) is False
        db, _ = _fake_db(existing_record=None)
        assert await post_self_review_followup(
            db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
            verdict=None, succeeded=True,
        ) is False

    @pytest.mark.asyncio
    async def test_slack_target_posts_thread_reply(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "slack", "channel": "C123", "thread_ts": "1700000000.1"},
        }
        db, collection = _fake_db(existing_record=record)
        fake_client = MagicMock()
        fake_client.chat_postMessage = AsyncMock()
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}), \
             patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client):
            delivered = await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict="✅ Self-review: no blocking issues found", succeeded=True,
            )
        assert delivered is True
        kwargs = fake_client.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == "C123"
        assert kwargs["thread_ts"] == "1700000000.1"
        assert "Self-review complete" in kwargs["text"]
        assert "no blocking issues" in kwargs["text"]
        # Delivery is recorded on the registration doc
        assert collection.update_one.await_count == 1

    @pytest.mark.asyncio
    async def test_review_posted_outcome_is_delivered_and_recorded(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "slack", "channel": "C123", "thread_ts": "1.2"},
        }
        db, collection = _fake_db(existing_record=record)
        fake_client = MagicMock()
        fake_client.chat_postMessage = AsyncMock()
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}), \
             patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client):
            delivered = await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=True, review_posted=True,
            )
        assert delivered is True
        text = fake_client.chat_postMessage.call_args.kwargs["text"]
        assert "posted without a verdict" in text
        recorded = collection.update_one.call_args.args[1]["$set"]
        assert recorded["last_followup_review_posted"] is True
        assert recorded["last_followup_succeeded"] is True

    @pytest.mark.asyncio
    async def test_linear_target_posts_marked_comment(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "linear", "issue_id": "uuid-1"},
        }
        db, _ = _fake_db(existing_record=record)
        with patch("webhooks.linear_api.post_comment", new_callable=AsyncMock, return_value="comment-1") as post_mock:
            delivered = await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict="🔴 Self-review: 1 blocking issue(s) found", succeeded=True,
            )
        assert delivered is True
        issue_id, body = post_mock.call_args.args
        assert issue_id == "uuid-1"
        assert body.startswith("<!-- loma -->")  # loop-prevention marker first
        assert "1 blocking issue(s)" in body

    @pytest.mark.asyncio
    async def test_loma_target_creates_inbox_notification(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "loma", "user_email": "vamsi@example.com", "conversation_id": "conv-1"},
        }
        db, _ = _fake_db(existing_record=record)
        with patch("observability.notifications.create_notification", new_callable=AsyncMock) as notif_mock:
            delivered = await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=True,
            )
        assert delivered is True
        kwargs = notif_mock.call_args.kwargs
        assert kwargs["user_email"] == "vamsi@example.com"
        assert kwargs["conversation_id"] == "conv-1"
        assert kwargs["link"] == PR_URL
        assert kwargs["source"] == "self_review"

    @pytest.mark.asyncio
    async def test_failed_review_posts_fail_visible_followup(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "slack", "channel": "C123", "thread_ts": "1.2"},
        }
        db, _ = _fake_db(existing_record=record)
        fake_client = MagicMock()
        fake_client.chat_postMessage = AsyncMock()
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}), \
             patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client):
            delivered = await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False,
            )
        assert delivered is True
        text = fake_client.chat_postMessage.call_args.kwargs["text"]
        assert "Self-review failed" in text
        assert "@loma-agent /rereview" in text

    @pytest.mark.asyncio
    async def test_disabled_followup_posts_once_per_registration(self):
        registered_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        base = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "slack", "channel": "C123", "thread_ts": "1.2"},
            "registered_at": registered_at,
        }
        fake_client = MagicMock()
        fake_client.chat_postMessage = AsyncMock()
        env = patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"})
        client_patch = patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client)

        # First time: delivered, and recorded as a disabled follow-up
        db, collection = _fake_db(existing_record=dict(base))
        with env, client_patch:
            assert await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False, disabled=True,
            ) is True
        assert "Self-review skipped" in fake_client.chat_postMessage.call_args.kwargs["text"]
        recorded = collection.update_one.call_args.args[1]["$set"]
        assert recorded["last_followup_disabled"] is True
        # The notice was claimed atomically for THIS registration first
        claim_filter, claim_update = collection.find_one_and_update.call_args.args
        assert claim_filter["disabled_notice_for"] == {"$ne": registered_at}
        assert claim_update["$set"]["disabled_notice_for"] == registered_at

        # Same registration, later synchronize: already told → no repeat post
        told = dict(base, last_followup_disabled=True,
                    last_followup_at=registered_at + timedelta(minutes=1))
        db, _ = _fake_db(existing_record=told)
        fake_client.chat_postMessage.reset_mock()
        with env, client_patch:
            assert await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False, disabled=True,
            ) is False
        fake_client.chat_postMessage.assert_not_awaited()

        # Re-registered after the last follow-up (new announcement) → post again
        retold = dict(told, registered_at=registered_at + timedelta(hours=1))
        db, _ = _fake_db(existing_record=retold)
        with env, client_patch:
            assert await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False, disabled=True,
            ) is True

    @pytest.mark.asyncio
    async def test_disabled_notice_is_claimed_atomically_across_concurrent_events(self):
        # `opened` and the first `synchronize` land within seconds and both
        # read the doc before either records a delivery. Only the event that
        # wins the atomic claim may post; a loser must not double-post.
        registered_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        record = {
            "repo_full_name": REPO, "pr_number": 42, "registered_at": registered_at,
            "target": {"type": "slack", "channel": "C123", "thread_ts": "1.2"},
        }
        fake_client = MagicMock()
        fake_client.chat_postMessage = AsyncMock()
        env = patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"})
        client_patch = patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client)

        db, collection = _fake_db(existing_record=dict(record))
        collection.find_one_and_update = AsyncMock(return_value=None)  # the other event won
        with env, client_patch:
            assert await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False, disabled=True,
            ) is False
        fake_client.chat_postMessage.assert_not_awaited()
        collection.update_one.assert_not_awaited()

        # Won the claim but Slack failed: hand the claim back so the next
        # event can retry instead of the notice being lost for good.
        db, collection = _fake_db(existing_record=dict(record))
        fake_client.chat_postMessage = AsyncMock(side_effect=RuntimeError("slack down"))
        with env, client_patch:
            assert await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=False, disabled=True,
            ) is False
        release_filter, release_update = collection.update_one.call_args.args
        assert release_filter["disabled_notice_for"] == registered_at
        assert release_update == {"$unset": {"disabled_notice_for": ""}}

    @pytest.mark.asyncio
    async def test_delivery_error_never_raises(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "slack", "channel": "C123", "thread_ts": "1.2"},
        }
        db, _ = _fake_db(existing_record=record)
        fake_client = MagicMock()
        fake_client.chat_postMessage = AsyncMock(side_effect=RuntimeError("slack down"))
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}), \
             patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client):
            delivered = await post_self_review_followup(
                db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
                verdict=None, succeeded=True,
            )
        assert delivered is False

    @pytest.mark.asyncio
    async def test_unknown_target_type_returns_false(self):
        record = {
            "repo_full_name": REPO, "pr_number": 42,
            "target": {"type": "pager"},
        }
        db, _ = _fake_db(existing_record=record)
        assert await post_self_review_followup(
            db, repo_full_name=REPO, pr_number=42, pr_url=PR_URL,
            verdict=None, succeeded=True,
        ) is False


class TestPipelineWiring:
    """Source-level assertions (same style as test_github_self_review.py)."""

    def setup_method(self):
        self.github_source = Path("webhooks/github.py").read_text()
        self.linear_source = Path("webhooks/linear.py").read_text()
        self.pr_util_source = Path("utils/github_pr.py").read_text()
        self.skill_source = Path("seed/skills/implement-ticket/SKILL.md").read_text()
        self.db_source = Path("observability/db.py").read_text()

    # Follow-up dispatch wiring (success / incomplete / failure) is covered
    # behaviourally by tests/test_github_self_review.py::TestSelfReviewPipeline.

    def test_linear_prompts_register_target_and_announce_pending_review(self):
        assert self.linear_source.count("tools/github_pr_notify.py register") == 2
        assert self.linear_source.count("--linear-issue-id {issue_id}") == 2
        # Registrations are pinned to the run's conversation so the CLI's
        # origin check can verify the issue is where this run came from.
        assert self.linear_source.count("{issue_id}{register_scope}") == 2
        assert self.linear_source.count("register_scope = f\" --conversation-id {") == 2
        assert "self-review" in self.linear_source.lower()

    def test_pr_util_has_no_dead_notify_target_parameter(self):
        # Registration happens through the CLI only; clone_and_run_claude has no
        # caller that could pass a target, so the parameter was removed.
        assert "notify_target" not in self.pr_util_source

    def test_seed_skill_has_two_stage_steps(self):
        assert "Step 6c: Register the Self-Review Follow-Up Target" in self.skill_source
        assert "tools/github_pr_notify.py register" in self.skill_source
        assert "self-review* of this PR is running" in self.skill_source
        # Dashboard registrations carry the requester's auth token via the
        # environment (out of the process list), never as a CLI flag.
        assert "LOMA_AUTH_TOKEN=<personal-auth-token> python3 tools/github_pr_notify.py register" in self.skill_source
        assert "--auth-token <personal-auth-token>" not in self.skill_source
        assert "--linear-issue-id <linear-issue-uuid> --conversation-id <conversation-id>" in self.skill_source
        # A Slack-started run is never told its channel/thread_ts (the Slack
        # ingress streams the reply itself), so the skill must not ask for
        # them: it registers with the conversation ID alone and the CLI
        # resolves the origin server-side.
        slack_block = self.skill_source.split("# Dashboard conversation")[0]
        assert "--conversation-id <conversation-id>" in slack_block
        assert "--slack-channel <channel-id>" not in self.skill_source
        # A failed registration must not leave a "verdict in this thread" promise
        assert 'drop the "verdict will be posted in this thread" line' in self.skill_source

    def test_review_prompt_requires_a_submitted_review(self):
        # The verdict lookup only ever sees SUBMITTED reviews. The prompt must
        # name the current review-writing tool (the old `create_pull_request_review`
        # no longer exists in the GitHub MCP tool set) and forbid leaving the
        # pending review from the create → add_comment → submit flow unsubmitted.
        assert "mcp__github__pull_request_review_write" in self.github_source
        assert '`method: \\"submit_pending\\"`' in self.github_source
        assert "You MUST end with the submitting call" in self.github_source
        assert "using `mcp__github__create_pull_request_review`:" not in self.github_source
        assert "mcp__github__add_reply_to_pull_request_comment" in self.github_source

    def test_conversation_id_is_injected_independently_of_user_email(self):
        # tools/github_pr_notify.py resolves a Slack run's origin from nothing
        # but its conversation ID, so the ID must reach the agent even when the
        # Slack ingress could not resolve the requester's email.
        source = Path("agent/client.py").read_text()
        assert 'if conversation_id:\n        text_parts.append(f"[Conversation ID: {conversation_id}]")' in source

    def test_notification_targets_have_unique_compound_index(self):
        assert "pr_notification_targets.create_index(" in self.db_source
        idx = self.db_source.index("pr_notification_targets.create_index(")
        assert 'unique=True' in self.db_source[idx: idx + 200]

    def test_self_review_locks_have_unique_compound_index(self):
        # Load-bearing: SelfReviewLock.acquire() relies on the unique index to
        # reject a second live holder.
        assert "pr_self_review_locks.create_index(" in self.db_source
        idx = self.db_source.index("pr_self_review_locks.create_index(")
        assert 'unique=True' in self.db_source[idx: idx + 200]


class TestSelfReviewFlag:
    def test_flag_uses_shared_env_flag_parser(self):
        import config.app_config as app_config

        for falsy in ("false", "0", "no", "off", "FALSE"):
            with patch.dict("os.environ", {"LOMA_ENABLE_SELF_REVIEW": falsy}):
                importlib.reload(app_config)
                assert app_config.LOMA_ENABLE_SELF_REVIEW is False, falsy
        with patch.dict("os.environ", {"LOMA_ENABLE_SELF_REVIEW": "true"}):
            importlib.reload(app_config)
            assert app_config.LOMA_ENABLE_SELF_REVIEW is True
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("LOMA_ENABLE_SELF_REVIEW", None)
            importlib.reload(app_config)
            assert app_config.LOMA_ENABLE_SELF_REVIEW is True  # default on

    def test_webhook_module_reads_flag_from_app_config(self):
        source = Path("webhooks/github.py").read_text()
        assert "from config.app_config import LOMA_ENABLE_SELF_REVIEW" in source
        assert "SELF_REVIEW_ENABLED = LOMA_ENABLE_SELF_REVIEW" in source
        assert 'os.environ.get("LOMA_ENABLE_SELF_REVIEW"' not in source

    def test_env_example_documents_flag(self):
        env_example = Path(".env.example").read_text()
        assert "LOMA_ENABLE_SELF_REVIEW=" in env_example
        # Agent PRs are detected by author login on `opened`; a deploy that
        # never set this skips every agent PR's self-review until the next push.
        assert "AGENT_GITHUB_LOGIN=" in env_example


def _load_notify_cli():
    spec = importlib.util.spec_from_file_location(
        "github_pr_notify", Path("tools/github_pr_notify.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNotifyCliAuth:
    def _args(self, **overrides):
        base = dict(slack_channel=None, thread_ts=None, linear_issue_id=None,
                    user_email=None, auth_token=None, conversation_id=None)
        base.update(overrides)
        return Namespace(**base)

    def test_loma_target_requires_auth_token(self):
        cli = _load_notify_cli()
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop(cli.AUTH_TOKEN_ENV, None)
            with pytest.raises(ValueError, match="LOMA_AUTH_TOKEN.*--auth-token"):
                cli._build_target(self._args(user_email="a@b.co"))

    def test_loma_target_reads_token_from_the_environment_first(self):
        # Env var keeps the token out of `ps`; it wins over a --auth-token flag.
        cli = _load_notify_cli()
        with patch.dict("os.environ", {cli.AUTH_TOKEN_ENV: "tok-env"}), \
             patch.object(cli, "_verify_auth", return_value=True) as verify:
            target = cli._build_target(self._args(user_email="a@b.co", auth_token="tok-flag"))
        verify.assert_called_once_with("tok-env", "a@b.co")
        assert target == {"type": "loma", "user_email": "a@b.co"}

    def test_loma_target_rejects_bad_token(self):
        cli = _load_notify_cli()
        with patch.object(cli, "_verify_auth", return_value=False):
            with pytest.raises(ValueError, match="Authentication failed"):
                cli._build_target(self._args(user_email="a@b.co", auth_token="nope"))

    def test_loma_target_accepts_verified_token(self):
        cli = _load_notify_cli()
        with patch.object(cli, "_verify_auth", return_value=True) as verify:
            target = cli._build_target(
                self._args(user_email="a@b.co", auth_token="tok", conversation_id="c1")
            )
        verify.assert_called_once_with("tok", "a@b.co")
        assert target == {"type": "loma", "user_email": "a@b.co", "conversation_id": "c1"}

    def test_slack_and_linear_targets_do_not_need_token(self):
        cli = _load_notify_cli()
        assert cli._build_target(self._args(slack_channel="C1", thread_ts="1.2"))["type"] == "slack"
        assert cli._build_target(self._args(linear_issue_id="u1"))["type"] == "linear"

    def test_conversation_id_alone_defers_to_origin_resolution(self):
        cli = _load_notify_cli()
        assert cli._build_target(self._args(conversation_id="c1")) is None
        with pytest.raises(ValueError, match="--conversation-id alone"):
            cli._build_target(self._args())


class TestNotifyCliOriginCheck:
    """Slack/Linear targets must be the verified origin of a Loma conversation
    (stamped server-side by the Slack ingress / Linear webhook), so a
    prompt-injected run cannot route the verdict into an arbitrary place."""

    def _db(self, found):
        db = MagicMock()
        db.conversations.find_one = AsyncMock(return_value=found)
        return db

    @pytest.mark.asyncio
    async def test_slack_target_must_match_a_conversation_origin(self):
        cli = _load_notify_cli()
        target = {"type": "slack", "channel": "C1", "thread_ts": "1.2"}
        db = self._db(None)
        with pytest.raises(ValueError, match="not the origin"):
            await cli._verify_target_origin(db, target)
        assert db.conversations.find_one.call_args.args[0] == {
            "metadata.slack_channel_id": "C1", "metadata.slack_thread_ts": "1.2",
        }
        db = self._db({"conversation_id": "c1"})
        await cli._verify_target_origin(db, target, "c1")  # pinned to one conversation
        assert db.conversations.find_one.call_args.args[0]["conversation_id"] == "c1"

    @pytest.mark.asyncio
    async def test_linear_target_must_match_a_conversation_origin(self):
        cli = _load_notify_cli()
        target = {"type": "linear", "issue_id": "uuid-1"}
        db = self._db(None)
        with pytest.raises(ValueError, match="Linear issue uuid-1 is not the origin"):
            await cli._verify_target_origin(db, target, "c9")
        assert db.conversations.find_one.call_args.args[0] == {
            "metadata.linear_issue_id": "uuid-1", "conversation_id": "c9",
        }
        await cli._verify_target_origin(self._db({"conversation_id": "c9"}), target, "c9")

    @pytest.mark.asyncio
    async def test_conversation_only_registration_resolves_the_stamped_origin(self):
        # A Slack-started run is never told its channel/thread_ts: the target
        # comes from what the Slack ingress stamped on the conversation, so the
        # target IS the origin and no separate origin check is needed.
        cli = _load_notify_cli()
        db = self._db({"conversation_id": "c1", "metadata": {
            "source": "slack", "slack_channel_id": "C1", "slack_thread_ts": "1.2"}})
        target = await cli._resolve_target_from_conversation(db, "c1")
        assert target == {"type": "slack", "channel": "C1", "thread_ts": "1.2", "conversation_id": "c1"}
        assert db.conversations.find_one.call_args.args[0] == {"conversation_id": "c1"}

        db = self._db({"conversation_id": "c2", "metadata": {"linear_issue_id": "uuid-1"}})
        target = await cli._resolve_target_from_conversation(db, "c2")
        assert target == {"type": "linear", "issue_id": "uuid-1", "conversation_id": "c2"}

    @pytest.mark.asyncio
    async def test_conversation_only_registration_refuses_unknown_or_dashboard_runs(self):
        cli = _load_notify_cli()
        with pytest.raises(ValueError, match="not found"):
            await cli._resolve_target_from_conversation(self._db(None), "nope")
        dashboard = self._db({"conversation_id": "c3", "metadata": {"source": "dashboard"}})
        with pytest.raises(ValueError, match="--user-email"):
            await cli._resolve_target_from_conversation(dashboard, "c3")

    @pytest.mark.asyncio
    async def test_register_with_conversation_id_only_writes_the_resolved_origin(self):
        cli = _load_notify_cli()
        db = self._db({"conversation_id": "c1", "metadata": {
            "slack_channel_id": "C1", "slack_thread_ts": "1.2"}})
        client = MagicMock()
        args = Namespace(repo="o/r", pr=7, slack_channel=None, thread_ts=None,
                         linear_issue_id=None, user_email=None, auth_token=None,
                         conversation_id="c1")
        with patch.object(cli, "_get_db", return_value=(client, db)), \
             patch.object(cli, "register_pr_notification_target", new_callable=AsyncMock,
                          return_value={"disabled_notice": None}) as reg:
            assert await cli._cmd_register(args) == 0
        assert reg.call_args.args[1:] == (
            "o/r", 7, {"type": "slack", "channel": "C1", "thread_ts": "1.2", "conversation_id": "c1"},
        )
        client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_loma_target_is_hmac_gated_not_origin_checked(self):
        cli = _load_notify_cli()
        db = self._db(None)
        await cli._verify_target_origin(db, {"type": "loma", "user_email": "a@b.co"})
        db.conversations.find_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_register_checks_origin_before_writing(self):
        cli = _load_notify_cli()
        db = self._db(None)
        client = MagicMock()
        args = Namespace(repo="o/r", pr=7, slack_channel="C1", thread_ts="1.2",
                         linear_issue_id=None, user_email=None, auth_token=None,
                         conversation_id=None)
        with patch.object(cli, "_get_db", return_value=(client, db)), \
             patch.object(cli, "register_pr_notification_target", new_callable=AsyncMock) as reg:
            with pytest.raises(ValueError, match="not the origin"):
                await cli._cmd_register(args)
        reg.assert_not_awaited()
        client.close.assert_called_once()

        db = self._db({"conversation_id": "c1"})
        with patch.object(cli, "_get_db", return_value=(client, db)), \
             patch.object(cli, "register_pr_notification_target", new_callable=AsyncMock,
                          return_value={"disabled_notice": None}) as reg:
            assert await cli._cmd_register(args) == 0
        reg.assert_awaited_once()
        assert reg.call_args.args[1:] == ("o/r", 7, {"type": "slack", "channel": "C1", "thread_ts": "1.2"})

    @pytest.mark.asyncio
    async def test_register_surfaces_a_failed_disabled_notice(self, capsys):
        # Single-push PR on a disabled deploy: the notice is delivered from the
        # CLI process at registration time. If that fails there is no later
        # webhook event to retry, so `registered: true` alone must not be the
        # whole story — the agent has to hear that the target was NOT told.
        cli = _load_notify_cli()
        client = MagicMock()
        args = Namespace(repo="o/r", pr=7, slack_channel="C1", thread_ts="1.2",
                         linear_issue_id=None, user_email=None, auth_token=None,
                         conversation_id=None)
        db = self._db({"conversation_id": "c1"})
        with patch.object(cli, "_get_db", return_value=(client, db)), \
             patch.object(cli, "register_pr_notification_target", new_callable=AsyncMock,
                          return_value={"disabled_notice": "failed"}):
            assert await cli._cmd_register(args) == 0
        out, err = capsys.readouterr()
        assert json.loads(out.strip().splitlines()[-1])["disabled_notice"] == "failed"
        assert "could not be delivered" in err
