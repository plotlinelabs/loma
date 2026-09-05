"""CLI to resolve PR review threads via the GitHub GraphQL API.

The code-review agent invokes this tool during a re-evaluation pass to
mark prior review threads as resolved once it has verified the underlying
issue was addressed in the latest commits.

Usage:
  python3 tools/github_pr_resolve.py resolve --thread-id <graphql_node_id>
  python3 tools/github_pr_resolve.py list-unresolved --repo <owner>/<name> --pr <num>

Requires GITHUB_API_KEY in the environment (same token used by the webhook).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# When invoked via `python3 tools/github_pr_resolve.py ...`, only the `tools/`
# directory is on sys.path by default — the repo root is not. Add it so the
# `webhooks.*` import below resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Reuse the existing async GraphQL helpers so auth/SSL/error handling stay
# identical to the webhook path.
from webhooks.github_graphql import (  # noqa: E402
    get_agent_review_threads,
    resolve_review_thread,
)


async def _cmd_resolve(thread_id: str) -> int:
    ok = await resolve_review_thread(thread_id)
    print(json.dumps({"thread_id": thread_id, "resolved": ok}))
    return 0 if ok else 1


async def _cmd_list_unresolved(repo: str, pr_number: int) -> int:
    if "/" not in repo:
        print(json.dumps({"error": "--repo must be in owner/name form"}), file=sys.stderr)
        return 2
    owner, name = repo.split("/", 1)
    threads = await get_agent_review_threads(owner, name, pr_number)
    unresolved = [t for t in threads if not t.get("is_resolved")]
    # Trim comment bodies so the agent gets a compact, stable payload.
    compact = [
        {
            "thread_id": t["id"],
            "path": t.get("path"),
            "line": t.get("line"),
            "first_comment": (t.get("comments") or [{}])[0].get("body", "")[:2000],
        }
        for t in unresolved
    ]
    print(json.dumps({"unresolved_count": len(compact), "threads": compact}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve PR review threads.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser("resolve", help="Resolve a single review thread by node ID.")
    p_resolve.add_argument("--thread-id", required=True, help="GraphQL node ID of the review thread.")

    p_list = sub.add_parser(
        "list-unresolved",
        help="List unresolved review threads authored by the agent on a PR.",
    )
    p_list.add_argument("--repo", required=True, help="Repository in owner/name form.")
    p_list.add_argument("--pr", required=True, type=int, help="PR number.")

    args = parser.parse_args()

    if args.command == "resolve":
        return asyncio.run(_cmd_resolve(args.thread_id))
    if args.command == "list-unresolved":
        return asyncio.run(_cmd_list_unresolved(args.repo, args.pr))

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
