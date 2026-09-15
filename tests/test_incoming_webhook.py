"""Tests for webhooks/incoming.py — flow webhook auth verification.

Covers verify_webhook_auth for hmac_sha256 (bare hex and GitHub's
"sha256="-prefixed header format), bearer_token, and none.
"""

import hashlib
import hmac

from aiohttp.test_utils import make_mocked_request

from webhooks.incoming import verify_webhook_auth

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
