"""GitHub webhook handler for automatic PR code reviews.

Listens for pull_request events and triggers the code-review skill to analyze
changes and post review comments.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import ssl
import uuid
from datetime import datetime, timezone

import aiohttp
import certifi
from aiohttp import web
from dotenv import load_dotenv

from agent.client import stream_agent
from observability.db import get_db
from webhooks.github_ingestion import ingest_github_event
from observability.observer import ConversationObserver
from webhooks.linear_api import _graphql_request as linear_graphql_request
from webhooks.github_graphql import (
    dismiss_review,
    get_agent_review_threads,
    get_pr_comments,
    get_pr_reviews,
    minimize_comment,
    resolve_review_thread,
)
from observability.review_quality import process_human_review_for_quality

load_dotenv()

logger = logging.getLogger(__name__)

GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
GITHUB_API_KEY = os.environ.get("GITHUB_API_KEY", "")

# GitHub login of the bot account whose token we use. Historically this was
# hardcoded to "loma-agent" in multiple places, but the production token
# actually authenticates as "loma-insights" — which broke loop prevention,
# minimize-on-re-review, thread attribution, and reaction cleanup. Centralise
# it here and allow override per-deploy via AGENT_GITHUB_LOGIN.
AGENT_GITHUB_LOGIN = os.environ.get("AGENT_GITHUB_LOGIN", "loma-insights")

# Regex to match Linear ticket references (ISSUE-1234)
# Case-insensitive to support lowercase branch names
LINEAR_TICKET_PATTERN = re.compile(r'\b(ISSUE-\d+)\b', re.IGNORECASE)

# Reusable SSL context for GitHub API calls (certifi provides CA bundle for macOS)
_ssl_context = ssl.create_default_context(cafile=certifi.where())

# Marker to identify review comments posted by this agent (prevents loop)
AGENT_REVIEW_MARKER = "<!-- loma-agent-review -->"

def _review_in_progress_comment(estimated_time: str) -> str:
    """Generate status comment with estimated review time."""
    return f"""⏳ **Reviewing this PR...**

The agent is analyzing your changes. Estimated time: **{estimated_time}**.

<!-- loma-agent-status -->
"""

# Status comment shown when review is complete
REVIEW_COMPLETE_COMMENT = """✅ **Review complete**

<!-- loma-agent-status -->
"""

# Status comment shown when review fails
REVIEW_FAILED_COMMENT = """❌ **Review failed**

Something went wrong while analyzing this PR. Please check the logs or re-trigger the review.

<!-- loma-agent-status -->
"""

# Slash commands for quick actions on review comments
SLASH_COMMANDS = {
    "/approve": "approve_suggestion",
    "/dismiss": "dismiss_suggestion",
    "/fix": "apply_fix",
    "/explain": "explain_more",
    "/resolve": "mark_resolved",
    "/rereview": "trigger_review",
}


def _parse_slash_command(comment_body: str) -> tuple[str, str] | None:
    """Parse slash commands from comment body.

    Supports formats like:
    - `/rereview`
    - `@loma-agent /rereview`
    - `@loma-agent /explain why is this needed?`

    Args:
        comment_body: The comment text.

    Returns:
        Tuple of (command_action, remaining_text) or None if no command found.
    """
    # Sort commands by length (longest first) to match /rereview before /re, etc.
    sorted_commands = sorted(SLASH_COMMANDS.items(), key=lambda x: -len(x[0]))

    for cmd, action in sorted_commands:
        # Use regex to match command as a whole word (not as substring)
        # Command must be preceded by start/whitespace and followed by whitespace/end
        pattern = rf'(?:^|\s)({re.escape(cmd)})(?:\s|$)'
        match = re.search(pattern, comment_body, re.IGNORECASE)
        if match:
            # Extract text after the command as arguments
            cmd_end = match.end(1)
            after_cmd = comment_body[cmd_end:].strip()
            # Remove any trailing @mentions or markers
            remaining = after_cmd.split('\n')[0].strip()  # Just first line
            return (action, remaining)
    return None


def _generate_handoff_context(
    repo_full_name: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    base_branch: str,
    head_branch: str,
    review_summary: str = "",
) -> str:
    """Generate a copyable context block for manual intervention.

    Args:
        repo_full_name: Full repo name (owner/repo).
        pr_number: PR number.
        pr_title: PR title.
        pr_url: PR URL.
        base_branch: Base branch name.
        head_branch: Head branch name.
        review_summary: Summary from the agent review (if available).

    Returns:
        Markdown formatted context block.
    """
    context = f"""## Manual Review Required

**PR**: [{pr_title}]({pr_url})
**Repository**: {repo_full_name}
**Branches**: `{head_branch}` → `{base_branch}`

### Quick Start Commands
```bash
# Checkout the PR locally
gh pr checkout {pr_number} --repo {repo_full_name}

# Open in your editor
cursor .
# or
code .

# Start Claude Code for AI assistance
claude
```
"""
    if review_summary:
        context += f"""
### Agent Review Summary
{review_summary}
"""

    context += f"""
### Context for AI Assistant
```
Review this PR: {pr_url}

Repository: {repo_full_name}
PR #{pr_number}: {pr_title}
Branch: {head_branch} -> {base_branch}

Please analyze the changes and help resolve any outstanding issues.
```
"""
    return context


def _review_needs_handoff_comment(context: str) -> str:
    """Generate status comment with manual handoff context."""
    return f"""⚠️ **Review needs human attention**

The automated review encountered issues that require manual intervention.

<details>
<summary>📋 Click to copy context for AI assistant</summary>

{context}
</details>

<!-- loma-agent-status -->
"""


async def _create_pr_comment(repo_owner: str, repo_name: str, pr_number: int, body: str) -> int | None:
    """Create a comment on a PR. Returns the comment ID or None on failure."""
    if not GITHUB_API_KEY:
        logger.warning("[GITHUB-WEBHOOK] No GITHUB_API_KEY set, skipping status comment")
        return None

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(url, headers=headers, json={"body": body}) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    comment_id = data.get("id")
                    logger.info("[GITHUB-WEBHOOK] Created status comment %d on PR %d", comment_id, pr_number)
                    return comment_id
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to create comment: %d %s", resp.status, text[:200])
                    return None
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error creating comment: %s", e)
        return None


async def _react_to_comment(repo_owner: str, repo_name: str, comment_id: int, reaction: str = "eyes") -> bool:
    """Add a reaction to a PR/issue comment. Returns True on success.

    Valid reactions: +1, -1, laugh, confused, heart, hooray, rocket, eyes
    """
    if not GITHUB_API_KEY or not comment_id:
        return False

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}/reactions"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(url, headers=headers, json={"content": reaction}) as resp:
                if resp.status in (200, 201):
                    logger.info("[GITHUB-WEBHOOK] Added %s reaction to comment %d", reaction, comment_id)
                    return True
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to add reaction: %d %s", resp.status, text[:200])
                    return False
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error adding reaction: %s", e)
        return False


async def _update_pr_comment(repo_owner: str, repo_name: str, comment_id: int, body: str) -> bool:
    """Update a PR comment. Returns True on success."""
    if not GITHUB_API_KEY or not comment_id:
        return False

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.patch(url, headers=headers, json={"body": body}) as resp:
                if resp.status == 200:
                    logger.info("[GITHUB-WEBHOOK] Updated comment %d", comment_id)
                    return True
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to update comment: %d %s", resp.status, text[:200])
                    return False
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error updating comment: %s", e)
        return False


async def _delete_pr_comment(repo_owner: str, repo_name: str, comment_id: int) -> bool:
    """Delete a PR comment. Returns True on success."""
    if not GITHUB_API_KEY or not comment_id:
        return False

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.delete(url, headers=headers) as resp:
                if resp.status == 204:
                    logger.info("[GITHUB-WEBHOOK] Deleted status comment %d", comment_id)
                    return True
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to delete comment: %d %s", resp.status, text[:200])
                    return False
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error deleting comment: %s", e)
        return False


async def _remove_reaction(repo_owner: str, repo_name: str, comment_id: int, reaction: str = "eyes") -> bool:
    """Remove the bot's reaction from a PR/issue comment. Returns True on success.

    First lists reactions to find the reaction ID for the bot user, then deletes it.
    """
    if not GITHUB_API_KEY or not comment_id:
        return False

    list_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}/reactions"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(list_url, headers=headers) as resp:
                if resp.status != 200:
                    logger.error("[GITHUB-WEBHOOK] Failed to list reactions: %d", resp.status)
                    return False
                reactions = await resp.json()

            reaction_id = None
            for r in reactions:
                if r.get("content") == reaction and r.get("user", {}).get("login") == AGENT_GITHUB_LOGIN:
                    reaction_id = r.get("id")
                    break

            if not reaction_id:
                logger.info("[GITHUB-WEBHOOK] No %s reaction by bot found on comment %d", reaction, comment_id)
                return False

            delete_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}/reactions/{reaction_id}"
            async with session.delete(delete_url, headers=headers) as resp:
                if resp.status == 204:
                    logger.info("[GITHUB-WEBHOOK] Removed %s reaction from comment %d", reaction, comment_id)
                    return True
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to remove reaction: %d %s", resp.status, text[:200])
                    return False
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error removing reaction: %s", e)
        return False


async def _react_to_review_comment(repo_owner: str, repo_name: str, comment_id: int, reaction: str = "eyes") -> bool:
    """Add a reaction to a PR review comment (different API from issue comments).

    Valid reactions: +1, -1, laugh, confused, heart, hooray, rocket, eyes
    """
    if not GITHUB_API_KEY or not comment_id:
        return False

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/comments/{comment_id}/reactions"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(url, headers=headers, json={"content": reaction}) as resp:
                if resp.status in (200, 201):
                    logger.info("[GITHUB-WEBHOOK] Added %s reaction to review comment %d", reaction, comment_id)
                    return True
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to add reaction to review comment: %d %s", resp.status, text[:200])
                    return False
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error adding reaction to review comment: %s", e)
        return False


async def _remove_review_comment_reaction(repo_owner: str, repo_name: str, comment_id: int, reaction: str = "eyes") -> bool:
    """Remove the bot's reaction from a PR review comment."""
    if not GITHUB_API_KEY or not comment_id:
        return False

    list_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/comments/{comment_id}/reactions"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(list_url, headers=headers) as resp:
                if resp.status != 200:
                    return False
                reactions = await resp.json()

            reaction_id = None
            for r in reactions:
                if r.get("content") == reaction and r.get("user", {}).get("login") == AGENT_GITHUB_LOGIN:
                    reaction_id = r.get("id")
                    break

            if not reaction_id:
                return False

            delete_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/comments/{comment_id}/reactions/{reaction_id}"
            async with session.delete(delete_url, headers=headers) as resp:
                if resp.status == 204:
                    logger.info("[GITHUB-WEBHOOK] Removed %s reaction from review comment %d", reaction, comment_id)
                    return True
                return False
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error removing review comment reaction: %s", e)
        return False


async def _get_pr_stats(repo_owner: str, repo_name: str, pr_number: int) -> dict | None:
    """Fetch PR stats (additions, deletions, changed_files). Returns None on failure."""
    pr_data = await _get_pr_details(repo_owner, repo_name, pr_number)
    if not pr_data:
        return None
    return {
        "additions": pr_data.get("additions", 0),
        "deletions": pr_data.get("deletions", 0),
        "changed_files": pr_data.get("changed_files", 0),
    }


def _estimate_review_time(stats: dict | None) -> str:
    """Estimate review time based on PR stats."""
    if not stats:
        return "1-2 minutes"

    total_changes = stats.get("additions", 0) + stats.get("deletions", 0)
    changed_files = stats.get("changed_files", 0)

    # Rough heuristic: base time + time per file + time per 100 lines
    if total_changes < 50 and changed_files <= 3:
        return "~1 minute"
    elif total_changes < 200 and changed_files <= 5:
        return "1-2 minutes"
    elif total_changes < 500 and changed_files <= 10:
        return "2-3 minutes"
    elif total_changes < 1000:
        return "3-5 minutes"
    else:
        return "5+ minutes"


async def _create_check_run(
    repo_owner: str,
    repo_name: str,
    head_sha: str,
    name: str = "Review Agent",
    status: str = "in_progress",
) -> int | None:
    """Create a GitHub Check Run. Returns the check_run_id or None on failure.

    Args:
        repo_owner: Repository owner.
        repo_name: Repository name.
        head_sha: The commit SHA to associate the check with.
        name: Name of the check run.
        status: Initial status ("queued", "in_progress").

    Returns:
        The check_run_id if created, None on failure.
    """
    if not GITHUB_API_KEY:
        logger.warning("[GITHUB-WEBHOOK] No GITHUB_API_KEY set, skipping check run")
        return None

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/check-runs"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "name": name,
        "head_sha": head_sha,
        "status": status,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    check_run_id = data.get("id")
                    logger.info(
                        "[GITHUB-WEBHOOK] Created check run %d for %s/%s@%s",
                        check_run_id, repo_owner, repo_name, head_sha[:7],
                    )
                    return check_run_id
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to create check run: %d %s", resp.status, text[:200])
                    return None
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error creating check run: %s", e)
        return None


async def _update_check_run(
    repo_owner: str,
    repo_name: str,
    check_run_id: int,
    status: str = "completed",
    conclusion: str | None = None,
    title: str | None = None,
    summary: str | None = None,
) -> bool:
    """Update a GitHub Check Run. Returns True on success.

    Args:
        repo_owner: Repository owner.
        repo_name: Repository name.
        check_run_id: The check run ID to update.
        status: New status ("queued", "in_progress", "completed").
        conclusion: Required if status is "completed". One of:
            "action_required", "cancelled", "failure", "neutral",
            "success", "skipped", "stale", "timed_out".
        title: Title for the check run output.
        summary: Summary markdown for the check run output.

    Returns:
        True if updated successfully, False otherwise.
    """
    if not GITHUB_API_KEY or not check_run_id:
        return False

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/check-runs/{check_run_id}"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload: dict = {"status": status}

    if status == "completed":
        payload["conclusion"] = conclusion or "neutral"
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()

    if title or summary:
        payload["output"] = {
            "title": title or "Review Complete",
            "summary": summary or "",
        }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.patch(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    logger.info(
                        "[GITHUB-WEBHOOK] Updated check run %d: status=%s, conclusion=%s",
                        check_run_id, status, conclusion,
                    )
                    return True
                else:
                    text = await resp.text()
                    logger.error("[GITHUB-WEBHOOK] Failed to update check run: %d %s", resp.status, text[:200])
                    return False
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error updating check run: %s", e)
        return False


def _verify_signature(signature_header: str | None, raw_body: bytes) -> bool:
    """Verify the GitHub webhook signature using HMAC-SHA256.

    GitHub signs webhooks by computing HMAC-SHA256(secret, raw_body) and sending
    the hex digest in the 'X-Hub-Signature-256' header with 'sha256=' prefix.
    """
    if not GITHUB_WEBHOOK_SECRET or not signature_header:
        return False

    if not signature_header.startswith("sha256="):
        return False

    try:
        header_sig = bytes.fromhex(signature_header[7:])  # Skip 'sha256=' prefix
    except ValueError:
        return False

    computed_sig = hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).digest()

    return hmac.compare_digest(computed_sig, header_sig)


async def handle_github_webhook(request: web.Request) -> web.Response:
    """Handle incoming GitHub webhook notifications."""
    raw_body = await request.read()

    # Verify HMAC-SHA256 signature
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(signature, raw_body):
        logger.warning("[GITHUB-WEBHOOK] Invalid signature — rejecting request")
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        body = json.loads(raw_body)
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Failed to parse JSON: %s", e)
        logger.error("[GITHUB-WEBHOOK] Raw body (first 500 chars): %s", raw_body[:500] if raw_body else "(empty)")
        logger.error("[GITHUB-WEBHOOK] Content-Type: %s", request.headers.get("Content-Type", "(not set)"))
        return web.json_response({"error": "Invalid JSON", "details": str(e)}, status=400)

    event_type = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    logger.info(
        "[GITHUB-WEBHOOK] Received: event=%s, delivery=%s",
        event_type, delivery_id,
    )

    # Fire-and-forget: ingest every webhook event for observability
    asyncio.create_task(ingest_github_event(event_type, delivery_id, body))

    # Handle pull_request events
    if event_type == "pull_request":
        action = body.get("action", "")
        pr = body.get("pull_request", {})
        repo = body.get("repository", {})

        # Only review on these actions
        if action not in ("opened", "synchronize", "ready_for_review", "reopened"):
            logger.info(
                "[GITHUB-WEBHOOK] Ignoring PR action=%s (not reviewable)", action
            )
            return web.json_response({"status": "ignored", "reason": "action_not_reviewable"})

        # Skip draft PRs (unless they become ready_for_review)
        if pr.get("draft", False) and action != "ready_for_review":
            logger.info("[GITHUB-WEBHOOK] Ignoring draft PR")
            return web.json_response({"status": "ignored", "reason": "draft_pr"})

        # Skip PRs created by bots (including ourselves)
        pr_author = pr.get("user", {}).get("login", "")
        if pr_author.endswith("[bot]") or pr_author == AGENT_GITHUB_LOGIN:
            logger.info("[GITHUB-WEBHOOK] Ignoring bot-created PR by %s", pr_author)
            return web.json_response({"status": "ignored", "reason": "bot_pr"})

        pr_number = pr.get("number")
        pr_title = pr.get("title", "")
        pr_url = pr.get("html_url", "")
        repo_name = repo.get("name", "")
        repo_full_name = repo.get("full_name", "")
        base_sha = pr.get("base", {}).get("sha", "")
        head_sha = pr.get("head", {}).get("sha", "")
        base_branch = pr.get("base", {}).get("ref", "")
        head_branch = pr.get("head", {}).get("ref", "")

        logger.info(
            "[GITHUB-WEBHOOK] Trigger: PR %s on %s — %s (action=%s)",
            pr_number, repo_full_name, pr_title, action,
        )

        conversation_id = str(uuid.uuid4())

        asyncio.create_task(
            _process_pr_review(
                repo_owner="example-org",
                repo_name=repo_name,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                pr_title=pr_title,
                pr_url=pr_url,
                base_sha=base_sha,
                head_sha=head_sha,
                base_branch=base_branch,
                head_branch=head_branch,
                action=action,
                pr_author=pr_author,
                conversation_id=conversation_id,
            )
        )

        return web.json_response({
            "status": "accepted",
            "trigger": f"pull_request_{action}",
            "pr_number": pr_number,
            "conversation_id": conversation_id,
        })

    # Handle pull_request_review_comment events (for follow-up discussions)
    if event_type == "pull_request_review_comment":
        action = body.get("action", "")
        comment = body.get("comment", {})
        comment_body = comment.get("body", "")

        # Skip comments from bots (including ourselves) - primary loop prevention
        comment_author = comment.get("user", {}).get("login", "")
        if comment_author.endswith("[bot]") or comment_author == AGENT_GITHUB_LOGIN:
            logger.info("[GITHUB-WEBHOOK] Ignoring bot review comment by %s", comment_author)
            return web.json_response({"status": "ignored", "reason": "bot_comment"})

        # Ignore our own comments (secondary loop prevention via marker)
        if AGENT_REVIEW_MARKER in comment_body:
            logger.info("[GITHUB-WEBHOOK] Ignoring own review comment (loop prevention)")
            return web.json_response({"status": "ignored", "reason": "self_comment"})

        # Only respond to new comments that mention the agent
        if action == "created" and "@loma-agent" in comment_body.lower():
            pr = body.get("pull_request", {})
            repo = body.get("repository", {})

            pr_number = pr.get("number")
            repo_name = repo.get("name", "")
            repo_full_name = repo.get("full_name", "")

            logger.info(
                "[GITHUB-WEBHOOK] Review comment mentioning agent on PR %s/%s",
                repo_full_name, pr_number,
            )

            conversation_id = str(uuid.uuid4())

            asyncio.create_task(
                _process_review_comment_reply(
                    repo_owner="example-org",
                    repo_name=repo_name,
                    pr_number=pr_number,
                    comment_id=comment.get("id"),
                    comment_body=comment_body,
                    comment_path=comment.get("path", ""),
                    comment_line=comment.get("line"),
                    conversation_id=conversation_id,
                    comment_author=comment_author,
                )
            )

            return web.json_response({
                "status": "accepted",
                "trigger": "review_comment_mention",
            })

    # Handle pull_request_review events (for human review quality tracking)
    if event_type == "pull_request_review":
        action = body.get("action", "")
        review = body.get("review", {})
        pr = body.get("pull_request", {})
        repo = body.get("repository", {})

        # Only track submitted reviews
        if action == "submitted":
            reviewer = review.get("user", {}).get("login", "")
            review_state = review.get("state", "")  # APPROVED, CHANGES_REQUESTED, COMMENTED

            # Skip reviews from bots (including ourselves)
            if reviewer.endswith("[bot]") or reviewer == AGENT_GITHUB_LOGIN:
                logger.info("[GITHUB-WEBHOOK] Ignoring bot review by %s", reviewer)
                return web.json_response({"status": "ignored", "reason": "bot_review"})

            pr_number = pr.get("number")
            repo_full_name = repo.get("full_name", "")

            logger.info(
                "[GITHUB-WEBHOOK] Human review submitted on %s#%d by %s (state=%s)",
                repo_full_name, pr_number, reviewer, review_state,
            )

            # Process quality assessment asynchronously
            asyncio.create_task(
                _track_human_review(
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    reviewer=reviewer,
                    review_state=review_state,
                    review_body=review.get("body") or "",
                    submitted_at=review.get("submitted_at"),
                )
            )

            return web.json_response({
                "status": "accepted",
                "trigger": "human_review_submitted",
                "reviewer": reviewer,
                "state": review_state,
            })

    # Handle issue_comment events (for @mentions in PR conversation)
    # Note: PRs are technically issues in GitHub, so PR conversation comments fire issue_comment
    if event_type == "issue_comment":
        action = body.get("action", "")
        comment = body.get("comment", {})
        comment_body = comment.get("body", "")
        issue = body.get("issue", {})

        # Skip comments from bots (including ourselves) - primary loop prevention
        comment_author = comment.get("user", {}).get("login", "")
        if comment_author.endswith("[bot]") or comment_author == AGENT_GITHUB_LOGIN:
            logger.info("[GITHUB-WEBHOOK] Ignoring bot comment by %s", comment_author)
            return web.json_response({"status": "ignored", "reason": "bot_comment"})

        # Ignore our own comments (secondary loop prevention via marker)
        if AGENT_REVIEW_MARKER in comment_body or "<!-- loma-agent-status -->" in comment_body:
            logger.info("[GITHUB-WEBHOOK] Ignoring own issue comment (loop prevention)")
            return web.json_response({"status": "ignored", "reason": "self_comment"})

        # Only handle comments on PRs (not regular issues)
        # PRs have a "pull_request" key in the issue object
        if "pull_request" not in issue:
            logger.info("[GITHUB-WEBHOOK] Ignoring issue comment (not a PR)")
            return web.json_response({"status": "ignored", "reason": "not_pr"})

        # Only respond to new comments that mention the agent
        if action == "created" and "@loma-agent" in comment_body.lower():
            repo = body.get("repository", {})
            pr_number = issue.get("number")
            repo_name = repo.get("name", "")
            repo_full_name = repo.get("full_name", "")

            logger.info(
                "[GITHUB-WEBHOOK] PR conversation comment mentioning agent on PR %s#%d",
                repo_full_name, pr_number,
            )

            conversation_id = str(uuid.uuid4())

            asyncio.create_task(
                _process_pr_conversation_comment(
                    repo_owner="example-org",
                    repo_name=repo_name,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    comment_id=comment.get("id"),
                    comment_body=comment_body,
                    comment_author=comment.get("user", {}).get("login", ""),
                    conversation_id=conversation_id,
                )
            )

            return web.json_response({
                "status": "accepted",
                "trigger": "pr_conversation_mention",
            })

    logger.info(
        "[GITHUB-WEBHOOK] Unhandled event type=%s — ignoring", event_type
    )
    return web.json_response({"status": "ignored", "reason": "unhandled_event"})



async def _minimize_previous_agent_comments(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    agent_login: str = AGENT_GITHUB_LOGIN,
) -> int:
    """Minimize (collapse) previous agent review comments and status comments on a PR.

    Before posting a new review, this function finds all previous agent comments
    (both PR conversation comments with agent markers and PR review bodies) and
    minimizes them as 'OUTDATED' so the PR conversation isn't flooded with
    stale review messages.

    Args:
        repo_owner: Repository owner.
        repo_name: Repository name.
        pr_number: Pull request number.
        agent_login: GitHub login of the agent (defaults to AGENT_GITHUB_LOGIN).

    Returns:
        Number of comments successfully minimized.
    """
    minimized_count = 0

    try:
        # Minimize PR conversation comments (status comments and agent replies)
        pr_comments = await get_pr_comments(repo_owner, repo_name, pr_number)
        for comment in pr_comments:
            # Skip already-minimized comments
            if comment.get("is_minimized"):
                continue

            body = comment.get("body", "")
            author = comment.get("author", "")
            node_id = comment.get("id")

            if not node_id:
                continue

            # Minimize if it's an agent comment (status or review marker)
            is_agent_comment = (
                author == agent_login
                and (
                    "<!-- loma-agent-status -->" in body
                    or AGENT_REVIEW_MARKER in body
                )
            )

            if is_agent_comment:
                success = await minimize_comment(node_id, "OUTDATED")
                if success:
                    minimized_count += 1

        # Minimize PR review bodies posted by the agent
        pr_reviews = await get_pr_reviews(repo_owner, repo_name, pr_number)
        for review in pr_reviews:
            body = review.get("body", "")
            author = review.get("author", "")
            node_id = review.get("id")
            state = review.get("state", "")

            if not node_id:
                continue

            # Skip already-dismissed reviews
            if state == "DISMISSED":
                continue

            # Minimize if it's an agent review with our marker
            is_agent_review = (
                author == agent_login
                and AGENT_REVIEW_MARKER in body
            )

            if is_agent_review:
                success = await minimize_comment(node_id, "OUTDATED")
                if success:
                    minimized_count += 1

    except Exception as e:
        logger.warning(
            "[GITHUB-WEBHOOK] Failed to minimize previous agent comments on %s/%s#%d: %s",
            repo_owner, repo_name, pr_number, e,
        )

    if minimized_count > 0:
        logger.info(
            "[GITHUB-WEBHOOK] Minimized %d previous agent comment(s) on %s/%s#%d",
            minimized_count, repo_owner, repo_name, pr_number,
        )

    return minimized_count


async def _snapshot_stale_agent_request_changes_ids(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    agent_login: str = AGENT_GITHUB_LOGIN,
) -> list[str]:
    """Return the GraphQL node IDs of the agent's currently-open CHANGES_REQUESTED reviews.

    Must be called BEFORE the agent posts its new review so the returned IDs
    refer only to pre-existing stale reviews — never to the fresh review the
    agent may be about to post in this same run.
    """
    try:
        reviews = await get_pr_reviews(repo_owner, repo_name, pr_number)
    except Exception as e:
        logger.warning(
            "[GITHUB-WEBHOOK] Failed to snapshot stale reviews on %s/%s#%d: %s",
            repo_owner, repo_name, pr_number, e,
        )
        return []

    return [
        review["id"]
        for review in reviews
        if review.get("author") == agent_login
        and review.get("state") == "CHANGES_REQUESTED"
        and review.get("id")
    ]


async def _dismiss_reviews_by_id(
    review_ids: list[str],
    message: str = "Superseded by re-evaluation on new commits",
) -> int:
    """Dismiss a specific set of PR reviews by GraphQL node ID.

    Scoped to IDs captured before the current agent run, so the agent's own
    fresh review posted during the run is never accidentally dismissed.

    Returns the number of reviews successfully dismissed.
    """
    dismissed = 0
    for node_id in review_ids:
        try:
            if await dismiss_review(node_id, message=message):
                dismissed += 1
        except Exception as e:
            logger.warning(
                "[GITHUB-WEBHOOK] Failed to dismiss review %s: %s", node_id, e
            )
    if dismissed:
        logger.info(
            "[GITHUB-WEBHOOK] Dismissed %d stale agent review(s)", dismissed
        )
    return dismissed


def _format_prior_threads_block(threads: list[dict], head_sha: str) -> str:
    """Render unresolved prior agent threads into a prompt section.

    Each entry gives the agent the thread_id it needs to pass to the
    `tools/github_pr_resolve.py resolve` CLI once it has verified a fix.
    """
    if not threads:
        return ""

    lines = [
        "## Prior Unresolved Issues You Flagged (re-evaluate each)",
        "",
        (
            "These are open review threads you authored on earlier commits of this "
            f"PR. The head is now {head_sha[:7]}. For each thread, fetch the current "
            "content at the given file+line and decide:"
        ),
        "",
        "- **FIXED**: the concern no longer applies (code changed, line removed, or the issue was genuinely addressed)",
        "- **STILL_BROKEN**: the issue remains in the latest commit",
        "- **OBSOLETE**: the surrounding code was deleted or rewritten such that the thread is no longer meaningful",
        "",
        "For every FIXED or OBSOLETE thread, run this from the repo root:",
        "",
        "```",
        "python3 tools/github_pr_resolve.py resolve --thread-id <thread_id>",
        "```",
        "",
        "Do **not** open a new inline comment duplicating a STILL_BROKEN thread — the existing thread is enough.",
        "",
        "Unresolved threads:",
        "",
    ]
    for t in threads:
        comments = t.get("comments") or []
        first_body = comments[0].get("body", "") if comments else ""
        snippet = first_body.strip().replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:297] + "…"
        lines.append(
            f"- `thread_id={t.get('id')}` — {t.get('path')}:{t.get('line')} — {snippet}"
        )
    lines.append("")
    return "\n".join(lines)


async def _process_pr_review(
    repo_owner: str,
    repo_name: str,
    repo_full_name: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    base_sha: str,
    head_sha: str,
    base_branch: str,
    head_branch: str,
    action: str,
    pr_author: str,
    conversation_id: str,
    force_review: bool = False,
    pr_stats: dict | None = None,
):
    """Run the agent to review a pull request and post review comments."""
    logger.info(
        "[GITHUB-WEBHOOK] Processing PR review: %s#%d — %s (force=%s)",
        repo_full_name, pr_number, pr_title, force_review,
    )

    # Check for duplicate processing (avoid reviewing same commit twice)
    # Skip this check if force_review is True (explicit re-review request)
    db = get_db()
    if db is not None and not force_review:
        existing = await db.conversations.find_one({
            "metadata.github_pr_number": pr_number,
            "metadata.github_repo": repo_full_name,
            "metadata.github_head_sha": head_sha,
            "source": "github_webhook",
            "status": {"$in": ["running", "completed"]},
        })
        if existing:
            logger.info(
                "[GITHUB-WEBHOOK] PR %s#%d at commit %s already reviewed — skipping",
                repo_full_name, pr_number, head_sha[:7],
            )
            return

    # Fetch PR stats and estimate review time (use passed-in stats if available)
    if pr_stats is None:
        pr_stats = await _get_pr_stats(repo_owner, repo_name, pr_number)
    estimated_time = _estimate_review_time(pr_stats)

    # Try to fetch Linear ticket context from branch name or PR title
    # Wrapped in try-except to ensure Linear failures don't crash the review
    linear_context = ""
    ticket_id = _extract_linear_ticket(head_branch, pr_title)
    if ticket_id:
        try:
            logger.info("[GITHUB-WEBHOOK] Found Linear ticket reference: %s", ticket_id)
            ticket_details = await _fetch_linear_ticket(ticket_id)
            if ticket_details:
                linear_context = _format_linear_context(ticket_details)
        except Exception as e:
            logger.warning("[GITHUB-WEBHOOK] Failed to fetch Linear ticket %s: %s", ticket_id, e)

    # Fetch prior unresolved agent review threads so the agent can re-evaluate
    # each one against the latest commit instead of starting from scratch.
    # This is what turns a "synchronize" or "/rereview" run into a proper
    # re-review rather than a duplicate initial review.
    prior_unresolved_threads: list[dict] = []
    try:
        agent_threads = await get_agent_review_threads(
            repo_owner, repo_name, pr_number
        )
        prior_unresolved_threads = [
            t for t in agent_threads if not t.get("is_resolved")
        ]
    except Exception as e:
        logger.warning(
            "[GITHUB-WEBHOOK] Failed to fetch prior agent threads on %s#%d: %s",
            repo_full_name, pr_number, e,
        )

    is_reevaluation = bool(prior_unresolved_threads)
    if is_reevaluation:
        logger.info(
            "[GITHUB-WEBHOOK] Re-evaluation mode on %s#%d — %d unresolved prior thread(s)",
            repo_full_name, pr_number, len(prior_unresolved_threads),
        )

    # Snapshot any pre-existing CHANGES_REQUESTED review IDs BEFORE the agent
    # runs. If we queried after the run, we would race the agent's own fresh
    # review and could dismiss it by mistake.
    stale_request_changes_ids: list[str] = []
    if is_reevaluation:
        stale_request_changes_ids = (
            await _snapshot_stale_agent_request_changes_ids(
                repo_owner, repo_name, pr_number
            )
        )

    # Minimize previous agent review comments/status messages to reduce PR noise.
    # Note: this only collapses issue comments and prior review summary bodies.
    # Inline thread comments stay visible so the agent can re-evaluate them.
    await _minimize_previous_agent_comments(repo_owner, repo_name, pr_number)

    # Post a status comment to let the user know a review is in progress
    status_comment_id = await _create_pr_comment(
        repo_owner, repo_name, pr_number, _review_in_progress_comment(estimated_time)
    )

    # Create a GitHub Check Run to show progress in PR UI
    check_run_id = await _create_check_run(
        repo_owner, repo_name, head_sha,
        name="Review Agent",
        status="in_progress",
    )

    # Build the prompt for the agent
    mode_header = (
        "## Mode: RE-EVALUATION\n\n"
        "This PR already has unresolved review threads you authored on earlier "
        "commits. Treat this run like a human reviewer returning to a PR after "
        "the author pushed fixes: **re-evaluate each prior thread first**, then "
        "look for new issues introduced by the latest commits.\n"
        if is_reevaluation
        else "## Mode: INITIAL REVIEW\n\nThis is the first review of this PR.\n"
    )

    prompt_parts = [
        f"Review PR #{pr_number} on {repo_full_name}.",
        "",
        mode_header,
        "## IMPORTANT: First Steps (MUST DO)",
        "",
        "Before anything else, you MUST:",
        "1. Use the `Skill` tool to load `code-review` - this contains the review methodology",
        "2. Check for repo-specific rules by fetching `.agent/skills/review/SKILL.md` from the repo",
        "",
        f"## PR Details",
        f"- **Repository**: {repo_full_name}",
        f"- **PR Number**: #{pr_number}",
        f"- **Title**: {pr_title}",
        f"- **URL**: {pr_url}",
        f"- **Author**: {pr_author}",
        f"- **Base Branch**: {base_branch} ({base_sha[:7]})",
        f"- **Head Branch**: {head_branch} ({head_sha[:7]})",
        f"- **Trigger Action**: {action}",
        "",
    ]

    if is_reevaluation:
        prompt_parts.append(
            _format_prior_threads_block(prior_unresolved_threads, head_sha)
        )
        prompt_parts.extend([
            "## Re-evaluation Workflow (do this BEFORE the normal review workflow)",
            "",
            "1. For each `thread_id` in the list above, fetch the current content "
            "of `<path>` at `<line>` on the head branch.",
            "2. Decide FIXED / STILL_BROKEN / OBSOLETE.",
            "3. For every FIXED or OBSOLETE thread, run from the repo root:",
            "   `python3 tools/github_pr_resolve.py resolve --thread-id <thread_id>`",
            "   Do this BEFORE you call `mcp__github__create_pull_request_review` "
            "— once you submit the final review, further resolves are still valid "
            "but posting a new inline comment on a line that already has a "
            "STILL_BROKEN thread is duplication.",
            "4. For STILL_BROKEN threads: do NOT create a new inline comment "
            "duplicating the same concern. The existing thread is enough. You "
            "MAY use `mcp__github__create_pull_request_review_comment_reply` to "
            "add a short note like 'Still present at line N' if helpful.",
            "5. Only add NEW inline comments for issues introduced by commits "
            f"since your last review (new code between the previous head and "
            f"{head_sha[:7]}).",
            "",
        ])

    prompt_parts.extend([
        "## Review Workflow",
        "",
        "After loading the skill, follow this workflow:",
        "",
        "1. **Get the list of changed files first:**",
        f"   `mcp__github__list_pull_request_files` with owner=`{repo_owner}`, repo=`{repo_name}`, pull_number={pr_number}",
        "   This tells you which files changed and how many lines.",
        "",
        "2. **Prioritize HIGH-RISK files** (review these FIRST and THOROUGHLY):",
        "   - Files in `components/common/` or `src/components/common/`",
        "   - Files with `Input`, `Editor`, `Picker`, `Modal`, `Form` in the name",
        "   - Files that are imported by many other files (shared utilities)",
        "   - For these files, fetch the FULL content (not just diff) to understand the API",
        "",
        "3. **Fetch the PR diff:**",
        f"   `mcp__github__get_pull_request_diff` with owner=`{repo_owner}`, repo=`{repo_name}`, pull_number={pr_number}",
        "",
        "4. **CRITICAL: Always read COMPLETE file contents.**",
        "   - GitHub's get_file_contents may truncate large files",
        "   - If you see `... (truncated)` or incomplete code, the file was cut off",
        "   - For truncated files: use `mcp__github__get_file_contents` with `ref` parameter set to the HEAD branch",
        "   - You MUST see the ENTIRE file before reviewing - missing context leads to false positives",
        "   - If a file appears incomplete (missing closing braces, imports without usage), re-fetch it",
        "",
        "5. **Fetch repo-specific review rules:**",
        f"   `mcp__github__get_file_contents` with owner=`{repo_owner}`, repo=`{repo_name}`, path=`.agent/skills/review/SKILL.md`",
        "",
        "6. **For each high-risk file, CHECK FOR BREAKING CHANGES:**",
        "   - Compare the old vs new API (props, callbacks, return types)",
        "   - Use `mcp__github__search_code` to find consumers of changed components",
        "   - Example: search for `import.*ComponentName` or `<ComponentName` in the repo",
        "   - If onChange/value/defaultValue props changed, this is likely BLOCKING",
        "   - If callback signature changed (string vs object), list ALL affected consumers",
        "",
        "7. **Analyze thoroughly** following the code-review skill's 4-phase methodology:",
        "   - Phase 1: Context Gathering (done above)",
        "   - Phase 2: High-Level Review (architecture, patterns)",
        "   - Phase 3: Line-by-Line Review (API contracts FIRST, then bugs, then style)",
        "   - Phase 4: Summary & Decision",
        "",
        "   **IMPORTANT**: Before flagging missing code/functions, verify you have the COMPLETE file.",
        "   If you only see part of a file, DO NOT claim code is missing - re-fetch the full file first.",
        "",
        "8. **Post the review** using `mcp__github__create_pull_request_review`:",
        f"   - owner: `{repo_owner}`",
        f"   - repo: `{repo_name}`",
        f"   - pull_number: {pr_number}",
        "   - event:",
        "       - `APPROVE` if **no** 🔴 BLOCKING issues remain (counting prior "
        "STILL_BROKEN threads plus any new blocking issues you found). On a "
        "re-evaluation run where every prior blocking thread is now FIXED and "
        "no new blocking issues exist, you **should** APPROVE.",
        "       - `REQUEST_CHANGES` if any 🔴 BLOCKING issues remain.",
        "       - `COMMENT` if no 🔴 but you still have 🟡/🟢 to surface.",
        "   - body: Review summary with severity-grouped findings. On re-evaluation, "
        "include a short 'Re-evaluation summary' section listing resolved vs "
        "still-open prior threads so the author can see what moved.",
        "   - comments: Inline comments on specific lines. Do NOT duplicate "
        "STILL_BROKEN prior threads — only NEW issues go here.",
        "",
        "## Review Priorities (most important first)",
        "",
        "1. 🔴 **BLOCKING**: Security issues, bugs, crashes, data loss risks",
        "2. 🔴 **BLOCKING**: Breaking changes, API contract violations",
        "3. 🟡 **SUGGESTION**: Performance issues, missing error handling",
        "4. 🟡 **SUGGESTION**: Architectural concerns, maintainability",
        "5. 🟢 **NIT**: Style issues (only if no linter handles them)",
        "",
        "## IMPORTANT: Preexisting vs New Issues",
        "",
        "For each issue you find, determine if it's NEW or PREEXISTING:",
        "",
        "- **INTRODUCED**: Code added or modified in this PR → full review severity",
        "- **PREEXISTING**: Issue existed in base branch before this PR → note in summary, DO NOT block",
        "",
        "**How to check**:",
        f"1. Fetch the base branch version: `mcp__github__get_file_contents` with ref=`{base_branch}`",
        "2. Compare the flagged code against the base version",
        "3. If the problematic code is identical in base, mark as **[PREEXISTING]**",
        "",
        "**Handling**:",
        "- INTRODUCED issues: Include with full severity, block if critical",
        "- PREEXISTING issues: Add to summary section as '📋 Preexisting Issues (not blocking)'",
        "  - Do NOT add inline comments for preexisting issues",
        "  - Do NOT use REQUEST_CHANGES just for preexisting issues",
        "",
        f"Include `{AGENT_REVIEW_MARKER}` at the end of your review body.",
        "",
        "## Repository-Specific Focus",
        "",
    ])

    prompt_parts.extend([
        "- Code correctness, error handling, security, performance, test coverage",
        "- **IMPORTANT**: Step 5 above fetches the repo-specific review rules from",
        "  `.agent/skills/review/SKILL.md` which override these defaults.",
        "  If that file exists, use it as your PRIMARY review guide for this repository.",
        "  Also check for `.agent/skills/review/CHECKLIST.md` and `.agent/skills/review/EXAMPLES.md`.",
        "",
    ])

    # Add Linear ticket context if available
    if linear_context:
        prompt_parts.extend(["", linear_context])

    prompt = "\n".join(prompt_parts)

    # Log prompt for debugging
    logger.info("[GITHUB-WEBHOOK] Sending prompt to agent (%d chars):\n%s", len(prompt), prompt[:3000])

    # Set up observability
    observer = None
    if db is not None:
        observer = ConversationObserver(db, metadata={
            "source": "github_webhook",
            "prompt": prompt,
            "model": os.environ.get("CLAUDE_MODEL", ""),
            "github_repo": repo_full_name,
            "github_pr_number": pr_number,
            "github_pr_title": pr_title,
            "github_pr_url": pr_url,
            "github_pr_author": pr_author,
            "github_base_sha": base_sha,
            "github_head_sha": head_sha,
            "github_action": action,
            "linear_ticket_id": ticket_id,
            "trigger_type": "pr_review",
        }, conversation_id=conversation_id)
        await observer.start()

    review_succeeded = True
    try:
        async for text in stream_agent(prompt=prompt, observer=observer, source="github_webhook"):
            logger.info("[GITHUB-WEBHOOK] Agent output: %.500s", text)
            # stream_agent catches exceptions and yields error messages instead of propagating
            if isinstance(text, str) and text.startswith("Sorry, I encountered an error:"):
                review_succeeded = False
                logger.error("[GITHUB-WEBHOOK] Agent error detected: %s", text)
        if review_succeeded:
            logger.info(
                "[GITHUB-WEBHOOK] Review complete for PR %s#%d",
                repo_full_name, pr_number,
            )
        else:
            logger.error(
                "[GITHUB-WEBHOOK] Review failed for PR %s#%d",
                repo_full_name, pr_number,
            )
    except Exception:
        review_succeeded = False
        logger.exception(
            "[GITHUB-WEBHOOK] Review failed for PR %s#%d",
            repo_full_name, pr_number,
        )
    finally:
        # On a successful re-evaluation, dismiss the CHANGES_REQUESTED reviews
        # we snapshotted BEFORE the agent ran — never a review that appeared
        # during this run (which could be the agent's own new REQUEST_CHANGES
        # if blocking issues remain).
        if review_succeeded and is_reevaluation and stale_request_changes_ids:
            try:
                await _dismiss_reviews_by_id(stale_request_changes_ids)
            except Exception:
                logger.exception(
                    "[GITHUB-WEBHOOK] Failed to dismiss stale agent reviews on %s#%d",
                    repo_full_name, pr_number,
                )

        # Delete status comment on success; update with handoff context on failure
        if status_comment_id:
            if review_succeeded:
                await _delete_pr_comment(repo_owner, repo_name, status_comment_id)
            else:
                # Generate handoff context for manual intervention
                handoff_context = _generate_handoff_context(
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    pr_title=pr_title,
                    pr_url=pr_url,
                    base_branch=base_branch,
                    head_branch=head_branch,
                )
                await _update_pr_comment(repo_owner, repo_name, status_comment_id, _review_needs_handoff_comment(handoff_context))

        # Update check run to show completion in PR UI
        if check_run_id:
            await _update_check_run(
                repo_owner, repo_name, check_run_id,
                status="completed",
                conclusion="success" if review_succeeded else "failure",
                title="Review Complete" if review_succeeded else "Review Failed",
                summary=f"Reviewed PR #{pr_number}: {pr_title}" if review_succeeded else "The review encountered an error.",
            )


async def _handle_slash_command(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    comment_id: int,
    command_action: str,
    command_args: str,
    comment_author: str,
) -> None:
    """Handle a slash command in a PR comment.

    Args:
        repo_owner: Repository owner.
        repo_name: Repository name.
        pr_number: Pull request number.
        comment_id: The comment ID containing the command.
        command_action: The parsed command action.
        command_args: Any additional arguments after the command.
        comment_author: User who issued the command.
    """
    logger.info(
        "[GITHUB-WEBHOOK] Handling slash command: %s (args=%s) from %s on %s/%s#%d",
        command_action, command_args, comment_author, repo_owner, repo_name, pr_number,
    )

    # React to acknowledge the command
    await _react_to_comment(repo_owner, repo_name, comment_id, "eyes")

    if command_action == "trigger_review":
        # Trigger a full re-review
        pr_details = await _get_pr_details(repo_owner, repo_name, pr_number)
        if pr_details:
            conversation_id = str(uuid.uuid4())
            await _process_pr_review(
                repo_owner=repo_owner,
                repo_name=repo_name,
                repo_full_name=f"{repo_owner}/{repo_name}",
                pr_number=pr_number,
                pr_title=pr_details.get("title", ""),
                pr_url=pr_details.get("html_url", ""),
                base_sha=pr_details.get("base", {}).get("sha", ""),
                head_sha=pr_details.get("head", {}).get("sha", ""),
                base_branch=pr_details.get("base", {}).get("ref", ""),
                head_branch=pr_details.get("head", {}).get("ref", ""),
                action="slash_command",
                pr_author=pr_details.get("user", {}).get("login", ""),
                conversation_id=conversation_id,
                force_review=True,
            )
        else:
            # Failed to fetch PR details - post error comment
            await _create_pr_comment(
                repo_owner, repo_name, pr_number,
                f"❌ Failed to fetch PR details. Please check if the PR is still open and try again.\n\n{AGENT_REVIEW_MARKER}"
            )

    elif command_action == "mark_resolved":
        # Resolve all unresolved agent review threads on this PR
        threads = await get_agent_review_threads(repo_owner, repo_name, pr_number)
        unresolved = [t for t in threads if not t.get("is_resolved")]

        if not unresolved:
            await _create_pr_comment(
                repo_owner, repo_name, pr_number,
                f"No unresolved review threads to resolve.\n\n{AGENT_REVIEW_MARKER}"
            )
            return

        resolved_count = 0
        for thread in unresolved:
            if await resolve_review_thread(thread["id"]):
                resolved_count += 1

        if resolved_count > 0:
            await _react_to_comment(repo_owner, repo_name, comment_id, "white_check_mark")
            await _create_pr_comment(
                repo_owner, repo_name, pr_number,
                f"Resolved {resolved_count} review thread(s).\n\n{AGENT_REVIEW_MARKER}"
            )
        else:
            # All resolution attempts failed
            await _react_to_comment(repo_owner, repo_name, comment_id, "x")
            await _create_pr_comment(
                repo_owner, repo_name, pr_number,
                f"❌ Failed to resolve {len(unresolved)} thread(s). This may be a permissions issue or the threads may have been modified.\n\n{AGENT_REVIEW_MARKER}"
            )

    elif command_action == "explain_more":
        # Ask the agent to explain the suggestion in more detail
        db = get_db()
        conversation_id = str(uuid.uuid4())

        prompt = f"""A user requested more explanation for a review comment on PR #{pr_number}.

## Context
- Repository: {repo_owner}/{repo_name}
- PR Number: #{pr_number}
- User's request: {command_args if command_args else "Please explain this suggestion in more detail."}

## Instructions
1. Fetch the PR diff and find the relevant context
2. Provide a detailed explanation of the suggestion
3. Include code examples if helpful
4. Reply using `mcp__github__create_issue_comment`:
   - owner: `{repo_owner}`
   - repo: `{repo_name}`
   - issue_number: {pr_number}
   - body: Your detailed explanation

Include `{AGENT_REVIEW_MARKER}` at the end.
"""
        observer = None
        if db is not None:
            observer = ConversationObserver(db, metadata={
                "source": "github_webhook",
                "prompt": prompt,
                "github_repo": f"{repo_owner}/{repo_name}",
                "github_pr_number": pr_number,
                "trigger_type": "slash_command_explain",
            }, conversation_id=conversation_id)
            await observer.start()

        async for text in stream_agent(prompt=prompt, observer=observer, source="github_webhook"):
            logger.info("[GITHUB-WEBHOOK] Explain output: %.300s", text)

    elif command_action == "apply_fix":
        # Trigger the agent to apply the suggested fix
        db = get_db()
        conversation_id = str(uuid.uuid4())

        prompt = f"""A user requested to apply a fix suggestion from a review comment on PR #{pr_number}.

## Context
- Repository: {repo_owner}/{repo_name}
- PR Number: #{pr_number}
- User's additional notes: {command_args if command_args else "None"}

## Instructions
1. Fetch the PR diff and understand the changes
2. Look at the review comments to find the suggested fix
3. Apply the fix by creating a commit with the changes
4. Use `mcp__github__create_or_update_file` to push the fix, with:
   - owner: `{repo_owner}`
   - repo: `{repo_name}`
   - Use the PR's head branch as the target
5. Post a comment confirming the fix was applied using `mcp__github__create_issue_comment`

Include `{AGENT_REVIEW_MARKER}` at the end of any comment.
"""
        observer = None
        if db is not None:
            observer = ConversationObserver(db, metadata={
                "source": "github_webhook",
                "prompt": prompt,
                "github_repo": f"{repo_owner}/{repo_name}",
                "github_pr_number": pr_number,
                "trigger_type": "slash_command_fix",
            }, conversation_id=conversation_id)
            await observer.start()

        async for text in stream_agent(prompt=prompt, observer=observer, source="github_webhook"):
            logger.info("[GITHUB-WEBHOOK] Fix output: %.300s", text)

    elif command_action in ("approve_suggestion", "dismiss_suggestion"):
        # Just acknowledge - these would need more context about which suggestion
        reaction = "thumbsup" if command_action == "approve_suggestion" else "thumbsdown"
        await _react_to_comment(repo_owner, repo_name, comment_id, reaction)


async def _track_human_review(
    repo_full_name: str,
    pr_number: int,
    reviewer: str,
    review_state: str,
    review_body: str,
    submitted_at: str | None,
) -> None:
    """Track a human review for quality assessment.

    Compares against prior agent reviews to detect if human intervention
    was needed (self-improvement signal).

    Args:
        repo_full_name: Full repository name.
        pr_number: Pull request number.
        reviewer: Human reviewer's GitHub login.
        review_state: Review state (APPROVED, CHANGES_REQUESTED, COMMENTED).
        review_body: Review body text.
        submitted_at: ISO timestamp when review was submitted.
    """
    db = get_db()
    if db is None:
        logger.warning("[GITHUB-WEBHOOK] No database, skipping quality tracking")
        return

    try:
        human_review = {
            "reviewer": reviewer,
            "state": review_state,
            "body": review_body,
            "submitted_at": submitted_at,
        }

        quality_id = await process_human_review_for_quality(
            db, repo_full_name, pr_number, human_review,
        )

        if quality_id:
            logger.info(
                "[GITHUB-WEBHOOK] Quality assessment created: %s for %s#%d",
                quality_id, repo_full_name, pr_number,
            )
    except Exception as e:
        logger.error(
            "[GITHUB-WEBHOOK] Failed to track human review on %s#%d: %s",
            repo_full_name, pr_number, e,
        )


async def _process_review_comment_reply(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    comment_id: int,
    comment_body: str,
    comment_path: str,
    comment_line: int | None,
    conversation_id: str,
    comment_author: str = "",
):
    """Reply to a review comment that mentions the agent."""
    logger.info(
        "[GITHUB-WEBHOOK] Processing review comment reply: %s#%d comment %d",
        repo_name, pr_number, comment_id,
    )

    # React with 👀 to acknowledge the mention immediately
    await _react_to_review_comment(repo_owner, repo_name, comment_id, "eyes")

    # Check for slash commands first (e.g., /resolve, /explain, /fix)
    slash_cmd = _parse_slash_command(comment_body)
    if slash_cmd:
        command_action, command_args = slash_cmd
        logger.info(
            "[GITHUB-WEBHOOK] Detected slash command in review comment: %s (args=%s)",
            command_action, command_args,
        )
        await _handle_slash_command(
            repo_owner, repo_name, pr_number, comment_id,
            command_action, command_args, comment_author,
        )
        return

    prompt_parts = [
        "A user mentioned @loma-agent in a PR review comment and expects a response.",
        "",
        f"## Context",
        f"- Repository: {repo_owner}/{repo_name}",
        f"- PR Number: #{pr_number}",
        f"- File: {comment_path}" if comment_path else "",
        f"- Line: {comment_line}" if comment_line else "",
        f"- Comment: {comment_body}",
        "",
        "## Instructions",
        "",
        "1. Read the comment and understand what the user is asking",
        "2. If they're asking about code, fetch the relevant file context",
        "3. Provide a helpful response",
        "4. Reply using `mcp__github__create_pull_request_review_comment_reply`:",
        f"   - owner: `{repo_owner}`",
        f"   - repo: `{repo_name}`",
        f"   - comment_id: {comment_id}",
        "   - body: Your response",
        "",
        f"Include `{AGENT_REVIEW_MARKER}` at the end of your reply.",
    ]

    prompt = "\n".join(prompt_parts)

    db = get_db()
    observer = None
    if db is not None:
        observer = ConversationObserver(db, metadata={
            "source": "github_webhook",
            "prompt": prompt,
            "model": os.environ.get("CLAUDE_MODEL", ""),
            "github_repo": f"{repo_owner}/{repo_name}",
            "github_pr_number": pr_number,
            "github_comment_id": comment_id,
            "trigger_type": "review_comment_reply",
        }, conversation_id=conversation_id)
        await observer.start()

    try:
        async for text in stream_agent(prompt=prompt, observer=observer, source="github_webhook"):
            logger.info("[GITHUB-WEBHOOK] Agent output (reply): %.500s", text)
        logger.info("[GITHUB-WEBHOOK] Reply complete for comment %d", comment_id)
    except Exception:
        logger.exception("[GITHUB-WEBHOOK] Reply failed for comment %d", comment_id)
    finally:
        # Remove the 👀 reaction now that we've replied
        await _remove_review_comment_reaction(repo_owner, repo_name, comment_id, "eyes")


async def _process_pr_conversation_comment(
    repo_owner: str,
    repo_name: str,
    repo_full_name: str,
    pr_number: int,
    comment_id: int,
    comment_body: str,
    comment_author: str,
    conversation_id: str,
):
    """Handle a PR conversation comment that mentions the agent.

    This could be a request to re-review, answer a question, or perform some other action.
    """
    logger.info(
        "[GITHUB-WEBHOOK] Processing PR conversation comment: %s#%d comment %d",
        repo_full_name, pr_number, comment_id,
    )

    # React with 👀 to acknowledge the mention immediately
    await _react_to_comment(repo_owner, repo_name, comment_id, "eyes")

    # Check for slash commands first (e.g., /rereview, /resolve, /explain)
    slash_cmd = _parse_slash_command(comment_body)
    if slash_cmd:
        command_action, command_args = slash_cmd
        logger.info(
            "[GITHUB-WEBHOOK] Detected slash command: %s (args=%s)",
            command_action, command_args,
        )
        await _handle_slash_command(
            repo_owner, repo_name, pr_number, comment_id,
            command_action, command_args, comment_author,
        )
        return

    # Check if this looks like a re-review request
    # Use specific phrases to avoid false positives like "thanks for the review"
    lower_body = comment_body.lower()
    review_trigger_phrases = [
        "re-review", "rerun review", "run review", "run the review",
        "review again", "review this", "review this pr", "review the pr",
        "please review", "can you review", "could you review",
        "check again", "look again", "take another look",
    ]
    is_review_request = any(phrase in lower_body for phrase in review_trigger_phrases)

    logger.info(
        "[GITHUB-WEBHOOK] Comment analysis: is_review_request=%s, body='%s'",
        is_review_request, lower_body[:100],
    )

    if is_review_request:
        # Trigger a full PR review
        logger.info("[GITHUB-WEBHOOK] Detected review request, triggering full PR review")

        # Fetch PR details to get base/head info
        pr_details = await _get_pr_details(repo_owner, repo_name, pr_number)
        if not pr_details:
            logger.error("[GITHUB-WEBHOOK] Failed to fetch PR details, cannot re-review")
            await _create_pr_comment(
                repo_owner, repo_name, pr_number,
                f"Sorry, I couldn't fetch the PR details to perform a review. Please try again later.\n\n{AGENT_REVIEW_MARKER}"
            )
            return

        # Extract stats from already-fetched PR details to avoid duplicate API call
        pr_stats = {
            "additions": pr_details.get("additions", 0),
            "deletions": pr_details.get("deletions", 0),
            "changed_files": pr_details.get("changed_files", 0),
        }

        await _process_pr_review(
            repo_owner=repo_owner,
            repo_name=repo_name,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_title=pr_details.get("title", ""),
            pr_url=pr_details.get("html_url", ""),
            base_sha=pr_details.get("base", {}).get("sha", ""),
            head_sha=pr_details.get("head", {}).get("sha", ""),
            base_branch=pr_details.get("base", {}).get("ref", ""),
            head_branch=pr_details.get("head", {}).get("ref", ""),
            action="conversation_mention",
            pr_author=pr_details.get("user", {}).get("login", ""),
            conversation_id=conversation_id,
            pr_stats=pr_stats,
            force_review=True,  # Skip deduplication for explicit re-review requests
        )
        return

    # Otherwise, handle as a general question/request
    prompt_parts = [
        "A user mentioned @loma-agent in a PR conversation and expects a response.",
        "",
        f"## Context",
        f"- Repository: {repo_full_name}",
        f"- PR Number: #{pr_number}",
        f"- Comment by: {comment_author}",
        f"- Comment: {comment_body}",
        "",
        "## Instructions",
        "",
        "1. Read the comment and understand what the user is asking",
        "2. If they're asking about the PR changes, fetch the diff and relevant files",
        "3. Provide a helpful response",
        f"4. Reply using `mcp__github__create_issue_comment` with:",
        f"   - owner: `{repo_owner}`",
        f"   - repo: `{repo_name}`",
        f"   - issue_number: {pr_number}",
        "   - body: Your response",
        "",
        f"Include `{AGENT_REVIEW_MARKER}` at the end of your reply.",
    ]

    prompt = "\n".join(prompt_parts)

    db = get_db()
    observer = None
    if db is not None:
        observer = ConversationObserver(db, metadata={
            "source": "github_webhook",
            "prompt": prompt,
            "model": os.environ.get("CLAUDE_MODEL", ""),
            "github_repo": repo_full_name,
            "github_pr_number": pr_number,
            "github_comment_id": comment_id,
            "trigger_type": "pr_conversation_comment",
        }, conversation_id=conversation_id)
        await observer.start()

    try:
        async for text in stream_agent(prompt=prompt, observer=observer, source="github_webhook"):
            logger.info("[GITHUB-WEBHOOK] Agent output (conversation): %.500s", text)
        logger.info("[GITHUB-WEBHOOK] Conversation reply complete for comment %d", comment_id)
    except Exception:
        logger.exception("[GITHUB-WEBHOOK] Conversation reply failed for comment %d", comment_id)
    finally:
        # Remove the 👀 reaction now that we've replied
        await _remove_reaction(repo_owner, repo_name, comment_id, "eyes")


async def _get_pr_details(repo_owner: str, repo_name: str, pr_number: int) -> dict | None:
    """Fetch full PR details. Returns None on failure."""
    if not GITHUB_API_KEY:
        return None

    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {GITHUB_API_KEY}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error("[GITHUB-WEBHOOK] Failed to fetch PR details: %d", resp.status)
                    return None
    except Exception as e:
        logger.error("[GITHUB-WEBHOOK] Error fetching PR details: %s", e)
        return None


def _extract_linear_ticket(branch_name: str, pr_title: str) -> str | None:
    """Extract Linear ticket reference from branch name or PR title.

    Looks for ISSUE-1234 pattern (case-insensitive).
    Returns the first match found (uppercased), or None.
    """
    # Check branch name first (more reliable)
    match = LINEAR_TICKET_PATTERN.search(branch_name)
    if match:
        return match.group(1).upper()

    # Fall back to PR title
    match = LINEAR_TICKET_PATTERN.search(pr_title)
    if match:
        return match.group(1).upper()

    return None


async def _fetch_linear_ticket(ticket_id: str) -> dict | None:
    """Fetch ticket details from Linear API.

    Returns dict with title, description, state, priority, labels, etc.
    Returns None on failure or if LINEAR_API_KEY is not set.
    """
    # GraphQL query to search for issue by identifier (e.g., ISSUE-1234)
    query = """
    query SearchIssue($term: String!) {
        searchIssues(term: $term, first: 1) {
            nodes {
                id
                identifier
                title
                description
                state {
                    name
                    type
                }
                priority
                priorityLabel
                labels {
                    nodes {
                        name
                    }
                }
                assignee {
                    name
                }
                project {
                    name
                }
            }
        }
    }
    """

    result = await linear_graphql_request(query, {"term": ticket_id})

    if "errors" in result:
        logger.error("[GITHUB-WEBHOOK] Linear GraphQL errors: %s", result["errors"])
        return None

    issues = result.get("data", {}).get("searchIssues", {}).get("nodes", [])
    if issues:
        issue = issues[0]
        # Verify the returned issue's identifier matches the expected ticket
        # (searchIssues is fuzzy and may return unrelated results)
        returned_id = issue.get("identifier", "").upper()
        if returned_id == ticket_id.upper():
            logger.info("[GITHUB-WEBHOOK] Fetched Linear ticket %s: %s", ticket_id, issue.get("title"))
            return issue
        else:
            logger.warning(
                "[GITHUB-WEBHOOK] Linear search returned %s but expected %s — ignoring",
                returned_id, ticket_id,
            )
            return None
    else:
        logger.warning("[GITHUB-WEBHOOK] Linear ticket %s not found", ticket_id)
        return None


def _format_linear_context(ticket: dict) -> str:
    """Format Linear ticket details for inclusion in review prompt."""
    lines = [
        f"## Linear Ticket Context: {ticket.get('identifier')}",
        "",
        f"**Title**: {ticket.get('title', 'N/A')}",
        f"**State**: {ticket.get('state', {}).get('name', 'N/A')}",
        f"**Priority**: {ticket.get('priorityLabel', 'N/A')}",
    ]

    if ticket.get("project"):
        lines.append(f"**Project**: {ticket['project'].get('name', 'N/A')}")

    if ticket.get("assignee"):
        lines.append(f"**Assignee**: {ticket['assignee'].get('name', 'N/A')}")

    labels = ticket.get("labels", {}).get("nodes", [])
    if labels:
        label_names = [l.get("name") for l in labels if l.get("name")]
        if label_names:
            lines.append(f"**Labels**: {', '.join(label_names)}")

    description = ticket.get("description", "")
    if description:
        lines.extend([
            "",
            "**Description/Acceptance Criteria**:",
            description[:2000] + ("..." if len(description) > 2000 else ""),
        ])

    lines.extend([
        "",
        "**Review Note**: Check that the PR implementation covers all requirements mentioned in the ticket description above.",
        "",
    ])

    return "\n".join(lines)


def setup_github_webhook_routes(app: web.Application):
    """Register GitHub webhook routes on the aiohttp app."""
    app.router.add_post("/webhooks/github", handle_github_webhook)
    
