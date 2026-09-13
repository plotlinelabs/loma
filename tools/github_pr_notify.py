"""CLI to register where a PR's self-review verdict should be threaded back.

Stage 1 of the two-stage PR notification: right after a flow creates an
agent PR and announces it ("PR ready — self-review running…"), it runs
this tool to record WHERE it announced it. When the fresh-context
self-review completes, the webhook pipeline (webhooks/github.py →
utils/pr_followup.py) posts the verdict back to that same place.

Exactly one target type must be provided per registration:

  # Slack thread (implement-ticket conversations started from Slack)
  python3 tools/github_pr_notify.py register --repo <owner>/<name> --pr <num> \
      --slack-channel C0123ABC --thread-ts 1700000000.123456

  # Linear issue (Linear webhook flows) — pass the issue UUID, not ENG-123
  python3 tools/github_pr_notify.py register --repo <owner>/<name> --pr <num> \
      --linear-issue-id <linear-issue-uuid>

  # Loma inbox (dashboard conversations)
  python3 tools/github_pr_notify.py register --repo <owner>/<name> --pr <num> \
      --user-email person@example.com [--conversation-id <uuid>]

  # Inspect the current registration
  python3 tools/github_pr_notify.py show --repo <owner>/<name> --pr <num>

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


def _get_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("OBSERVABILITY_MONGODB_URI environment variable is not set")
    db_name = os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability").strip()
    client = AsyncIOMotorClient(uri)
    return client, client[db_name]


def _build_target(args: argparse.Namespace) -> dict:
    provided = []
    if args.slack_channel or args.thread_ts:
        provided.append("slack")
    if args.linear_issue_id:
        provided.append("linear")
    if args.user_email:
        provided.append("loma")

    if len(provided) != 1:
        raise ValueError(
            "Provide exactly one target: --slack-channel + --thread-ts, "
            "or --linear-issue-id, or --user-email"
        )

    target_type = provided[0]
    if target_type == "slack":
        if not (args.slack_channel and args.thread_ts):
            raise ValueError("Slack targets require both --slack-channel and --thread-ts")
        return {"type": "slack", "channel": args.slack_channel, "thread_ts": args.thread_ts}
    if target_type == "linear":
        return {"type": "linear", "issue_id": args.linear_issue_id}
    target: dict = {"type": "loma", "user_email": args.user_email}
    if args.conversation_id:
        target["conversation_id"] = args.conversation_id
    return target


async def _cmd_register(args: argparse.Namespace) -> int:
    target = _build_target(args)
    client, db = _get_db()
    try:
        await register_pr_notification_target(db, args.repo, args.pr, target)
    finally:
        client.close()
    print(json.dumps({
        "registered": True,
        "repo": args.repo,
        "pr_number": args.pr,
        "target": target,
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
    reg.add_argument("--user-email", help="Loma inbox target: user email")
    reg.add_argument("--conversation-id", help="Optional Loma conversation to deep-link")

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
