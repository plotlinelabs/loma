"""GitHub PRs linked to a task, shown as chips next to its title.

A task (a conversation) collects PRs from two places:
- PRs the agent opened: the result of a `gh pr create` / GitHub MCP
  create_pull_request call (recorded as soon as the call returns, so the chip
  shows while the task is still running).
- PRs the agent's reply links to: reviews, updates to an existing PR, follow-ups.

Both are stored on the conversation as ``task_prs: [{repo, number, url}]``.
Tasks that finished before this existed have no ``task_prs``; for those the
board derives PRs from the stored reply (``reply_pr_matches`` in the list
projection) so old cards get chips too.
"""
from __future__ import annotations

import re

# owner/repo/pull/<n>. Trailing "/files", "#discussion..." etc. are ignored.
PR_URL_PATTERN = r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([0-9]+)"
_PR_URL_RE = re.compile(PR_URL_PATTERN)

# A card shows a handful; the cap only bounds a runaway reply listing dozens.
MAX_TASK_PRS = 20

_CREATE_PR_TOOLS = {"mcp__github__create_pull_request"}


def _pr(repo: str, number) -> dict:
    repo = str(repo).removesuffix(".git")
    number = int(number)
    return {"repo": repo, "number": number, "url": f"https://github.com/{repo}/pull/{number}"}


def merge_prs(*groups) -> list[dict]:
    """Dedupe PR refs (repo is case-insensitive on GitHub), keeping first-seen order."""
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for group in groups:
        for pr in group or []:
            try:
                ref = _pr(pr["repo"], pr["number"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (ref["repo"].lower(), ref["number"])
            if key in seen:
                continue
            seen.add(key)
            out.append(ref)
            if len(out) >= MAX_TASK_PRS:
                return out
    return out


def extract_prs(text) -> list[dict]:
    """Every distinct PR URL in ``text``, in order."""
    if not text or not isinstance(text, str):
        return []
    return merge_prs({"repo": m.group(1), "number": m.group(2)} for m in _PR_URL_RE.finditer(text))


def is_pr_create_call(tool_name, input_str) -> bool:
    """A tool call that opens a PR, so its output's PR URL is one the task created."""
    if tool_name in _CREATE_PR_TOOLS:
        return True
    return tool_name == "Bash" and "gh pr create" in (input_str or "")


def prs_from_matches(matches) -> list[dict]:
    """PRs from a Mongo ``$regexFindAll`` result (captures = [repo, number])."""
    out = []
    for m in matches or []:
        caps = (m or {}).get("captures") or []
        if len(caps) == 2 and caps[0] and caps[1]:
            out.append({"repo": caps[0], "number": caps[1]})
    return merge_prs(out)


# Board list projection: PRs linked in the stored reply, only for tasks with no
# recorded ``task_prs`` (older tasks). The $type guard keeps one malformed
# document from failing the whole board query.
REPLY_PR_MATCHES_PROJECTION = {
    "$cond": [
        {"$and": [
            {"$eq": [{"$size": {"$ifNull": ["$task_prs", []]}}, 0]},
            {"$eq": [{"$type": "$final_response"}, "string"]},
        ]},
        {"$regexFindAll": {"input": "$final_response", "regex": PR_URL_PATTERN}},
        [],
    ]
}


def task_prs_view(task: dict) -> list[dict]:
    """The PR chips for a board card."""
    return merge_prs(task.get("task_prs"), prs_from_matches(task.get("reply_pr_matches")))
