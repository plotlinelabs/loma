"""Ashby ATS — jobs, candidates, applications, and form data (reads + job/opening writes).

Read commands:
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
 11. ashby.py list-departments                  — Departments/teams (for teamId on create-job)
 12. ashby.py list-locations                    — Locations (for locationId on create-job)
 13. ashby.py list-openings [--limit N]         — Openings (positions)

Write commands (require an API key with jobsWrite / openingsWrite):
 14. ashby.py create-job --title T --team-id ID --location-id ID
                         [--default-interview-plan-id ID] [--job-template-id ID]
                                                — Create a new job
 15. ashby.py update-job JOB_ID [--title T] [--team-id ID] [--location-id ID]
                         [--default-interview-plan-id ID] [--custom-requisition-id ID]
                                                — Update job fields
 16. ashby.py set-job-status JOB_ID --status Draft|Open|Closed|Archived
                                                — Change a job's status
 17. ashby.py duplicate-job SOURCE_JOB_ID [--title "New title"]
                                                — Copy core fields of an existing job into a
                                                  new job (job.info -> job.create). Copies
                                                  title/team/location/interview plan only —
                                                  NOT postings, custom fields, or comp bands.
 18. ashby.py create-opening [--identifier X] [--description D] [--team-id ID]
                         [--location-ids ID1,ID2] [--employment-type FullTime|...]
                         [--job-ids ID1,ID2]     — Create an opening (position)
 19. ashby.py add-job-to-opening --opening-id ID --job-id ID
                                                — Attach a job to an opening

Ashby key + per-user access control:
  * Every invocation REQUIRES `--user-email EMAIL --auth-token TOKEN` — the same
    HMAC-signed token used by the personal Google/Slack tools. The token is
    minted server-side for the authenticated Loma user, so one user cannot
    invoke this tool as another user.
  * Only allowlisted users may use this tool at all. Allowlist comes from the
    ASHBY_ALLOWED_USERS env var (comma-separated emails); if unset, ALL access
    is denied (deny-by-default — the allowlist must be configured explicitly).
  * The API key is resolved per user: ASHBY_API_KEY__<EMAIL_UPPERCASED_WITH_
    NON_ALNUM_AS_UNDERSCORE> (e.g. ASHBY_API_KEY__JANE_EXAMPLE_COM for
    jane@example.com) is checked first, then the shared ASHBY_API_KEY as
    fallback.
API docs: https://developers.ashbyhq.com
Auth to Ashby: HTTP Basic — API key as username, blank password.

SECURITY — compensation / offer data must never leak through this tool:
  * Endpoint allowlists: reads are limited to ENDPOINT_ALLOWLIST and writes to
    WRITE_ENDPOINT_ALLOWLIST (job.* and opening.* only). offer.*, apiKey.*,
    candidate writes, and every other endpoint are rejected locally, even if
    the configured API key has permission for them.
  * Write payload sanitization: any payload key matching the sensitive
    patterns (compensation, salary, equity...) is stripped before sending, so
    this tool can never SET compensation data either.
  * No `expand` passthrough: job.info is always called WITHOUT the
    `compensation` expansion, and any `expand` values are filtered against
    EXPAND_ALLOWLIST before the request is sent.
  * Response redaction: every API response is recursively scrubbed —
    keys matching SENSITIVE_KEY_PATTERNS (compensation, salary, equity,
    bonus, CTC, pay, offer amounts...) are replaced with "[REDACTED]".
    Form/survey answers whose field titles look compensation-related are
    redacted the same way.
  * The Ashby API key used with this tool should have jobsRead/jobsWrite and
    openingsRead/openingsWrite as needed, but NOT `offersRead` / `offersWrite`,
    so offer data is blocked at the key level too (defense in depth).

Usage (called by the agent via Bash — auth flags are required on EVERY call):
  python3 tools/ashby.py list-jobs --status Open --user-email jane@example.com --auth-token TOKEN
  python3 tools/ashby.py create-job --title "Backend Engineer" --team-id ... --location-id ... \
      --user-email jane@example.com --auth-token TOKEN
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
    "department.list",
    "location.list",
    "opening.list",
    "opening.info",
}

# Write endpoints — job and opening management ONLY. offer.*, candidate.*,
# application write endpoints, and apiKey.* remain blocked.
WRITE_ENDPOINT_ALLOWLIST = {
    "job.create",
    "job.update",
    "job.setStatus",
    "opening.create",
    "opening.update",
    "opening.addJob",
    "opening.removeJob",
    "opening.setOpeningState",
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


# ---------------------------------------------------------------------------
# Per-user access control
# ---------------------------------------------------------------------------

# If ASHBY_ALLOWED_USERS is not set, NO user may use the tool (deny-by-default).
# Configure the allowlist via the ASHBY_ALLOWED_USERS env var, never in code.
DEFAULT_ALLOWED_USERS = ""

# Set by _authorize_user() after the auth token is verified.
_AUTHED_EMAIL: str | None = None


def _allowed_users() -> set[str]:
    raw = os.environ.get("ASHBY_ALLOWED_USERS", "").strip() or DEFAULT_ALLOWED_USERS
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _authorize_user(user_email: str | None, auth_token: str | None) -> None:
    """Verify the caller's identity and gate access to the allowlist.

    The auth token is an HMAC-signed token minted server-side for the
    authenticated Loma user (see tools/_auth_token.py). This binds every
    invocation to a real logged-in user — another Loma user's session cannot
    produce a valid token for an allowlisted email.
    """
    global _AUTHED_EMAIL
    if not user_email or not auth_token:
        raise AshbyToolError(
            "Access denied: this tool requires --user-email and --auth-token. "
            "The Ashby integration is restricted to specific users "
            "(ASHBY_ALLOWED_USERS)."
        )
    email = user_email.strip().lower()
    if email not in _allowed_users():
        raise AshbyToolError(
            f"Access denied: {email} is not authorized to use the Ashby tool. "
            "Allowed users are configured via the ASHBY_ALLOWED_USERS env var."
        )
    try:
        from _auth_token import verify_user_auth_token
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _auth_token import verify_user_auth_token
    if not verify_user_auth_token(auth_token, user_email.strip()):
        raise AshbyToolError(
            "Access denied: invalid or expired auth token for this user."
        )
    _AUTHED_EMAIL = email


def _user_key_env_name(email: str) -> str:
    return "ASHBY_API_KEY__" + re.sub(r"[^A-Z0-9]", "_", email.upper())


def _get_api_key() -> str:
    if _AUTHED_EMAIL is None:
        raise AshbyToolError(
            "Access denied: user not authorized (missing --user-email/--auth-token)."
        )
    per_user = os.environ.get(_user_key_env_name(_AUTHED_EMAIL), "")
    if per_user:
        return per_user
    key = os.environ.get("ASHBY_API_KEY", "")
    if not key:
        raise AshbyToolError(
            f"No Ashby API key configured. Set {_user_key_env_name(_AUTHED_EMAIL)} "
            "(preferred, per-user) or ASHBY_API_KEY in the Loma server environment. "
            "Provision the key WITHOUT offersRead/offersWrite."
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


def _sanitize_write_payload(obj: Any) -> Any:
    """Strip compensation-related keys from write payloads (defense in depth —
    this tool must never SET compensation/offer data either)."""
    if isinstance(obj, list):
        return [_sanitize_write_payload(item) for item in obj]
    if not isinstance(obj, dict):
        return obj
    return {
        k: _sanitize_write_payload(v)
        for k, v in obj.items()
        if not _SENSITIVE_KEY_RE.search(k)
    }


async def _post(endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call an allowlisted Ashby endpoint and return the redacted JSON response."""
    is_write = endpoint in WRITE_ENDPOINT_ALLOWLIST
    if endpoint not in ENDPOINT_ALLOWLIST and not is_write:
        raise AshbyToolError(
            f"Endpoint '{endpoint}' is not allowed by tools/ashby.py. "
            f"Only allowlisted read endpoints and job/opening write endpoints are permitted; "
            f"offer/compensation endpoints are always blocked. "
            f"Allowed reads: {sorted(ENDPOINT_ALLOWLIST)}; "
            f"allowed writes: {sorted(WRITE_ENDPOINT_ALLOWLIST)}"
        )
    payload = dict(payload or {})
    if is_write:
        payload = _sanitize_write_payload(payload)
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
        "note": (
            "Offer endpoints (offer.list/offer.info) are blocked by this tool regardless of key permissions. "
            "Compensation fields are always redacted. "
            "Write commands (create-job, update-job, set-job-status, duplicate-job, create-opening, add-job-to-opening) "
            "additionally require jobsWrite/openingsWrite on the API key — they are not probed here to avoid side effects."
        ),
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
# Reference lookups (for finding teamId / locationId before creating a job)
# ---------------------------------------------------------------------------

async def list_departments() -> dict[str, Any]:
    departments = await _paginate("department.list", {})
    return {
        "count": len(departments),
        "departments": [
            {"id": d.get("id"), "name": d.get("name"), "parentId": d.get("parentId"), "isArchived": d.get("isArchived")}
            for d in departments
        ],
    }


async def list_locations() -> dict[str, Any]:
    locations = await _paginate("location.list", {})
    return {
        "count": len(locations),
        "locations": [
            {"id": l.get("id"), "name": l.get("name"), "type": l.get("type"),
             "address": l.get("address"), "isArchived": l.get("isArchived"), "isRemote": l.get("isRemote")}
            for l in locations
        ],
    }


async def list_openings(limit: int = 100) -> dict[str, Any]:
    max_pages = max(1, (limit + 99) // 100)
    openings = await _paginate("opening.list", {}, max_pages=max_pages)
    openings = openings[:limit]
    return {
        "count": len(openings),
        "openings": [
            {"id": o.get("id"), "identifier": o.get("identifier"), "openingState": o.get("openingState"),
             "description": o.get("description"), "jobIds": o.get("jobIds"), "teamId": o.get("teamId"),
             "locationIds": o.get("locationIds"), "createdAt": o.get("createdAt")}
            for o in openings
        ],
    }


# ---------------------------------------------------------------------------
# Write commands (require jobsWrite / openingsWrite on the API key)
# ---------------------------------------------------------------------------

async def create_job(
    title: str,
    team_id: str,
    location_id: str,
    default_interview_plan_id: str | None = None,
    job_template_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title, "teamId": team_id, "locationId": location_id}
    if default_interview_plan_id:
        payload["defaultInterviewPlanId"] = default_interview_plan_id
    if job_template_id:
        payload["jobTemplateId"] = job_template_id
    data = await _post("job.create", payload)
    return data.get("results", {})


async def update_job(job_id: str, **fields: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jobId": job_id}
    mapping = {
        "title": "title",
        "team_id": "teamId",
        "location_id": "locationId",
        "default_interview_plan_id": "defaultInterviewPlanId",
        "custom_requisition_id": "customRequisitionId",
    }
    for arg, api_field in mapping.items():
        if fields.get(arg):
            payload[api_field] = fields[arg]
    if len(payload) == 1:
        raise AshbyToolError("update-job: provide at least one field to update (--title, --team-id, --location-id, --default-interview-plan-id, --custom-requisition-id)")
    data = await _post("job.update", payload)
    return data.get("results", {})


async def set_job_status(job_id: str, status: str) -> dict[str, Any]:
    valid = {"Draft", "Open", "Closed", "Archived"}
    if status not in valid:
        raise AshbyToolError(f"set-job-status: --status must be one of {sorted(valid)}")
    data = await _post("job.setStatus", {"jobId": job_id, "status": status})
    return data.get("results", {})


async def duplicate_job(source_job_id: str, title: str | None = None) -> dict[str, Any]:
    """Emulate dashboard 'duplicate job': job.info -> job.create with core fields.

    Copies title, teamId/departmentId, locationId, and default interview plan.
    Does NOT copy: job postings, custom fields, hiring team, or compensation
    bands (the Ashby API has no native duplicate endpoint).
    """
    source = await get_job(source_job_id)
    if not source or not source.get("id"):
        raise AshbyToolError(f"duplicate-job: source job not found: {source_job_id}")

    team_id = source.get("teamId") or source.get("departmentId")
    location_id = source.get("locationId")
    if not team_id or not location_id:
        raise AshbyToolError(
            "duplicate-job: source job is missing teamId/locationId; "
            "create the job explicitly with create-job instead."
        )

    new_title = title or f"{source.get('title')} (Copy)"
    plan_ids = source.get("interviewPlanIds") or []
    default_plan = source.get("defaultInterviewPlanId") or (plan_ids[0] if plan_ids else None)

    created = await create_job(
        title=new_title,
        team_id=team_id,
        location_id=location_id,
        default_interview_plan_id=default_plan,
    )
    return {
        "source_job": {"id": source.get("id"), "title": source.get("title")},
        "created_job": created,
        "note": "Core fields copied only. Job postings, custom fields, hiring team, and compensation are NOT copied (no native duplicate endpoint in the Ashby API).",
    }


async def create_opening(
    identifier: str | None = None,
    description: str | None = None,
    team_id: str | None = None,
    location_ids: list[str] | None = None,
    employment_type: str | None = None,
    job_ids: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if identifier:
        payload["identifier"] = identifier
    if description:
        payload["description"] = description
    if team_id:
        payload["teamId"] = team_id
    if location_ids:
        payload["locationIds"] = location_ids
    if employment_type:
        payload["employmentType"] = employment_type
    data = await _post("opening.create", payload)
    opening = data.get("results", {})

    attached: list[dict[str, Any]] = []
    opening_id = opening.get("id")
    for job_id in (job_ids or []):
        if not opening_id:
            break
        try:
            res = await _post("opening.addJob", {"openingId": opening_id, "jobId": job_id})
            attached.append({"jobId": job_id, "ok": True, "results": res.get("results")})
        except AshbyToolError as e:
            attached.append({"jobId": job_id, "ok": False, "error": str(e)})
    result: dict[str, Any] = {"opening": opening}
    if job_ids:
        result["attached_jobs"] = attached
    return result


async def add_job_to_opening(opening_id: str, job_id: str) -> dict[str, Any]:
    data = await _post("opening.addJob", {"openingId": opening_id, "jobId": job_id})
    return data.get("results", {})


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


def _pop_flag(rest: list[str], name: str) -> str | None:
    """Extract a flag + value from the arg list, removing both."""
    if name in rest:
        idx = rest.index(name)
        if idx + 1 < len(rest):
            value = rest[idx + 1]
            del rest[idx:idx + 2]
            return value
        del rest[idx:idx + 1]
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
        # Per-user access control — runs before ANY command, including reads.
        _authorize_user(_pop_flag(rest, "--user-email"), _pop_flag(rest, "--auth-token"))

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
        elif command == "list-departments":
            result = asyncio.run(list_departments())
        elif command == "list-locations":
            result = asyncio.run(list_locations())
        elif command == "list-openings":
            limit = int(_flag(rest, "--limit") or 100)
            result = asyncio.run(list_openings(limit=limit))
        elif command == "create-job":
            title = _flag(rest, "--title")
            team_id = _flag(rest, "--team-id")
            location_id = _flag(rest, "--location-id")
            if not (title and team_id and location_id):
                raise AshbyToolError("create-job requires --title, --team-id, and --location-id (use list-departments / list-locations to find IDs)")
            result = asyncio.run(create_job(
                title=title,
                team_id=team_id,
                location_id=location_id,
                default_interview_plan_id=_flag(rest, "--default-interview-plan-id"),
                job_template_id=_flag(rest, "--job-template-id"),
            ))
        elif command == "update-job":
            if not rest or rest[0].startswith("--"):
                raise AshbyToolError("update-job requires a JOB_ID as the first argument")
            result = asyncio.run(update_job(
                rest[0],
                title=_flag(rest, "--title"),
                team_id=_flag(rest, "--team-id"),
                location_id=_flag(rest, "--location-id"),
                default_interview_plan_id=_flag(rest, "--default-interview-plan-id"),
                custom_requisition_id=_flag(rest, "--custom-requisition-id"),
            ))
        elif command == "set-job-status":
            if not rest or rest[0].startswith("--"):
                raise AshbyToolError("set-job-status requires a JOB_ID as the first argument")
            status = _flag(rest, "--status")
            if not status:
                raise AshbyToolError("set-job-status requires --status Draft|Open|Closed|Archived")
            result = asyncio.run(set_job_status(rest[0], status))
        elif command == "duplicate-job":
            if not rest or rest[0].startswith("--"):
                raise AshbyToolError("duplicate-job requires a SOURCE_JOB_ID as the first argument")
            result = asyncio.run(duplicate_job(rest[0], title=_flag(rest, "--title")))
        elif command == "create-opening":
            loc = _flag(rest, "--location-ids")
            jobs = _flag(rest, "--job-ids")
            result = asyncio.run(create_opening(
                identifier=_flag(rest, "--identifier"),
                description=_flag(rest, "--description"),
                team_id=_flag(rest, "--team-id"),
                location_ids=[x.strip() for x in loc.split(",") if x.strip()] if loc else None,
                employment_type=_flag(rest, "--employment-type"),
                job_ids=[x.strip() for x in jobs.split(",") if x.strip()] if jobs else None,
            ))
        elif command == "add-job-to-opening":
            opening_id = _flag(rest, "--opening-id")
            job_id = _flag(rest, "--job-id")
            if not (opening_id and job_id):
                raise AshbyToolError("add-job-to-opening requires --opening-id and --job-id")
            result = asyncio.run(add_job_to_opening(opening_id, job_id))
        else:
            print(f"Unknown command: {command}")
            _print_usage()
            return
    except AshbyToolError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    from _integration_access import authorize_cli
    authorize_cli('ashby', preserve_identity=True)
    main()
