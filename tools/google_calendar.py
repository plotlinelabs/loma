"""Google Calendar API client for the Loma agent.

Provides CLI commands to read and manage calendar events using a user's
personal Google OAuth tokens.

Commands:
  1. google_calendar.py list-events --user-email EMAIL [--limit N] [--time-min ISO] [--time-max ISO]
  2. google_calendar.py get-event --user-email EMAIL --event-id ID
  3. google_calendar.py create-event --user-email EMAIL --summary S --start ISO --end ISO [--description D] [--attendees a@b.com,c@d.com] [--location L]
  4. google_calendar.py search --user-email EMAIL --query Q [--limit N]
  5. google_calendar.py update-event --user-email EMAIL --event-id ID [--summary S] [--start ISO] [--end ISO] [--description D] [--attendees a@b.com,c@d.com] [--location L]
  6. google_calendar.py delete-event --user-email EMAIL --event-id ID

Requires:
  - User must have connected their Google account via the Integrations page
  - OBSERVABILITY_MONGODB_URI, OAUTH_ENCRYPTION_KEY, GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET environment variables

Usage (called by the agent via Bash):
  python3 tools/google_calendar.py list-events --user-email adarsh@example.com --limit 10
  python3 tools/google_calendar.py get-event --user-email adarsh@example.com --event-id abc123
  python3 tools/google_calendar.py create-event --user-email adarsh@example.com --summary "Team standup" --start 2026-03-01T10:00:00+05:30 --end 2026-03-01T10:30:00+05:30
  python3 tools/google_calendar.py search --user-email adarsh@example.com --query "standup"
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from tools._google_auth import get_google_credentials  # noqa: E402


async def _get_service(user_email: str):
    """Build an authenticated Google Calendar API service."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("calendar", "v3", credentials=creds)


def _format_event(event: dict) -> dict[str, Any]:
    """Format a Calendar event into a compact summary."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id"),
        "summary": event.get("summary", "(no title)"),
        "description": event.get("description", ""),
        "location": event.get("location", ""),
        "start": start.get("dateTime") or start.get("date", ""),
        "end": end.get("dateTime") or end.get("date", ""),
        "status": event.get("status", ""),
        "htmlLink": event.get("htmlLink", ""),
        "organizer": event.get("organizer", {}).get("email", ""),
        "attendees": [
            {"email": a.get("email", ""), "responseStatus": a.get("responseStatus", "")}
            for a in event.get("attendees", [])
        ],
        "hangoutLink": event.get("hangoutLink", ""),
        "conferenceData": _format_conference(event.get("conferenceData")),
    }


def _format_conference(conf: dict | None) -> str:
    """Extract video call link from conference data."""
    if not conf:
        return ""
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            return ep.get("uri", "")
    return ""


# ── Commands ──────────────────────────────────────────────────────────────


async def list_events(
    user_email: str, limit: int = 10, time_min: str = "", time_max: str = "",
) -> dict:
    """List upcoming calendar events."""
    service = await _get_service(user_email)

    now_iso = datetime.now(timezone.utc).isoformat()
    params: dict[str, Any] = {
        "calendarId": "primary",
        "maxResults": limit,
        "singleEvents": True,
        "orderBy": "startTime",
        "timeMin": time_min or now_iso,
    }
    if time_max:
        params["timeMax"] = time_max

    result = service.events().list(**params).execute()
    events = [_format_event(e) for e in result.get("items", [])]
    return {"events": events, "total": len(events)}


async def get_event(user_email: str, event_id: str) -> dict:
    """Get details of a specific calendar event."""
    service = await _get_service(user_email)
    event = service.events().get(calendarId="primary", eventId=event_id).execute()
    return _format_event(event)


async def create_event(
    user_email: str,
    summary: str,
    start: str,
    end: str,
    description: str = "",
    attendees: str = "",
    location: str = "",
) -> dict:
    """Create a new calendar event."""
    service = await _get_service(user_email)

    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "conferenceData": {
            "createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            },
        },
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    # Always include meetings@example.com; append any extra attendees
    attendee_list = [{"email": "meetings@example.com"}]
    if attendees:
        attendee_list += [{"email": a.strip()} for a in attendees.split(",") if a.strip()]
    body["attendees"] = attendee_list

    event = service.events().insert(
        calendarId="primary", body=body, conferenceDataVersion=1,
    ).execute()
    return {
        "created": True,
        "id": event.get("id"),
        "htmlLink": event.get("htmlLink"),
        "summary": event.get("summary"),
        "start": event.get("start", {}).get("dateTime", ""),
        "end": event.get("end", {}).get("dateTime", ""),
        "hangoutLink": event.get("hangoutLink", ""),
        "conferenceLink": _format_conference(event.get("conferenceData")),
    }


async def update_event(
    user_email: str,
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    attendees: str = "",
    location: str = "",
) -> dict:
    """Update (reschedule/edit) an existing calendar event.

    Only the provided fields are updated; omitted fields remain unchanged.
    """
    service = await _get_service(user_email)

    # Fetch the existing event first
    existing = service.events().get(calendarId="primary", eventId=event_id).execute()

    if summary:
        existing["summary"] = summary
    if start:
        existing["start"] = {"dateTime": start}
    if end:
        existing["end"] = {"dateTime": end}
    if description:
        existing["description"] = description
    if location:
        existing["location"] = location
    if attendees:
        attendee_list = [{"email": "meetings@example.com"}]
        attendee_list += [{"email": a.strip()} for a in attendees.split(",") if a.strip()]
        existing["attendees"] = attendee_list

    event = service.events().update(
        calendarId="primary", eventId=event_id, body=existing,
    ).execute()
    return {
        "updated": True,
        "id": event.get("id"),
        "htmlLink": event.get("htmlLink"),
        "summary": event.get("summary"),
        "start": event.get("start", {}).get("dateTime", ""),
        "end": event.get("end", {}).get("dateTime", ""),
    }


async def delete_event(user_email: str, event_id: str) -> dict:
    """Delete a calendar event."""
    service = await _get_service(user_email)
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    return {"deleted": True, "eventId": event_id}


async def search_events(user_email: str, query: str, limit: int = 10) -> dict:
    """Search calendar events by text query."""
    service = await _get_service(user_email)

    result = service.events().list(
        calendarId="primary",
        q=query,
        maxResults=limit,
        singleEvents=True,
        orderBy="startTime",
        timeMin=datetime.now(timezone.utc).isoformat(),
    ).execute()

    events = [_format_event(e) for e in result.get("items", [])]
    return {"events": events, "total": len(events)}


# ── CLI ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Google Calendar tool for Loma agent")
    parser.add_argument("--auth-token", required=True, help="HMAC-signed user auth token")
    sub = parser.add_subparsers(dest="command", required=True)

    # list-events
    p_list = sub.add_parser("list-events", help="List upcoming calendar events")
    p_list.add_argument("--user-email", required=True)
    p_list.add_argument("--limit", type=int, default=10)
    p_list.add_argument("--time-min", default="")
    p_list.add_argument("--time-max", default="")

    # get-event
    p_get = sub.add_parser("get-event", help="Get a specific event")
    p_get.add_argument("--user-email", required=True)
    p_get.add_argument("--event-id", required=True)

    # create-event
    p_create = sub.add_parser("create-event", help="Create a calendar event")
    p_create.add_argument("--user-email", required=True)
    p_create.add_argument("--summary", required=True)
    p_create.add_argument("--start", required=True, help="ISO 8601 datetime")
    p_create.add_argument("--end", required=True, help="ISO 8601 datetime")
    p_create.add_argument("--description", default="")
    p_create.add_argument("--attendees", default="", help="Comma-separated emails")
    p_create.add_argument("--location", default="")

    # update-event
    p_update = sub.add_parser("update-event", help="Update/reschedule a calendar event")
    p_update.add_argument("--user-email", required=True)
    p_update.add_argument("--event-id", required=True)
    p_update.add_argument("--summary", default="")
    p_update.add_argument("--start", default="", help="New start ISO 8601 datetime")
    p_update.add_argument("--end", default="", help="New end ISO 8601 datetime")
    p_update.add_argument("--description", default="")
    p_update.add_argument("--attendees", default="", help="Comma-separated emails (replaces existing)")
    p_update.add_argument("--location", default="")

    # delete-event
    p_delete = sub.add_parser("delete-event", help="Delete a calendar event")
    p_delete.add_argument("--user-email", required=True)
    p_delete.add_argument("--event-id", required=True)

    # search
    p_search = sub.add_parser("search", help="Search calendar events")
    p_search.add_argument("--user-email", required=True)
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    # Verify auth token matches the requested user
    from tools._auth_token import verify_user_auth_token
    if not verify_user_auth_token(args.auth_token, args.user_email):
        print(json.dumps({"error": "Authentication failed — user identity mismatch or expired token. "
                          "You can only access your own Google account."}))
        sys.exit(1)

    try:
        if args.command == "list-events":
            result = asyncio.run(list_events(
                args.user_email, args.limit, args.time_min, args.time_max,
            ))
        elif args.command == "get-event":
            result = asyncio.run(get_event(args.user_email, args.event_id))
        elif args.command == "create-event":
            result = asyncio.run(create_event(
                args.user_email, args.summary, args.start, args.end,
                args.description, args.attendees, args.location,
            ))
        elif args.command == "update-event":
            result = asyncio.run(update_event(
                args.user_email, args.event_id, args.summary, args.start,
                args.end, args.description, args.attendees, args.location,
            ))
        elif args.command == "delete-event":
            result = asyncio.run(delete_event(args.user_email, args.event_id))
        elif args.command == "search":
            result = asyncio.run(search_events(args.user_email, args.query, args.limit))
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Google Calendar API error: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
