"""Two-stage notification for agent PR self-reviews.

Stage 1 — the flow that creates an agent PR announces it immediately
("PR ready — self-review running…") and registers WHERE it announced it
(a Slack thread, a Linear issue, or a user's Loma inbox). Registration
happens via the `tools/github_pr_notify.py` CLI (skill-driven flows) or
the `notify_target` parameter of `utils.github_pr.clone_and_run_claude`.

Stage 2 — when the fresh-context self-review completes (or fails),
`webhooks/github.py` calls `post_self_review_followup()`, which looks up
the registered target and threads the review verdict back to the same
place. This makes the self-review's pending/complete/failed state visible
to the human instead of implicit: the initial message says the review is
running, and the follow-up carries the verdict. If the review pipeline
dies, the failure is posted too — never a silent skip.

Targets are stored in the `pr_notification_targets` collection keyed by
(repo_full_name, pr_number). The latest registration wins.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

COLLECTION = "pr_notification_targets"

# Supported target types and the fields each requires.
TARGET_REQUIRED_FIELDS = {
    "slack": ("channel", "thread_ts"),
    "linear": ("issue_id",),
    "loma": ("user_email",),
}


def _validate_target(target: dict) -> str | None:
    """Return an error string if the target dict is invalid, else None."""
    if not isinstance(target, dict):
        return "target must be a dict"
    target_type = target.get("type", "")
    if target_type not in TARGET_REQUIRED_FIELDS:
        return f"target type must be one of {sorted(TARGET_REQUIRED_FIELDS)}, got {target_type!r}"
    missing = [f for f in TARGET_REQUIRED_FIELDS[target_type] if not target.get(f)]
    if missing:
        return f"target type {target_type!r} requires fields: {', '.join(missing)}"
    return None


async def register_pr_notification_target(
    db,
    repo_full_name: str,
    pr_number: int,
    target: dict,
) -> dict:
    """Upsert the notification target for a PR. Latest registration wins.

    Raises ValueError on an invalid target so callers fail loudly at
    registration time (seconds after PR creation) rather than silently at
    follow-up time (minutes later).
    """
    error = _validate_target(target)
    if error:
        raise ValueError(error)
    if not repo_full_name or "/" not in repo_full_name:
        raise ValueError("repo_full_name must be in owner/name form")
    if not pr_number or pr_number <= 0:
        raise ValueError("pr_number must be a positive integer")

    doc = {
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "target": target,
        "registered_at": datetime.now(timezone.utc),
    }
    await db[COLLECTION].update_one(
        {"repo_full_name": repo_full_name, "pr_number": pr_number},
        {"$set": doc},
        upsert=True,
    )
    logger.info(
        "[PR-FOLLOWUP] Registered %s target for %s#%d",
        target.get("type"), repo_full_name, pr_number,
    )
    return doc


async def get_pr_notification_target(
    db,
    repo_full_name: str,
    pr_number: int,
) -> dict | None:
    """Fetch the registered notification target for a PR, or None."""
    return await db[COLLECTION].find_one(
        {"repo_full_name": repo_full_name, "pr_number": pr_number}
    )


def extract_self_review_verdict(reviews: list[dict], agent_login: str) -> str | None:
    """Pull the verdict line out of the agent's most recent self-review.

    The self-review prompt requires the review body to START with a single
    verdict line (`✅ Self-review: …` or `🔴 Self-review: …`). Reviews come
    from `webhooks.github_graphql.get_pr_reviews` in chronological order, so
    the last matching review is the freshest.
    """
    for review in reversed(reviews or []):
        if review.get("author") != agent_login:
            continue
        body = (review.get("body") or "").strip()
        for line in body.splitlines():
            line = line.strip()
            if "Self-review:" in line:
                return line
        # Agent review without a verdict line — keep looking at older reviews
    return None


def _build_messages(
    pr_number: int,
    pr_url: str,
    verdict: str | None,
    succeeded: bool,
) -> tuple[str, str, str]:
    """Return (title, plain_body, slack_text) for the follow-up."""
    if succeeded:
        verdict_text = verdict or "review posted — see the PR for findings"
        title = f"Self-review complete: PR #{pr_number}"
        body = f"{verdict_text}\n\n[View PR]({pr_url})"
        slack_text = (
            f"🔍 *Self-review complete* for PR #{pr_number}: {verdict_text}\n{pr_url}"
        )
    else:
        title = f"Self-review FAILED: PR #{pr_number}"
        body = (
            "The fresh-context self-review did not complete — no findings were "
            "posted. Treat the PR as **unreviewed** and comment `/rereview` on "
            f"it to retry.\n\n[View PR]({pr_url})"
        )
        slack_text = (
            f"⚠️ *Self-review failed* for PR #{pr_number} — no findings were "
            f"posted. Treat the PR as unreviewed; comment `/rereview` on it to "
            f"retry.\n{pr_url}"
        )
    return title, body, slack_text


async def _dispatch_slack(target: dict, slack_text: str) -> bool:
    from slack_sdk.web.async_client import AsyncWebClient

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        logger.error("[PR-FOLLOWUP] SLACK_BOT_TOKEN not set — cannot post follow-up")
        return False
    client = AsyncWebClient(token=token)
    await client.chat_postMessage(
        channel=target["channel"],
        thread_ts=target["thread_ts"],
        text=slack_text,
    )
    return True


async def _dispatch_linear(target: dict, title: str, body: str) -> bool:
    from webhooks.linear_api import AGENT_COMMENT_MARKER, post_comment

    comment_body = f"{AGENT_COMMENT_MARKER}\n**{title}** 🔍\n\n{body}"
    comment_id = await post_comment(target["issue_id"], comment_body)
    return comment_id is not None


async def _dispatch_loma(db, target: dict, title: str, body: str, pr_url: str) -> bool:
    from observability.notifications import create_notification

    await create_notification(
        db,
        user_email=target["user_email"],
        title=title,
        body=body,
        conversation_id=target.get("conversation_id"),
        link=pr_url,
        source="self_review",
    )
    return True


async def post_self_review_followup(
    db,
    *,
    repo_full_name: str,
    pr_number: int,
    pr_url: str,
    verdict: str | None,
    succeeded: bool,
) -> bool:
    """Stage 2: thread the self-review outcome back to where the PR was announced.

    Returns True if a follow-up was delivered, False if no target was
    registered or delivery failed. Never raises — this runs in the review
    pipeline's cleanup path and must not mask the review result.
    """
    if db is None:
        logger.info("[PR-FOLLOWUP] No database — skipping follow-up for %s#%d",
                    repo_full_name, pr_number)
        return False

    try:
        record = await get_pr_notification_target(db, repo_full_name, pr_number)
    except Exception:
        logger.exception("[PR-FOLLOWUP] Target lookup failed for %s#%d",
                         repo_full_name, pr_number)
        return False

    if not record or not record.get("target"):
        logger.info(
            "[PR-FOLLOWUP] No notification target registered for %s#%d — "
            "skipping follow-up", repo_full_name, pr_number,
        )
        return False

    target = record["target"]
    title, body, slack_text = _build_messages(pr_number, pr_url, verdict, succeeded)

    try:
        target_type = target.get("type")
        if target_type == "slack":
            delivered = await _dispatch_slack(target, slack_text)
        elif target_type == "linear":
            delivered = await _dispatch_linear(target, title, body)
        elif target_type == "loma":
            delivered = await _dispatch_loma(db, target, title, body, pr_url)
        else:
            logger.error("[PR-FOLLOWUP] Unknown target type %r for %s#%d",
                         target_type, repo_full_name, pr_number)
            return False
    except Exception:
        logger.exception(
            "[PR-FOLLOWUP] Failed to deliver %s follow-up for %s#%d",
            target.get("type"), repo_full_name, pr_number,
        )
        return False

    if delivered:
        try:
            await db[COLLECTION].update_one(
                {"repo_full_name": repo_full_name, "pr_number": pr_number},
                {"$set": {
                    "last_followup_at": datetime.now(timezone.utc),
                    "last_followup_succeeded": succeeded,
                    "last_followup_verdict": verdict,
                }},
            )
        except Exception:
            logger.warning("[PR-FOLLOWUP] Failed to record follow-up delivery for %s#%d",
                           repo_full_name, pr_number)
        logger.info(
            "[PR-FOLLOWUP] Delivered %s follow-up for %s#%d (succeeded=%s)",
            target.get("type"), repo_full_name, pr_number, succeeded,
        )
    return delivered
