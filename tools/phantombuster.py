"""PhantomBuster API integration for LinkedIn outreach.

Provides three LinkedIn automation capabilities via PhantomBuster Phantoms:
  1. send    — Send connection requests (LinkedIn Auto Connect Phantom)
  2. message — Send direct messages to existing connections (LinkedIn Message Sender Phantom)
  3. inbox   — Scrape messages from LinkedIn inbox (LinkedIn Inbox Scraper Phantom)

Requires environment variables:
  - PHANTOMBUSTER_API_KEY: PhantomBuster API key
  - PHANTOMBUSTER_PHANTOM_ID: ID of the LinkedIn Auto Connect Phantom
  - PHANTOMBUSTER_MESSAGE_PHANTOM_ID: ID of the LinkedIn Message Sender Phantom
  - PHANTOMBUSTER_INBOX_PHANTOM_ID: ID of the LinkedIn Inbox Scraper Phantom
  - PHANTOMBUSTER_LI_SESSION_COOKIE: LinkedIn session cookie (li_at value)

Usage:
  python3 tools/phantombuster.py send <linkedin_profile_url> <message>
  python3 tools/phantombuster.py message <linkedin_profile_url> <message>
  python3 tools/phantombuster.py inbox [--type all|archived|unread|inMail|spam] [--count N]
  python3 tools/phantombuster.py status [connect|message|inbox]

API docs: https://api.phantombuster.com/api/v2
Linear ticket: PA-42
"""

import asyncio
import json
import os
import re
import sys

import aiohttp
from dotenv import load_dotenv

PHANTOMBUSTER_BASE_URL = "https://api.phantombuster.com/api/v2"

LINKEDIN_PROFILE_PATTERN = re.compile(
    r"^https?://(www\.)?linkedin\.com/in/[\w\-%.]+/?$"
)

# Map phantom type names to their env var for the phantom ID
PHANTOM_ID_ENV_MAP = {
    "connect": "PHANTOMBUSTER_PHANTOM_ID",
    "message": "PHANTOMBUSTER_MESSAGE_PHANTOM_ID",
    "inbox": "PHANTOMBUSTER_INBOX_PHANTOM_ID",
}

VALID_INBOX_TYPES = ("all", "archived", "unread", "inMail", "spam")


def _env(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise ValueError(f"{name} environment variable is not set.")
    return val


def sanitize_message(message: str) -> str:
    """Sanitize a message before sending via PhantomBuster.

    Removes escape artifacts and normalizes special characters that can
    render incorrectly on LinkedIn:
      - Strips all backslash characters (escape artifacts)
      - Replaces em dashes (\u2014) with regular dashes (-)
      - Replaces en dashes (\u2013) with regular dashes (-)

    Args:
        message: The raw message string.

    Returns:
        The sanitized message safe for LinkedIn delivery.
    """
    # Strip backslashes (escape artifacts from string processing)
    message = message.replace("\\", "")
    # Replace em dashes with regular dashes
    message = message.replace("\u2014", "-")
    # Replace en dashes with regular dashes
    message = message.replace("\u2013", "-")
    return message


async def send_connection(linkedin_url: str, message: str) -> dict:
    """Launch the LinkedIn Auto Connect Phantom for a single profile."""
    if not linkedin_url:
        return {"error": "linkedin_url is required."}
    if not message:
        return {"error": "message is required."}
    if not LINKEDIN_PROFILE_PATTERN.match(linkedin_url):
        return {"error": f"Invalid LinkedIn URL: {linkedin_url}. Expected: https://www.linkedin.com/in/username/"}

    # Sanitize the message before sending (PA-43)
    message = sanitize_message(message)

    try:
        api_key = _env("PHANTOMBUSTER_API_KEY")
        phantom_id = _env("PHANTOMBUSTER_PHANTOM_ID")
        session_cookie = _env("PHANTOMBUSTER_LI_SESSION_COOKIE")
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    except ValueError as e:
        return {"error": str(e)}

    payload = {
        "id": phantom_id,
        "argument": {
            "numberOfAddsPerLaunch": 10,
            "onlySecondCircle": False,
            "dwellTime": False,
            "inputType": "profileUrl",
            "sessionCookie": session_cookie,
            "userAgent": user_agent,
            "profileUrl": linkedin_url,
            "message": message,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "X-Phantombuster-Key": api_key,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{PHANTOMBUSTER_BASE_URL}/agents/launch",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 429:
                    return {"error": "Rate limit reached. Wait and retry."}
                if resp.status == 401:
                    return {"error": "API key invalid or expired."}
                if resp.status == 404:
                    return {"error": f"Phantom not found (ID: {phantom_id})."}
                if resp.status not in (200, 201):
                    return {"error": f"API error (HTTP {resp.status}): {(await resp.text())[:300]}"}
                data = await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Connection failed: {e}"}

    return {
        "success": True,
        "target": linkedin_url,
        "message": message,
        "phantom_id": phantom_id,
        "container_id": data.get("containerId", "N/A"),
    }


async def _launch_phantom(phantom_id: str, argument: dict, api_key: str) -> dict:
    """Launch any PhantomBuster phantom and return the API response."""
    headers = {
        "Content-Type": "application/json",
        "X-Phantombuster-Key": api_key,
    }
    payload = {"id": phantom_id, "argument": argument}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{PHANTOMBUSTER_BASE_URL}/agents/launch",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 429:
                    return {"error": "Rate limit reached. Wait and retry."}
                if resp.status == 401:
                    return {"error": "API key invalid or expired."}
                if resp.status == 404:
                    return {"error": f"Phantom not found (ID: {phantom_id})."}
                if resp.status not in (200, 201):
                    return {"error": f"API error (HTTP {resp.status}): {(await resp.text())[:300]}"}
                return await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Connection failed: {e}"}


async def send_message(linkedin_url: str, message: str) -> dict:
    """Send a direct message to an existing LinkedIn connection."""
    if not linkedin_url:
        return {"error": "linkedin_url is required."}
    if not message:
        return {"error": "message is required."}
    if not LINKEDIN_PROFILE_PATTERN.match(linkedin_url):
        return {"error": f"Invalid LinkedIn URL: {linkedin_url}. Expected: https://www.linkedin.com/in/username/"}

    message = sanitize_message(message)

    try:
        api_key = _env("PHANTOMBUSTER_API_KEY")
        phantom_id = _env("PHANTOMBUSTER_MESSAGE_PHANTOM_ID")
        session_cookie = _env("PHANTOMBUSTER_LI_SESSION_COOKIE")
    except ValueError as e:
        return {"error": str(e)}

    argument = {
        "spreadsheetUrl": linkedin_url,
        "spreadsheetUrlExclusionList": [],
        "sessionCookie": session_cookie,
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "message": message,
    }

    data = await _launch_phantom(phantom_id, argument, api_key)
    if "error" in data:
        return data

    return {
        "success": True,
        "action": "message",
        "target": linkedin_url,
        "message": message,
        "phantom_id": phantom_id,
        "container_id": data.get("containerId", "N/A"),
    }


async def scrape_inbox(inbox_type: str = "all", count: int = 50) -> dict:
    """Scrape messages from LinkedIn inbox."""
    if inbox_type not in VALID_INBOX_TYPES:
        return {"error": f"Invalid inbox type: {inbox_type}. Must be one of: {', '.join(VALID_INBOX_TYPES)}"}
    if count < 1 or count > 500:
        return {"error": "count must be between 1 and 500."}

    try:
        api_key = _env("PHANTOMBUSTER_API_KEY")
        phantom_id = _env("PHANTOMBUSTER_INBOX_PHANTOM_ID")
        session_cookie = _env("PHANTOMBUSTER_LI_SESSION_COOKIE")
    except ValueError as e:
        return {"error": str(e)}

    argument = {
        "sessionCookie": session_cookie,
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "inboxFilter": inbox_type,
        "numberOfThreadsPerLaunch": count,
    }

    data = await _launch_phantom(phantom_id, argument, api_key)
    if "error" in data:
        return data

    return {
        "success": True,
        "action": "inbox_scrape",
        "inbox_type": inbox_type,
        "count": count,
        "phantom_id": phantom_id,
        "container_id": data.get("containerId", "N/A"),
    }


async def check_status(phantom_type: str = "connect") -> dict:
    """Check the latest execution output of a Phantom.

    Args:
        phantom_type: One of "connect", "message", or "inbox".
    """
    if phantom_type not in PHANTOM_ID_ENV_MAP:
        return {"error": f"Unknown phantom type: {phantom_type}. Must be one of: {', '.join(PHANTOM_ID_ENV_MAP)}"}

    try:
        api_key = _env("PHANTOMBUSTER_API_KEY")
        phantom_id = _env(PHANTOM_ID_ENV_MAP[phantom_type])
    except ValueError as e:
        return {"error": str(e)}

    headers = {
        "Content-Type": "application/json",
        "X-Phantombuster-Key": api_key,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{PHANTOMBUSTER_BASE_URL}/agents/fetch-output",
                params={"id": phantom_id},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 429:
                    return {"error": "Rate limit reached. Wait and retry."}
                if resp.status == 401:
                    return {"error": "API key invalid or expired."}
                if resp.status == 404:
                    return {"error": f"Phantom not found (ID: {phantom_id})."}
                if resp.status != 200:
                    return {"error": f"API error (HTTP {resp.status}): {(await resp.text())[:300]}"}
                data = await resp.json()
    except aiohttp.ClientError as e:
        return {"error": f"Connection failed: {e}"}

    status = data.get("status", "unknown")
    exit_code = data.get("exitCode")

    if status == "running":
        status_text = "Running"
    elif status == "finished" and exit_code == 0:
        status_text = "Completed successfully"
    elif status == "finished":
        status_text = f"Finished with error (exit code: {exit_code})"
    else:
        status_text = str(status).capitalize()

    result = {
        "status": status_text,
        "phantom_id": phantom_id,
        "container_id": data.get("containerId", "N/A"),
    }
    if data.get("lastEndMessage"):
        result["last_message"] = data["lastEndMessage"]
    if data.get("resultObject"):
        result["result"] = str(data["resultObject"])[:500]
    if data.get("output"):
        o = data["output"]
        result["output"] = o[:10000] + ("... (truncated)" if len(o) > 10000 else "")

    return result


if __name__ == "__main__":
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage:")
        print('  python3 tools/phantombuster.py send <linkedin_url> "<message>"')
        print('  python3 tools/phantombuster.py message <linkedin_url> "<message>"')
        print("  python3 tools/phantombuster.py inbox [--type all|archived|unread|inMail|spam] [--count N]")
        print("  python3 tools/phantombuster.py status [connect|message|inbox]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "send":
        if len(sys.argv) < 4:
            print("Error: send requires <linkedin_url> and <message>")
            sys.exit(1)
        result = asyncio.run(send_connection(sys.argv[2], sys.argv[3]))
    elif cmd == "message":
        if len(sys.argv) < 4:
            print("Error: message requires <linkedin_url> and <message>")
            sys.exit(1)
        result = asyncio.run(send_message(sys.argv[2], sys.argv[3]))
    elif cmd == "inbox":
        inbox_type = "all"
        count = 50
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--type" and i + 1 < len(args):
                inbox_type = args[i + 1]
                i += 2
            elif args[i] == "--count" and i + 1 < len(args):
                count = int(args[i + 1])
                i += 2
            else:
                print(f"Unknown option: {args[i]}")
                sys.exit(1)
        result = asyncio.run(scrape_inbox(inbox_type, count))
    elif cmd == "status":
        phantom_type = sys.argv[2] if len(sys.argv) > 2 else "connect"
        result = asyncio.run(check_status(phantom_type))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    print(json.dumps(result, indent=2))
