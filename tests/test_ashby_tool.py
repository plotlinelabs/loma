"""Tests for tools/ashby.py — focused on the compensation-leak guardrails.

These tests run fully offline (no Ashby API calls).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.ashby as ashby_mod  # noqa: E402
from tools.ashby import (  # noqa: E402
    ENDPOINT_ALLOWLIST,
    EXPAND_ALLOWLIST,
    REDACTED,
    WRITE_ENDPOINT_ALLOWLIST,
    AshbyToolError,
    _post,
    _sanitize_expand,
    _sanitize_write_payload,
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
        "jobPosting.update",
        "interviewPlan.update",
    ],
)
def test_blocked_endpoints_raise_locally(endpoint):
    """Blocked endpoints raise before any network request is made."""
    with pytest.raises(AshbyToolError, match="not allowed"):
        asyncio.run(_post(endpoint, {}))


def test_no_offer_or_write_endpoints_in_read_allowlist():
    for endpoint in ENDPOINT_ALLOWLIST:
        assert not endpoint.startswith("offer."), f"offer endpoint in allowlist: {endpoint}"
        for verb in ("create", "update", "delete", "change", "set", "add", "upload", "move"):
            assert verb not in endpoint.lower(), f"write endpoint in allowlist: {endpoint}"


def test_write_allowlist_covers_only_job_and_opening():
    for endpoint in WRITE_ENDPOINT_ALLOWLIST:
        assert endpoint.startswith(("job.", "opening.")), (
            f"non job/opening write endpoint in write allowlist: {endpoint}"
        )
    assert not any(e.startswith("offer.") for e in WRITE_ENDPOINT_ALLOWLIST)


@pytest.mark.parametrize("endpoint", sorted(WRITE_ENDPOINT_ALLOWLIST))
def test_write_endpoints_pass_allowlist(endpoint, monkeypatch):
    """Allowlisted write endpoints clear the local endpoint gate.

    With no API key configured they must fail on the missing key (i.e. AFTER
    the allowlist check), proving the endpoint itself is permitted — and no
    network request is ever made.
    """
    monkeypatch.setattr(ashby_mod, "_AUTHED_EMAIL", "user@example.com")
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    monkeypatch.delenv("ASHBY_API_KEY__USER_EXAMPLE_COM", raising=False)
    with pytest.raises(AshbyToolError, match="ASHBY_API_KEY"):
        asyncio.run(_post(endpoint, {}))


def test_write_payload_sanitizer_strips_comp_keys():
    payload = {
        "title": "Backend Engineer",
        "teamId": "t-1",
        "compensation": {"min": 100},
        "salaryRange": "100-200",
        "nested": {"equityGrant": "1%", "locationId": "l-1"},
    }
    out = _sanitize_write_payload(payload)
    assert "compensation" not in out
    assert "salaryRange" not in out
    assert "equityGrant" not in out["nested"]
    assert out["title"] == "Backend Engineer"
    assert out["nested"]["locationId"] == "l-1"


def test_unauthorized_user_cannot_reach_api(monkeypatch):
    """Without a verified user, even allowlisted endpoints fail closed."""
    monkeypatch.setattr(ashby_mod, "_AUTHED_EMAIL", None)
    monkeypatch.setenv("ASHBY_API_KEY", "dummy")
    with pytest.raises(AshbyToolError, match="not authorized"):
        asyncio.run(_post("job.list", {}))


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
    monkeypatch.setattr(ashby_mod, "_AUTHED_EMAIL", "user@example.com")
    monkeypatch.delenv("ASHBY_API_KEY", raising=False)
    monkeypatch.delenv("ASHBY_API_KEY__USER_EXAMPLE_COM", raising=False)
    with pytest.raises(AshbyToolError, match="ASHBY_API_KEY"):
        asyncio.run(_post("job.list", {}))
