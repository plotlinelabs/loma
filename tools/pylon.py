"""Pylon support ticket API client.

Provides CLI commands for the Loma agent:
  1. pylon.py issue <id>                          — Fetch issue details
  2. pylon.py messages <id>                       — List messages for an issue
  3. pylon.py threads <id>                        — List threads for an issue
  4. pylon.py teams                               — List all teams
  5. pylon.py issues [--days N] [--state S] [--team T] — Search issues (last N days)
  6. echo '<html>' | pylon.py reply <id> <message_id> [--to E] [--cc E ...] [--attachment P ...] — Post customer-facing reply (with optional attachments)
  7. echo '<html>' | pylon.py note <id> [--thread T | --message M] [--attachment P ...] — Post internal note (with optional attachments)
  8. pylon.py update <id> --state <state>          — Update issue (e.g. close, waiting_on_customer)
  9. pylon.py create-thread <id> <name>            — Create a new thread

Requires PYLON_API_KEY environment variable.
API docs: https://docs.usepylon.com/pylon-docs/developer/api/api-reference

Usage (called by the agent via Bash):
  python3 tools/pylon.py teams
  python3 tools/pylon.py issues --days 3 --state new,waiting_on_you
  python3 tools/pylon.py issue abc123
  python3 tools/pylon.py messages abc123
"""

import asyncio
import html
import json
import mimetypes
import os
import re
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

PYLON_BASE_URL = "https://api.usepylon.com"


def _get_api_key() -> str:
    key = os.environ.get("PYLON_API_KEY", "")
    if not key:
        raise ValueError(
            "PYLON_API_KEY environment variable is not set. "
            "Please configure it before using Pylon tools."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }


async def _api_get(path: str) -> dict[str, Any]:
    """Shared GET helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{PYLON_BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                async with session.get(
                    url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 401:
                        return {"error": "Pylon API key is invalid or expired."}
                    if resp.status == 404:
                        return {"error": f"Not found: {path}"}
                    if resp.status == 429:
                        if attempt == 2:
                            return {"error": "Pylon rate limit reached. Try again shortly."}
                        retry_after = min(30, max(1, int(resp.headers.get("Retry-After", "2"))))
                        await asyncio.sleep(retry_after * (attempt + 1))
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        return {"error": f"Pylon API error (HTTP {resp.status}): {text[:500]}"}
                    return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Pylon API: {e}"}


async def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Shared POST helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{PYLON_BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                async with session.post(
                    url, headers=_headers(), json=body, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 401:
                        return {"error": "Pylon API key is invalid or expired."}
                    if resp.status == 429:
                        if attempt == 2:
                            return {"error": "Pylon rate limit reached. Try again shortly."}
                        retry_after = min(30, max(1, int(resp.headers.get("Retry-After", "2"))))
                        await asyncio.sleep(retry_after * (attempt + 1))
                        continue
                    if resp.status not in (200, 201):
                        text = await resp.text()
                        return {"error": f"Pylon API error (HTTP {resp.status}): {text[:500]}"}
                    return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Pylon API: {e}"}


async def _api_patch(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Shared PATCH helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{PYLON_BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                url, headers=_headers(), json=body, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "Pylon API key is invalid or expired."}
                if resp.status == 429:
                    return {"error": "Pylon rate limit reached. Try again shortly."}
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return {"error": f"Pylon API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Pylon API: {e}"}


async def _api_post_multipart(
    path: str,
    fields: dict[str, str],
    files: list[tuple[str, str, bytes]] | None = None,
) -> dict[str, Any]:
    """POST with multipart/form-data encoding for file attachments.

    Args:
        path: API endpoint path.
        fields: Form fields as key-value pairs.
        files: List of (filename, content_type, file_data) tuples.

    Returns:
        Parsed JSON response or {"error": "..."}.
    """
    url = f"{PYLON_BASE_URL}{path}"
    try:
        data = aiohttp.FormData()
        for key, value in fields.items():
            data.add_field(key, value)

        if files:
            for filename, file_content_type, file_data in files:
                data.add_field(
                    "attachments",
                    file_data,
                    filename=filename,
                    content_type=file_content_type,
                )

        headers = {
            "Authorization": f"Bearer {_get_api_key()}",
            "Accept": "*/*",
            # Note: Content-Type is set automatically by aiohttp for FormData
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 401:
                    return {"error": "Pylon API key is invalid or expired."}
                if resp.status == 429:
                    return {"error": "Pylon rate limit reached. Try again shortly."}
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return {"error": f"Pylon API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Pylon API: {e}"}


def _load_attachment_files(
    attachment_paths: list[str],
) -> list[tuple[str, str, bytes]]:
    """Load files from disk and return as (filename, content_type, data) tuples.

    Raises ValueError if any file is missing or too large (>10 MB).
    """
    max_size = 10 * 1024 * 1024  # conservative public default
    result = []
    for path in attachment_paths:
        if not os.path.isfile(path):
            raise ValueError(f"Attachment file not found: {path}")
        file_size = os.path.getsize(path)
        if file_size > max_size:
            raise ValueError(
                f"Attachment too large: {path} ({file_size / 1024 / 1024:.1f} MB). "
                f"Configured limit is 10 MB."
            )
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            result.append((os.path.basename(path), content_type, f.read()))
    return result


async def _upload_attachment(
    filename: str, content_type: str, file_data: bytes
) -> str:
    """Upload a single file to Pylon's POST /attachments endpoint.

    Pylon requires a two-step attachment flow:
      1. Upload the file here to get a hosted URL.
      2. Pass the URL(s) in ``attachment_urls`` when calling reply/note.

    Returns the hosted attachment URL string.
    Raises RuntimeError if the upload fails.
    """
    url = f"{PYLON_BASE_URL}/attachments"
    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Accept": "*/*",
    }
    data = aiohttp.FormData()
    data.add_field(
        "file",
        file_data,
        filename=filename,
        content_type=content_type,
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    raise RuntimeError(
                        f"Pylon attachment upload failed (HTTP {resp.status}): {text[:500]}"
                    )
                result = await resp.json()
                attachment_url = result.get("data", {}).get("url")
                if not attachment_url:
                    raise RuntimeError(
                        f"Pylon attachment upload returned no URL: {json.dumps(result)[:500]}"
                    )
                return attachment_url
    except aiohttp.ClientError as e:
        raise RuntimeError(f"Failed to upload attachment to Pylon: {e}") from e


async def _upload_attachments(attachment_paths: list[str]) -> list[str]:
    """Upload multiple files and return their hosted URLs.

    Validates sizes, reads files from disk, uploads each to Pylon's
    ``POST /attachments`` endpoint, and returns the list of URLs to
    embed in a reply or note via the ``attachment_urls`` field.
    """
    file_tuples = _load_attachment_files(attachment_paths)
    urls: list[str] = []
    for filename, content_type, file_data in file_tuples:
        attachment_url = await _upload_attachment(filename, content_type, file_data)
        urls.append(attachment_url)
    return urls


# ---------------------------------------------------------------------------
# Public async functions (importable by webhooks/pylon.py and other modules)
# ---------------------------------------------------------------------------


async def get_issue(issue_id: str) -> dict[str, Any]:
    """Fetch issue details by ID."""
    return await _api_get(f"/issues/{issue_id}")


async def get_messages(issue_id: str) -> dict[str, Any]:
    """Fetch all messages for an issue."""
    return await _api_get(f"/issues/{issue_id}/messages")


def normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    """Return the safe, display-ready fields from a Pylon message."""
    raw_body = (
        message.get("message_html")
        or message.get("body_html")
        or message.get("body")
        or message.get("text")
        or ""
    )
    body = re.sub(r"(?i)<br\s*/?>", "\n", str(raw_body))
    body = re.sub(r"(?i)</(?:p|div|li|blockquote)>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body)
    body = html.unescape(body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    author = message.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    return {
        "id": str(message.get("id") or ""),
        "body": body,
        "author": author.get("name") or "Unknown sender",
        "timestamp": message.get("timestamp") or message.get("created_at"),
        "source": message.get("source"),
        "is_private": bool(
            message.get("is_private", False)
            or message.get("private", False)
            or message.get("message_type") in {"internal_note", "note"}
            or message.get("source") in {"internal_note", "note"}
        ),
    }


async def get_threads(issue_id: str) -> dict[str, Any]:
    """Fetch all threads for an issue."""
    return await _api_get(f"/issues/{issue_id}/threads")


def issue_web_url(issue: dict[str, Any]) -> str | None:
    """Return the Plotline Pylon issue URL used by the support workspace."""
    number = issue.get("number")
    if number is None:
        return issue.get("link") or issue.get("url") or issue.get("web_url")
    view_id = os.getenv(
        "PYLON_SUPPORT_VIEW_ID",
        "ab8a8a4e-a550-4c1b-9479-c00066f233cb",
    )
    return (
        f"https://app.usepylon.com/support/issues/views/{view_id}"
        f"?issueNumber={number}"
    )


async def get_teams() -> dict[str, Any]:
    """Fetch all teams."""
    return await _api_get("/teams")


async def list_issues(
    days: int = 7,
    state: str | None = None,
    team_id: str | None = None,
    limit: int = 100,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Search issues from the last N days with optional filters.

    Uses POST /issues/search with created_at time_is_after filter.
    Auto-paginates up to max_pages.
    """
    after_dt = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    filters: list[dict[str, Any]] = [
        {"field": "created_at", "operator": "time_is_after", "value": after_dt},
    ]

    if state:
        states = [s.strip() for s in state.split(",")]
        filters.append({"field": "state", "operator": "in", "value": states})

    if team_id:
        filters.append({"field": "team_id", "operator": "equals", "value": team_id})

    all_issues: list[dict[str, Any]] = []
    cursor: str | None = None

    for _ in range(max_pages):
        body: dict[str, Any] = {
            "filter": {"op": "and", "value": filters} if len(filters) > 1 else filters[0],
            "limit": limit,
        }
        if cursor:
            body["cursor"] = cursor

        result = await _api_post("/issues/search", body)
        if "error" in result:
            if all_issues:
                break  # return what we have
            return result

        page = result.get("data", [])
        all_issues.extend(page)

        pagination = result.get("pagination", {})
        if not pagination.get("has_next_page"):
            break
        cursor = pagination.get("cursor")
        if not cursor:
            break

    # Return compact summaries
    summaries = []
    for issue in all_issues:
        summaries.append({
            "id": issue.get("id", ""),
            "title": issue.get("title", ""),
            "state": issue.get("state", ""),
            "team_id": issue.get("team_id", ""),
            "created_at": issue.get("created_at", ""),
            "updated_at": issue.get("updated_at", ""),
            "assignee": issue.get("assignee"),
            "customer": (issue.get("account") or {}).get("name", ""),
            "customer_id": (issue.get("account") or {}).get("id", ""),
        })

    return {
        "period": f"last {days} days",
        "count": len(summaries),
        "issues": summaries,
    }


async def list_account_issues_page(
    account_id: str,
    *,
    limit: int = 25,
    cursor: str | None = None,
    state: str | None = None,
    assignee_id: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Fetch one lightweight, server-filtered issue page for a Pylon account."""
    filters: list[dict[str, Any]] = [
        {"field": "account_id", "operator": "equals", "value": account_id},
    ]
    if state:
        states = [value.strip() for value in state.split(",") if value.strip()]
        filters.append({
            "field": "state",
            "operator": "equals" if len(states) == 1 else "in",
            "value": states[0] if len(states) == 1 else states,
        })
    if assignee_id:
        filters.append({"field": "assignee_id", "operator": "equals", "value": assignee_id})
    if query:
        filters.append({"field": "title", "operator": "string_contains", "value": query[:100]})
    body: dict[str, Any] = {
        "filter": (
            {"operator": "and", "subfilters": filters}
            if len(filters) > 1
            else filters[0]
        ),
        "limit": min(50, max(1, limit)),
    }
    if cursor:
        body["cursor"] = cursor
    result = await _api_post("/issues/search", body)
    if result.get("error"):
        return result
    def assignee_summary(issue: dict[str, Any]) -> Any:
        """Normalize the assignee shapes returned by different Pylon API versions."""
        assignee = (
            issue.get("assignee")
            or issue.get("assigned_to")
            or issue.get("assignee_user")
            or issue.get("user")
        )
        if assignee:
            return assignee
        assignee_id = issue.get("assignee_id")
        return {"id": assignee_id} if assignee_id else None

    assignee_ids = {
        str(issue.get("assignee_id"))
        for issue in result.get("data", [])
        if issue.get("assignee_id")
    }
    assignee_directory: dict[str, dict[str, Any]] = {}
    if assignee_ids:
        users_result = await _api_get("/users")
        users = (
            users_result.get("data")
            or users_result.get("users")
            or []
        ) if not users_result.get("error") else []
        assignee_directory = {
            str(user.get("id")): user for user in users if str(user.get("id")) in assignee_ids
        }
        missing_ids = assignee_ids - set(assignee_directory)
        if missing_ids:
            fetched = await asyncio.gather(*(_api_get(f"/users/{user_id}") for user_id in missing_ids))
            for user_id, user_result in zip(missing_ids, fetched):
                if user_result.get("error"):
                    continue
                user = user_result.get("data") or user_result
                if isinstance(user, dict):
                    assignee_directory[user_id] = user

    issues = []
    for issue in result.get("data", []):
        assignee = assignee_summary(issue)
        if isinstance(assignee, str):
            assignee = assignee_directory.get(assignee, {"id": assignee})
        elif isinstance(assignee, dict) and assignee.get("id"):
            assignee = {**assignee_directory.get(str(assignee["id"]), {}), **assignee}
        issues.append({
            "id": str(issue.get("id") or ""),
            "number": issue.get("number"),
            "title": issue.get("title") or "Untitled issue",
            "state": issue.get("state") or "unknown",
            "assignee": assignee,
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "account_id": (issue.get("account") or {}).get("id") or issue.get("account_id"),
            "url": issue_web_url(issue),
        })
    pagination = result.get("pagination") or {}
    return {
        "issues": issues,
        "pagination": {
            "next_cursor": pagination.get("cursor") if pagination.get("has_next_page") else None,
            "has_next_page": bool(pagination.get("has_next_page")),
        },
    }


async def search_accounts(query: str, limit: int = 50) -> dict[str, Any]:
    """Search Pylon accounts by name without scanning the issue archive."""
    query = (query or "").strip()
    if not query:
        return {"accounts": []}

    result = await _api_post("/accounts/search", {
        "filter": {
            "field": "name",
            "operator": "string_contains",
            "value": query,
        },
        "limit": min(100, max(1, limit)),
    })
    if "error" in result:
        return result

    accounts = []
    for account in result.get("data", []):
        account_id = str(account.get("id") or "")
        name = str(account.get("name") or "").strip()
        if account_id and name:
            accounts.append({
                "customer_id": account_id,
                "name": name,
                "domains": account.get("domains") or [],
            })
    return {"accounts": accounts}


async def reply(
    issue_id: str,
    body_html: str,
    message_id: str,
    email_info: dict[str, Any] | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Post a customer-facing reply to an issue, optionally with file attachments.

    For email-sourced tickets, email_info should contain:
      {"to_emails": ["a@x.com"], "cc_emails": ["b@x.com", "c@x.com"]}

    Args:
        attachments: List of file paths to attach to the reply.

    Attachment flow: files are first uploaded to POST /attachments to
    obtain hosted URLs, then the URLs are passed in the JSON body via
    the ``attachment_urls`` field.
    """
    payload: dict[str, Any] = {
        "body_html": body_html,
        "message_id": message_id,
    }
    if email_info:
        payload["email_info"] = email_info
    if attachments:
        payload["attachment_urls"] = await _upload_attachments(attachments)
    return await _api_post(f"/issues/{issue_id}/reply", payload)


async def post_note(
    issue_id: str,
    body_html: str,
    thread_id: str | None = None,
    message_id: str | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Post an internal note on an issue, optionally with file attachments.

    Args:
        attachments: List of file paths to attach to the note.

    Attachment flow: files are first uploaded to POST /attachments to
    obtain hosted URLs, then the URLs are passed in the JSON body via
    the ``attachment_urls`` field.
    """
    payload: dict[str, Any] = {"body_html": body_html}
    if thread_id:
        payload["thread_id"] = thread_id
    if message_id:
        payload["message_id"] = message_id
    if attachments:
        payload["attachment_urls"] = await _upload_attachments(attachments)
    return await _api_post(f"/issues/{issue_id}/note", payload)


async def update_issue(issue_id: str, **fields: Any) -> dict[str, Any]:
    """Update issue fields (e.g. state)."""
    return await _api_patch(f"/issues/{issue_id}", fields)


async def create_thread(issue_id: str, name: str) -> dict[str, Any]:
    """Create a new thread on an issue."""
    return await _api_post(f"/issues/{issue_id}/threads", {"name": name})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_usage():
    print("Usage:")
    print("  python3 tools/pylon.py issue <id>")
    print("    Fetch issue details")
    print()
    print("  python3 tools/pylon.py messages <id>")
    print("    List messages for an issue")
    print()
    print("  python3 tools/pylon.py threads <id>")
    print("    List threads for an issue")
    print()
    print("  python3 tools/pylon.py teams")
    print("    List all teams")
    print()
    print("  python3 tools/pylon.py issues [--days N] [--state S] [--team T]")
    print("    Search issues from the last N days (default 7)")
    print("    --state: filter by state (comma-separated, e.g. new,waiting_on_you)")
    print("    --team: filter by team ID")
    print()
    print("  echo '<html>' | python3 tools/pylon.py reply <id> <message_id> [--to E] [--cc E ...] [--attachment P ...]")
    print("    Post a customer-facing reply (body_html on stdin), optionally with file attachments")
    print()
    print("  echo '<html>' | python3 tools/pylon.py note <id> [--thread T] [--message M] [--attachment P ...]")
    print("    Post an internal note (body_html on stdin), optionally with file attachments")
    print()
    print("  python3 tools/pylon.py update <id> --state <state>")
    print("    Update issue state (e.g. closed, waiting_on_customer)")
    print()
    print("  python3 tools/pylon.py create-thread <id> <name>")
    print("    Create a new thread on an issue")
    sys.exit(1)


def _parse_flag(args: list[str], flag: str, nargs: int = 1) -> str | list[str] | None:
    """Extract a flag and its value(s) from args list, mutating args in place."""
    if flag not in args:
        return None
    idx = args.index(flag)
    if idx + nargs >= len(args):
        print(f"Error: {flag} requires {nargs} argument(s)")
        sys.exit(1)
    if nargs == 1:
        val = args[idx + 1]
        del args[idx : idx + 2]
        return val
    vals = args[idx + 1 : idx + 1 + nargs]
    del args[idx : idx + 1 + nargs]
    return vals


def _collect_flag_list(args: list[str], flag: str) -> list[str]:
    """Collect all values for a repeatable flag (e.g. --cc a --cc b)."""
    values = []
    while flag in args:
        idx = args.index(flag)
        if idx + 1 >= len(args):
            print(f"Error: {flag} requires an argument")
            sys.exit(1)
        values.append(args[idx + 1])
        del args[idx : idx + 2]
    return values


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = list(sys.argv[2:])

    if command == "issue":
        if not rest:
            print("Error: issue requires an issue ID")
            sys.exit(1)
        result = asyncio.run(get_issue(rest[0]))

    elif command == "messages":
        if not rest:
            print("Error: messages requires an issue ID")
            sys.exit(1)
        result = asyncio.run(get_messages(rest[0]))

    elif command == "threads":
        if not rest:
            print("Error: threads requires an issue ID")
            sys.exit(1)
        result = asyncio.run(get_threads(rest[0]))

    elif command == "teams":
        result = asyncio.run(get_teams())

    elif command == "issues":
        days_str = _parse_flag(rest, "--days")
        days = int(days_str) if days_str else 7
        state = _parse_flag(rest, "--state")
        team = _parse_flag(rest, "--team")
        result = asyncio.run(list_issues(days=days, state=state, team_id=team))

    elif command == "reply":
        to_emails = _collect_flag_list(rest, "--to")
        cc_emails = _collect_flag_list(rest, "--cc")
        attach_paths = _collect_flag_list(rest, "--attachment")
        if len(rest) < 2:
            print("Error: reply requires <issue_id> <message_id>, then body_html on stdin")
            sys.exit(1)
        issue_id, message_id = rest[0], rest[1]
        body_html = sys.stdin.read().strip()
        if not body_html:
            print("Error: body_html must be provided on stdin")
            sys.exit(1)
        ei = None
        if to_emails:
            ei = {"to_emails": to_emails, "cc_emails": cc_emails}
        result = asyncio.run(reply(issue_id, body_html, message_id, email_info=ei,
                                   attachments=attach_paths or None))

    elif command == "note":
        thread = _parse_flag(rest, "--thread")
        message = _parse_flag(rest, "--message")
        attach_paths = _collect_flag_list(rest, "--attachment")
        if len(rest) < 1:
            print("Error: note requires <issue_id>, then body_html on stdin")
            sys.exit(1)
        body_html = sys.stdin.read().strip()
        if not body_html:
            print("Error: body_html must be provided on stdin")
            sys.exit(1)
        result = asyncio.run(post_note(rest[0], body_html, thread_id=thread, message_id=message,
                                       attachments=attach_paths or None))

    elif command == "update":
        # Support both --state (correct) and --status (legacy alias) flags
        state_val = _parse_flag(rest, "--state")
        if not state_val:
            state_val = _parse_flag(rest, "--status")  # legacy alias
        if not rest:
            print("Error: update requires an issue ID")
            sys.exit(1)
        kwargs = {}
        if state_val:
            # Pylon API uses "state" field, not "status"
            kwargs["state"] = state_val
        result = asyncio.run(update_issue(rest[0], **kwargs))

    elif command == "create-thread":
        if len(rest) < 2:
            print("Error: create-thread requires <issue_id> <name>")
            sys.exit(1)
        result = asyncio.run(create_thread(rest[0], rest[1]))

    else:
        print(f"Unknown command: {command}")
        _print_usage()

    print(json.dumps(result, indent=2))
