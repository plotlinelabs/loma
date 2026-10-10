"""Tests for the PR failure-fallback comment (webhooks/github_run_failure.py).

Covers:
- extract_pull_request_target: GitHub pull_request payload detection
- build_failure_comment: marker, reason handling, empty-reason default
- post_run_failure_comment: create vs edit-in-place, best-effort semantics
- execute_webhook_flow integration: fallback fires on start/run failures for
  GitHub pull_request payloads only, and the original error is still recorded
  even when posting to GitHub fails
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webhooks.github_run_failure import (
    DEFAULT_FAILURE_REASON,
    REVIEW_STATUS_MARKER,
    build_failure_comment,
    extract_pull_request_target,
    post_run_failure_comment,
)


REPO = "plotlinehq/plotline-services"


def _pr_payload(number=42, repo=REPO):
    """A minimal GitHub pull_request webhook payload."""
    return {
        "action": "opened",
        "pull_request": {"number": number, "title": "Fix things"},
        "repository": {"full_name": repo},
    }


# ---------------------------------------------------------------------------
# extract_pull_request_target
# ---------------------------------------------------------------------------


class TestExtractPullRequestTarget:
    def test_valid_pull_request_payload(self):
        assert extract_pull_request_target(_pr_payload()) == (REPO, 42)

    def test_non_dict_payload(self):
        assert extract_pull_request_target("not a dict") is None
        assert extract_pull_request_target(None) is None
        assert extract_pull_request_target([1, 2]) is None

    def test_missing_pull_request(self):
        assert extract_pull_request_target({"repository": {"full_name": REPO}}) is None

    def test_pull_request_not_a_dict(self):
        payload = {"pull_request": "yes", "repository": {"full_name": REPO}}
        assert extract_pull_request_target(payload) is None

    def test_missing_number(self):
        payload = {"pull_request": {}, "repository": {"full_name": REPO}}
        assert extract_pull_request_target(payload) is None

    def test_non_integer_number(self):
        payload = {"pull_request": {"number": "42"}, "repository": {"full_name": REPO}}
        assert extract_pull_request_target(payload) is None

    def test_missing_repository_full_name(self):
        assert extract_pull_request_target({"pull_request": {"number": 42}}) is None
        payload = {"pull_request": {"number": 42}, "repository": {}}
        assert extract_pull_request_target(payload) is None

    def test_pylon_payload_ignored(self):
        assert extract_pull_request_target({"data": {"issue_id": "py-1"}}) is None

    def test_linear_payload_ignored(self):
        payload = {"action": "create", "data": {"id": "lin-1"}, "type": "Issue"}
        assert extract_pull_request_target(payload) is None


# ---------------------------------------------------------------------------
# build_failure_comment
# ---------------------------------------------------------------------------


class TestBuildFailureComment:
    def test_marker_at_top(self):
        body = build_failure_comment("boom")
        assert body.startswith(REVIEW_STATUS_MARKER)

    def test_reason_included(self):
        body = build_failure_comment("OAuth session expired and could not be refreshed")
        assert "**Automated review: failed to start**" in body
        assert "OAuth session expired and could not be refreshed" in body
        assert "re-add the `review` label to retry" in body

    def test_empty_reason_uses_default(self):
        body = build_failure_comment("")
        assert DEFAULT_FAILURE_REASON in body

    def test_none_reason_uses_default(self):
        body = build_failure_comment(None)
        assert DEFAULT_FAILURE_REASON in body

    def test_bare_timeout_error_uses_default(self):
        import asyncio

        body = build_failure_comment(asyncio.TimeoutError())
        assert DEFAULT_FAILURE_REASON in body

    def test_exception_reason_stringified(self):
        body = build_failure_comment(RuntimeError("Pool is closed"))
        assert "Pool is closed" in body

    def test_multiline_reason_collapsed(self):
        body = build_failure_comment("line one\nline two\n\n  spaced")
        assert "line one line two spaced" in body

    def test_long_reason_truncated(self):
        body = build_failure_comment("x" * 1000)
        assert "x" * 300 + "..." in body
        assert "x" * 301 not in body


# ---------------------------------------------------------------------------
# post_run_failure_comment
# ---------------------------------------------------------------------------


def _request_mock(existing_comments=None, create_status=201, update_status=200,
                  list_status=200):
    """Mock _github_request: GET lists comments, POST creates, PATCH updates."""
    calls = []

    async def fake_request(method, url, json_body=None):
        calls.append((method, url, json_body))
        if method == "GET":
            return list_status, existing_comments or []
        if method == "POST":
            return create_status, {"id": 999}
        if method == "PATCH":
            return update_status, {}
        raise AssertionError(f"unexpected method {method}")

    return fake_request, calls


class TestPostRunFailureComment:
    @pytest.mark.asyncio
    async def test_creates_comment_with_marker(self):
        fake_request, calls = _request_mock(existing_comments=[
            {"id": 1, "body": "unrelated comment"},
        ])
        with patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request", side_effect=fake_request):
            ok = await post_run_failure_comment(_pr_payload(), "pool exhausted")

        assert ok is True
        methods = [c[0] for c in calls]
        assert methods == ["GET", "POST"]
        method, url, body = calls[1]
        assert url.endswith(f"/repos/{REPO}/issues/42/comments")
        assert REVIEW_STATUS_MARKER in body["body"]
        assert "pool exhausted" in body["body"]

    @pytest.mark.asyncio
    async def test_edits_existing_marker_comment(self):
        fake_request, calls = _request_mock(existing_comments=[
            {"id": 7, "body": "hello"},
            {"id": 8, "body": f"{REVIEW_STATUS_MARKER}\nReviewing..."},
        ])
        with patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request", side_effect=fake_request):
            ok = await post_run_failure_comment(_pr_payload(), "boom")

        assert ok is True
        methods = [c[0] for c in calls]
        assert methods == ["GET", "PATCH"]  # edited in place, no new comment
        method, url, body = calls[1]
        assert url.endswith("/issues/comments/8")
        assert REVIEW_STATUS_MARKER in body["body"]

    @pytest.mark.asyncio
    async def test_non_github_payload_makes_no_github_call(self):
        fake_request, calls = _request_mock()
        with patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request", side_effect=fake_request):
            ok = await post_run_failure_comment({"data": {"issue_id": "pylon-1"}}, "boom")

        assert ok is False
        assert calls == []

    @pytest.mark.asyncio
    async def test_no_api_key_makes_no_call(self):
        fake_request, calls = _request_mock()
        with patch("webhooks.github_run_failure.GITHUB_API_KEY", ""), \
             patch("webhooks.github_run_failure._github_request", side_effect=fake_request):
            ok = await post_run_failure_comment(_pr_payload(), "boom")

        assert ok is False
        assert calls == []

    @pytest.mark.asyncio
    async def test_github_error_swallowed(self):
        """A GitHub outage must never raise out of the fallback."""
        with patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request",
                   side_effect=ConnectionError("github down")):
            ok = await post_run_failure_comment(_pr_payload(), "boom")

        assert ok is False

    @pytest.mark.asyncio
    async def test_list_failure_falls_back_to_create(self):
        fake_request, calls = _request_mock(list_status=500)
        with patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request", side_effect=fake_request):
            ok = await post_run_failure_comment(_pr_payload(), "boom")

        assert ok is True
        assert [c[0] for c in calls] == ["GET", "POST"]

    @pytest.mark.asyncio
    async def test_empty_error_uses_default_reason(self):
        fake_request, calls = _request_mock()
        with patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request", side_effect=fake_request):
            ok = await post_run_failure_comment(_pr_payload(), "")

        assert ok is True
        assert DEFAULT_FAILURE_REASON in calls[1][2]["body"]


# ---------------------------------------------------------------------------
# execute_webhook_flow integration — fallback wiring
# ---------------------------------------------------------------------------


def _make_flow(flow_id="flow-1", name="PR Review Flow", status="active"):
    return {
        "flow_id": flow_id,
        "name": name,
        "status": status,
        "prompt_template": "Review: {{payload}}",
        "visibility": "shared",
        "created_by": {},
        "run_as": "owner@example.com",
    }


def _make_db_mock(flow=None, convo_after_run=None):
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value={"status": "active"})
    db.flows.find_one = AsyncMock(return_value=flow or _make_flow())
    db.flows.update_one = AsyncMock()
    db.conversations.find_one = AsyncMock(return_value=convo_after_run)
    db.conversations.insert_one = AsyncMock()
    db.conversations.update_one = AsyncMock()
    db.webhook_logs.update_one = AsyncMock()
    return db


def _make_observer_mock():
    observer = MagicMock()
    observer.conversation_id = "convo-1"
    observer.start = AsyncMock()
    observer.resume = AsyncMock()
    observer.record_error = AsyncMock()
    return observer


async def _empty_async_gen():
    return
    yield  # noqa: unreachable — makes this a generator


def _raising_async_gen(exc):
    async def gen():
        raise exc
        yield  # noqa: unreachable

    return gen()


def _run_flow(flow, payload, db, log_id="log-1"):
    from scheduler.webhook_executor import execute_webhook_flow

    return execute_webhook_flow(flow, json.dumps(payload).encode(), {}, log_id)


def _webhook_log_statuses(db):
    return [
        call.args[1]["$set"].get("execution_status")
        for call in db.webhook_logs.update_one.call_args_list
    ]


class TestExecuteWebhookFlowFallback:
    @pytest.mark.asyncio
    async def test_start_failure_posts_fallback(self):
        """require_execution_account failure → fallback comment for PR payloads."""
        db = _make_db_mock()
        flow = _make_flow()

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.require_execution_account",
                   AsyncMock(side_effect=ValueError("Execution blocked: account inactive"))), \
             patch("scheduler.webhook_executor.post_run_failure_comment",
                   AsyncMock(return_value=True)) as mock_post:
            result = await _run_flow(flow, _pr_payload(), db)

        assert result is None
        mock_post.assert_awaited_once()
        payload_arg, error_arg = mock_post.await_args.args
        assert payload_arg["pull_request"]["number"] == 42
        assert "Execution blocked" in str(error_arg)
        # Original failure still recorded on the webhook log
        assert "failed" in _webhook_log_statuses(db)

    @pytest.mark.asyncio
    async def test_run_failure_posts_fallback(self):
        """Unhandled exception during the run → fallback comment."""
        db = _make_db_mock()
        flow = _make_flow()
        observer = _make_observer_mock()

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent",
                   return_value=_raising_async_gen(RuntimeError("Pool is closed"))), \
             patch("scheduler.webhook_executor.ConversationObserver", return_value=observer), \
             patch("scheduler.webhook_executor.post_run_failure_comment",
                   AsyncMock(return_value=True)) as mock_post:
            await _run_flow(flow, _pr_payload(), db)

        mock_post.assert_awaited_once()
        _, error_arg = mock_post.await_args.args
        assert "Pool is closed" in str(error_arg)
        assert "error" in _webhook_log_statuses(db)

    @pytest.mark.asyncio
    async def test_github_post_failure_does_not_mask_original_error(self):
        """GitHub being down must not prevent recording the run failure."""
        db = _make_db_mock()
        flow = _make_flow()
        observer = _make_observer_mock()

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent",
                   return_value=_raising_async_gen(RuntimeError("agent crashed"))), \
             patch("scheduler.webhook_executor.ConversationObserver", return_value=observer), \
             patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request",
                   side_effect=ConnectionError("github down")):
            # Must not raise even though the GitHub call fails
            await _run_flow(flow, _pr_payload(), db)

        observer.record_error.assert_awaited_once()
        assert "agent crashed" in observer.record_error.await_args.args[0]
        assert "error" in _webhook_log_statuses(db)
        error_updates = [
            call.args[1]["$set"] for call in db.webhook_logs.update_one.call_args_list
            if call.args[1]["$set"].get("execution_status") == "error"
        ]
        assert "agent crashed" in error_updates[0]["error"]

    @pytest.mark.asyncio
    async def test_non_github_payload_makes_no_github_call(self):
        """Linear/Pylon-style payloads never touch the GitHub API on failure."""
        db = _make_db_mock()
        flow = _make_flow()
        observer = _make_observer_mock()
        request_mock = AsyncMock()

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent",
                   return_value=_raising_async_gen(RuntimeError("boom"))), \
             patch("scheduler.webhook_executor.ConversationObserver", return_value=observer), \
             patch("webhooks.github_run_failure.GITHUB_API_KEY", "tok"), \
             patch("webhooks.github_run_failure._github_request", request_mock):
            await _run_flow(flow, {"event": "linear-thing", "data": {"x": 1}}, db)

        request_mock.assert_not_called()
        assert "error" in _webhook_log_statuses(db)

    @pytest.mark.asyncio
    async def test_swallowed_startup_failure_posts_fallback(self):
        """Pool-acquire failures don't raise — stream_agent records the error on
        the conversation and ends the stream. The runner must still surface it."""
        errored_convo = {
            "status": "error",
            "error": "Client initialization failed: OAuth session expired",
        }
        db = _make_db_mock(convo_after_run=errored_convo)
        flow = _make_flow()
        observer = _make_observer_mock()

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent",
                   return_value=_empty_async_gen()), \
             patch("scheduler.webhook_executor.ConversationObserver", return_value=observer), \
             patch("scheduler.webhook_executor.ingest_dashboard_chat", AsyncMock()), \
             patch("scheduler.webhook_executor.post_run_failure_comment",
                   AsyncMock(return_value=True)) as mock_post:
            await _run_flow(flow, _pr_payload(), db)

        mock_post.assert_awaited_once()
        _, error_arg = mock_post.await_args.args
        assert "OAuth session expired" in str(error_arg)

    @pytest.mark.asyncio
    async def test_successful_run_posts_nothing(self):
        """Normal completions (including silent-exit decisions) post no comment."""
        db = _make_db_mock(convo_after_run={"status": "completed", "error": None})
        flow = _make_flow()
        observer = _make_observer_mock()

        with patch("scheduler.webhook_executor.get_db", return_value=db), \
             patch("scheduler.webhook_executor.stream_agent",
                   return_value=_empty_async_gen()), \
             patch("scheduler.webhook_executor.ConversationObserver", return_value=observer), \
             patch("scheduler.webhook_executor.ingest_dashboard_chat", AsyncMock()), \
             patch("scheduler.webhook_executor.post_run_failure_comment",
                   AsyncMock(return_value=True)) as mock_post:
            await _run_flow(flow, _pr_payload(), db)

        mock_post.assert_not_awaited()
        assert "completed" in _webhook_log_statuses(db)

    @pytest.mark.asyncio
    async def test_dispatch_wrapper_posts_and_reraises(self):
        """Failures before execute_webhook_flow's own handling still post,
        and the original exception is re-raised unchanged."""
        from scheduler.webhook_executor import _execute_with_failure_fallback

        flow = _make_flow()
        raw_body = json.dumps(_pr_payload()).encode()

        with patch("scheduler.webhook_executor.execute_webhook_flow",
                   AsyncMock(side_effect=RuntimeError("observer setup died"))), \
             patch("scheduler.webhook_executor.post_run_failure_comment",
                   AsyncMock(return_value=True)) as mock_post:
            with pytest.raises(RuntimeError, match="observer setup died"):
                await _execute_with_failure_fallback(flow, raw_body, {}, "log-x")

        mock_post.assert_awaited_once()
        payload_arg, error_arg = mock_post.await_args.args
        assert payload_arg["pull_request"]["number"] == 42
