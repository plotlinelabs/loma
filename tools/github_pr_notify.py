"""CLI to register where a PR's self-review verdict should be threaded back.

Stage 1 of the two-stage PR notification: right after a flow creates an
agent PR and announces it ("PR ready — self-review running…"), it runs
this tool to record WHERE it announced it. When the fresh-context
self-review completes, the webhook pipeline (webhooks/github.py →
utils/pr_followup.py) posts the verdict back to that same place.

Exactly one target type must be provided per registration:

  # Slack thread or Linear issue — the ORIGIN of the run. Pass only the Loma
  # conversation ID (the `[Conversation ID: ...]` line in the run's prompt); the
  # target is resolved server-side from what the Slack ingress / Linear webhook
  # stamped on that conversation. A Slack-started run never sees its channel
  # or thread_ts, so this is the only form that works for it.
  python3 tools/github_pr_notify.py register --repo <owner>/<name> --pr <num> \
      --conversation-id <uuid>

  # Explicit Slack thread / Linear issue (still origin-checked against the
  # conversation; kept for flows that already know these values)
  python3 tools/github_pr_notify.py register --repo <owner>/<name> --pr <num> \
      --slack-channel C0123ABC --thread-ts 1700000000.123456
  python3 tools/github_pr_notify.py register --repo <owner>/<name> --pr <num> \
      --linear-issue-id <linear-issue-uuid> --conversation-id <uuid>

  # Loma inbox (dashboard conversations) — requires the requester's personal
  # auth token, same HMAC check as tools/notify.py. Pass it via the
  # LOMA_AUTH_TOKEN environment variable so it never appears in the process
  # argument list (`--auth-token` is accepted as a fallback):
  LOMA_AUTH_TOKEN=<token> python3 tools/github_pr_notify.py register \
      --repo <owner>/<name> --pr <num> --user-email person@example.com \
      [--conversation-id <uuid>]

  # Inspect the current registration
  python3 tools/github_pr_notify.py show --repo <owner>/<name> --pr <num>

Who may register what (defence in depth — this CLI is the interface agent
runs are told to use; a process holding OBSERVABILITY_MONGODB_URI can of
course write the collection directly):

  - Loma inbox targets are gated by the requester's per-user HMAC token, so a
    run cannot push a notification into an arbitrary user's inbox.
  - Slack and Linear targets must be the verified ORIGIN of a Loma
    conversation: a Slack thread the Slack ingress actually started a run in
    (``metadata.slack_channel_id`` / ``metadata.slack_thread_ts``) or a Linear
    issue the Linear webhook actually started a run for
    (``metadata.linear_issue_id``). ``--conversation-id`` pins the check to
    one conversation. A prompt-injected run therefore cannot use this tool
    to route the bot's verdict message into an arbitrary channel or issue.

Requires OBSERVABILITY_MONGODB_URI in the environment (same DB used by
the webhook pipeline).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# When invoked via `python3 tools/github_pr_notify.py ...`, only the `tools/`
# directory is on sys.path by default — the repo root is not. Add it so the
# `utils.*` import below resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.pr_followup import (  # noqa: E402
    get_pr_notification_target,
    register_pr_notification_target,
)

AUTH_TOKEN_ENV = "LOMA_AUTH_TOKEN"


def _verify_auth(auth_token: str, user_email: str) -> bool:
    """Verify the HMAC auth token matches the user email (mirrors tools/notify.py)."""
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from _auth_token import verify_user_auth_token

    return verify_user_auth_token(auth_token, user_email)


def _resolve_auth_token(args: argparse.Namespace) -> str:
    """Token from the environment first (keeps it out of `ps`), then --auth-token."""
    return (os.environ.get(AUTH_TOKEN_ENV, "") or "").strip() or (args.auth_token or "")


def _get_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("OBSERVABILITY_MONGODB_URI environment variable is not set")
    db_name = os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability").strip()
    client = AsyncIOMotorClient(uri)
    return client, client[db_name]


def _build_target(args: argparse.Namespace) -> dict | None:
    """Build the target from explicit flags.

    Returns ``None`` when no explicit target was given but ``--conversation-id``
    was: the caller then resolves the target from the conversation's stamped
    origin (``_resolve_target_from_conversation``).
    """
    provided = []
    if args.slack_channel or args.thread_ts:
        provided.append("slack")
    if args.linear_issue_id:
        provided.append("linear")
    if args.user_email:
        provided.append("loma")

    if not provided and args.conversation_id:
        return None
    if len(provided) != 1:
        raise ValueError(
            "Provide exactly one target: --conversation-id alone (resolves the "
            "Slack thread / Linear issue the run came from), or --slack-channel + "
            "--thread-ts, or --linear-issue-id, or --user-email"
        )

    target_type = provided[0]
    target: dict
    if target_type == "slack":
        if not (args.slack_channel and args.thread_ts):
            raise ValueError("Slack targets require both --slack-channel and --thread-ts")
        target = {"type": "slack", "channel": args.slack_channel, "thread_ts": args.thread_ts}
    elif target_type == "linear":
        target = {"type": "linear", "issue_id": args.linear_issue_id}
    else:
        # Loma inbox targets write into a specific user's inbox — gate them
        # behind the same per-user HMAC token that tools/notify.py requires.
        auth_token = _resolve_auth_token(args)
        if not auth_token:
            raise ValueError(
                f"--user-email targets require the same user's auth token, via the "
                f"{AUTH_TOKEN_ENV} environment variable or --auth-token"
            )
        if not _verify_auth(auth_token, args.user_email):
            raise ValueError(
                "Authentication failed. The auth token is invalid, expired, or "
                "doesn't match --user-email"
            )
        target = {"type": "loma", "user_email": args.user_email}
    if args.conversation_id:
        target["conversation_id"] = args.conversation_id
    return target


async def _verify_target_origin(db, target: dict, conversation_id: str | None = None) -> None:
    """Refuse Slack/Linear targets that are not the origin of a Loma conversation.

    The Slack ingress stamps every run it starts with the channel/thread it
    came from, and the Linear webhook stamps runs with the issue id — both
    set server-side from authenticated events, never by the agent. A target
    that matches no such conversation was not where this PR was announced.
    Loma inbox targets are HMAC-gated in ``_build_target`` instead.
    """
    target_type = target.get("type")
    if target_type == "slack":
        query = {
            "metadata.slack_channel_id": target["channel"],
            "metadata.slack_thread_ts": target["thread_ts"],
        }
        what = f"Slack thread {target['channel']}/{target['thread_ts']}"
    elif target_type == "linear":
        query = {"metadata.linear_issue_id": target["issue_id"]}
        what = f"Linear issue {target['issue_id']}"
    else:
        return
    if conversation_id:
        query["conversation_id"] = conversation_id
    found = await db.conversations.find_one(query, {"conversation_id": 1})
    if not found:
        scope = f" for conversation {conversation_id}" if conversation_id else ""
        raise ValueError(
            f"{what} is not the origin of any Loma conversation{scope} — refusing to "
            f"register it as a follow-up target. Register the thread/issue this run "
            f"was started from."
        )


async def _resolve_target_from_conversation(db, conversation_id: str) -> dict:
    """Derive the follow-up target from the conversation's server-stamped origin.

    The Slack ingress stamps ``metadata.slack_channel_id`` / ``slack_thread_ts``
    and the Linear webhook stamps ``metadata.linear_issue_id`` on every run it
    starts, from authenticated events the agent cannot influence. Reading the
    target from there means the target IS the origin — no separate origin check
    is needed, and a run only ever has to know its own conversation ID.
    Dashboard runs have neither and must register a Loma inbox target with
    ``--user-email`` (HMAC-gated) instead.
    """
    found = await db.conversations.find_one(
        {"conversation_id": conversation_id}, {"conversation_id": 1, "metadata": 1}
    )
    if not found:
        raise ValueError(
            f"Conversation {conversation_id} not found — refusing to register a "
            f"follow-up target for it. Pass the conversation ID from this run's prompt."
        )
    metadata = found.get("metadata") or {}
    channel = metadata.get("slack_channel_id")
    thread_ts = metadata.get("slack_thread_ts")
    issue_id = metadata.get("linear_issue_id")
    if channel and thread_ts:
        target = {"type": "slack", "channel": channel, "thread_ts": thread_ts}
    elif issue_id:
        target = {"type": "linear", "issue_id": issue_id}
    else:
        raise ValueError(
            f"Conversation {conversation_id} did not start from a Slack thread or a "
            f"Linear issue, so there is nothing to resolve. Dashboard runs must register "
            f"a Loma inbox target with --user-email (and the requester's auth token)."
        )
    target["conversation_id"] = conversation_id
    return target


async def _cmd_register(args: argparse.Namespace) -> int:
    target = _build_target(args)
    client, db = _get_db()
    try:
        if target is None:
            target = await _resolve_target_from_conversation(db, args.conversation_id)
        else:
            await _verify_target_origin(db, target, args.conversation_id)
        result = await register_pr_notification_target(db, args.repo, args.pr, target)
    finally:
        client.close()
    # Self-review was disabled on this deploy before a target existed, so the
    # "self-review skipped" notice was delivered (or attempted) from THIS
    # process during registration. "failed" means the target has NOT been told
    # and, for a single-push PR, nothing will retry — say so instead of
    # printing an unqualified `registered: true`.
    disabled_notice = result.get("disabled_notice")
    if disabled_notice == "failed":
        print(
            "warning: self-review is disabled on this deploy and the 'self-review "
            "skipped' notice could not be delivered to the registered target from "
            "this process (missing channel credentials?). Tell the requester "
            "directly that no verdict will follow.",
            file=sys.stderr,
        )
    print(json.dumps({
        "registered": True,
        "repo": args.repo,
        "pr_number": args.pr,
        "target": target,
        "disabled_notice": disabled_notice,
    }))
    return 0


async def _cmd_show(args: argparse.Namespace) -> int:
    client, db = _get_db()
    try:
        record = await get_pr_notification_target(db, args.repo, args.pr)
    finally:
        client.close()
    if not record:
        print(json.dumps({"found": False, "repo": args.repo, "pr_number": args.pr}))
        return 1
    record.pop("_id", None)
    print(json.dumps({"found": True, **record}, default=str, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="Register the follow-up target for a PR")
    reg.add_argument("--repo", required=True, help="Repo in owner/name form")
    reg.add_argument("--pr", required=True, type=int, help="PR number")
    reg.add_argument("--slack-channel", help="Slack channel ID (with --thread-ts)")
    reg.add_argument("--thread-ts", help="Slack thread timestamp (with --slack-channel)")
    reg.add_argument("--linear-issue-id", help="Linear issue UUID")
    reg.add_argument("--user-email", help="Loma inbox target: user email (requires the user's auth token)")
    reg.add_argument(
        "--auth-token",
        help=f"Personal auth token for --user-email (HMAC-verified). Prefer the "
             f"{AUTH_TOKEN_ENV} environment variable so the token stays out of the process list",
    )
    reg.add_argument(
        "--conversation-id",
        help="Loma conversation this registration belongs to. On its own, resolves the "
             "target from the Slack thread / Linear issue the conversation was started "
             "from; with an explicit target it pins the origin check to that conversation "
             "and lets inbox notifications deep-link back",
    )

    show = sub.add_parser("show", help="Show the registered target for a PR")
    show.add_argument("--repo", required=True, help="Repo in owner/name form")
    show.add_argument("--pr", required=True, type=int, help="PR number")

    args = parser.parse_args()
    try:
        if args.command == "register":
            return asyncio.run(_cmd_register(args))
        return asyncio.run(_cmd_show(args))
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
