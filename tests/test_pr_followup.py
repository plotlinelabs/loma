"""Tests for the two-stage self-review notification (utils/pr_followup.py).

Stage 1: PR-creating flows register where the PR was announced.
Stage 2: the self-review pipeline threads the verdict back to that target.
"""

import importlib
import importlib.util
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.pr_followup import (
    COLLECTION,
    REREVIEW_COMMAND,
    VERDICT_MAX_CHARS,
    _build_messages,
    extract_self_review_verdict,
    get_pr_notification_target,
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

    def test_success_with_verdict(self):
        title, body, slack = _build_messages(42, PR_URL, "✅ Self-review: ok", True)
        assert "complete" in title.lower()
        assert "✅ Self-review: ok" in body and "✅ Self-review: ok" in slack

    def test_disabled_outcome(self):
        title, body, slack = _build_messages(42, PR_URL, None, False, disabled=True)
        assert "skipped" in title.lower()
        assert "LOMA_ENABLE_SELF_REVIEW" in body
        assert "disabled" in slack and "unreviewed" in slack


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
        assert "self-review" in self.linear_source.lower()

    def test_pr_util_has_no_dead_notify_target_parameter(self):
        # Registration happens through the CLI only; clone_and_run_claude has no
        # caller that could pass a target, so the parameter was removed.
        assert "notify_target" not in self.pr_util_source

    def test_seed_skill_has_two_stage_steps(self):
        assert "Step 6c: Register the Self-Review Follow-Up Target" in self.skill_source
        assert "tools/github_pr_notify.py register" in self.skill_source
        assert "self-review* of this PR is running" in self.skill_source
        # Dashboard registrations must carry the requester's auth token
        assert "--user-email <requester-email> --auth-token <personal-auth-token>" in self.skill_source

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
        assert "LOMA_ENABLE_SELF_REVIEW=" in Path(".env.example").read_text()


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
        with pytest.raises(ValueError, match="--auth-token"):
            cli._build_target(self._args(user_email="a@b.co"))

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
