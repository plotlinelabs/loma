"""Tests for the two-stage self-review notification (utils/pr_followup.py).

Stage 1: PR-creating flows register where the PR was announced.
Stage 2: the self-review pipeline threads the verdict back to that target.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.pr_followup import (
    COLLECTION,
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
        assert "/rereview" in text

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

    def test_review_pipeline_dispatches_followup_on_self_review(self):
        assert "if self_review:" in self.github_source
        assert "post_self_review_followup(" in self.github_source
        assert "extract_self_review_verdict(reviews, AGENT_GITHUB_LOGIN)" in self.github_source

    def test_followup_runs_on_success_and_failure(self):
        # The dispatch must sit in the finally-block region and pass the
        # review outcome through, so failures are visible, not silent.
        assert "succeeded=review_succeeded" in self.github_source

    def test_linear_prompts_register_target_and_announce_pending_review(self):
        assert self.linear_source.count("tools/github_pr_notify.py register") == 2
        assert self.linear_source.count("--linear-issue-id {issue_id}") == 2
        assert "self-review" in self.linear_source.lower()

    def test_pr_util_accepts_notify_target(self):
        assert "notify_target: dict | None = None" in self.pr_util_source
        assert "register_pr_notification_target(" in self.pr_util_source

    def test_seed_skill_has_two_stage_steps(self):
        assert "Step 6c: Register the Self-Review Follow-Up Target" in self.skill_source
        assert "tools/github_pr_notify.py register" in self.skill_source
        assert "self-review* of this PR is running" in self.skill_source
