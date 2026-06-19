"""PostHog product analytics — event definitions, property definitions, and event queries.

Provides CLI commands for the Loma agent:
  1. posthog.py projects                                       — List available PostHog projects
  2. posthog.py definitions [--search <term>] [--limit 200]   — List event definitions
  3. posthog.py definition-properties <event_name>             — List properties for an event
  4. posthog.py events <event_name> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--limit 100] [--filter "prop=value"] ...

All commands support --project <id> to target a specific project (defaults to company dashboard).

Requires POSTHOG_API_KEY environment variable (Personal API Key).
Optionally set POSTHOG_PROJECT_ID to override auto-detection.
API docs: https://posthog.com/docs/api

Usage (called by the agent via Bash):
  python3 tools/posthog.py projects
  python3 tools/posthog.py definitions --search "Flow"
  python3 tools/posthog.py definition-properties "Flow Published"
  python3 tools/posthog.py events "Flow Published" --from 2026-03-01 --to 2026-03-13 --limit 50
  python3 tools/posthog.py events "Logged In" --filter "properties.method=google"
  python3 tools/posthog.py events "$pageview" --project 13662 --limit 10
"""

import asyncio
import json
import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)

POSTHOG_BASE_URL = "https://us.posthog.com"

_cached_project_id: str | None = None


# ---------------------------------------------------------------------------
# Auth & HTTP helpers
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    key = os.environ.get("POSTHOG_API_KEY", "")
    if not key:
        raise ValueError(
            "POSTHOG_API_KEY environment variable is not set. "
            "Please configure it before using PostHog tools."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


async def _api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET request to PostHog API. Returns parsed JSON or {"error": ...}."""
    url = f"{POSTHOG_BASE_URL}{path}"
    if params:
        url += f"?{urlencode(params)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401:
                    return {"error": "PostHog API key is invalid or expired."}
                if resp.status == 403:
                    return {"error": "PostHog API key lacks permission for this resource."}
                if resp.status == 429:
                    return {"error": "PostHog rate limit reached. Try again shortly."}
                if resp.status != 200:
                    text = await resp.text()
                    return {"error": f"PostHog API error (HTTP {resp.status}): {text[:300]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to PostHog API: {e}"}


async def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST request to PostHog API. Returns parsed JSON or {"error": ...}."""
    url = f"{POSTHOG_BASE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=body,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 401:
                    return {"error": "PostHog API key is invalid or expired."}
                if resp.status == 403:
                    return {"error": "PostHog API key lacks permission for this resource."}
                if resp.status == 429:
                    return {"error": "PostHog rate limit reached. Try again shortly."}
                if resp.status not in (200, 201):
                    text = await resp.text()
                    return {"error": f"PostHog API error (HTTP {resp.status}): {text[:300]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to PostHog API: {e}"}


async def _get_project_id() -> str:
    """Get the project ID. Uses POSTHOG_PROJECT_ID env var if set, otherwise
    auto-detects by picking the 'company dashboard' project (or first project)."""
    global _cached_project_id
    if _cached_project_id is not None:
        return _cached_project_id

    # Allow explicit override via env var
    explicit_id = os.environ.get("POSTHOG_PROJECT_ID", "")
    if explicit_id:
        _cached_project_id = explicit_id
        return _cached_project_id

    data = await _api_get("/api/projects/")
    if "error" in data:
        raise ValueError(f"Failed to fetch PostHog project ID: {data['error']}")

    results = data.get("results", [])
    if not results:
        raise ValueError("No PostHog projects found for this API key.")

    # Prefer the Dashboard project over Website/Voice projects
    for project in results:
        if "dashboard" in project.get("name", "").lower():
            _cached_project_id = str(project["id"])
            return _cached_project_id

    _cached_project_id = str(results[0]["id"])
    return _cached_project_id


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

async def list_projects() -> dict[str, Any]:
    """List all PostHog projects accessible with the current API key."""
    data = await _api_get("/api/projects/")
    if "error" in data:
        return data

    results = data.get("results", [])
    return {
        "count": len(results),
        "projects": [
            {
                "id": p.get("id"),
                "name": p.get("name", ""),
                "api_token": p.get("api_token", "")[:20] + "...",
            }
            for p in results
        ],
    }


async def get_event_definitions(
    search: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List event definitions, optionally filtered by search term. Auto-paginates."""
    project_id = await _get_project_id()

    all_definitions: list[dict[str, Any]] = []
    offset = 0
    page_size = min(limit, 100)

    while len(all_definitions) < limit:
        params: dict[str, Any] = {
            "limit": page_size,
            "offset": offset,
            "event_type": "event",
        }
        if search:
            params["search"] = search

        data = await _api_get(f"/api/projects/{project_id}/event_definitions/", params)
        if "error" in data:
            if all_definitions:
                break
            return data

        results = data.get("results", [])
        for r in results:
            all_definitions.append({
                "name": r.get("name", ""),
                "volume_30_day": r.get("volume_30_day"),
                "query_usage_30_day": r.get("query_usage_30_day"),
                "last_seen_at": r.get("last_seen_at"),
                "created_at": r.get("created_at"),
            })

        if not data.get("next"):
            break
        offset += page_size

    all_definitions = all_definitions[:limit]

    return {
        "count": len(all_definitions),
        "definitions": all_definitions,
    }


async def get_event_property_definitions(event_name: str) -> dict[str, Any]:
    """List property definitions for a specific event. Auto-paginates."""
    project_id = await _get_project_id()

    all_properties: list[dict[str, Any]] = []
    offset = 0
    page_size = 100

    while True:
        params: dict[str, Any] = {
            "limit": page_size,
            "offset": offset,
            "event_names": json.dumps([event_name]),
            "filter_by_event_property": "true",
        }

        data = await _api_get(f"/api/projects/{project_id}/property_definitions/", params)
        if "error" in data:
            if all_properties:
                break
            return data

        results = data.get("results", [])
        for r in results:
            all_properties.append({
                "name": r.get("name", ""),
                "property_type": r.get("property_type", ""),
                "query_usage_30_day": r.get("query_usage_30_day"),
                "is_numerical": r.get("is_numerical", False),
            })

        if not data.get("next"):
            break
        offset += page_size

    return {
        "event_name": event_name,
        "count": len(all_properties),
        "properties": all_properties,
    }


# ---------------------------------------------------------------------------
# HogQL query builder helpers
# ---------------------------------------------------------------------------

def _escape_sql(value: str) -> str:
    """Escape a string for safe interpolation into HogQL (double single-quotes)."""
    return value.replace("'", "''")


def _build_filter_clause(prop: str, op: str, value: str) -> str:
    """Build a single HogQL WHERE clause from a property filter."""
    col = f"properties.{prop}"
    escaped = _escape_sql(value)

    if op == "is_set":
        return f"{col} IS NOT NULL"
    if op == "is_not_set":
        return f"{col} IS NULL"
    if op == "contains":
        return f"{col} LIKE '%{escaped}%'"
    if op == "not_contains":
        return f"{col} NOT LIKE '%{escaped}%'"
    # Standard comparison operators: =, !=, >, <, >=, <=
    return f"{col} {op} '{escaped}'"


def _parse_filter(raw: str) -> tuple[str, str, str]:
    """Parse a filter string like 'property=value' into (prop, op, value).

    Supported operators: !=, >=, <=, =, >, <, contains, not_contains, is_set, is_not_set
    """
    # Check keyword operators first
    for kw_op in ("not_contains", "contains", "is_not_set", "is_set"):
        sep = f" {kw_op} "
        if sep in raw:
            prop, value = raw.split(sep, 1)
            return (prop.strip(), kw_op, value.strip())
        if raw.endswith(f" {kw_op}"):
            prop = raw[: -len(kw_op) - 1].strip()
            return (prop, kw_op, "")

    # Multi-char operators before single-char
    for op in ("!=", ">=", "<="):
        if op in raw:
            prop, value = raw.split(op, 1)
            return (prop.strip(), op, value.strip())

    # Single-char operators
    for op in ("=", ">", "<"):
        if op in raw:
            prop, value = raw.split(op, 1)
            return (prop.strip(), op, value.strip())

    raise ValueError(f"Cannot parse filter: {raw}")


async def get_events(
    event_name: str,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    filters: list[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Query events using HogQL via the PostHog Query API.

    Args:
        event_name: The event name to query (e.g. "$pageview", "button_clicked").
        date_from: Start date in YYYY-MM-DD format. Defaults to 7 days ago.
        date_to: End date in YYYY-MM-DD format. Defaults to today.
        limit: Max number of events to return (default 100).
        filters: Optional list of (property, operator, value) tuples.
    """
    project_id = await _get_project_id()

    now = datetime.now(timezone.utc)
    if not date_from:
        date_from = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = now.strftime("%Y-%m-%d")

    # Build WHERE clauses
    where_parts = [
        f"event = '{_escape_sql(event_name)}'",
        f"timestamp >= '{_escape_sql(date_from)}'",
        f"timestamp <= '{_escape_sql(date_to)} 23:59:59'",
    ]

    if filters:
        for prop, op, value in filters:
            where_parts.append(_build_filter_clause(prop, op, value))

    where_str = " AND ".join(where_parts)
    query = (
        f"SELECT uuid, event, timestamp, distinct_id, properties "
        f"FROM events "
        f"WHERE {where_str} "
        f"ORDER BY timestamp DESC "
        f"LIMIT {int(limit)}"
    )

    body = {"query": {"kind": "HogQLQuery", "query": query}}
    data = await _api_post(f"/api/projects/{project_id}/query/", body)

    # The query API returns {"error": null} on success — only treat as error if truthy
    if data.get("error"):
        return {"error": data["error"]}

    # Transform columnar results into rows
    columns = data.get("columns", [])
    results_raw = data.get("results", [])
    rows = [dict(zip(columns, row)) for row in results_raw]

    # Parse properties JSON string if present
    for row in rows:
        if "properties" in row and isinstance(row["properties"], str):
            try:
                row["properties"] = json.loads(row["properties"])
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "event_name": event_name,
        "date_from": date_from,
        "date_to": date_to,
        "count": len(rows),
        "hogql": query,
        "events": rows,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_usage():
    print("Usage:")
    print("  python3 tools/posthog.py projects")
    print("    List all PostHog projects accessible with the current API key")
    print()
    print("  python3 tools/posthog.py definitions [--search <term>] [--limit 200]")
    print("    List all event definitions (optionally filtered by search term)")
    print()
    print("  python3 tools/posthog.py definition-properties <event_name>")
    print("    List property definitions for a specific event")
    print()
    print("  python3 tools/posthog.py events <event_name> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--limit 100] [--filter \"prop=value\"] ...")
    print("    Query events using HogQL. Supports multiple --filter flags.")
    print("    Filter operators: =, !=, >, <, >=, <=, contains, not_contains, is_set, is_not_set")
    print()
    print("  All commands support --project <id> to target a specific project (defaults to company dashboard).")
    print()
    print("  Examples:")
    print('    python3 tools/posthog.py events "Flow Published" --from 2026-03-01 --to 2026-03-13')
    print('    python3 tools/posthog.py events "Logged In" --filter "properties.method=google"')
    print('    python3 tools/posthog.py events "$pageview" --project 13662 --limit 10')
    sys.exit(1)


def _extract_project_flag(args: list[str]) -> list[str]:
    """Extract --project flag from args and set it as the cached project ID.
    Returns the remaining args with the flag removed."""
    global _cached_project_id
    result = []
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            _cached_project_id = args[i + 1]
            i += 2
        else:
            result.append(args[i])
            i += 1
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = _extract_project_flag(sys.argv[2:])

    try:
        if command == "projects":
            result = asyncio.run(list_projects())
            print(json.dumps(result, indent=2, default=str))

        elif command == "definitions":
            search = None
            limit = 200
            i = 0
            while i < len(rest):
                if rest[i] == "--search" and i + 1 < len(rest):
                    search = rest[i + 1]
                    i += 2
                elif rest[i] == "--limit" and i + 1 < len(rest):
                    limit = int(rest[i + 1])
                    i += 2
                else:
                    i += 1
            result = asyncio.run(get_event_definitions(search=search, limit=limit))
            print(json.dumps(result, indent=2, default=str))

        elif command == "definition-properties":
            if not rest:
                print("Error: definition-properties requires an event_name")
                sys.exit(1)
            event_name = rest[0]
            result = asyncio.run(get_event_property_definitions(event_name))
            print(json.dumps(result, indent=2, default=str))

        elif command == "events":
            if not rest:
                print("Error: events requires an event_name")
                sys.exit(1)
            event_name = rest[0]
            rest = rest[1:]

            date_from = None
            date_to = None
            limit = 100
            filters_raw: list[str] = []

            i = 0
            while i < len(rest):
                if rest[i] == "--from" and i + 1 < len(rest):
                    date_from = rest[i + 1]
                    i += 2
                elif rest[i] == "--to" and i + 1 < len(rest):
                    date_to = rest[i + 1]
                    i += 2
                elif rest[i] == "--limit" and i + 1 < len(rest):
                    limit = int(rest[i + 1])
                    i += 2
                elif rest[i] == "--filter" and i + 1 < len(rest):
                    filters_raw.append(rest[i + 1])
                    i += 2
                else:
                    i += 1

            filters = [_parse_filter(f) for f in filters_raw] if filters_raw else None
            result = asyncio.run(get_events(
                event_name=event_name,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                filters=filters,
            ))
            print(json.dumps(result, indent=2, default=str))

        else:
            print(f"Unknown command: {command}")
            _print_usage()

    except ValueError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)
