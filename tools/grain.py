"""Grain meeting recordings, transcripts, and AI summaries.

Provides CLI commands for the Loma agent:
  1. grain.py search <query>              — Search recordings by title
  2. grain.py transcript <id> [--text]    — Get transcript for a recording
  3. grain.py recent [days]               — List recordings from last N days (default 7)

Requires GRAIN_API_TOKEN environment variable (Workspace Access Token).
API docs: https://developers.grain.com
Rate limit: 300 requests/min

Usage (called by the agent via Bash):
  python3 tools/grain.py search "product demo"
  python3 tools/grain.py transcript pppp6666-qq77-rr88-ss99-tttt00000000
  python3 tools/grain.py transcript pppp6666-qq77-rr88-ss99-tttt00000000 --text
  python3 tools/grain.py recent 14
"""

import asyncio
import json
import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

GRAIN_BASE_URL = "https://api.grain.com/_/public-api/v2"
GRAIN_API_VERSION = "2025-10-31"

DEFAULT_INCLUDE = {
    "participants": True,
    "ai_summary": True,
    "ai_action_items": True,
}


def _get_api_token() -> str:
    token = os.environ.get("GRAIN_API_TOKEN", "")
    if not token:
        raise ValueError(
            "GRAIN_API_TOKEN environment variable is not set. "
            "Please configure it before using Grain tools."
        )
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_token()}",
        "Content-Type": "application/json",
        "Public-Api-Version": GRAIN_API_VERSION,
    }


def _format_recording(recording: dict[str, Any]) -> dict[str, Any]:
    """Format a Grain recording into a clean dict for agent consumption."""
    duration_ms = recording.get("duration_ms")
    result: dict[str, Any] = {
        "id": recording.get("id", ""),
        "title": recording.get("title", "Untitled"),
        "date": recording.get("start_datetime", "N/A"),
        "duration_minutes": round(duration_ms / 60000, 1) if duration_ms else "N/A",
        "url": recording.get("url", ""),
        "source": recording.get("source", ""),
    }

    participants = recording.get("participants")
    if participants:
        result["participants"] = [
            {"name": p.get("name", "Unknown"), "email": p.get("email", ""), "scope": p.get("scope", "")}
            for p in participants
            if p.get("confirmed_attendee", True)
        ]

    meeting_type = recording.get("meeting_type")
    if meeting_type:
        result["meeting_type"] = meeting_type.get("name", "")

    ai_summary = recording.get("ai_summary")
    if ai_summary:
        result["ai_summary"] = ai_summary.get("text", "")

    ai_action_items = recording.get("ai_action_items")
    if ai_action_items:
        result["action_items"] = [
            {
                "text": item.get("text", ""),
                "status": item.get("status", ""),
                "assignee": (item.get("assignee") or {}).get("name", ""),
            }
            for item in ai_action_items
        ]

    tags = recording.get("tags")
    if tags:
        result["tags"] = tags

    return result


async def _fetch_all_recordings(
    filter_params: dict[str, Any],
    include: dict[str, Any] | None = None,
    max_pages: int = 20,
) -> dict[str, Any]:
    """Fetch recordings with automatic pagination.

    Returns {"recordings": [...]} on success or {"error": "..."} on failure.
    """
    all_recordings: list[dict[str, Any]] = []
    cursor: str | None = None

    async with aiohttp.ClientSession() as session:
        for _ in range(max_pages):
            body: dict[str, Any] = {
                "filter": filter_params,
                "include": include or DEFAULT_INCLUDE,
            }
            if cursor:
                body["cursor"] = cursor

            try:
                async with session.post(
                    f"{GRAIN_BASE_URL}/recordings",
                    json=body,
                    headers=_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After", "unknown")
                        if all_recordings:
                            break  # return what we have so far
                        return {"error": f"Grain rate limit reached. Retry after {retry_after} seconds."}
                    if resp.status == 401:
                        return {"error": "Grain API token is invalid or expired."}
                    if resp.status != 200:
                        error_text = await resp.text()
                        if all_recordings:
                            break
                        return {"error": f"Grain API error (HTTP {resp.status}): {error_text[:300]}"}
                    data = await resp.json()
            except aiohttp.ClientError as e:
                if all_recordings:
                    break
                return {"error": f"Failed to connect to Grain API: {e}"}

            page = data.get("recordings", [])
            all_recordings.extend(page)

            cursor = data.get("cursor")
            if not cursor:
                break  # no more pages

    return {"recordings": all_recordings}


async def search_recordings(query: str) -> dict[str, Any]:
    """Search Grain recordings by title (auto-paginates)."""
    result = await _fetch_all_recordings({"title_search": query})

    if "error" in result:
        return result

    recordings = result["recordings"]
    if not recordings:
        return {"error": f"No recordings found matching '{query}'. Try a different search term."}

    return {
        "query": query,
        "count": len(recordings),
        "recordings": [_format_recording(r) for r in recordings],
    }


async def get_transcript(recording_id: str, fmt: str = "json") -> dict[str, Any]:
    """Get the transcript for a specific recording."""
    if fmt == "text":
        url = f"{GRAIN_BASE_URL}/recordings/{recording_id}/transcript.txt"
    else:
        url = f"{GRAIN_BASE_URL}/recordings/{recording_id}/transcript"

    headers = _headers()
    if fmt == "text":
        # GET endpoints don't need Content-Type
        del headers["Content-Type"]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "unknown")
                    return {"error": f"Grain rate limit reached. Retry after {retry_after} seconds."}
                if resp.status == 401:
                    return {"error": "Grain API token is invalid or expired."}
                if resp.status == 404:
                    return {"error": f"Recording not found: {recording_id}"}
                if resp.status != 200:
                    error_text = await resp.text()
                    return {"error": f"Grain API error (HTTP {resp.status}): {error_text[:300]}"}

                if fmt == "text":
                    text = await resp.text()
                    return {
                        "recording_id": recording_id,
                        "format": "text",
                        "transcript": text,
                    }
                else:
                    data = await resp.json()
                    return {
                        "recording_id": recording_id,
                        "format": "json",
                        "segments": data,
                    }
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to Grain API: {e}"}


async def find_recording_by_id(recording_id: str, days: int = 2) -> dict[str, Any] | None:
    """Return the raw recording dict for ``recording_id`` by scanning recent recordings.

    Grain's public API does not expose ``GET /recordings/{id}``; we instead list
    recent recordings (filter by after_datetime) and match by id locally. This
    is cheap because the just-completed recording that triggered the webhook is
    always in the recent window.
    """
    after_dt = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = await _fetch_all_recordings({"after_datetime": after_dt})
    if "error" in result:
        return None
    for rec in result.get("recordings", []):
        if rec.get("id") == recording_id:
            return rec
    return None


async def recent_recordings(days: int = 7) -> dict[str, Any]:
    """List recordings from the last N days (auto-paginates)."""
    after_dt = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = await _fetch_all_recordings({"after_datetime": after_dt})

    if "error" in result:
        return result

    recordings = result["recordings"]
    if not recordings:
        return {"error": f"No recordings found in the last {days} days."}

    return {
        "period": f"last {days} days",
        "since": after_dt,
        "count": len(recordings),
        "recordings": [_format_recording(r) for r in recordings],
    }


def _print_usage():
    print("Usage:")
    print("  python3 tools/grain.py search <query>")
    print("    Search recordings by title")
    print()
    print("  python3 tools/grain.py transcript <recording_id> [--text]")
    print("    Get transcript for a recording (JSON by default, --text for plain text)")
    print()
    print("  python3 tools/grain.py recent [days]")
    print("    List recordings from last N days (default 7)")
    sys.exit(1)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = sys.argv[2:]

    if command == "search":
        if not rest:
            print("Error: search requires a query string")
            sys.exit(1)
        query = " ".join(rest)
        result = asyncio.run(search_recordings(query))
        print(json.dumps(result, indent=2))

    elif command == "transcript":
        if not rest:
            print("Error: transcript requires a recording_id")
            sys.exit(1)
        recording_id = rest[0]
        fmt = "text" if "--text" in rest else "json"
        result = asyncio.run(get_transcript(recording_id, fmt=fmt))
        print(json.dumps(result, indent=2))

    elif command == "recent":
        days = 7
        if rest and rest[0].isdigit():
            days = int(rest[0])
        result = asyncio.run(recent_recordings(days))
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}")
        _print_usage()
