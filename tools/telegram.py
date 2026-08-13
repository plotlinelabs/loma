"""Telegram personal tool — send messages to the authenticated user's Telegram.

Uses the shared Loma bot (TELEGRAM_BOT_TOKEN) to message the Telegram
account the user linked from Integrations > Personal > Telegram. The
mapping lives in the ``telegram_links`` collection; a user can only send
to their own linked chat (or an explicit chat id they own).

Commands:
  telegram.py --auth-token T --user-email E send-message --text MSG
  telegram.py --auth-token T --user-email E status
"""

import asyncio
import json
import os
import sys
import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
_MAX_MESSAGE_LEN = 4000


# ── Auth ──────────────────────────────────────────────────────────────────


def _verify_auth(auth_token: str, user_email: str) -> bool:
    """Verify the HMAC auth token matches the user email."""
    sys.path.insert(0, os.path.dirname(__file__))
    from _auth_token import verify_user_auth_token
    return verify_user_auth_token(auth_token, user_email)


async def _get_link(user_email: str) -> dict | None:
    """Look up the user's Telegram link in MongoDB."""
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("OBSERVABILITY_MONGODB_URI environment variable is not set")
    db_name = os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability").strip()
    client = AsyncIOMotorClient(uri)
    try:
        return await client[db_name].telegram_links.find_one({"user_email": user_email})
    finally:
        client.close()


# ── Commands ──────────────────────────────────────────────────────────────


def _bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")
    return token


def send_message(user_email: str, text: str) -> dict:
    """Send a plain-text message to the user's own linked Telegram chat."""
    link = asyncio.run(_get_link(user_email))
    if not link:
        return {
            "error": "No Telegram account linked for this user. "
            "Connect one from Integrations > Personal > Telegram in the dashboard."
        }

    chat_id = link["telegram_chat_id"]
    token = _bot_token()
    sent = []
    remaining = text.strip()
    while remaining:
        chunk, remaining = remaining[:_MAX_MESSAGE_LEN], remaining[_MAX_MESSAGE_LEN:]
        resp = requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk},
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            return {"error": f"Telegram sendMessage failed: {data.get('description')}", "sent_chunks": len(sent)}
        sent.append(data["result"]["message_id"])

    return {"sent": True, "chat_id": chat_id, "message_ids": sent}


def status(user_email: str) -> dict:
    """Show whether this user has a linked Telegram account."""
    link = asyncio.run(_get_link(user_email))
    if not link:
        return {"linked": False}
    return {
        "linked": True,
        "telegram_username": link.get("telegram_username"),
        "telegram_first_name": link.get("telegram_first_name"),
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_single(args: list[str], flag: str, default: str | None = None) -> str | None:
    """Extract a single value for a flag."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return default


def _print_usage():
    print(json.dumps({
        "usage": [
            "telegram.py --auth-token T --user-email E send-message --text MSG",
            "telegram.py --auth-token T --user-email E status",
        ]
    }))
    sys.exit(1)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    args = sys.argv[1:]
    auth_token = _parse_single(args, "--auth-token")
    user_email = _parse_single(args, "--user-email")

    if not auth_token or not user_email:
        print(json.dumps({"error": "Missing required --auth-token and --user-email arguments"}))
        sys.exit(1)

    if not _verify_auth(auth_token, user_email):
        print(json.dumps({
            "error": "Authentication failed. The auth token is invalid, expired, or doesn't match the user email. "
            "This is a system error — please try your request again."
        }))
        sys.exit(1)

    filtered = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--auth-token", "--user-email"):
            skip_next = True
            continue
        filtered.append(arg)

    if not filtered:
        _print_usage()

    command = filtered[0]
    rest = filtered[1:]

    if command == "send-message":
        text = _parse_single(rest, "--text")
        if not text:
            print(json.dumps({"error": "send-message requires --text"}))
            sys.exit(1)
        print(json.dumps(send_message(user_email, text), indent=2))

    elif command == "status":
        print(json.dumps(status(user_email), indent=2))

    else:
        print(json.dumps({"error": f"Unknown command: {command}"}))
        _print_usage()
