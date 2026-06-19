"""
GitHub Event Ingestion (Pass 1)

Normalizes GitHub webhook events into the unified event schema and stores
them in MongoDB. Called as fire-and-forget from the existing webhook handler.
"""

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone

import aiohttp

from observability.db import get_db

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
# Caps on attached diffs keep stored event docs bounded in size.
_MAX_DIFF_FILES = 50
_MAX_PATCH_LINES = 500
# PR actions where fetching the diff is meaningful (code actually changed).
_DIFF_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

# GitHub event types that map to specific normalized types
_EVENT_TYPE_MAP = {
    "pull_request": "pull_request",
    "pull_request_review": "review",
    "pull_request_review_comment": "review_comment",
    "issue_comment": "comment",
    "issues": "issue",
    "push": "push",
    "commit_comment": "commit_comment",
    "release": "release",
    "workflow_run": "ci_run",
    "check_run": "ci_check",
    "check_suite": "ci_suite",
    "discussion": "discussion",
    "discussion_comment": "discussion_comment",
    "deployment": "deployment",
    "deployment_status": "deployment_status",
    "star": "star",
    "fork": "fork",
    "ping": "ping",
}

# create/delete need special handling (branch vs tag)
_CREATE_DELETE_TYPES = {"create", "delete"}


def _content_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_timestamp(event_type: str, body: dict) -> datetime:
    """Extract the most relevant timestamp from the webhook payload."""
    ts_str = None

    if event_type == "pull_request":
        pr = body.get("pull_request", {})
        ts_str = pr.get("updated_at") or pr.get("created_at")
    elif event_type == "pull_request_review":
        ts_str = body.get("review", {}).get("submitted_at")
    elif event_type in ("pull_request_review_comment", "issue_comment", "commit_comment"):
        ts_str = body.get("comment", {}).get("updated_at") or body.get("comment", {}).get("created_at")
    elif event_type == "issues":
        ts_str = body.get("issue", {}).get("updated_at") or body.get("issue", {}).get("created_at")
    elif event_type == "push":
        head = body.get("head_commit")
        if head:
            ts_str = head.get("timestamp")
    elif event_type == "release":
        ts_str = body.get("release", {}).get("published_at") or body.get("release", {}).get("created_at")
    elif event_type == "workflow_run":
        ts_str = body.get("workflow_run", {}).get("updated_at") or body.get("workflow_run", {}).get("created_at")
    elif event_type == "check_run":
        ts_str = body.get("check_run", {}).get("completed_at") or body.get("check_run", {}).get("started_at")
    elif event_type == "discussion":
        ts_str = body.get("discussion", {}).get("updated_at") or body.get("discussion", {}).get("created_at")
    elif event_type == "discussion_comment":
        ts_str = body.get("comment", {}).get("updated_at") or body.get("comment", {}).get("created_at")

    if ts_str:
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return datetime.now(timezone.utc)


def _extract_text(event_type: str, body: dict) -> str | None:
    """Extract the meaningful text content from the webhook payload."""
    if event_type == "pull_request":
        pr = body.get("pull_request", {})
        title = pr.get("title", "")
        pr_body = pr.get("body") or ""
        return f"{title}\n\n{pr_body}".strip() if title else pr_body or None

    if event_type in ("pull_request_review_comment", "issue_comment", "commit_comment"):
        return body.get("comment", {}).get("body")

    if event_type == "pull_request_review":
        return body.get("review", {}).get("body")

    if event_type == "issues":
        issue = body.get("issue", {})
        title = issue.get("title", "")
        issue_body = issue.get("body") or ""
        return f"{title}\n\n{issue_body}".strip() if title else issue_body or None

    if event_type == "push":
        commits = body.get("commits", [])
        if commits:
            messages = [c.get("message", "") for c in commits if c.get("message")]
            return "\n".join(messages) if messages else None
        return None

    if event_type == "release":
        release = body.get("release", {})
        name = release.get("name", "")
        release_body = release.get("body") or ""
        return f"{name}\n\n{release_body}".strip() if name else release_body or None

    if event_type == "discussion":
        disc = body.get("discussion", {})
        title = disc.get("title", "")
        disc_body = disc.get("body") or ""
        return f"{title}\n\n{disc_body}".strip() if title else disc_body or None

    if event_type == "discussion_comment":
        return body.get("comment", {}).get("body")

    return None


def _extract_thread_ref(event_type: str, body: dict) -> str | None:
    """Extract a thread reference in ``owner/repo#number`` format for grouping."""
    repo_full = (body.get("repository") or {}).get("full_name", "")

    if event_type in ("pull_request", "pull_request_review", "pull_request_review_comment"):
        pr = body.get("pull_request", {})
        if not pr and event_type == "pull_request_review_comment":
            pr = body.get("pull_request", {})
        number = pr.get("number")
        if not number:
            number = body.get("pull_request", {}).get("number")
        return f"{repo_full}#{number}" if number and repo_full else None

    if event_type == "issue_comment":
        issue = body.get("issue", {})
        number = issue.get("number")
        if number and repo_full:
            return f"{repo_full}#{number}"
        return None

    if event_type == "issues":
        number = body.get("issue", {}).get("number")
        return f"{repo_full}#{number}" if number and repo_full else None

    if event_type in ("discussion", "discussion_comment"):
        number = body.get("discussion", {}).get("number")
        return f"{repo_full}#{number}" if number and repo_full else None

    if event_type == "push":
        return body.get("ref")  # e.g., "refs/heads/main"

    if event_type == "commit_comment":
        return body.get("comment", {}).get("commit_id")

    return None


def normalize_github_event(event_type: str, delivery_id: str, body: dict) -> dict | None:
    """Normalize a GitHub webhook payload into the unified event schema."""
    if not event_type or not body:
        return None

    # Determine normalized event_type
    action = body.get("action")

    if event_type in _CREATE_DELETE_TYPES:
        ref_type = body.get("ref_type", "branch")  # "branch" or "tag"
        normalized_type = f"{ref_type}_{event_type}"  # e.g., "branch_create", "tag_delete"
        normalized_subtype = None
    elif event_type in _EVENT_TYPE_MAP:
        normalized_type = _EVENT_TYPE_MAP[event_type]
        normalized_subtype = action
    else:
        # Unknown event — still capture it
        normalized_type = event_type
        normalized_subtype = action

    # Special case: PR closed + merged → subtype "merged"
    if event_type == "pull_request" and action == "closed":
        if body.get("pull_request", {}).get("merged"):
            normalized_subtype = "merged"

    # Extract repo info
    repo = body.get("repository", {})
    repo_full_name = repo.get("full_name", "")
    repo_name = repo.get("name")

    # Extract sender
    sender = body.get("sender", {})
    user_id = sender.get("login")
    is_bot = sender.get("type") == "Bot" or (user_id or "").endswith("[bot]")

    return {
        "event_id": str(uuid.uuid4()),
        "source": "github",
        "source_event_id": f"gh:{delivery_id}",
        "event_type": normalized_type,
        "event_subtype": normalized_subtype,
        "timestamp": _parse_timestamp(event_type, body),
        "ingested_at": datetime.now(timezone.utc),
        "channel_id": repo_full_name,
        "channel_name": repo_name,
        "thread_ts": _extract_thread_ref(event_type, body),
        "thread_refs": {
            "github_pr": [ref] for ref in [_extract_thread_ref(event_type, body)] if ref
        } if _extract_thread_ref(event_type, body) else {},
        "user_id": user_id,
        "user_name": user_id,  # GitHub login is the display name
        "is_bot": is_bot,
        "text": _extract_text(event_type, body),
        "content_hash": _content_hash(_extract_text(event_type, body)),
        "files": [],
        "reaction": None,
        "reaction_target_ts": None,
        "entities": [],
        "embedding": None,
        "raw_event": body,
        "processed": False,
        "processing_version": 1,
    }


def _truncate_patch(patch: str | None) -> tuple[str | None, bool]:
    """Cap a single file patch at _MAX_PATCH_LINES. Returns (patch, truncated)."""
    if not patch:
        return patch, False
    lines = patch.splitlines()
    if len(lines) <= _MAX_PATCH_LINES:
        return patch, False
    kept = lines[:_MAX_PATCH_LINES]
    kept.append(f"... ({len(lines) - _MAX_PATCH_LINES} more lines truncated)")
    return "\n".join(kept), True


def _format_diff_as_text(diff: dict) -> str:
    """Flatten a structured diff into a plain-text unified-diff block.

    Used to fold diff content into the event's ``text`` field so downstream
    semantic reasoning has a single, uniform field across all event sources.
    """
    header = (
        f"--- patch ---\n"
        f"{diff['total_files']} file(s), "
        f"+{diff['total_additions']} -{diff['total_deletions']}"
    )
    if diff.get("truncated"):
        header += f" (showing {len(diff['files'])} of {diff['total_files']})"
    parts = [header]
    for f in diff["files"]:
        parts.append("")
        parts.append(f"{f['filename']} [{f['status']}] +{f['additions']} -{f['deletions']}")
        if f.get("patch"):
            parts.append(f["patch"])
            if f.get("patch_truncated"):
                parts.append("(patch truncated)")
        else:
            parts.append("(no patch — binary or too large)")
    return "\n".join(parts)


def _build_diff_files(file_data: list) -> tuple[list[dict], int, int, int, bool]:
    """Cap + format a GitHub files[] array. Returns (files, total_files, additions, deletions, truncated)."""
    total_additions = sum(int(f.get("additions", 0) or 0) for f in file_data)
    total_deletions = sum(int(f.get("deletions", 0) or 0) for f in file_data)
    total_files = len(file_data)
    truncated = total_files > _MAX_DIFF_FILES
    files: list[dict] = []
    for f in file_data[:_MAX_DIFF_FILES]:
        patch, patch_truncated = _truncate_patch(f.get("patch"))
        files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": int(f.get("additions", 0) or 0),
            "deletions": int(f.get("deletions", 0) or 0),
            "patch": patch,
            "patch_truncated": patch_truncated,
        })
    return files, total_files, total_additions, total_deletions, truncated


async def _fetch_diff(target: dict) -> dict | None:
    """Fetch per-file patches from GitHub for a PR or a push-range.

    ``target`` shape:
      {"kind": "pr", "repo": "...", "pr_number": N}
      {"kind": "push", "repo": "...", "before": sha, "after": sha}
    """
    token = os.environ.get("GITHUB_API_KEY", "").strip()
    if not token:
        logger.info("[GH-INGESTION] diff fetch skipped — GITHUB_API_KEY not set")
        return None

    repo = target.get("repo", "")
    if not repo:
        return None

    if target["kind"] == "pr":
        url = f"{_GITHUB_API}/repos/{repo}/pulls/{target['pr_number']}/files"
        label = f"repo={repo} pr={target['pr_number']}"
    else:
        before = target["before"]
        after = target["after"]
        url = f"{_GITHUB_API}/repos/{repo}/compare/{before}...{after}"
        label = f"repo={repo} push={before[:7]}..{after[:7]}"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, params={"per_page": 100}) as resp:
                if resp.status != 200:
                    body_text = (await resp.text())[:200]
                    logger.warning(
                        "[GH-INGESTION] diff fetch failed %s status=%s body=%s",
                        label, resp.status, body_text,
                    )
                    return None
                payload = await resp.json()
    except Exception:
        logger.exception("[GH-INGESTION] diff fetch error %s", label)
        return None

    # /pulls/{n}/files returns a list; /compare returns an object with a "files" key.
    if isinstance(payload, list):
        file_data = payload
    elif isinstance(payload, dict):
        file_data = payload.get("files") or []
    else:
        return None

    files, total_files, total_additions, total_deletions, truncated = _build_diff_files(file_data)
    return {
        "total_files": total_files,
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "truncated": truncated,
        "files": files,
    }


_ZERO_SHA = "0" * 40


def _should_fetch_diff(event_type: str, body: dict) -> dict | None:
    """Return a target dict if this event warrants a diff fetch, else None."""
    repo_full_name = (body.get("repository") or {}).get("full_name", "")
    if not repo_full_name:
        return None

    if event_type == "pull_request":
        action = body.get("action")
        if action not in _DIFF_ACTIONS:
            return None
        pr = body.get("pull_request") or {}
        number = pr.get("number")
        if not number:
            return None
        return {"kind": "pr", "repo": repo_full_name, "pr_number": int(number)}

    if event_type == "push":
        before = body.get("before") or ""
        after = body.get("after") or ""
        # Skip branch creates/deletes (all-zero SHAs).
        if not before or not after or before == _ZERO_SHA or after == _ZERO_SHA:
            return None
        return {"kind": "push", "repo": repo_full_name, "before": before, "after": after}

    return None


async def _store_event(normalized: dict) -> None:
    """Upsert a normalized event into the changestreams collection."""
    db = get_db()
    if db is None:
        return
    try:
        await db.changestreams.update_one(
            {"source_event_id": normalized["source_event_id"]},
            {"$setOnInsert": normalized},
            upsert=True,
        )
    except Exception:
        logger.exception("[GH-INGESTION] Failed to store event: %s", normalized.get("source_event_id"))


async def ingest_github_event(event_type: str, delivery_id: str, body: dict) -> None:
    """Normalize and store a GitHub webhook event. Safe for fire-and-forget.

    PR events (opened/synchronize/reopened/ready_for_review) are enriched with
    per-file diff patches fetched from the GitHub API.
    """
    try:
        normalized = normalize_github_event(event_type, delivery_id, body)
        if normalized is None:
            return
        # Enrich PR events with per-file diff so the dashboard can show what changed.
        diff_target = _should_fetch_diff(event_type, body)
        logger.info(
            "[GH-INGESTION] event_type=%s action=%s diff_target=%s",
            event_type, body.get("action"), diff_target,
        )
        if diff_target is not None:
            diff = await _fetch_diff(diff_target)
            if diff is not None:
                normalized["diff"] = diff
                # Fold diff text into `text` so semantic reasoning has one
                # unified field across all event sources.
                diff_text = _format_diff_as_text(diff)
                existing = (normalized.get("text") or "").strip()
                combined = f"{existing}\n\n{diff_text}" if existing else diff_text
                normalized["text"] = combined
                normalized["content_hash"] = _content_hash(combined)
                logger.info(
                    "[GH-INGESTION] attached diff target=%s files=%d +%d -%d",
                    diff_target, diff["total_files"], diff["total_additions"], diff["total_deletions"],
                )
            else:
                logger.info("[GH-INGESTION] no diff attached target=%s", diff_target)
        await _store_event(normalized)
        logger.debug("[GH-INGESTION] Stored %s event: %s", normalized["event_type"], normalized["source_event_id"])
    except Exception:
        logger.exception("[GH-INGESTION] Failed to ingest event delivery=%s", delivery_id)
