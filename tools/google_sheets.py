"""Google Sheets API client for the Loma agent.

Provides CLI commands to read and edit Google Sheets using a user's
personal Google OAuth tokens.

Commands:
  1. google_sheets.py list-sheets --user-email EMAIL --spreadsheet-id ID
  2. google_sheets.py read-range --user-email EMAIL --spreadsheet-id ID --range RANGE
  3. google_sheets.py write-range --user-email EMAIL --spreadsheet-id ID --range RANGE --values JSON
  4. google_sheets.py get-info --user-email EMAIL --spreadsheet-id ID
  5. google_sheets.py copy-spreadsheet --user-email EMAIL --spreadsheet-id ID [--title T]

Requires:
  - User must have connected their Google account via the Integrations page
  - OBSERVABILITY_MONGODB_URI, OAUTH_ENCRYPTION_KEY, GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET environment variables

Usage (called by the agent via Bash):
  python3 tools/google_sheets.py get-info --user-email adarsh@example.com --spreadsheet-id 1abc2def
  python3 tools/google_sheets.py list-sheets --user-email adarsh@example.com --spreadsheet-id 1abc2def
  python3 tools/google_sheets.py read-range --user-email adarsh@example.com --spreadsheet-id 1abc2def --range "Sheet1!A1:D10"
  python3 tools/google_sheets.py write-range --user-email adarsh@example.com --spreadsheet-id 1abc2def --range "Sheet1!A1" --values '[["Name","Score"],["Alice",95]]'
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from tools._google_auth import get_google_credentials  # noqa: E402


async def _get_service(user_email: str):
    """Build an authenticated Google Sheets API service."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("sheets", "v4", credentials=creds)


# ── Commands ──────────────────────────────────────────────────────────────


async def get_info(user_email: str, spreadsheet_id: str) -> dict:
    """Get spreadsheet metadata (title, sheets, locale)."""
    service = await _get_service(user_email)
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    props = spreadsheet.get("properties", {})
    sheets = [
        {
            "sheetId": s["properties"]["sheetId"],
            "title": s["properties"]["title"],
            "rowCount": s["properties"]["gridProperties"]["rowCount"],
            "columnCount": s["properties"]["gridProperties"]["columnCount"],
        }
        for s in spreadsheet.get("sheets", [])
    ]
    return {
        "spreadsheetId": spreadsheet.get("spreadsheetId"),
        "title": props.get("title", ""),
        "locale": props.get("locale", ""),
        "sheets": sheets,
        "spreadsheetUrl": spreadsheet.get("spreadsheetUrl", ""),
    }


async def list_sheets(user_email: str, spreadsheet_id: str) -> dict:
    """List all sheet tabs in a spreadsheet."""
    info = await get_info(user_email, spreadsheet_id)
    return {"sheets": info["sheets"], "title": info["title"]}


async def read_range(user_email: str, spreadsheet_id: str, range_: str) -> dict:
    """Read values from a specific range (e.g., 'Sheet1!A1:D10')."""
    service = await _get_service(user_email)
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_,
        valueRenderOption="FORMATTED_VALUE",
    ).execute()

    values = result.get("values", [])
    return {
        "range": result.get("range", range_),
        "rows": len(values),
        "values": values,
    }


async def write_range(
    user_email: str, spreadsheet_id: str, range_: str, values: list[list[Any]],
) -> dict:
    """Write values to a specific range."""
    service = await _get_service(user_email)
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_,
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()

    return {
        "updatedRange": result.get("updatedRange", ""),
        "updatedRows": result.get("updatedRows", 0),
        "updatedColumns": result.get("updatedColumns", 0),
        "updatedCells": result.get("updatedCells", 0),
    }


async def copy_spreadsheet(user_email: str, spreadsheet_id: str, title: str = "") -> dict:
    """Create a copy of a Google Sheets spreadsheet."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    drive = build("drive", "v3", credentials=creds)

    metadata: dict = {}
    if title:
        metadata["name"] = title

    result = drive.files().copy(
        fileId=spreadsheet_id,
        body=metadata,
        fields="id,name,webViewLink",
    ).execute()

    return {
        "copied": True,
        "spreadsheetId": result.get("id"),
        "title": result.get("name", ""),
        "url": result.get("webViewLink", ""),
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Google Sheets tool for Loma agent")
    parser.add_argument("--auth-token", required=True, help="HMAC-signed user auth token")
    sub = parser.add_subparsers(dest="command", required=True)

    # get-info
    p_info = sub.add_parser("get-info", help="Get spreadsheet metadata")
    p_info.add_argument("--user-email", required=True)
    p_info.add_argument("--spreadsheet-id", required=True)

    # list-sheets
    p_list = sub.add_parser("list-sheets", help="List sheet tabs")
    p_list.add_argument("--user-email", required=True)
    p_list.add_argument("--spreadsheet-id", required=True)

    # read-range
    p_read = sub.add_parser("read-range", help="Read values from a range")
    p_read.add_argument("--user-email", required=True)
    p_read.add_argument("--spreadsheet-id", required=True)
    p_read.add_argument("--range", required=True)

    # write-range
    p_write = sub.add_parser("write-range", help="Write values to a range")
    p_write.add_argument("--user-email", required=True)
    p_write.add_argument("--spreadsheet-id", required=True)
    p_write.add_argument("--range", required=True)
    p_write.add_argument("--values", required=True, help="JSON array of arrays")

    # copy-spreadsheet
    p_copy = sub.add_parser("copy-spreadsheet", help="Create a copy of a spreadsheet")
    p_copy.add_argument("--user-email", required=True)
    p_copy.add_argument("--spreadsheet-id", required=True)
    p_copy.add_argument("--title", default="", help="Title for the copy")

    args = parser.parse_args()

    # Verify auth token matches the requested user
    from tools._auth_token import verify_user_auth_token
    if not verify_user_auth_token(args.auth_token, args.user_email):
        print(json.dumps({"error": "Authentication failed — user identity mismatch or expired token. "
                          "You can only access your own Google account."}))
        sys.exit(1)

    try:
        if args.command == "get-info":
            result = asyncio.run(get_info(args.user_email, args.spreadsheet_id))
        elif args.command == "list-sheets":
            result = asyncio.run(list_sheets(args.user_email, args.spreadsheet_id))
        elif args.command == "read-range":
            result = asyncio.run(read_range(args.user_email, args.spreadsheet_id, args.range))
        elif args.command == "write-range":
            values = json.loads(args.values)
            result = asyncio.run(write_range(
                args.user_email, args.spreadsheet_id, args.range, values,
            ))
        elif args.command == "copy-spreadsheet":
            result = asyncio.run(copy_spreadsheet(
                args.user_email, args.spreadsheet_id, args.title,
            ))
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON for --values: {e}"}))
        sys.exit(1)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Google Sheets API error: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
