"""Notify personal tool — leave a persistent notification in the user's Loma inbox.

Creates a doc in the `notifications` collection (see
observability/notifications.py) and best-effort fires a web push to the
user's subscribed browsers. Use at the end of a flow run or long task so
the result surfaces under the bell icon in the dashboard and deep-links
back to this conversation.

Commands:
  notify.py --auth-token T --user-email E send --title TITLE [--body BODY] \
      [--conversation-id ID] [--link URL]
  notify.py --auth-token T --user-email E list [--limit N]
"""

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ── Auth ──────────────────────────────────────────────────────────────────


def _verify_auth(auth_token: str, user_email: str) -> bool:
    """Verify the HMAC auth token matches the user email."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _auth_token import verify_user_auth_token
    return verify_user_auth_token(auth_token, user_email)


def _get_client():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("OBSERVABILITY_MONGODB_URI environment variable is not set")
    db_name = os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability").strip()
    client = AsyncIOMotorClient(uri)
    return client, client[db_name]


# ── Commands ──────────────────────────────────────────────────────────────


async def _send(user_email: str, title: str, body: str,
                conversation_id: str | None, link: str | None) -> dict:
    from observability.notifications import create_notification
    from observability.push import send_user_push

    client, db = _get_client()
    try:
        doc = await create_notification(
            db,
            user_email=user_email,
            title=title,
            body=body,
            conversation_id=conversation_id,
            link=link,
            source="agent",
            fire_push=False,  # fire-and-forget tasks die with this short-lived CLI; send inline instead
        )
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        # Push clicks always land on the notifications inbox — the user expands
        # the notification there and opens the conversation explicitly.
        url = f"{base_url}/notifications"
        await send_user_push(
            db, user_email,
            title=doc["title"], body=doc["body"], url=url, tag=doc["notification_id"],
        )
        return {
            "created": True,
            "notification_id": doc["notification_id"],
            "conversation_id": doc.get("conversation_id"),
        }
    finally:
        client.close()


async def _list(user_email: str, limit: int) -> dict:
    client, db = _get_client()
    try:
        docs = await db.notifications.find(
            {"user_email": user_email, "dismissed": {"$ne": True}},
        ).sort("created_at", -1).to_list(limit)
        return {"notifications": [
            {
                "notification_id": d["notification_id"],
                "title": d.get("title"),
                "body": d.get("body"),
                "conversation_id": d.get("conversation_id"),
                "read": d.get("read", False),
                "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
            }
            for d in docs
        ]}
    finally:
        client.close()


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
            "notify.py --auth-token T --user-email E send --title TITLE "
            "[--body BODY] [--conversation-id ID] [--link URL]",
            "notify.py --auth-token T --user-email E list [--limit N]",
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
            "error": "Authentication failed. The auth token is invalid, expired, or doesn't "
            "match the user email. This is a system error — please try your request again."
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

    if command == "send":
        title = _parse_single(rest, "--title")
        if not title:
            print(json.dumps({"error": "send requires --title"}))
            sys.exit(1)
        body = _parse_single(rest, "--body", "") or ""
        conversation_id = _parse_single(rest, "--conversation-id")
        link = _parse_single(rest, "--link")
        try:
            result = asyncio.run(_send(user_email, title, body, conversation_id, link))
        except ValueError as e:
            result = {"error": str(e)}
        print(json.dumps(result, indent=2))
        if "error" in result:
            sys.exit(1)

    elif command == "list":
        try:
            limit = int(_parse_single(rest, "--limit", "20") or "20")
        except ValueError:
            limit = 20
        print(json.dumps(asyncio.run(_list(user_email, min(limit, 100))), indent=2))

    else:
        print(json.dumps({"error": f"Unknown command: {command}"}))
        _print_usage()
