"""PR fallback comment for GitHub-triggered flow runs that fail to start.

When a webhook-triggered flow run fails to start or dies with an unhandled
exception (agent pool exhaustion, OAuth refresh failure, client init errors),
the run used to die silently — no comment ever appeared on the PR and authors
assumed the bot ignored them.  This module lets the runner itself post (or
update in place) the sticky review-status comment on the triggering PR so the
failure is visible.

Only GitHub ``pull_request`` webhook payloads are handled; all other webhook
sources (Linear, Pylon, etc.) never match the payload shape and are ignored.
Posting is strictly best-effort: every failure is logged and swallowed so the
original error is never masked.
"""

import logging
import os
import ssl

import aiohttp
import certifi

logger = logging.getLogger(__name__)

GITHUB_API_KEY = os.environ.get("GITHUB_API_KEY", "")
GITHUB_API_BASE = "https://api.github.com"

# Sticky status comment marker maintained by the PR-review flows' agent.
REVIEW_STATUS_MARKER = "<!-- loma-review-status -->"

# Used when the failure has no message (e.g. a bare asyncio.TimeoutError from
# waiting on the client pool).
DEFAULT_FAILURE_REASON = "no agent capacity (client pool timeout)"

_MAX_REASON_LEN = 300

# Reusable SSL context for GitHub API calls (certifi provides CA bundle for macOS)
_ssl_context = ssl.create_default_context(cafile=certifi.where())


def extract_pull_request_target(payload) -> tuple[str, int] | None:
    """Return ``(repo_full_name, pr_number)`` for a GitHub pull_request payload.

    Returns None for anything that is not a GitHub ``pull_request`` event
    (Linear/Pylon/etc. payloads never have this shape).
    """
    if not isinstance(payload, dict):
        return None
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return None
    number = pr.get("number")
    repository = payload.get("repository")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    if not isinstance(number, int) or not full_name or not isinstance(full_name, str):
        return None
    return full_name, number


def build_failure_comment(error) -> str:
    """Build the fallback comment body for a run that failed to start."""
    reason = " ".join(("" if error is None else str(error)).split())
    if not reason:
        reason = DEFAULT_FAILURE_REASON
    if len(reason) > _MAX_REASON_LEN:
        reason = reason[:_MAX_REASON_LEN].rstrip() + "..."
    return (
        f"{REVIEW_STATUS_MARKER}\n"
        f"**Automated review: failed to start** — {reason}. "
        "Push a commit or re-add the `review` label to retry."
    )


async def _github_request(method: str, url: str, json_body: dict | None = None):
    """Make an authenticated GitHub API request. Returns (status, parsed JSON)."""
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    connector = aiohttp.TCPConnector(ssl=_ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.request(method, url, headers=headers, json=json_body) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = None
            return resp.status, data


async def _find_status_comment_id(repo_full_name: str, pr_number: int) -> int | None:
    """Find the existing sticky review-status comment on the PR, if any."""
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments?per_page=100"
    status, data = await _github_request("GET", url)
    if status != 200 or not isinstance(data, list):
        logger.warning(
            "[WEBHOOK-FALLBACK] Failed to list comments on %s#%d (status %s)",
            repo_full_name, pr_number, status,
        )
        return None
    for comment in data:
        if isinstance(comment, dict) and REVIEW_STATUS_MARKER in (comment.get("body") or ""):
            return comment.get("id")
    return None


async def post_run_failure_comment(payload, error) -> bool:
    """Post/update the sticky review-status comment for a failed flow run.

    Edits the existing ``<!-- loma-review-status -->`` comment in place when
    one exists; otherwise creates a new comment carrying the marker.

    Best-effort: never raises, so callers can invoke it from error-handling
    paths without masking the original error.  Returns True if a comment was
    successfully created or updated.
    """
    try:
        target = extract_pull_request_target(payload)
        if target is None:
            return False
        if not GITHUB_API_KEY:
            logger.warning(
                "[WEBHOOK-FALLBACK] No GITHUB_API_KEY set, cannot post run-failure comment",
            )
            return False
        repo_full_name, pr_number = target
        body = build_failure_comment(error)

        comment_id = await _find_status_comment_id(repo_full_name, pr_number)
        if comment_id:
            url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/comments/{comment_id}"
            status, _ = await _github_request("PATCH", url, {"body": body})
            ok = status == 200
        else:
            url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"
            status, _ = await _github_request("POST", url, {"body": body})
            ok = status == 201

        if ok:
            logger.info(
                "[WEBHOOK-FALLBACK] Posted run-failure comment on %s#%d (%s)",
                repo_full_name, pr_number, "updated" if comment_id else "created",
            )
        else:
            logger.error(
                "[WEBHOOK-FALLBACK] Failed to post run-failure comment on %s#%d (status %s)",
                repo_full_name, pr_number, status,
            )
        return ok
    except Exception:
        logger.exception(
            "[WEBHOOK-FALLBACK] Error posting run-failure comment (original error unaffected)",
        )
        return False
