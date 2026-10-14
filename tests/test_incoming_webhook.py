"""Tests for webhooks/incoming.py — flow webhook auth verification.

Covers verify_webhook_auth for hmac_sha256 (bare hex and GitHub's
"sha256="-prefixed header format), bearer_token, and none, and
passes_event_filter against the PR-review flow filter.
"""

import hashlib
import hmac

from aiohttp.test_utils import make_mocked_request

import pytest

from webhooks.incoming import passes_event_filter, verify_webhook_auth

SECRET = "test-webhook-secret"
BODY = b'{"action": "ready_for_review"}'
HEX_SIG = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()

HMAC_CONFIG = {
    "auth_method": "hmac_sha256",
    "auth_secret": SECRET,
    "signature_header": "X-Hub-Signature-256",
}


def _req(headers: dict):
    return make_mocked_request("POST", "/webhook?flowId=x", headers=headers)


class TestHmacSha256:
    def test_bare_hex_signature_accepted(self):
        req = _req({"X-Hub-Signature-256": HEX_SIG})
        assert verify_webhook_auth(HMAC_CONFIG, req, BODY) is True

    def test_github_sha256_prefixed_signature_accepted(self):
        req = _req({"X-Hub-Signature-256": f"sha256={HEX_SIG}"})
        assert verify_webhook_auth(HMAC_CONFIG, req, BODY) is True

    def test_wrong_signature_rejected(self):
        req = _req({"X-Hub-Signature-256": "sha256=" + "0" * 64})
        assert verify_webhook_auth(HMAC_CONFIG, req, BODY) is False

    def test_tampered_body_rejected(self):
        req = _req({"X-Hub-Signature-256": f"sha256={HEX_SIG}"})
        assert verify_webhook_auth(HMAC_CONFIG, req, b'{"action": "tampered"}') is False

    def test_non_hex_signature_rejected(self):
        req = _req({"X-Hub-Signature-256": "sha256=not-hex!"})
        assert verify_webhook_auth(HMAC_CONFIG, req, BODY) is False

    def test_missing_header_rejected(self):
        req = _req({})
        assert verify_webhook_auth(HMAC_CONFIG, req, BODY) is False

    def test_prefix_only_rejected(self):
        req = _req({"X-Hub-Signature-256": "sha256="})
        assert verify_webhook_auth(HMAC_CONFIG, req, BODY) is False

    def test_missing_secret_rejected(self):
        config = dict(HMAC_CONFIG, auth_secret="")
        req = _req({"X-Hub-Signature-256": f"sha256={HEX_SIG}"})
        assert verify_webhook_auth(config, req, BODY) is False


class TestOtherMethods:
    def test_bearer_token_accepted(self):
        config = {"auth_method": "bearer_token", "auth_secret": SECRET}
        req = _req({"Authorization": f"Bearer {SECRET}"})
        assert verify_webhook_auth(config, req, BODY) is True

    def test_bearer_token_wrong_rejected(self):
        config = {"auth_method": "bearer_token", "auth_secret": SECRET}
        req = _req({"Authorization": "Bearer nope"})
        assert verify_webhook_auth(config, req, BODY) is False

    def test_none_method_accepted(self):
        assert verify_webhook_auth({"auth_method": "none"}, _req({}), BODY) is True

    def test_unknown_method_rejected(self):
        assert verify_webhook_auth({"auth_method": "wat"}, _req({}), BODY) is False


PR_REVIEW_FILTER = {"accept_any": [
    [
        {"path": "headers.X-GitHub-Event", "eq": "pull_request"},
        {"path": "action", "in": ["ready_for_review", "reopened", "synchronize"]},
        {"path": "pull_request.draft", "eq": False},
        {"path": "pull_request.state", "eq": "open"},
        {"path": "sender.type", "ne": "Bot"},
        {"path": "sender.login", "ne": "review-bot"},
    ],
    [
        {"path": "headers.X-GitHub-Event", "eq": "pull_request"},
        {"path": "action", "eq": "labeled"},
        {"path": "label.name", "eq": "review"},
        {"path": "pull_request.state", "eq": "open"},
        {"path": "sender.type", "ne": "Bot"},
        {"path": "sender.login", "ne": "review-bot"},
    ],
]}
GH = {"x-github-event": "pull_request"}


def _pr_event(action, draft=False, label=None, sender="octocat", sender_type="User", state="open"):
    event = {
        "action": action,
        "pull_request": {"draft": draft, "state": state},
        "sender": {"login": sender, "type": sender_type},
    }
    if label:
        event["label"] = {"name": label}
    return event


class TestEventFilter:
    @pytest.mark.parametrize("event", [
        _pr_event("labeled", label="review"),
        _pr_event("labeled", label="review", draft=True),
        _pr_event("synchronize"),
        _pr_event("ready_for_review"),
        _pr_event("reopened"),
    ])
    def test_review_events_run(self, event):
        assert passes_event_filter(PR_REVIEW_FILTER, event, GH) is True

    @pytest.mark.parametrize("event", [
        _pr_event("opened"),
        _pr_event("edited"),
        _pr_event("closed", state="closed"),
        _pr_event("assigned"),
        _pr_event("unlabeled", label="review"),
        _pr_event("review_requested"),
        _pr_event("labeled", label="preview"),
        _pr_event("synchronize", draft=True),
        _pr_event("labeled", label="review", state="closed"),
        _pr_event("synchronize", sender="dependabot[bot]", sender_type="Bot"),
        _pr_event("labeled", label="review", sender="review-bot"),
    ])
    def test_noop_events_filtered(self, event):
        assert passes_event_filter(PR_REVIEW_FILTER, event, GH) is False

    def test_other_github_event_filtered(self):
        assert passes_event_filter(PR_REVIEW_FILTER, {"zen": "hi"}, {"X-GitHub-Event": "ping"}) is False

    def test_header_match_is_case_insensitive(self):
        assert passes_event_filter(PR_REVIEW_FILTER, _pr_event("synchronize"), {"X-GITHUB-EVENT": "pull_request"}) is True

    @pytest.mark.parametrize("event_filter", [None, {}, {"accept_any": []}])
    def test_no_filter_accepts_everything(self, event_filter):
        assert passes_event_filter(event_filter, _pr_event("opened"), GH) is True

    @pytest.mark.parametrize("event_filter", [
        {"accept_any": [[{"path": "action", "regex": "x"}]]},
        {"accept_any": [[{"eq": "opened"}]]},
        {"accept_any": "not-a-list-of-rules"},
    ])
    def test_malformed_filter_accepts(self, event_filter):
        assert passes_event_filter(event_filter, _pr_event("opened"), GH) is True

    def test_non_dict_payload_is_filtered_not_crashed(self):
        assert passes_event_filter(PR_REVIEW_FILTER, ["not", "a", "dict"], GH) is False
