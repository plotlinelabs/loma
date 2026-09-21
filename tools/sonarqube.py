"""SonarQube quality-gate reader for pull requests.

Answers "what does Sonar say about PR #N" using the analysis CI already ran for
that PR, optionally waiting for the CI gate check to finish first. Used by the
``sonarqube`` skill, and by ``implement-ticket`` to clear the gate on the draft
PR it opens before handing the work over.

Requires env:
  SONAR_HOST_URL        e.g. https://sonar.example.com
  SONAR_TOKEN           Sonar user token with Browse on the mapped projects
  SONAR_PROJECTS        JSON: {"owner/repo": {"path/prefix": "sonar-project-key", ...}}
                        A PR's changed files decide which projects it's gated on.
  GITHUB_API_KEY        Reads PR files and check runs
Optional:
  SONAR_IAP_AUDIENCE    Set when Sonar sits behind Google IAP: the IAP OAuth client ID
  SONAR_SA_KEY_FILE     Service-account key JSON used to mint the IAP identity token
                        (falls back to GOOGLE_APPLICATION_CREDENTIALS)
  SONAR_GATE_CHECK      GitHub check-run name of the gate (default "SonarQube gate")

CLI:
  python3 tools/sonarqube.py check
  python3 tools/sonarqube.py pr --repo owner/repo --pr 123 [--wait] [--timeout 1800] [--json]

Exit codes for ``pr``: 0 gate passed, 1 gate failed, 2 unknown (timeout, no analysis, config).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

GITHUB_API = "https://api.github.com"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_GATE_CHECK = "SonarQube gate"
POLL_SECONDS = 20
TIMEOUT = aiohttp.ClientTimeout(total=60)


class SonarToolError(Exception):
    """Configuration or API problem the caller should report as-is."""


@dataclass
class ProjectResult:
    key: str
    status: str  # OK | ERROR | NONE (no analysis for this PR)
    failed_conditions: list[str] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    small_diff_skip: bool = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise SonarToolError(f"{name} is not set")
    return value


def load_project_map(repo: str) -> dict[str, str]:
    raw = _env("SONAR_PROJECTS")
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SonarToolError(f"SONAR_PROJECTS is not valid JSON: {exc}") from exc
    if repo not in mapping:
        raise SonarToolError(f"{repo} has no entry in SONAR_PROJECTS; configured: {', '.join(mapping) or 'none'}")
    return mapping[repo]


def projects_for_files(files: list[str], prefix_map: dict[str, str]) -> list[str]:
    """Sonar project keys touched by these paths, in config order."""
    keys: list[str] = []
    for prefix, key in prefix_map.items():
        normalized = prefix.rstrip("/") + "/"
        if any(path.startswith(normalized) for path in files) and key not in keys:
            keys.append(key)
    return keys


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_iap_cache: dict[str, Any] = {"token": None, "exp": 0.0}


async def _iap_identity_token(session: aiohttp.ClientSession) -> str | None:
    audience = _env("SONAR_IAP_AUDIENCE", required=False)
    if not audience:
        return None
    if _iap_cache["token"] and time.time() < _iap_cache["exp"] - 300:
        return _iap_cache["token"]

    from google.auth import crypt, jwt  # google-auth is already a backend dependency

    key_file = _env("SONAR_SA_KEY_FILE", required=False) or _env("GOOGLE_APPLICATION_CREDENTIALS", required=False)
    if not key_file:
        raise SonarToolError("SONAR_IAP_AUDIENCE is set but neither SONAR_SA_KEY_FILE nor GOOGLE_APPLICATION_CREDENTIALS is")
    with open(key_file) as f:
        info = json.load(f)
    now = int(time.time())
    assertion = jwt.encode(crypt.RSASigner.from_service_account_info(info), {
        "iss": info["client_email"], "sub": info["client_email"], "aud": GOOGLE_TOKEN_URL,
        "iat": now, "exp": now + 3600, "target_audience": audience,
    })
    data = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion.decode() if isinstance(assertion, bytes) else assertion}
    async with session.post(GOOGLE_TOKEN_URL, data=data, timeout=TIMEOUT) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200 or "id_token" not in body:
            raise SonarToolError(f"Couldn't mint IAP identity token: HTTP {resp.status} {str(body)[:200]}")
    _iap_cache.update(token=body["id_token"], exp=now + 3600)
    return body["id_token"]


async def _sonar_get(session: aiohttp.ClientSession, path: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {_env('SONAR_TOKEN')}", "Accept": "application/json"}
    iap = await _iap_identity_token(session)
    if iap:
        # IAP reads Proxy-Authorization, leaving Authorization for Sonar's own token.
        headers["Proxy-Authorization"] = f"Bearer {iap}"
    url = _env("SONAR_HOST_URL").rstrip("/") + path
    async with session.get(url, headers=headers, params=params, timeout=TIMEOUT, allow_redirects=False) as resp:
        if resp.status in (301, 302):
            raise SonarToolError("IAP redirected to sign-in: the service account lacks IAP access or the audience is wrong")
        text = await resp.text()
        try:
            return resp.status, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return resp.status, {"raw": text[:300]}


async def _github_get(session: aiohttp.ClientSession, path: str, params: dict[str, Any] | None = None) -> Any:
    headers = {"Authorization": f"Bearer {_env('GITHUB_API_KEY')}", "Accept": "application/vnd.github+json"}
    async with session.get(f"{GITHUB_API}{path}", headers=headers, params=params, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            raise SonarToolError(f"GitHub GET {path} -> HTTP {resp.status}: {(await resp.text())[:200]}")
        return await resp.json()


# ---------------------------------------------------------------------------
# GitHub: PR files and gate check
# ---------------------------------------------------------------------------

async def pr_head_and_files(session: aiohttp.ClientSession, repo: str, pr: int) -> tuple[str, list[str]]:
    info = await _github_get(session, f"/repos/{repo}/pulls/{pr}")
    files: list[str] = []
    page = 1
    while True:
        batch = await _github_get(session, f"/repos/{repo}/pulls/{pr}/files", {"per_page": 100, "page": page})
        files.extend(f["filename"] for f in batch)
        if len(batch) < 100:
            break
        page += 1
    return info["head"]["sha"], files


def gate_state(check_runs: list[dict[str, Any]]) -> tuple[str, str | None]:
    """(status, conclusion) of the most recent gate run; ("missing", None) if none yet."""
    if not check_runs:
        return "missing", None
    latest = max(check_runs, key=lambda r: r.get("started_at") or "")
    return latest.get("status", "unknown"), latest.get("conclusion")


async def wait_for_gate(session: aiohttp.ClientSession, repo: str, sha: str, timeout_s: int) -> tuple[str, str | None]:
    name = _env("SONAR_GATE_CHECK", required=False) or DEFAULT_GATE_CHECK
    deadline = time.time() + timeout_s
    while True:
        runs = await _github_get(session, f"/repos/{repo}/commits/{sha}/check-runs", {"check_name": name, "per_page": 20})
        status, conclusion = gate_state(runs.get("check_runs", []))
        if status == "completed" or time.time() >= deadline:
            return status, conclusion
        await asyncio.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------------
# Sonar: per-project result for a PR analysis
# ---------------------------------------------------------------------------

async def project_result(session: aiohttp.ClientSession, key: str, pr_key: str) -> ProjectResult:
    code, status = await _sonar_get(session, "/api/qualitygates/project_status", {"projectKey": key, "pullRequest": pr_key})
    if code == 404:
        return ProjectResult(key=key, status="NONE")
    if code != 200:
        raise SonarToolError(f"Sonar project_status {key} -> HTTP {code}: {str(status)[:200]}")
    ps = status["projectStatus"]
    result = ProjectResult(
        key=key,
        status=ps["status"],
        failed_conditions=[
            f"{c['metricKey']}: {c.get('actualValue', '?')} (fails when {c['comparator']} {c['errorThreshold']})"
            for c in ps.get("conditions", []) if c.get("status") != "OK"
        ],
        small_diff_skip=bool(ps.get("ignoredConditions")),
    )
    _, issues = await _sonar_get(session, "/api/issues/search",
                                 {"componentKeys": key, "pullRequest": pr_key, "resolved": "false", "ps": 500})
    result.issues = [
        {"file": i["component"].split(":", 1)[-1], "line": i.get("line"), "rule": i["rule"],
         "severity": i.get("severity"), "message": i["message"]}
        for i in issues.get("issues", [])
    ]
    _, hotspots = await _sonar_get(session, "/api/hotspots/search",
                                   {"projectKey": key, "pullRequest": pr_key, "status": "TO_REVIEW", "ps": 500})
    result.hotspots = [
        {"file": h["component"].split(":", 1)[-1], "line": h.get("line"), "message": h["message"]}
        for h in hotspots.get("hotspots", [])
    ]
    return result


def verdict(results: list[ProjectResult], gate_conclusion: str | None) -> int:
    if gate_conclusion == "failure":
        return 1
    if any(r.status == "ERROR" for r in results):
        return 1
    if any(r.status == "NONE" for r in results):
        return 2
    return 0


def render(repo: str, pr: int, gate: tuple[str, str | None] | None, results: list[ProjectResult]) -> str:
    lines = [f"SonarQube for {repo}#{pr}"]
    if gate:
        status, conclusion = gate
        lines.append(f"CI gate check: {conclusion or status}")
    if not results:
        lines.append("No Sonar-gated paths changed: nothing to check.")
    for r in results:
        lines.append(f"\n[{r.key}] {r.status if r.status != 'NONE' else 'no analysis for this PR'}")
        if r.status == "NONE":
            lines.append("  The scan didn't run; usually tests or lint failed first. Check the CI job logs.")
        for c in r.failed_conditions:
            lines.append(f"  x {c}")
        if r.small_diff_skip:
            lines.append("  (coverage/duplication skipped: diff too small)")
        for i in sorted(r.issues, key=lambda x: (x["file"], x["line"] or 0)):
            lines.append(f"  {i['file']}:{i['line'] or '-'}  [{i['rule']}] {i['message']}")
        for h in r.hotspots:
            lines.append(f"  hotspot {h['file']}:{h['line'] or '-'}  {h['message']}")
    return "\n".join(lines)


async def run_pr(repo: str, pr: int, wait: bool, timeout_s: int, as_json: bool) -> int:
    prefix_map = load_project_map(repo)
    async with aiohttp.ClientSession() as session:
        sha, files = await pr_head_and_files(session, repo, pr)
        keys = projects_for_files(files, prefix_map)
        gate = await wait_for_gate(session, repo, sha, timeout_s) if wait and keys else None
        if gate and gate[0] != "completed":
            print(f"Gate still {gate[0]} after {timeout_s}s; result unknown.")
            return 2
        results = [await project_result(session, key, str(pr)) for key in keys]
    code = verdict(results, gate[1] if gate else None)
    if as_json:
        print(json.dumps({"repo": repo, "pr": pr, "head": sha, "gate": gate, "verdict": ["pass", "fail", "unknown"][code],
                          "projects": [r.__dict__ for r in results]}, indent=2))
    else:
        print(render(repo, pr, gate, results))
    return code


async def run_check() -> int:
    async with aiohttp.ClientSession() as session:
        code, body = await _sonar_get(session, "/api/authentication/validate", {})
    ok = code == 200 and body.get("valid") is True
    print(f"Sonar reachable, token {'valid' if ok else 'INVALID'} (HTTP {code})")
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SonarQube quality gate for pull requests")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="Verify Sonar is reachable and the token is valid")
    pr = sub.add_parser("pr", help="Gate verdict and issues for a pull request")
    pr.add_argument("--repo", required=True, help="owner/repo")
    pr.add_argument("--pr", required=True, type=int)
    pr.add_argument("--wait", action="store_true", help="Wait for the CI gate check to finish first")
    pr.add_argument("--timeout", type=int, default=1800, help="Seconds to wait with --wait")
    pr.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.cmd == "check":
            return asyncio.run(run_check())
        return asyncio.run(run_pr(args.repo, args.pr, args.wait, args.timeout, args.json))
    except SonarToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
