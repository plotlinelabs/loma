"""Ashby ATS — jobs, candidates, applications, and form data (READ-ONLY).

Provides CLI commands for the Loma agent:
  1. ashby.py check-auth                        — Verify API key and endpoint access
  2. ashby.py list-jobs [--status Open]         — List jobs (id, title, status)
  3. ashby.py get-job JOB_ID                    — Job details (compensation always redacted)
  4. ashby.py list-stages --job-id JOB_ID       — Interview stages for a job's interview plan
  5. ashby.py list-candidates [--limit N]       — List candidates
  6. ashby.py get-candidate CANDIDATE_ID        — Candidate profile + resume download URL
  7. ashby.py list-applications --job-id ID [--status Active|Archived]
                                                — All applications for a job (auto-paginates)
  8. ashby.py get-application APPLICATION_ID    — Full application detail
  9. ashby.py export-application APPLICATION_ID — Aggregated export (application + candidate
                                                  + resume URL + feedback) for AI review
 10. ashby.py export-job-applications --job-id ID [--status Active] [--limit N]
                                                — Bulk export of a job's applications

Requires ASHBY_API_KEY environment variable.
API docs: https://developers.ashbyhq.com
Auth: HTTP Basic — API key as username, blank password.

SECURITY — compensation / offer data must never leak through this tool:
  * READ-ONLY: no write/update/delete endpoints are implemented.
  * Endpoint allowlist: only the endpoints listed in ENDPOINT_ALLOWLIST can be
    called. offer.*, apiKey.*, and every other endpoint are rejected locally,
    even if the configured API key has permission for them.
  * No `expand` passthrough: job.info is always called WITHOUT the
    `compensation` expansion, and any `expand` values are filtered against
    EXPAND_ALLOWLIST before the request is sent.
  * Response redaction: every API response is recursively scrubbed —
    keys matching SENSITIVE_KEY_PATTERNS (compensation, salary, equity,
    bonus, CTC, pay, offer amounts...) are replaced with "[REDACTED]".
    Form/survey answers whose field titles look compensation-related are
    redacted the same way.
  * The Ashby API key used with this tool should additionally NOT have the
    `offersRead` / `offersWrite` permissions, so offer data is blocked at the
    key level too (defense in depth).

Usage (called by the agent via Bash):
  python3 tools/ashby.py list-jobs --status Open
  python3 tools/ashby.py list-applications --job-id 463d3765-... --status Active
  python3 tools/ashby.py export-application 1f3a6f32-...
"""

import asyncio
import base64
import json
import os
import re
import sys
from typing import Any

import aiohttp

ASHBY_BASE_URL = "https://api.ashbyhq.com"

# Only these endpoints may ever be called. Everything else (offer.*, apiKey.*,
# any write endpoint) is rejected before a request is made.
ENDPOINT_ALLOWLIST = {
    "job.list",
    "job.info",
    "interviewPlan.list",
    "interviewStage.list",
    "candidate.list",
    "candidate.info",
    "application.list",
    "application.info",
    "applicationFeedback.list",
    "file.info",
    "archiveReason.list",
    "source.list",
}

# `expand` values that are safe to forward. "compensation" is deliberately
# absent and can never be requested through this tool.
EXPAND_ALLOWLIST: set[str] = {"openings"}

# Any dict key matching one of these patterns is redacted from every response.
SENSITIVE_KEY_PATTERNS = [
    r"compensation",
    r"salary",
    r"equity",
    r"bonus",
    r"\bctc\b",
    r"payrate",
    r"pay_rate",
    r"remuneration",
    r"offeramount",
    r"offer_amount",
]

# Form-field titles (application questions, survey questions, custom fields)
# matching these patterns get their VALUES redacted.
SENSITIVE_TITLE_PATTERNS = [
    r"compensation",
    r"salary",
    r"\bctc\b",
    r"equity",
    r"\bpay\b",
    r"remuneration",
]

_SENSITIVE_KEY_RE = re.compile("|".join(SENSITIVE_KEY_PATTERNS), re.IGNORECASE)
_SENSITIVE_TITLE_RE = re.compile("|".join(SENSITIVE_TITLE_PATTERNS), re.IGNORECASE)

REDACTED = "[REDACTED:compensation-data-blocked-by-loma]"


class AshbyToolError(Exception):
    pass


def _get_api_key() -> str:
    key = os.environ.get("ASHBY_API_KEY", "")
    if not key:
        raise AshbyToolError(
            "ASHBY_API_KEY environment variable is not set. "
            "Configure a READ-ONLY Ashby API key (without offersRead/offersWrite) before using this tool."
        )
    return key


def redact_sensitive(obj: Any) -> Any:
    """Recursively scrub compensation-related data from any API response.

    - Dict keys matching SENSITIVE_KEY_PATTERNS -> value replaced with REDACTED.
    - Dicts whose title/path/label looks compensation-related get their
      answer/value fields redacted (covers form submissions and custom fields).
    """
    if isinstance(obj, list):
        return [redact_sensitive(item) for item in obj]
    if not isinstance(obj, dict):
        return obj

    title_is_sensitive = any(
        isinstance(obj.get(k), str) and _SENSITIVE_TITLE_RE.search(obj[k])
        for k in ("title", "humanReadablePath", "path", "label", "name")
    )
    result: dict[str, Any] = {}
    for key, value in obj.items():
        if _SENSITIVE_KEY_RE.search(key):
            result[key] = REDACTED
        elif title_is_sensitive and key in ("value", "answer", "answers", "selectedValues", "response"):
            result[key] = REDACTED
        else:
            result[key] = redact_sensitive(value)
    return result


def _sanitize_expand(expand: list[str] | None) -> list[str]:
    if not expand:
        return []
    return [e for e in expand if e in EXPAND_ALLOWLIST]


async def _post(endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an allowlisted Ashby endpoint and return the redacted JSON response."""
    if endpoint not in ENDPOINT_ALLOWLIST:
        raise AshbyToolError(
            f"Endpoint '{endpoint}' is not allowed by tools/ashby.py. "
            f"This tool is read-only and blocks offer/compensation endpoints. "
            f"Allowed: {sorted(ENDPOINT_ALLOWLIST)}"
        )
    payload = dict(payload or {})
    if "expand" in payload:
        payload["expand"] = _sanitize_expand(payload.get("expand"))
        if not payload["expand"]:
            del payload["expand"]

    basic = base64.b64encode(f"{_get_api_key()}:".encode()).decode()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{ASHBY_BASE_URL}/{endpoint}",
            json=payload,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/json",
                "Accept": "application/json; version=1",
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status == 429:
                raise AshbyToolError("Ashby rate limit reached. Retry shortly.")
            if resp.status == 401:
                raise AshbyToolError("Ashby API key is invalid or revoked.")
            data = await resp.json(content_type=None)

    if not data.get("success", False):
        errors = data.get("errors") or [data.get("errorInfo", {}).get("code", "unknown_error")]
        if "missing_endpoint_permission" in errors:
            raise AshbyToolError(
                f"The configured Ashby API key does not have permission for '{endpoint}'."
            )
        raise AshbyToolError(f"Ashby API error on '{endpoint}': {errors}")

    return redact_sensitive(data)


async def _paginate(endpoint: str, payload: dict[str, Any], max_pages: int = 50) -> list[dict[str, Any]]:
    """Fetch all pages of a list endpoint (cursor-based)."""
    results: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(max_pages):
        body = dict(payload)
        if cursor:
            body["cursor"] = cursor
        data = await _post(endpoint, body)
        results.extend(data.get("results", []))
        if not data.get("moreDataAvailable"):
            break
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return results


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def check_auth() -> dict[str, Any]:
    """Probe which allowlisted endpoints the configured key can access."""
    access: dict[str, str] = {}
    probes = {
        "job.list": {"limit": 1},
        "candidate.list": {"limit": 1},
        "application.list": {"limit": 1},
        "applicationFeedback.list": {"limit": 1},
    }
    for endpoint, payload in probes.items():
        try:
            await _post(endpoint, payload)
            access[endpoint] = "ok"
        except AshbyToolError as e:
            access[endpoint] = str(e)
    return {
        "base_url": ASHBY_BASE_URL,
        "endpoint_access": access,
        "note": "Offer endpoints (offer.list/offer.info) are blocked by this tool regardless of key permissions. Compensation fields are always redacted.",
    }


def _format_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "title": job.get("title"),
        "status": job.get("status"),
        "confidential": job.get("confidential"),
        "employmentType": job.get("employmentType"),
        "locationId": job.get("locationId"),
        "departmentId": job.get("departmentId"),
        "createdAt": job.get("createdAt"),
        "openedAt": job.get("openedAt"),
        "closedAt": job.get("closedAt"),
    }


async def list_jobs(status: str | None = None) -> dict[str, Any]:
    jobs = await _paginate("job.list", {})
    if status:
        jobs = [j for j in jobs if (j.get("status") or "").lower() == status.lower()]
    return {"count": len(jobs), "jobs": [_format_job(j) for j in jobs]}


async def get_job(job_id: str) -> dict[str, Any]:
    data = await _post("job.info", {"id": job_id})
    return data.get("results", {})


async def list_stages(job_id: str) -> dict[str, Any]:
    job = await get_job(job_id)
    plan_ids = job.get("interviewPlanIds") or []
    stages: list[dict[str, Any]] = []
    for plan_id in plan_ids:
        data = await _post("interviewStage.list", {"interviewPlanId": plan_id})
        for s in data.get("results", []):
            stages.append({
                "id": s.get("id"),
                "title": s.get("title"),
                "type": s.get("type"),
                "orderInInterviewPlan": s.get("orderInInterviewPlan"),
                "interviewPlanId": plan_id,
            })
    return {"jobId": job_id, "jobTitle": job.get("title"), "count": len(stages), "stages": stages}


def _format_candidate(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "primaryEmail": (c.get("primaryEmailAddress") or {}).get("value"),
        "phoneNumbers": [p.get("value") for p in (c.get("phoneNumbers") or [])],
        "socialLinks": c.get("socialLinks"),
        "position": c.get("position"),
        "company": c.get("company"),
        "school": c.get("school"),
        "applicationIds": c.get("applicationIds"),
        "tags": [t.get("title") for t in (c.get("tags") or []) if isinstance(t, dict)],
        "createdAt": c.get("createdAt"),
    }


async def list_candidates(limit: int = 100) -> dict[str, Any]:
    max_pages = max(1, (limit + 99) // 100)
    candidates = await _paginate("candidate.list", {"limit": min(limit, 100)}, max_pages=max_pages)
    candidates = candidates[:limit]
    return {"count": len(candidates), "candidates": [_format_candidate(c) for c in candidates]}


async def _resolve_file_url(file_handle: dict[str, Any] | None) -> str | None:
    if not file_handle or not file_handle.get("handle"):
        return None
    try:
        data = await _post("file.info", {"fileHandle": file_handle["handle"]})
        return (data.get("results") or {}).get("url")
    except AshbyToolError:
        return None


async def get_candidate(candidate_id: str) -> dict[str, Any]:
    data = await _post("candidate.info", {"id": candidate_id})
    candidate = data.get("results", {})
    result = _format_candidate(candidate)
    result["resumeUrl"] = await _resolve_file_url(candidate.get("resumeFileHandle"))
    result["fileHandles"] = candidate.get("fileHandles")
    return result


def _format_application(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("id"),
        "candidate": a.get("candidate"),
        "status": a.get("status"),
        "currentStage": (a.get("currentInterviewStage") or {}).get("title"),
        "currentStageId": (a.get("currentInterviewStage") or {}).get("id"),
        "source": (a.get("source") or {}).get("title"),
        "archiveReason": (a.get("archiveReason") or {}).get("text"),
        "createdAt": a.get("createdAt"),
        "updatedAt": a.get("updatedAt"),
        "archivedAt": a.get("archivedAt"),
    }


async def list_applications(job_id: str, status: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jobId": job_id, "limit": 100}
    if status:
        payload["status"] = status
    apps = await _paginate("application.list", payload)
    by_stage: dict[str, int] = {}
    for a in apps:
        stage = (a.get("currentInterviewStage") or {}).get("title") or "Unknown"
        by_stage[stage] = by_stage.get(stage, 0) + 1
    return {
        "jobId": job_id,
        "status_filter": status,
        "count": len(apps),
        "by_stage": by_stage,
        "applications": [_format_application(a) for a in apps],
    }


async def get_application(application_id: str) -> dict[str, Any]:
    data = await _post("application.info", {"applicationId": application_id})
    return data.get("results", {})


async def export_application(application_id: str) -> dict[str, Any]:
    """Aggregate everything needed to review one application with AI."""
    app = await get_application(application_id)
    if not app:
        return {"error": f"Application not found: {application_id}"}

    candidate_id = (app.get("candidate") or {}).get("id")
    candidate = await get_candidate(candidate_id) if candidate_id else None

    resume_url = await _resolve_file_url(app.get("resumeFileHandle"))
    if not resume_url and candidate:
        resume_url = candidate.get("resumeUrl")

    feedback: list[dict[str, Any]] = []
    try:
        fb = await _post("applicationFeedback.list", {"applicationId": application_id})
        feedback = fb.get("results", [])
    except AshbyToolError:
        pass

    return {
        "application": {
            **_format_application(app),
            "customFields": app.get("customFields"),
            "applicationHistory": app.get("applicationHistory"),
            "job": {"id": (app.get("job") or {}).get("id"), "title": (app.get("job") or {}).get("title")},
        },
        "candidate": candidate,
        "resumeUrl": resume_url,
        "feedback": feedback,
    }


async def export_job_applications(job_id: str, status: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Bulk export for AI review: every application on a job with candidate + resume URL."""
    listing = await list_applications(job_id, status=status)
    apps = listing["applications"][:limit]

    async def _one(a: dict[str, Any]) -> dict[str, Any]:
        try:
            return await export_application(a["id"])
        except AshbyToolError as e:
            return {"application": a, "error": str(e)}

    exported: list[dict[str, Any]] = []
    batch = 5
    for i in range(0, len(apps), batch):
        exported.extend(await asyncio.gather(*[_one(a) for a in apps[i:i + batch]]))

    return {
        "jobId": job_id,
        "status_filter": status,
        "total_on_job": listing["count"],
        "exported": len(exported),
        "by_stage": listing["by_stage"],
        "applications": exported,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_usage():
    print(__doc__)
    sys.exit(1)


def _flag(rest: list[str], name: str) -> str | None:
    if name in rest:
        idx = rest.index(name)
        if idx + 1 < len(rest):
            return rest[idx + 1]
    return None


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = sys.argv[2:]

    try:
        if command == "check-auth":
            result = asyncio.run(check_auth())
        elif command == "list-jobs":
            result = asyncio.run(list_jobs(status=_flag(rest, "--status")))
        elif command == "get-job":
            if not rest:
                raise AshbyToolError("get-job requires a JOB_ID")
            result = asyncio.run(get_job(rest[0]))
        elif command == "list-stages":
            job_id = _flag(rest, "--job-id")
            if not job_id:
                raise AshbyToolError("list-stages requires --job-id")
            result = asyncio.run(list_stages(job_id))
        elif command == "list-candidates":
            limit = int(_flag(rest, "--limit") or 100)
            result = asyncio.run(list_candidates(limit=limit))
        elif command == "get-candidate":
            if not rest:
                raise AshbyToolError("get-candidate requires a CANDIDATE_ID")
            result = asyncio.run(get_candidate(rest[0]))
        elif command == "list-applications":
            job_id = _flag(rest, "--job-id")
            if not job_id:
                raise AshbyToolError("list-applications requires --job-id")
            result = asyncio.run(list_applications(job_id, status=_flag(rest, "--status")))
        elif command == "get-application":
            if not rest:
                raise AshbyToolError("get-application requires an APPLICATION_ID")
            result = asyncio.run(get_application(rest[0]))
        elif command == "export-application":
            if not rest:
                raise AshbyToolError("export-application requires an APPLICATION_ID")
            result = asyncio.run(export_application(rest[0]))
        elif command == "export-job-applications":
            job_id = _flag(rest, "--job-id")
            if not job_id:
                raise AshbyToolError("export-job-applications requires --job-id")
            limit = int(_flag(rest, "--limit") or 200)
            result = asyncio.run(export_job_applications(job_id, status=_flag(rest, "--status"), limit=limit))
        else:
            print(f"Unknown command: {command}")
            _print_usage()
            return
    except AshbyToolError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
