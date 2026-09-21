"""Tests for tools/sonarqube.py. Fully offline: GitHub and Sonar calls are stubbed."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.sonarqube as sq  # noqa: E402
from tools.sonarqube import ProjectResult, SonarToolError  # noqa: E402

PREFIXES = {"apps/api": "api", "apps/web/": "web", "apps/worker": "worker"}


def test_projects_for_files_maps_by_prefix_in_config_order():
    files = ["apps/web/src/a.js", "apps/api/pkg/x.go", "apps/api/pkg/y.go", "README.md"]
    assert sq.projects_for_files(files, PREFIXES) == ["api", "web"]


def test_projects_for_files_requires_a_path_boundary():
    # "apps/api" must not match "apps/api-gateway/..."
    assert sq.projects_for_files(["apps/api-gateway/main.go"], PREFIXES) == []


def test_projects_for_files_none_when_nothing_gated():
    assert sq.projects_for_files(["docs/x.md", ".github/workflows/ci.yml"], PREFIXES) == []


def test_load_project_map(monkeypatch):
    monkeypatch.setenv("SONAR_PROJECTS", json.dumps({"acme/mono": PREFIXES}))
    assert sq.load_project_map("acme/mono") == PREFIXES


def test_load_project_map_unknown_repo(monkeypatch):
    monkeypatch.setenv("SONAR_PROJECTS", json.dumps({"acme/mono": PREFIXES}))
    with pytest.raises(SonarToolError, match="no entry"):
        sq.load_project_map("acme/other")


def test_load_project_map_bad_json(monkeypatch):
    monkeypatch.setenv("SONAR_PROJECTS", "{not json")
    with pytest.raises(SonarToolError, match="not valid JSON"):
        sq.load_project_map("acme/mono")


def test_gate_state_picks_latest_run():
    runs = [
        {"started_at": "2026-01-01T10:00:00Z", "status": "completed", "conclusion": "failure"},
        {"started_at": "2026-01-01T11:00:00Z", "status": "in_progress", "conclusion": None},
    ]
    assert sq.gate_state(runs) == ("in_progress", None)
    assert sq.gate_state([]) == ("missing", None)


@pytest.mark.parametrize(
    "statuses, gate, expected",
    [
        (["OK", "OK"], "success", 0),
        (["OK", "ERROR"], "success", 1),
        (["OK"], "failure", 1),        # gate red for a non-Sonar reason still fails
        (["OK", "NONE"], None, 2),     # a missing analysis is unknown, never a pass
        ([], None, 0),
    ],
)
def test_verdict(statuses, gate, expected):
    results = [ProjectResult(key=f"p{i}", status=s) for i, s in enumerate(statuses)]
    assert sq.verdict(results, gate) == expected


def test_render_lists_issues_sorted_and_explains_missing_analysis():
    results = [
        ProjectResult(key="api", status="ERROR", failed_conditions=["new_violations: 2 (fails when GT 0)"],
                      issues=[{"file": "b.go", "line": 9, "rule": "go:S107", "message": "too many params"},
                              {"file": "a.go", "line": 3, "rule": "go:S3776", "message": "too complex"}]),
        ProjectResult(key="web", status="NONE"),
    ]
    text = sq.render("acme/mono", 7, ("completed", "failure"), results)
    assert text.index("a.go:3") < text.index("b.go:9")
    assert "x new_violations: 2" in text
    assert "no analysis for this PR" in text and "Check the CI job logs" in text
    assert "CI gate check: failure" in text


def test_iap_token_skipped_without_audience(monkeypatch):
    monkeypatch.delenv("SONAR_IAP_AUDIENCE", raising=False)
    assert asyncio.run(sq._iap_identity_token(None)) is None


def test_iap_requires_key_file(monkeypatch):
    monkeypatch.setenv("SONAR_IAP_AUDIENCE", "aud")
    monkeypatch.delenv("SONAR_SA_KEY_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    sq._iap_cache.update(token=None, exp=0.0)
    with pytest.raises(SonarToolError, match="SONAR_SA_KEY_FILE"):
        asyncio.run(sq._iap_identity_token(None))


def _stub_pr_flow(monkeypatch, files, gate, statuses):
    monkeypatch.setenv("SONAR_PROJECTS", json.dumps({"acme/mono": PREFIXES}))
    calls = {"gate": 0, "projects": []}

    async def fake_head_and_files(session, repo, pr):
        return "abc123", files

    async def fake_wait(session, repo, sha, timeout_s):
        calls["gate"] += 1
        return gate

    async def fake_project(session, key, pr_key):
        calls["projects"].append((key, pr_key))
        return ProjectResult(key=key, status=statuses[key])

    monkeypatch.setattr(sq, "pr_head_and_files", fake_head_and_files)
    monkeypatch.setattr(sq, "wait_for_gate", fake_wait)
    monkeypatch.setattr(sq, "project_result", fake_project)
    return calls


def test_run_pr_waits_then_reads_each_touched_project(monkeypatch, capsys):
    calls = _stub_pr_flow(monkeypatch, ["apps/api/x.go", "apps/web/y.js"], ("completed", "success"),
                          {"api": "OK", "web": "ERROR"})
    code = asyncio.run(sq.run_pr("acme/mono", 42, wait=True, timeout_s=10, as_json=False))
    assert code == 1
    assert calls["gate"] == 1
    assert calls["projects"] == [("api", "42"), ("web", "42")]
    assert "[web] ERROR" in capsys.readouterr().out


def test_run_pr_timeout_is_unknown(monkeypatch):
    _stub_pr_flow(monkeypatch, ["apps/api/x.go"], ("in_progress", None), {"api": "OK"})
    assert asyncio.run(sq.run_pr("acme/mono", 42, wait=True, timeout_s=1, as_json=False)) == 2


def test_run_pr_skips_gate_wait_when_nothing_gated(monkeypatch, capsys):
    calls = _stub_pr_flow(monkeypatch, ["docs/readme.md"], ("completed", "success"), {})
    assert asyncio.run(sq.run_pr("acme/mono", 42, wait=True, timeout_s=10, as_json=False)) == 0
    assert calls["gate"] == 0
    assert "nothing to check" in capsys.readouterr().out


def test_main_reports_config_errors_as_exit_2(monkeypatch, capsys):
    monkeypatch.delenv("SONAR_PROJECTS", raising=False)
    assert sq.main(["pr", "--repo", "acme/mono", "--pr", "1"]) == 2
    assert "SONAR_PROJECTS is not set" in capsys.readouterr().err
