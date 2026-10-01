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
 10. pylon.py users                              — Resolve assignee IDs
 11. pylon.py download-attachment <id> <message_id> <index> <output_path>

Search supports --account, --requester, --assignee and --query; --days 0 searches
all time. Updates accept --team and --assignee (empty string unassigns).
List commands support --max-pages and --cursor, returning complete/next_cursor.
Partial results and errors exit nonzero; inspect JSON before taking further action.
Attachment indexes are zero-based entries from a message's file_urls list.

Requires PYLON_API_KEY environment variable.
API docs: https://docs.usepylon.com/pylon-docs/developer/api/api-reference

Usage (called by the agent via Bash):
  python3 tools/pylon.py teams
  python3 tools/pylon.py issues --days 3 --state new,waiting_on_you
  python3 tools/pylon.py issue abc123
  python3 tools/pylon.py messages abc123
"""

import asyncio
import json
import mimetypes
import os
import sys
import logging
import ipaddress
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urljoin
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

PYLON_BASE_URL = "https://api.usepylon.com"


def _get_api_key() -> str:
    key = os.environ.get("PYLON_API_KEY", "")
    if not key:
        from tools._integration_key import get_integration_key
        key = get_integration_key("pylon")
    if not key:
        raise ValueError(
            "PYLON_API_KEY not set and no Pylon integration found on the dashboard."
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
            async with session.get(
                url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 401:
                    return {"error": "Pylon API key is invalid or expired."}
                if resp.status == 404:
                    return {"error": f"Not found: {path}"}
                if resp.status == 429:
                    return {"error": "Pylon rate limit reached. Try again shortly."}
                if resp.status != 200:
                    text = await resp.text()
                    return {"error": f"Pylon API error (HTTP {resp.status}): {text[:500]}"}
                return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return {"error": f"Failed to connect to Pylon API: {e}"}


async def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Shared POST helper. Returns parsed JSON or {"error": "..."}."""
    url = f"{PYLON_BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
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
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
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
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
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
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
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
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
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


async def _paginate(fetch_page, max_pages: int = 100, cursor: str | None = None) -> dict[str, Any]:
    """Collect cursor pages without silently treating truncated results as complete."""
    if max_pages < 1:
        return {"error": "max_pages must be positive", "data": [], "complete": False}
    rows, seen_ids, seen_cursors = [], set(), set()
    for page_number in range(1, max_pages + 1):
        result = await fetch_page(cursor)
        if "error" in result:
            return {**result, "data": rows, "complete": False, "next_cursor": cursor}
        page = result.get("data")
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            return {"error": "Invalid Pylon list response", "data": rows, "complete": False,
                    "next_cursor": cursor}
        for row in page:
            row_id = row.get("id")
            if not row_id or row_id not in seen_ids:
                rows.append(row)
                if row_id:
                    seen_ids.add(row_id)
        pagination = result.get("pagination") or {}
        if not pagination.get("has_next_page"):
            return {"data": rows, "complete": True, "pages": page_number, "next_cursor": None}
        next_cursor = pagination.get("cursor")
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            return {"error": "Pylon pagination cursor missing or repeated", "data": rows,
                    "complete": False, "next_cursor": next_cursor}
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return {"data": rows, "complete": False, "pages": max_pages, "next_cursor": cursor,
            "warning": "Page limit reached; resume with next_cursor"}


async def _get_all(path: str, max_pages: int = 100, cursor: str | None = None,
                   limit: int | None = None) -> dict[str, Any]:
    async def fetch(current):
        params = {}
        if limit is not None:
            params["limit"] = limit
        if current:
            params["cursor"] = current
        return await _api_get(path + ("?" + urlencode(params) if params else ""))
    return await _paginate(fetch, max_pages, cursor)


async def get_messages(issue_id: str, max_pages: int = 100,
                       cursor: str | None = None) -> dict[str, Any]:
    """Fetch messages, including attachments and authors, across all bounded pages."""
    return await _get_all(f"/issues/{issue_id}/messages", max_pages, cursor, limit=100)


async def get_threads(issue_id: str, max_pages: int = 100,
                      cursor: str | None = None) -> dict[str, Any]:
    return await _get_all(f"/issues/{issue_id}/threads", max_pages, cursor)


async def get_teams(max_pages: int = 100, cursor: str | None = None) -> dict[str, Any]:
    return await _get_all("/teams", max_pages, cursor)


async def get_users(max_pages: int = 100, cursor: str | None = None) -> dict[str, Any]:
    """List assignable users so callers do not guess owner IDs."""
    return await _get_all("/users", max_pages, cursor)


async def list_issues(
    days: int = 7,
    state: str | None = None,
    team_id: str | None = None,
    limit: int = 100,
    max_pages: int = 100,
    account_id: str | None = None,
    requester_id: str | None = None,
    assignee_id: str | None = None,
    query: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Search related tickets using documented Pylon filters. days=0 means all time."""
    if days < 0 or not 1 <= limit < 1000:
        return {"error": "days must be nonnegative and limit must be between 1 and 999"}
    filters = []
    if days:
        after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        filters.append({"field": "created_at", "operator": "time_is_after", "value": after})
    if state:
        filters.append({"field": "state", "operator": "in",
                        "values": [s.strip() for s in state.split(",") if s.strip()]})
    for field, value in (("team_id", team_id), ("account_id", account_id),
                         ("requester_id", requester_id), ("assignee_id", assignee_id)):
        if value:
            filters.append({"field": field, "operator": "equals", "value": value})
    body = {"limit": limit}
    if filters:
        body["filter"] = filters[0] if len(filters) == 1 else {
            "operator": "and", "subfilters": filters}
    if query:
        body["search_text"] = query

    async def fetch(current):
        return await _api_post("/issues/search", {**body, **({"cursor": current} if current else {})})

    result = await _paginate(fetch, max_pages, cursor)
    issues = result.pop("data", [])
    summaries = [{
        "id": issue.get("id", ""), "title": issue.get("title", ""),
        "state": issue.get("state", ""), "team_id": issue.get("team_id", ""),
        "created_at": issue.get("created_at", ""),
        "customer": (issue.get("account") or {}).get("name", ""),
        "account_id": (issue.get("account") or {}).get("id"),
        "requester": issue.get("requester"), "assignee": issue.get("assignee"),
        "link": issue.get("link"),
    } for issue in issues]
    return {**result, "period": f"last {days} days" if days else "all time",
            "count": len(summaries), "issues": summaries}


class _PublicResolver(aiohttp.resolver.DefaultResolver):
    """Reject private DNS results in the actual connection path (including redirects)."""
    async def resolve(self, host, port=0, family=0):
        records = await super().resolve(host, port, family)
        if any(not ipaddress.ip_address(record["host"]).is_global for record in records):
            raise ValueError("Attachment host must resolve to public addresses")
        return records


def _validate_attachment_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443)):
        raise ValueError("Attachments require a public HTTPS URL on port 443")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return  # DNS is validated by the connector's resolver.
    if not address.is_global:
        raise ValueError("Attachment host must be public")


async def download_attachment(issue_id: str, message_id: str, index: int,
                              output_path: str, max_bytes: int = 25 * 1024 * 1024) -> dict[str, Any]:
    """Download a message's zero-based file index, never arbitrary caller-supplied URLs.

    No API credentials are forwarded to file hosts. Existing files are never overwritten.
    """
    if index < 0 or max_bytes < 1:
        return {"error": "index must be nonnegative and max_bytes must be positive"}
    messages = await get_messages(issue_id)
    if not messages.get("complete"):
        return {"error": "Cannot resolve attachment from an incomplete message history"}
    message = next((m for m in messages["data"] if m.get("id") == message_id), None)
    urls = (message or {}).get("file_urls") or []
    if index >= len(urls):
        return {"error": "Message or attachment index not found on this issue"}
    target, created = Path(output_path), False
    try:
        url = urls[index]
        connector = aiohttp.TCPConnector(resolver=_PublicResolver())
        async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
            for _ in range(6):
                _validate_attachment_url(url)
                async with session.get(url, allow_redirects=False,
                                       timeout=aiohttp.ClientTimeout(total=60)) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("Attachment redirect has no location")
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise ValueError(f"Attachment download failed (HTTP {response.status})")
                    if response.content_length is not None and response.content_length > max_bytes:
                        raise ValueError("Attachment exceeds download size limit")
                    size = 0
                    with target.open("xb") as output:
                        created = True
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > max_bytes:
                                raise ValueError("Attachment exceeds download size limit")
                            output.write(chunk)
                    return {"path": str(target.resolve()), "bytes": size,
                            "issue_id": issue_id, "message_id": message_id, "index": index}
            raise ValueError("Too many attachment redirects")
    except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
        if created:
            target.unlink(missing_ok=True)
        # Signed URLs and credentials must not appear in error output.
        return {"error": "Attachment download failed: check public HTTPS access, file size, "
                         "and that the output path does not already exist"}


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
    """Update supplied fields only; empty owner/team strings explicitly unassign."""
    if not fields:
        return {"error": "At least one update field is required"}
    return await _api_patch(f"/issues/{issue_id}", fields)


async def create_thread(issue_id: str, name: str) -> dict[str, Any]:
    """Create a new thread on an issue."""
    return await _api_post(f"/issues/{issue_id}/threads", {"name": name})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_usage():
    print("Usage:")
    print("  users: list assignable user IDs")
    print("  messages/threads/teams/users: --max-pages N --cursor CURSOR")
    print("  issues: --account ID --requester ID --assignee ID --query TEXT")
    print("          --days 0 for all time; --max-pages N --cursor CURSOR")
    print("  update <id>: --team ID --assignee ID (empty string explicitly unassigns)")
    print("  download-attachment <issue_id> <message_id> <zero-based index> <output_path>")
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
    if idx + nargs >= len(args) or args[idx + 1].startswith("--"):
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
        max_pages = int(_parse_flag(rest, "--max-pages") or 100)
        cursor = _parse_flag(rest, "--cursor")
        if not rest:
            print("Error: messages requires an issue ID")
            sys.exit(1)
        result = asyncio.run(get_messages(rest[0], max_pages=max_pages, cursor=cursor))

    elif command == "threads":
        max_pages = int(_parse_flag(rest, "--max-pages") or 100)
        cursor = _parse_flag(rest, "--cursor")
        if not rest:
            print("Error: threads requires an issue ID")
            sys.exit(1)
        result = asyncio.run(get_threads(rest[0], max_pages=max_pages, cursor=cursor))

    elif command in ("teams", "users"):
        max_pages = int(_parse_flag(rest, "--max-pages") or 100)
        cursor = _parse_flag(rest, "--cursor")
        result = asyncio.run((get_teams if command == "teams" else get_users)(max_pages, cursor))

    elif command == "issues":
        days_str = _parse_flag(rest, "--days")
        days = int(days_str) if days_str else 7
        state = _parse_flag(rest, "--state")
        team = _parse_flag(rest, "--team")
        account = _parse_flag(rest, "--account")
        requester = _parse_flag(rest, "--requester")
        assignee = _parse_flag(rest, "--assignee")
        query = _parse_flag(rest, "--query")
        cursor = _parse_flag(rest, "--cursor")
        max_pages = int(_parse_flag(rest, "--max-pages") or 100)
        result = asyncio.run(list_issues(days=days, state=state, team_id=team,
            account_id=account, requester_id=requester, assignee_id=assignee,
            query=query, cursor=cursor, max_pages=max_pages))

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
        team_val = _parse_flag(rest, "--team")
        assignee_val = _parse_flag(rest, "--assignee")
        if len(rest) != 1:
            print("Error: unexpected update arguments")
            sys.exit(1)
        kwargs = {}
        if team_val is not None:
            kwargs["team_id"] = team_val
        if assignee_val is not None:
            kwargs["assignee_id"] = assignee_val
        if state_val:
            # Pylon API uses "state" field, not "status"
            kwargs["state"] = state_val
        result = asyncio.run(update_issue(rest[0], **kwargs))

    elif command == "download-attachment":
        if len(rest) != 4:
            print("Error: download-attachment requires <issue_id> <message_id> <index> <output_path>")
            sys.exit(1)
        result = asyncio.run(download_attachment(rest[0], rest[1], int(rest[2]), rest[3]))

    elif command == "create-thread":
        if len(rest) < 2:
            print("Error: create-thread requires <issue_id> <name>")
            sys.exit(1)
        result = asyncio.run(create_thread(rest[0], rest[1]))

    else:
        print(f"Unknown command: {command}")
        _print_usage()

    print(json.dumps(result, indent=2))
    if "error" in result or result.get("complete") is False:
        sys.exit(1)
