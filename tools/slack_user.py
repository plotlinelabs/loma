"""Slack personal tool — read, search, and send messages as the authenticated user.

Uses the user's personal Slack OAuth token (xoxp-) to act on their behalf.
Unlike the bot-level slack_reader.py, this tool can:
  - Read any channel the user is in (even ones the bot isn't in)
  - Send messages as the user (their name + avatar)
  - Search messages (user-token-only capability)
  - Open DM / group DM conversations
  - Add and remove emoji reactions
  - Upload files to channels

Commands:
  slack_user.py --auth-token T --user-email E read-channel --channel CH [--limit N]
  slack_user.py --auth-token T --user-email E send-message --channel CH --text T [--thread-ts TS] [--file PATH] [--file-title T]
  slack_user.py --auth-token T --user-email E search --query Q [--limit N]
  slack_user.py --auth-token T --user-email E open-dm --users email1,email2
  slack_user.py --auth-token T --user-email E react --channel CH --ts TS --emoji NAME
  slack_user.py --auth-token T --user-email E unreact --channel CH --ts TS --emoji NAME
  slack_user.py --auth-token T --user-email E upload-file --channels CH1,CH2 --file PATH [--title T] [--message M]
  slack_user.py --auth-token T --user-email E list-shared-channels [--include-internal-shared]
  slack_user.py --auth-token T --user-email E channel-members --channel CH [--external-only] [--include-bots]
"""

import asyncio
import json
import os
import sys
import logging
from datetime import datetime, timezone
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


# ── Auth ──────────────────────────────────────────────────────────────────


def _verify_auth(auth_token: str, user_email: str) -> bool:
    """Verify the HMAC auth token matches the user email."""
    sys.path.insert(0, os.path.dirname(__file__))
    from _auth_token import verify_user_auth_token
    return verify_user_auth_token(auth_token, user_email)


def _get_user_token(user_email: str) -> str:
    """Get the user's Slack OAuth token from MongoDB."""
    from _slack_auth import get_slack_user_token
    return asyncio.run(get_slack_user_token(user_email))


# ── Helpers ───────────────────────────────────────────────────────────────


def _resolve_channel_id(client: WebClient, channel_input: str) -> tuple[str | None, str | None]:
    """Resolve a channel name, ID, or email to a channel ID.

    Accepts:
      - Channel IDs (C..., D..., G...)
      - Channel names (#general, general)
      - Email addresses (user@example.com) — opens/resolves a DM

    Returns (channel_id, error_message). One will be None.
    """
    channel_input = channel_input.lstrip("#")

    # If it looks like a channel ID (starts with C, D, or G, alphanumeric), use directly
    if len(channel_input) >= 9 and channel_input[0] in ("C", "D", "G") and channel_input.isalnum():
        return channel_input, None

    # If it looks like an email, resolve to a DM channel
    if "@" in channel_input and "." in channel_input.split("@")[-1]:
        uid, err = _resolve_user_email_to_id(client, channel_input)
        if err:
            return None, err
        try:
            result = client.conversations_open(users=[uid], return_im=True)
            ch_id = result.get("channel", {}).get("id", "")
            if ch_id:
                return ch_id, None
            return None, f"Could not open DM with {channel_input}"
        except SlackApiError as e:
            return None, f"Failed to open DM with {channel_input}: {e.response['error']}"

    # Otherwise, search by name
    try:
        cursor = None
        while True:
            kwargs: dict[str, Any] = {
                "types": "public_channel,private_channel,im,mpim",
                "limit": 200,
                "exclude_archived": True,
            }
            if cursor:
                kwargs["cursor"] = cursor

            result = client.conversations_list(**kwargs)
            channels = result.get("channels", [])

            for ch in channels:
                if ch.get("name") == channel_input:
                    return ch["id"], None

            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    except SlackApiError as e:
        return None, f"Slack API error while looking up channel: {e.response['error']}"

    return None, f"Channel not found: #{channel_input}. Make sure you are a member of the channel."


def _resolve_username(client: WebClient, user_id: str, cache: dict[str, str]) -> str:
    """Resolve a Slack user ID to a display name, with caching."""
    if user_id in cache:
        return cache[user_id]

    try:
        result = client.users_info(user=user_id)
        user = result.get("user", {})
        name = (
            user.get("profile", {}).get("display_name")
            or user.get("real_name")
            or user.get("name")
            or user_id
        )
        cache[user_id] = name
        return name
    except SlackApiError:
        cache[user_id] = user_id
        return user_id


def _resolve_user_email_to_id(client: WebClient, email: str) -> tuple[str | None, str | None]:
    """Resolve a user email to a Slack user ID.

    Returns (user_id, error_message). One will be None.
    """
    try:
        result = client.users_lookupByEmail(email=email.strip())
        user = result.get("user", {})
        return user.get("id"), None
    except SlackApiError as e:
        error = e.response.get("error", str(e))
        if error == "users_not_found":
            return None, f"No Slack user found for email: {email}"
        return None, f"Error looking up {email}: {error}"


def _format_ts(ts: str) -> str:
    """Convert a Slack timestamp to a human-readable datetime."""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OSError):
        return ts


def _format_message(msg: dict[str, Any], client: WebClient, users_cache: dict[str, str]) -> dict[str, Any]:
    """Format a Slack message for agent consumption."""
    user_id = msg.get("user", "")
    result: dict[str, Any] = {
        "timestamp": _format_ts(msg.get("ts", "")),
        "ts": msg.get("ts", ""),
        "user": _resolve_username(client, user_id, users_cache) if user_id else "bot",
        "text": msg.get("text", ""),
    }

    # Bot messages
    if msg.get("bot_id") or msg.get("subtype") == "bot_message":
        bot_name = msg.get("username") or msg.get("bot_profile", {}).get("name", "bot")
        result["user"] = f"[bot] {bot_name}"

    # Thread info
    reply_count = msg.get("reply_count")
    if reply_count:
        result["thread_replies"] = reply_count
        result["thread_ts"] = msg.get("thread_ts", msg.get("ts", ""))

    # Reactions
    reactions = msg.get("reactions")
    if reactions:
        result["reactions"] = [
            {"emoji": r.get("name", ""), "count": r.get("count", 0)}
            for r in reactions
        ]

    # File attachments
    files = msg.get("files")
    if files:
        result["files"] = [
            {"name": f.get("name", "unknown"), "type": f.get("filetype", "")}
            for f in files
        ]

    return result


def _get_channel_name(client: WebClient, channel_id: str, users_cache: dict[str, str] | None = None) -> str:
    """Get a human-readable name for a channel."""
    if users_cache is None:
        users_cache = {}
    try:
        info = client.conversations_info(channel=channel_id)
        ch = info.get("channel", {})
        if ch.get("is_im"):
            # DM — resolve the other user's name
            other_user = ch.get("user", "")
            if other_user:
                return f"DM with {_resolve_username(client, other_user, users_cache)}"
            return f"DM ({channel_id})"
        return f"#{ch.get('name', channel_id)}"
    except SlackApiError:
        return channel_id


# ── Commands ──────────────────────────────────────────────────────────────


def read_channel(user_email: str, channel: str, limit: int = 50) -> dict[str, Any]:
    """Read recent messages from a channel."""
    token = _get_user_token(user_email)
    client = WebClient(token=token)

    channel_id, error = _resolve_channel_id(client, channel)
    if error:
        return {"error": error}

    try:
        result = client.conversations_history(
            channel=channel_id,
            limit=min(limit, 200),
        )
        messages = result.get("messages", [])
    except SlackApiError as e:
        error_msg = e.response.get("error", str(e))
        if error_msg == "channel_not_found":
            return {"error": f"Channel not found: {channel}. Make sure you are a member."}
        if error_msg == "not_in_channel":
            return {"error": f"You are not a member of {channel}."}
        return {"error": f"Slack API error: {error_msg}"}

    if not messages:
        return {"error": f"No messages found in {channel}."}

    messages.reverse()

    users_cache: dict[str, str] = {}
    formatted = [_format_message(m, client, users_cache) for m in messages]

    channel_name = _get_channel_name(client, channel_id, users_cache)

    return {
        "channel": channel_name,
        "channel_id": channel_id,
        "count": len(formatted),
        "messages": formatted,
    }


def send_message(
    user_email: str, channel: str, text: str, thread_ts: str = "",
    file_path: str = "", file_title: str = "",
) -> dict[str, Any]:
    """Send a message as the user, optionally with a file attachment.

    When file_path is provided, the file is uploaded with the text as the
    initial comment, creating a single combined message+file post.
    """
    token = _get_user_token(user_email)
    client = WebClient(token=token)

    channel_id, error = _resolve_channel_id(client, channel)
    if error:
        return {"error": error}

    try:
        # If a file is provided, use files_upload_v2 for combined file+message
        if file_path and os.path.isfile(file_path):
            upload_kwargs: dict[str, Any] = {
                "channel": channel_id,
                "file": file_path,
                "title": file_title or os.path.basename(file_path),
                "initial_comment": text,
            }
            if thread_ts:
                upload_kwargs["thread_ts"] = thread_ts

            result = client.files_upload_v2(**upload_kwargs)
            file_info = result.get("file", {})

            return {
                "sent": True,
                "method": "file_upload_with_message",
                "channel": channel,
                "channel_id": channel_id,
                "file_id": file_info.get("id", ""),
                "filename": file_info.get("name", os.path.basename(file_path)),
            }
        elif file_path:
            return {"error": f"File not found: {file_path}"}

        # Text-only message (existing behavior)
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "text": text,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        result = client.chat_postMessage(**kwargs)

        return {
            "sent": True,
            "method": "text_message",
            "channel": channel,
            "channel_id": channel_id,
            "message_ts": result.get("ts", ""),
            "thread_ts": result.get("message", {}).get("thread_ts", ""),
        }
    except SlackApiError as e:
        return {"error": f"Failed to send message: {e.response['error']}"}


def search_messages(user_email: str, query: str, limit: int = 20) -> dict[str, Any]:
    """Search Slack messages (user-token-only capability)."""
    token = _get_user_token(user_email)
    client = WebClient(token=token)

    try:
        result = client.search_messages(
            query=query,
            count=min(limit, 100),
            sort="timestamp",
            sort_dir="desc",
        )
        matches = result.get("messages", {}).get("matches", [])
    except SlackApiError as e:
        return {"error": f"Slack search error: {e.response['error']}"}

    if not matches:
        return {"query": query, "count": 0, "results": []}

    users_cache: dict[str, str] = {}
    formatted = []
    for m in matches:
        user_id = m.get("user", "") or m.get("username", "")
        entry: dict[str, Any] = {
            "text": m.get("text", ""),
            "user": _resolve_username(client, user_id, users_cache) if user_id else "unknown",
            "channel": m.get("channel", {}).get("name", ""),
            "timestamp": _format_ts(m.get("ts", "")),
            "ts": m.get("ts", ""),
            "permalink": m.get("permalink", ""),
        }
        formatted.append(entry)

    total = result.get("messages", {}).get("total", len(formatted))

    return {
        "query": query,
        "count": len(formatted),
        "total": total,
        "results": formatted,
    }


def open_dm(user_email: str, users_csv: str) -> dict[str, Any]:
    """Open a DM or group DM conversation.

    users_csv: comma-separated list of email addresses.
    1 email = 1:1 DM, 2+ emails = group DM (MPIM).
    """
    token = _get_user_token(user_email)
    client = WebClient(token=token)

    emails = [e.strip() for e in users_csv.split(",") if e.strip()]
    if not emails:
        return {"error": "No user emails provided."}

    # Resolve emails to Slack user IDs
    user_ids = []
    for email in emails:
        uid, err = _resolve_user_email_to_id(client, email)
        if err:
            return {"error": err}
        user_ids.append(uid)

    try:
        result = client.conversations_open(users=user_ids, return_im=True)
        channel = result.get("channel", {})
        ch_id = channel.get("id", "")
        already_open = result.get("already_open", False)

        return {
            "channel_id": ch_id,
            "already_open": already_open,
            "is_im": channel.get("is_im", False),
            "is_mpim": channel.get("is_mpim", False),
            "users": emails,
        }
    except SlackApiError as e:
        return {"error": f"Failed to open conversation: {e.response['error']}"}


def add_reaction(user_email: str, channel: str, timestamp: str, emoji: str) -> dict[str, Any]:
    """Add an emoji reaction to a message."""
    token = _get_user_token(user_email)
    client = WebClient(token=token)

    channel_id, error = _resolve_channel_id(client, channel)
    if error:
        return {"error": error}

    # Strip colons if provided (e.g. :thumbsup: -> thumbsup)
    emoji = emoji.strip(":")

    try:
        client.reactions_add(channel=channel_id, timestamp=timestamp, name=emoji)
        return {
            "added": True,
            "emoji": emoji,
            "channel_id": channel_id,
            "message_ts": timestamp,
        }
    except SlackApiError as e:
        error_msg = e.response.get("error", str(e))
        if error_msg == "already_reacted":
            return {"added": False, "emoji": emoji, "note": "You already reacted with this emoji."}
        return {"error": f"Failed to add reaction: {error_msg}"}


def remove_reaction(user_email: str, channel: str, timestamp: str, emoji: str) -> dict[str, Any]:
    """Remove an emoji reaction from a message."""
    token = _get_user_token(user_email)
    client = WebClient(token=token)

    channel_id, error = _resolve_channel_id(client, channel)
    if error:
        return {"error": error}

    emoji = emoji.strip(":")

    try:
        client.reactions_remove(channel=channel_id, timestamp=timestamp, name=emoji)
        return {
            "removed": True,
            "emoji": emoji,
            "channel_id": channel_id,
            "message_ts": timestamp,
        }
    except SlackApiError as e:
        error_msg = e.response.get("error", str(e))
        if error_msg == "no_reaction":
            return {"removed": False, "emoji": emoji, "note": "You haven't reacted with this emoji."}
        return {"error": f"Failed to remove reaction: {error_msg}"}


def upload_file(
    user_email: str,
    channels_csv: str,
    file_path: str,
    title: str = "",
    initial_comment: str = "",
) -> dict[str, Any]:
    """Upload a file to one or more channels."""
    token = _get_user_token(user_email)
    client = WebClient(token=token)

    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}"}

    # Resolve channel names/IDs
    channel_ids = []
    for ch in channels_csv.split(","):
        ch = ch.strip()
        if not ch:
            continue
        ch_id, error = _resolve_channel_id(client, ch)
        if error:
            return {"error": error}
        channel_ids.append(ch_id)

    if not channel_ids:
        return {"error": "No channels provided."}

    try:
        kwargs: dict[str, Any] = {
            "channel_ids": channel_ids,
            "file": file_path,
        }
        if title:
            kwargs["title"] = title
        if initial_comment:
            kwargs["initial_comment"] = initial_comment

        result = client.files_upload_v2(**kwargs)
        file_info = result.get("file", {})

        return {
            "uploaded": True,
            "file_id": file_info.get("id", ""),
            "filename": file_info.get("name", ""),
            "channels": channel_ids,
        }
    except SlackApiError as e:
        return {"error": f"Failed to upload file: {e.response['error']}"}


def _client_with_retries(token: str) -> WebClient:
    """WebClient that waits and retries on Slack rate limits (429)."""
    from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
    client = WebClient(token=token)
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=5))
    return client


def list_shared_channels(user_email: str, include_internal_shared: bool = False) -> dict[str, Any]:
    """List Slack Connect channels (shared with other orgs) the user is a member of.

    By default only externally shared channels (is_ext_shared) are returned.
    With include_internal_shared, channels shared across the user's own
    Enterprise Grid workspaces (is_shared) are also included.
    """
    token = _get_user_token(user_email)
    client = _client_with_retries(token)

    channels: list[dict[str, Any]] = []
    cursor = None
    try:
        while True:
            kwargs: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "exclude_archived": True,
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor
            result = client.users_conversations(**kwargs)
            for ch in result.get("channels", []):
                ext = bool(ch.get("is_ext_shared") or ch.get("is_pending_ext_shared"))
                shared = bool(ch.get("is_shared"))
                if not (ext or (include_internal_shared and shared)):
                    continue
                channels.append({
                    "id": ch.get("id", ""),
                    "name": ch.get("name", ""),
                    "is_private": bool(ch.get("is_private")),
                    "is_ext_shared": bool(ch.get("is_ext_shared")),
                    "is_pending_ext_shared": bool(ch.get("is_pending_ext_shared")),
                    "created": _format_ts(str(ch.get("created", ""))),
                })
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        if err == "missing_scope":
            return {"error": "Your Slack connection is missing channels:read / groups:read. "
                             "Reconnect Slack on the Loma Integrations page."}
        return {"error": f"Slack API error: {err}"}

    channels.sort(key=lambda c: c["name"])
    return {"count": len(channels), "channels": channels}


def channel_members(user_email: str, channel: str, external_only: bool = False,
                    include_bots: bool = False) -> dict[str, Any]:
    """List members of a channel with name, title, email and org.

    Each member is flagged is_external when they belong to a different Slack
    team than the requesting user (e.g. client users in a Slack Connect channel).
    Emails can be empty when the other org hides them from external users.
    """
    token = _get_user_token(user_email)
    client = _client_with_retries(token)

    channel_id, error = _resolve_channel_id(client, channel)
    if error:
        return {"error": error}

    try:
        my_team = client.auth_test().get("team_id", "")
    except SlackApiError as e:
        return {"error": f"Slack auth error: {e.response.get('error', str(e))}"}

    member_ids: list[str] = []
    cursor = None
    try:
        while True:
            kwargs: dict[str, Any] = {"channel": channel_id, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            result = client.conversations_members(**kwargs)
            member_ids.extend(result.get("members", []))
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        err = e.response.get("error", str(e))
        if err in ("channel_not_found", "not_in_channel"):
            return {"error": f"Channel not found or you are not a member: {channel}"}
        if err == "missing_scope":
            return {"error": "Your Slack connection is missing channels:read / groups:read. "
                             "Reconnect Slack on the Loma Integrations page."}
        return {"error": f"Slack API error: {err}"}

    members: list[dict[str, Any]] = []
    lookup_errors: list[str] = []
    for uid in member_ids:
        try:
            user = client.users_info(user=uid).get("user", {})
        except SlackApiError as e:
            lookup_errors.append(f"{uid}: {e.response.get('error', str(e))}")
            continue
        if user.get("deleted"):
            continue
        is_bot = bool(user.get("is_bot")) or uid == "USLACKBOT"
        if is_bot and not include_bots:
            continue
        profile = user.get("profile", {}) or {}
        team_id = user.get("team_id", "") or profile.get("team", "")
        is_external = bool(team_id and my_team and team_id != my_team)
        if external_only and not is_external:
            continue
        members.append({
            "id": uid,
            "name": profile.get("real_name") or user.get("real_name") or user.get("name", ""),
            "display_name": profile.get("display_name", ""),
            "title": profile.get("title", ""),
            "email": profile.get("email", ""),
            "team_id": team_id,
            "is_external": is_external,
            "is_bot": is_bot,
        })

    out: dict[str, Any] = {
        "channel": _get_channel_name(client, channel_id),
        "channel_id": channel_id,
        "count": len(members),
        "members": members,
    }
    if lookup_errors:
        out["lookup_errors"] = lookup_errors
    return out


# ── CLI entry point ───────────────────────────────────────────────────────


def _print_usage():
    print("Usage:")
    print("  python3 tools/slack_user.py --auth-token T --user-email E <command> [options]")
    print()
    print("Commands:")
    print("  read-channel --channel CH [--limit N]                          Read recent messages")
    print("  send-message --channel CH --text T [--thread-ts TS] [--file P] [--file-title T]  Send a message (optionally with file)")
    print("  search --query Q [--limit N]                                   Search messages")
    print("  open-dm --users email1,email2,...                              Open a DM or group DM")
    print("  react --channel CH --ts TS --emoji NAME                        Add emoji reaction")
    print("  unreact --channel CH --ts TS --emoji NAME                      Remove emoji reaction")
    print("  upload-file --channels CH1,CH2 --file PATH [--title T] [--message M]  Upload a file")
    print("  list-shared-channels [--include-internal-shared]               List Slack Connect channels you're in")
    print("  channel-members --channel CH [--external-only] [--include-bots]  List member names/emails")
    sys.exit(1)


def _parse_single(args: list[str], flag: str, default: str | None = None) -> str | None:
    """Extract a single value for a flag."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return default


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    # Parse global args
    args = sys.argv[1:]
    auth_token = _parse_single(args, "--auth-token")
    user_email = _parse_single(args, "--user-email")

    if not auth_token or not user_email:
        print(json.dumps({"error": "Missing required --auth-token and --user-email arguments"}))
        sys.exit(1)

    # Verify auth
    if not _verify_auth(auth_token, user_email):
        print(json.dumps({
            "error": "Authentication failed. The auth token is invalid, expired, or doesn't match the user email. "
            "This is a system error — please try your request again."
        }))
        sys.exit(1)

    # Strip global args to find command
    filtered = []
    skip_next = False
    for i, arg in enumerate(args):
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

    if command == "read-channel":
        channel = _parse_single(rest, "--channel")
        if not channel:
            print(json.dumps({"error": "read-channel requires --channel"}))
            sys.exit(1)
        limit = int(_parse_single(rest, "--limit", "50"))
        result = read_channel(user_email, channel, limit)
        print(json.dumps(result, indent=2))

    elif command == "send-message":
        channel = _parse_single(rest, "--channel")
        text = _parse_single(rest, "--text")
        if not channel or not text:
            print(json.dumps({"error": "send-message requires --channel and --text"}))
            sys.exit(1)
        thread_ts = _parse_single(rest, "--thread-ts", "")
        file_path = _parse_single(rest, "--file", "")
        file_title = _parse_single(rest, "--file-title", "")
        result = send_message(user_email, channel, text, thread_ts, file_path, file_title)
        print(json.dumps(result, indent=2))

    elif command == "search":
        query = _parse_single(rest, "--query")
        if not query:
            print(json.dumps({"error": "search requires --query"}))
            sys.exit(1)
        limit = int(_parse_single(rest, "--limit", "20"))
        result = search_messages(user_email, query, limit)
        print(json.dumps(result, indent=2))

    elif command == "open-dm":
        users = _parse_single(rest, "--users")
        if not users:
            print(json.dumps({"error": "open-dm requires --users (comma-separated emails)"}))
            sys.exit(1)
        result = open_dm(user_email, users)
        print(json.dumps(result, indent=2))

    elif command == "react":
        channel = _parse_single(rest, "--channel")
        ts = _parse_single(rest, "--ts")
        emoji = _parse_single(rest, "--emoji")
        if not channel or not ts or not emoji:
            print(json.dumps({"error": "react requires --channel, --ts, and --emoji"}))
            sys.exit(1)
        result = add_reaction(user_email, channel, ts, emoji)
        print(json.dumps(result, indent=2))

    elif command == "unreact":
        channel = _parse_single(rest, "--channel")
        ts = _parse_single(rest, "--ts")
        emoji = _parse_single(rest, "--emoji")
        if not channel or not ts or not emoji:
            print(json.dumps({"error": "unreact requires --channel, --ts, and --emoji"}))
            sys.exit(1)
        result = remove_reaction(user_email, channel, ts, emoji)
        print(json.dumps(result, indent=2))

    elif command == "upload-file":
        channels = _parse_single(rest, "--channels")
        file = _parse_single(rest, "--file")
        if not channels or not file:
            print(json.dumps({"error": "upload-file requires --channels and --file"}))
            sys.exit(1)
        title = _parse_single(rest, "--title", "")
        message = _parse_single(rest, "--message", "")
        result = upload_file(user_email, channels, file, title, message)
        print(json.dumps(result, indent=2))

    elif command == "list-shared-channels":
        result = list_shared_channels(user_email, "--include-internal-shared" in rest)
        print(json.dumps(result, indent=2))

    elif command == "channel-members":
        channel = _parse_single(rest, "--channel")
        if not channel:
            print(json.dumps({"error": "channel-members requires --channel"}))
            sys.exit(1)
        result = channel_members(
            user_email, channel,
            external_only="--external-only" in rest,
            include_bots="--include-bots" in rest,
        )
        print(json.dumps(result, indent=2))

    else:
        print(json.dumps({"error": f"Unknown command: {command}"}))
        _print_usage()
