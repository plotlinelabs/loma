"""Tests for fresh-context self-review of agent-authored PRs (webhooks/github.py).

Agent-created draft PRs (implement-ticket conversations, Linear webhook flows,
utils/github_pr.py) must be routed into a clean-context self-review instead of
being skipped, while other bots' PRs and human draft PRs keep the old behavior.
"""

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webhooks.github import AGENT_GITHUB_LOGIN, handle_github_webhook

SECRET = "test-github-secret"


def _signed(body: dict) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, sig


def _pr_event(author: str, draft: bool = True, action: str = "opened") -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": 42,
            "title": "feat: something",
            "html_url": "https://github.com/example-org/example-repo/pull/42",
            "draft": draft,
            "user": {"login": author},
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
             patch("webhooks.github.ingest_github_event", new_callable=AsyncMock), \
             patch("webhooks.github._process_pr_review", new_callable=AsyncMock) as review_mock:
            response = await handle_github_webhook(request)
        body = json.loads(response.body)
        assert body == {"status": "ignored", "reason": "self_review_disabled"}
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
