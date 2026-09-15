"""Two-stage notification for agent PR self-reviews.

Stage 1 — the flow that creates an agent PR announces it immediately
("PR ready — self-review running…") and registers WHERE it announced it
(a Slack thread, a Linear issue, or a user's Loma inbox) via the
`tools/github_pr_notify.py` CLI.

Stage 2 — when the fresh-context self-review completes (or fails),
`webhooks/github.py` calls `post_self_review_followup()`, which looks up
the registered target and threads the review verdict back to the same
place. This makes the self-review's pending/complete/failed state visible
to the human instead of implicit: the initial message says the review is
running, and the follow-up carries the verdict. If the review pipeline
dies, the failure is posted too — never a silent skip. If self-review is
disabled on the deploy, that is posted as well, so the Stage-1 "verdict
coming" promise is always answered.

Targets are stored in the `pr_notification_targets` collection keyed by
(repo_full_name, pr_number). The latest registration wins.
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

COLLECTION = "pr_notification_targets"

# The one recovery path advertised on a failed self-review. The issue_comment
# webhook only dispatches comments that mention the agent, so a bare
# `/rereview` is silently ignored — always tell humans the mention form.
REREVIEW_COMMAND = "@loma-agent /rereview"

# Tolerance applied to `started_at` in the timestamp FALLBACK path of
# `extract_self_review_verdict` (only used when the pipeline could not
# snapshot pre-run review IDs), to absorb clock skew between this host and
# GitHub. Timestamps are never the primary run-scoping mechanism: a coalesced
# re-run starts seconds after the previous run posted, well inside any skew
# window, so only the ID snapshot can tell the two apart.
_RUN_SCOPE_SKEW = timedelta(seconds=60)

# Supported target types and the fields each requires.
TARGET_REQUIRED_FIELDS = {
    "slack": ("channel", "thread_ts"),
    "linear": ("issue_id",),
    "loma": ("user_email",),
}


def _normalize_repo(repo_full_name: str) -> str:
    """Normalize a repo full name for storage and lookup.

    GitHub repo full names are case-insensitive (`ExampleOrg/Repo` and
    `exampleorg/repo` are the same repo) but Mongo string equality is not.
    The CLI stores whatever the agent typed while the webhook looks up the
    canonical casing GitHub sends, so without normalising on BOTH the write
    (register) and read (lookup) paths a casing mismatch silently drops the
    follow-up. Applied to every key touching the collection.
    """
    return (repo_full_name or "").strip().lower()


def _escape_slack(text: str) -> str:
    """Escape the three characters Slack parses as control sequences in `text`.

    The verdict line is agent-controlled (it comes from the PR review body) and
    is relayed verbatim into a Slack message. Without this a verdict containing
    `<!channel>`, `<@U…>` or `<url|label>` would ping the whole channel / users
    or inject a link from the bot. Order matters: `&` first, so the `&lt;`/`&gt;`
    entities produced for `<`/`>` are not double-escaped.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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

    repo_full_name = _normalize_repo(repo_full_name)
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

    # If self-review was found disabled on this deploy BEFORE a target had
    # registered, the webhook's `opened` handler could not deliver the
    # "self-review skipped" notice (no target yet) and left a `disabled_pending`
    # marker instead. Now that a target exists, answer the Stage-1 promise. For
    # a single-push PR there is no later `synchronize` to trigger it otherwise.
    try:
        stored = await db[COLLECTION].find_one(
            {"repo_full_name": repo_full_name, "pr_number": pr_number}
        )
    except Exception:
        stored = None
    disabled_notice = None
    if stored and stored.get("disabled_pending"):
        delivered = await post_self_review_followup(
            db,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_url=stored.get("disabled_pending_pr_url", ""),
            verdict=None,
            succeeded=False,
            disabled=True,
        )
        # `post_self_review_followup` clears the marker itself once the notice
        # is out (or found already delivered). If delivery failed here — this
        # is usually the agent-side CLI process, which may lack the channel's
        # credentials — the marker stays so a later event can retry, and the
        # caller is told: for a single-push PR there may be no later event, so
        # "registered: true" alone would hide that the Stage-1 promise dangles.
        disabled_notice = "delivered" if delivered else "failed"
        if not delivered:
            logger.warning(
                "[PR-FOLLOWUP] Self-review is disabled for %s#%d but the notice could "
                "not be delivered from this process — the registered target has NOT "
                "been told; a later webhook event will retry",
                repo_full_name, pr_number,
            )
    return {**doc, "disabled_notice": disabled_notice}


async def mark_self_review_disabled(
    db,
    repo_full_name: str,
    pr_number: int,
    pr_url: str,
) -> None:
    """Record that self-review is disabled for this PR (Stage-1 promise pending).

    Called by the webhook on a reviewable event when ``LOMA_ENABLE_SELF_REVIEW``
    is off. On ``opened`` the announcing flow has usually not registered its
    target yet (skill Step 6c runs after PR creation), so a direct
    ``post_self_review_followup`` finds nothing and the "verdict coming" promise
    dangles forever for a single-push PR. Persisting the intent lets
    ``register_pr_notification_target`` deliver the notice once a target lands.
    """
    if db is None:
        return
    repo_full_name = _normalize_repo(repo_full_name)
    try:
        await db[COLLECTION].update_one(
            {"repo_full_name": repo_full_name, "pr_number": pr_number},
            {"$set": {"disabled_pending": True, "disabled_pending_pr_url": pr_url}},
            upsert=True,
        )
    except Exception:
        logger.warning(
            "[PR-FOLLOWUP] Could not record disabled_pending marker for %s#%d",
            repo_full_name, pr_number,
        )


async def get_pr_notification_target(
    db,
    repo_full_name: str,
    pr_number: int,
) -> dict | None:
    """Fetch the registered notification target for a PR, or None.

    Keys are stored lowercased (see ``_normalize_repo``). Docs registered
    before that normalisation shipped may still carry GitHub's mixed casing;
    ``init_observability`` folds them at boot, but a target registered by an
    older CLI against a newer server (or vice-versa) during the deploy window
    would otherwise be unreachable, so miss → one case-insensitive retry.
    """
    normalized = _normalize_repo(repo_full_name)
    record = await db[COLLECTION].find_one(
        {"repo_full_name": normalized, "pr_number": pr_number}
    )
    if record is not None or not normalized:
        return record
    return await db[COLLECTION].find_one({
        "repo_full_name": {"$regex": f"^{re.escape(normalized)}$", "$options": "i"},
        "pr_number": pr_number,
    })


def _parse_github_timestamp(value) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp (`2026-01-02T03:04:05Z`) to aware UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# The self-review prompt requires the review body to START with one of these.
VERDICT_PREFIXES = ("✅ Self-review:", "🔴 Self-review:")
VERDICT_MAX_CHARS = 300


@dataclass(frozen=True)
class SelfReviewLookup:
    """Outcome of looking for THIS run's self-review on the PR.

    ``review_found`` and ``verdict`` are deliberately separate: "the agent
    posted nothing" and "the agent posted a review that lacks the verdict
    line" need different follow-up copy. In the second case findings ARE on
    the PR and the human should read them, not treat the PR as unreviewed.
    """

    review_found: bool
    verdict: str | None = None


def _login_set(agent_login) -> set[str]:
    """One login or a collection of logins → set (empty strings dropped)."""
    logins = {agent_login} if isinstance(agent_login, str) else set(agent_login or ())
    return {login for login in logins if login}


def find_self_review(
    reviews: list[dict],
    agent_login,
    started_at: datetime | None = None,
    exclude_review_ids: set[str] | list[str] | None = None,
) -> SelfReviewLookup:
    """Find the agent's self-review for THIS run and its verdict line, if any.

    ``agent_login`` is the login that posted the review, or a collection of
    candidate logins: the pipeline passes both the PR author and
    ``AGENT_GITHUB_LOGIN`` because the two differ when the run reached
    self-review via the ``Agent PR`` label backstop (either the env login is
    misconfigured and the PR author IS the posting token, or a human's
    labelled draft is being reviewed by the correctly configured agent token).

    The self-review prompt requires the review body to START with a single
    verdict line (`✅ Self-review: …` or `🔴 Self-review: …`). Reviews come
    from `webhooks.github_graphql.get_pr_reviews` in chronological order, so
    the last matching review is the freshest.

    Run scoping — a run whose agent finished WITHOUT posting (max turns, MCP
    error, 422) must never report a previous run's verdict as fresh:

    - ``exclude_review_ids`` (primary, structural): the node IDs of every agent
      review that existed BEFORE this run's agent started. Anything in the set
      — or with no ID at all — cannot be this run's review and is skipped.
      This is the only scoping that survives a coalesced re-run, which starts
      seconds after the previous run posted, and it does not depend on which
      head SHA GitHub stamped on the review (a review submitted while the head
      is moving is stamped with the NEW head, not the reviewed one).
    - ``started_at`` (fallback, temporal): only reviews created at or after
      that instant minus ``_RUN_SCOPE_SKEW``. Used when the pipeline could not
      take the ID snapshot; both filters apply when both are given.

    ``PENDING`` reviews never count. GitHub returns a pending (unsubmitted)
    review to its author — which is the very token this pipeline queries with
    — so an agent that opened a pending review and died before submitting it
    (max turns, MCP error) would otherwise register as "review posted" for a
    review nobody but the bot can see.

    Returns ``review_found=True`` as soon as any review passes the run scoping,
    with ``verdict`` set from the newest such review that carries a verdict
    line (``None`` if none of them does).
    """
    cutoff = started_at - _RUN_SCOPE_SKEW if started_at else None
    excluded = set(exclude_review_ids) if exclude_review_ids is not None else None
    logins = _login_set(agent_login)
    review_found = False
    for review in reversed(reviews or []):
        if review.get("author") not in logins:
            continue
        if (review.get("state") or "").upper() == "PENDING":
            continue
        if excluded is not None:
            review_id = review.get("id")
            # No ID → cannot prove it is new → skip, same rule as timestamps.
            if not review_id or review_id in excluded:
                continue
        if cutoff is not None:
            created_at = _parse_github_timestamp(review.get("created_at"))
            # Unparseable timestamp → cannot prove it belongs to this run → skip.
            if created_at is None or created_at < cutoff:
                continue
        review_found = True
        body = (review.get("body") or "").strip()
        # The prompt requires the body to START with the verdict line, so only
        # the FIRST non-empty line can be the verdict. Scanning every line would
        # let a quoted prior verdict (a `>` blockquote while explaining what
        # changed) or a fenced template line anywhere in the body override the
        # real verdict — and this line is relayed verbatim to Slack / Linear /
        # the inbox, so a red verdict must not be turned green by quoted text.
        # Strip leading markdown emphasis/heading noise only; do NOT strip `>`
        # (a blockquoted line is not this run's verdict), and cap a runaway line.
        first_line = next((ln for ln in body.splitlines() if ln.strip()), "")
        candidate = first_line.strip().strip("*_`# ").strip()
        if candidate.startswith(VERDICT_PREFIXES):
            return SelfReviewLookup(review_found=True, verdict=candidate[:VERDICT_MAX_CHARS])
        # This run's review has no verdict line — keep looking at any other
        # review from this run, but remember that one WAS posted.
    return SelfReviewLookup(review_found=review_found, verdict=None)


def extract_self_review_verdict(
    reviews: list[dict],
    agent_login,
    started_at: datetime | None = None,
    exclude_review_ids: set[str] | list[str] | None = None,
) -> str | None:
    """Verdict line of this run's self-review, or ``None``. See ``find_self_review``."""
    return find_self_review(
        reviews, agent_login, started_at=started_at, exclude_review_ids=exclude_review_ids,
    ).verdict


def _build_messages(
    pr_number: int,
    pr_url: str,
    verdict: str | None,
    succeeded: bool,
    disabled: bool = False,
    review_posted: bool = False,
    verdict_unknown: bool = False,
) -> tuple[str, str, str]:
    """Return (title, plain_body, slack_text) for the follow-up.

    Six outcomes, so the Stage-1 "verdict will follow" promise is always
    answered with something a human can act on:
      - disabled: the deploy has LOMA_ENABLE_SELF_REVIEW off — no review will come
      - verdict_unknown: the review ran but GitHub could not be queried for the
        verdict afterwards — read the PR; do NOT treat it as unreviewed
      - succeeded + verdict: the normal case
      - succeeded + review_posted, no verdict: the agent posted a review this
        run but it lacks the verdict line — findings ARE on the PR, read them
      - succeeded, nothing posted: the agent finished but posted nothing this run
      - failed: the review pipeline errored
    """
    if disabled:
        title = f"Self-review skipped: PR #{pr_number}"
        body = (
            "Fresh-context self-review is **disabled** on this deployment "
            "(`LOMA_ENABLE_SELF_REVIEW=false`), so no verdict will be posted. "
            f"Treat the PR as **unreviewed**.\n\n[View PR]({pr_url})"
        )
        slack_text = (
            f"ℹ️ *Self-review skipped* for PR #{pr_number} — self-review is "
            f"disabled on this deployment; treat the PR as unreviewed.\n{pr_url}"
        )
    elif verdict_unknown:
        title = f"Self-review verdict unknown: PR #{pr_number}"
        body = (
            "The fresh-context self-review **completed**, but Loma could not read "
            "the review back from GitHub (a transient error), so the verdict could "
            "not be summarised here. **Read the PR directly** — most likely it was "
            f"reviewed — or comment `{REREVIEW_COMMAND}` on it to retry.\n\n"
            f"[View PR]({pr_url})"
        )
        slack_text = (
            f"❓ *Self-review verdict unknown* for PR #{pr_number} — the review "
            f"completed but Loma could not read it back from GitHub. Read the PR "
            f"directly or comment `{REREVIEW_COMMAND}` to retry.\n{pr_url}"
        )
    elif succeeded and verdict:
        title = f"Self-review complete: PR #{pr_number}"
        body = f"{verdict}\n\n[View PR]({pr_url})"
        # The verdict is agent-controlled — escape Slack control chars so it
        # cannot @-mention the channel or inject a link from the bot.
        slack_text = (
            f"🔍 *Self-review complete* for PR #{pr_number}: "
            f"{_escape_slack(verdict)}\n{pr_url}"
        )
    elif succeeded and review_posted:
        title = f"Self-review posted without a verdict: PR #{pr_number}"
        body = (
            "The fresh-context self-review **posted a review on the PR**, but it "
            "does not start with the required verdict line, so the outcome could "
            "not be summarised here. **Read the review on the PR directly** — do "
            f"not treat the PR as unreviewed.\n\n[View PR]({pr_url})"
        )
        slack_text = (
            f"⚠️ *Self-review posted without a verdict* for PR #{pr_number} — the "
            f"agent posted a review but no verdict line, so it is not summarised "
            f"here. Read the review on the PR directly.\n{pr_url}"
        )
    elif succeeded:
        title = f"Self-review incomplete: PR #{pr_number}"
        body = (
            "The fresh-context self-review completed but **no review from this "
            "run was found on the PR** — the agent finished without posting. "
            f"Treat the PR as **unreviewed** and comment `{REREVIEW_COMMAND}` "
            f"on it to retry.\n\n[View PR]({pr_url})"
        )
        slack_text = (
            f"⚠️ *Self-review incomplete* for PR #{pr_number} — the agent "
            f"finished but no review from this run was found on the PR. Treat "
            f"the PR as unreviewed; comment `{REREVIEW_COMMAND}` on it to "
            f"retry.\n{pr_url}"
        )
    else:
        title = f"Self-review FAILED: PR #{pr_number}"
        body = (
            "The fresh-context self-review did not complete — no findings were "
            f"posted. Treat the PR as **unreviewed** and comment "
            f"`{REREVIEW_COMMAND}` on it to retry.\n\n[View PR]({pr_url})"
        )
        slack_text = (
            f"⚠️ *Self-review failed* for PR #{pr_number} — no findings were "
            f"posted. Treat the PR as unreviewed; comment `{REREVIEW_COMMAND}` "
            f"on it to retry.\n{pr_url}"
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
    disabled: bool = False,
    review_posted: bool = False,
    verdict_unknown: bool = False,
) -> bool:
    """Stage 2: thread the self-review outcome back to where the PR was announced.

    ``disabled=True`` posts the "self-review is off on this deploy" outcome
    instead of a verdict, so Stage 1's promise is never left dangling.
    ``review_posted=True`` with ``verdict=None`` means the agent DID post a
    review this run but without a verdict line; the copy then points the human
    at the review instead of calling the PR unreviewed.

    Returns True if a follow-up was delivered, False if no target was
    registered or delivery failed. Never raises — this runs in the review
    pipeline's cleanup path and must not mask the review result.
    """
    if db is None:
        logger.info("[PR-FOLLOWUP] No database — skipping follow-up for %s#%d",
                    repo_full_name, pr_number)
        return False

    # Normalize once so the lookup AND every subsequent key/update below match
    # the casing the doc was stored under (see _normalize_repo).
    repo_full_name = _normalize_repo(repo_full_name)
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
    key = {"repo_full_name": repo_full_name, "pr_number": pr_number}

    async def _clear_disabled_pending() -> None:
        # The webhook re-arms `disabled_pending` on EVERY reviewable event
        # (opened, each synchronize). Once this registration has been told —
        # delivered now, or found already delivered — the marker must go, or
        # it goes stale: flip the deploy to enabled later and any
        # re-registration for this PR would replay a spurious "self-review
        # skipped, treat as unreviewed" notice while a real review runs.
        if not record.get("disabled_pending"):
            return
        try:
            await db[COLLECTION].update_one(
                key, {"$unset": {"disabled_pending": "", "disabled_pending_pr_url": ""}},
            )
        except Exception:
            logger.warning("[PR-FOLLOWUP] Could not clear disabled_pending marker for %s#%d",
                           repo_full_name, pr_number)

    # "Disabled" is answered once per registration: every reviewable
    # pull_request event (opened, each synchronize, …) re-enters this path,
    # but the human only needs to hear "no verdict is coming" once per
    # announcement, not once per push.
    disabled_notice_key = None
    if disabled:
        last_at = record.get("last_followup_at")
        registered_at = record.get("registered_at")
        if (
            record.get("last_followup_disabled")
            and last_at is not None
            and registered_at is not None
            and last_at >= registered_at
        ):
            logger.info(
                "[PR-FOLLOWUP] Already told %s#%d's target that self-review is "
                "disabled — skipping repeat", repo_full_name, pr_number,
            )
            await _clear_disabled_pending()
            return False
        # The read above is not enough on its own: `opened` and the first
        # `synchronize` land within seconds and both read the doc before
        # either records a delivery. Claim the notice for THIS registration
        # atomically — the claim is keyed by `registered_at`, so a later
        # re-registration (new announcement) can be told again.
        disabled_notice_key = registered_at or "unregistered"
        try:
            claimed = await db[COLLECTION].find_one_and_update(
                {**key, "disabled_notice_for": {"$ne": disabled_notice_key}},
                {"$set": {"disabled_notice_for": disabled_notice_key}},
            )
        except Exception:
            logger.exception(
                "[PR-FOLLOWUP] Could not claim the disabled notice for %s#%d",
                repo_full_name, pr_number,
            )
            return False
        if not claimed:
            logger.info(
                "[PR-FOLLOWUP] Another event already claimed %s#%d's disabled "
                "notice — skipping repeat", repo_full_name, pr_number,
            )
            await _clear_disabled_pending()
            return False

    title, body, slack_text = _build_messages(
        pr_number, pr_url, verdict, succeeded, disabled=disabled,
        review_posted=review_posted, verdict_unknown=verdict_unknown,
    )

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
        delivered = False

    if not delivered and disabled_notice_key is not None:
        # Give the claim back so the next event can retry the notice.
        try:
            await db[COLLECTION].update_one(
                {**key, "disabled_notice_for": disabled_notice_key},
                {"$unset": {"disabled_notice_for": ""}},
            )
        except Exception:
            logger.warning("[PR-FOLLOWUP] Could not release the disabled-notice claim for %s#%d",
                           repo_full_name, pr_number)

    if delivered:
        if disabled:
            await _clear_disabled_pending()
        try:
            # Condition on the registration we actually delivered to: if the flow
            # re-registered (new announcement) between our read and this write,
            # the newer registration must not be marked as already-notified.
            await db[COLLECTION].update_one(
                {**key, "registered_at": record.get("registered_at")},
                {"$set": {
                    "last_followup_at": datetime.now(timezone.utc),
                    "last_followup_succeeded": succeeded,
                    "last_followup_verdict": verdict,
                    "last_followup_disabled": disabled,
                    "last_followup_review_posted": review_posted,
                    "last_followup_verdict_unknown": verdict_unknown,
                }},
            )
        except Exception:
            logger.warning("[PR-FOLLOWUP] Failed to record follow-up delivery for %s#%d",
                           repo_full_name, pr_number)
        logger.info(
            "[PR-FOLLOWUP] Delivered %s follow-up for %s#%d (succeeded=%s, disabled=%s)",
            target.get("type"), repo_full_name, pr_number, succeeded, disabled,
        )
    return delivered
