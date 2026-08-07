"""Tests for tools/ashby.py — focused on the compensation-leak guardrails.

These tests run fully offline (no Ashby API calls).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ashby import (  # noqa: E402
    ENDPOINT_ALLOWLIST,
    EXPAND_ALLOWLIST,
    REDACTED,
    AshbyToolError,
    _post,
    _sanitize_expand,
    redact_sensitive,
)


# ---------------------------------------------------------------------------
# Endpoint allowlist — offer/write endpoints must be locally blocked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "endpoint",
    [
        "offer.list",
        "offer.info",
        "offer.create",
        "offer.start",
        "apiKey.info",
        "application.changeStage",
        "application.update",
        "candidate.update",
        "candidate.create",
        "job.create",
        "job.update",
        "job.setStatus",
    ],
)
def test_blocked_endpoints_raise_locally(endpoint):
    """Blocked endpoints raise before any network request is made."""
    with pytest.raises(AshbyToolError, match="not allowed"):
        asyncio.run(_post(endpoint, {}))


def test_no_offer_or_write_endpoints_in_allowlist():
    for endpoint in ENDPOINT_ALLOWLIST:
        assert not endpoint.startswith("offer."), f"offer endpoint in allowlist: {endpoint}"
        for verb in ("create", "update", "delete", "change", "set", "add", "upload", "move"):
            assert verb not in endpoint.lower(), f"write endpoint in allowlist: {endpoint}"


# ---------------------------------------------------------------------------
# Expand sanitization — the compensation expansion can never be requested
# ---------------------------------------------------------------------------

def test_compensation_expand_is_stripped():
    assert _sanitize_expand(["compensation"]) == []
    assert _sanitize_expand(["compensation", "openings"]) == ["openings"]
    assert "compensation" not in EXPAND_ALLOWLIST


# ---------------------------------------------------------------------------
# Response redaction — comp keys are scrubbed recursively
# ---------------------------------------------------------------------------

def test_redacts_compensation_key_at_any_depth():
    payload = {
        "results": {
            "id": "job-1",
            "title": "Technical Support Engineer",
            "compensation": {
                "compensationTiers": [{"title": "Tier A", "components": [{"summary": "INR 2,000,000"}]}],
            },
            "nested": [{"salaryRange": {"min": 100, "max": 200}}],
        }
    }
    redacted = redact_sensitive(payload)
    assert redacted["results"]["compensation"] == REDACTED
    assert redacted["results"]["nested"][0]["salaryRange"] == REDACTED
    # Non-sensitive fields survive untouched
    assert redacted["results"]["title"] == "Technical Support Engineer"


@pytest.mark.parametrize(
    "key",
    [
        "compensation",
        "compensationTierId",
        "salary",
        "expectedSalary",
        "equityGrant",
        "bonusAmount",
        "payRate",
        "pay_rate",
        "remuneration",
    ],
)
def test_sensitive_key_variants_are_redacted(key):
    assert redact_sensitive({key: "secret"})[key] == REDACTED


def test_redacts_form_answers_with_compensation_titles():
    """Application form / survey answers about salary get their values scrubbed."""
    payload = {
        "results": [
            {"title": "Expected CTC", "path": "expected_ctc", "value": "35 LPA"},
            {"title": "What is your current salary?", "value": "28 LPA"},
            {"title": "Years of experience", "value": "5"},
        ]
    }
    redacted = redact_sensitive(payload)
    assert redacted["results"][0]["value"] == REDACTED
    assert redacted["results"][1]["value"] == REDACTED
    assert redacted["results"][2]["value"] == "5"


def test_no_comp_values_survive_serialization():
    """End-to-end style check: known comp numbers never appear in redacted output."""
    payload = {
        "results": {
            "compensation": {"compensationTiers": [{"summary": "$180,000 - $220,000"}]},
            "customFields": [{"title": "Salary expectation", "value": "45 LPA"}],
            "candidate": {"name": "Jane Doe"},
        }
    }
    out = json.dumps(redact_sensitive(payload))
    assert "180,000" not in out
    assert "45 LPA" not in out
    assert "Jane Doe" in out


def test_redaction_preserves_non_dict_types():
    assert redact_sensitive([1, "a", None]) == [1, "a", None]
    assert redact_sensitive("plain") == "plain"


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------

def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    with pytest.raises(AshbyToolError, match="ASHBY_API_KEY"):
        asyncio.run(_post("job.list", {}))
