"""Slack channel tool — list channels, read history, and send messages.

Provides CLI commands for the Loma agent:
  1. slack_reader.py channels [--query <name>]    — List accessible channels
  2. slack_reader.py history <channel> [--limit N] [--thread-ts TS] — Read recent channel messages, or replies in a specific thread when --thread-ts is given
  3. slack_reader.py send <channel> --text "msg" [--thread-ts TS] [--require-thread] [--file PATH] [--file-title T] — Send a message (optionally with file). --require-thread refuses to post unless --thread-ts is given.
  4. echo "msg" | slack_reader.py send <channel>   — Send a message via stdin (for long/multiline text)

Requires SLACK_BOT_TOKEN environment variable.
Bot must be invited to private channels to read/write them.
Bot needs chat:write scope to send messages.

Usage (called by the agent via Bash):
  python3 tools/slack_reader.py channels
  python3 tools/slack_reader.py channels --query general
  python3 tools/slack_reader.py history "#general" --limit 20
  python3 tools/slack_reader.py history C12345ABC
  python3 tools/slack_reader.py history C12345ABC --thread-ts 1234567890.123456
  python3 tools/slack_reader.py send "#general" --text "Hello from Loma!"
  python3 tools/slack_reader.py send C12345ABC --thread-ts 1234567890.123456 --text "Thread reply"
  echo "Long message here" | python3 tools/slack_reader.py send "#general"
"""

import json
import os
import sys
import logging
from datetime import datetime, timezone
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


def _get_bot_token() -> str:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "SLACK_BOT_TOKEN environment variable is not set. "
            "Please configure it before using Slack tools."
        )
    return token


def _resolve_channel_id(client: WebClient, channel_input: str) -> tuple[str | None, str | None]:
    """Resolve a channel name or ID to a channel ID.

    Returns (channel_id, error_message). One will be None.
    """
    # Strip leading # if present
    channel_input = channel_input.lstrip("#")

    # If it looks like a channel ID (starts with C or G, alphanumeric), use directly
    if len(channel_input) >= 9 and channel_input[0] in ("C", "G") and channel_input.isalnum():
        return channel_input, None

    # Otherwise, search by name
    try:
        cursor = None
        while True:
            kwargs: dict[str, Any] = {
                "types": "public_channel,private_channel",
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

    return None, f"Channel not found: #{channel_input}. Make sure the bot is a member of the channel."


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

    # Attachments (links, unfurled content)
    attachments = msg.get("attachments")
    if attachments:
        result["attachments"] = [
            a.get("title") or a.get("fallback") or a.get("text", "")
            for a in attachments
            if a.get("title") or a.get("fallback") or a.get("text")
        ]

    return result


def list_channels(query: str | None = None) -> dict[str, Any]:
    """List Slack channels the bot has access to."""
    client = WebClient(token=_get_bot_token())
    all_channels = []

    try:
        cursor = None
        while True:
            kwargs: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "limit": 200,
                "exclude_archived": True,
            }
            if cursor:
                kwargs["cursor"] = cursor

            result = client.conversations_list(**kwargs)
            channels = result.get("channels", [])
            all_channels.extend(channels)

            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    except SlackApiError as e:
        return {"error": f"Slack API error: {e.response['error']}"}

    # Filter by query if provided
    if query:
        query_lower = query.lower()
        all_channels = [
            ch for ch in all_channels
            if query_lower in (ch.get("name", "")).lower()
            or query_lower in (ch.get("purpose", {}).get("value", "")).lower()
        ]

    if not all_channels:
        msg = f"No channels found matching '{query}'." if query else "No accessible channels found."
        return {"error": msg}

    formatted = []
    for ch in sorted(all_channels, key=lambda c: c.get("name", "")):
        entry: dict[str, Any] = {
            "name": f"#{ch.get('name', '')}",
            "id": ch.get("id", ""),
            "is_private": ch.get("is_private", False),
            "num_members": ch.get("num_members", 0),
        }
        purpose = ch.get("purpose", {}).get("value", "")
        if purpose:
            entry["purpose"] = purpose
        topic = ch.get("topic", {}).get("value", "")
        if topic:
            entry["topic"] = topic
        formatted.append(entry)

    return {
        "count": len(formatted),
        "channels": formatted,
    }


def read_history(
    channel: str, limit: int = 50, thread_ts: str | None = None
) -> dict[str, Any]:
    """Read recent messages from a Slack channel.

    When ``thread_ts`` is provided, returns the replies of that thread (the
    root message plus all replies, in chronological order) via
    ``conversations_replies`` instead of the channel-level history. This is
    what callers need to gather the full context of a threaded report — the
    channel-level ``conversations_history`` only returns top-level messages
    and never the replies inside a thread.
    """
    client = WebClient(token=_get_bot_token())

    # Resolve channel name to ID
    channel_id, error = _resolve_channel_id(client, channel)
    if error:
        return {"error": error}

    try:
        if thread_ts:
            result = client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=min(limit, 200),
            )
        else:
            result = client.conversations_history(
                channel=channel_id,
                limit=min(limit, 200),
            )
        messages = result.get("messages", [])
    except SlackApiError as e:
        error_msg = e.response.get("error", str(e))
        if error_msg == "channel_not_found":
            return {"error": f"Channel not found: {channel}. Make sure the bot is a member."}
        if error_msg == "not_in_channel":
            return {"error": f"Bot is not a member of {channel}. Invite the bot to the channel first."}
        if error_msg == "thread_not_found":
            return {"error": f"Thread {thread_ts} not found in {channel}."}
        return {"error": f"Slack API error: {error_msg}"}

    if not messages:
        where = f"thread {thread_ts}" if thread_ts else channel
        return {"error": f"No messages found in {where}."}

    # conversations_replies already returns chronological order (root first);
    # conversations_history returns newest-first, so reverse only that case.
    if not thread_ts:
        messages.reverse()

    users_cache: dict[str, str] = {}
    formatted = [_format_message(m, client, users_cache) for m in messages]

    result_payload: dict[str, Any] = {
        "channel": channel,
        "channel_id": channel_id,
        "count": len(formatted),
        "messages": formatted,
    }
    if thread_ts:
        result_payload["thread_ts"] = thread_ts
    return result_payload


def send_message(
    channel: str, text: str, thread_ts: str | None = None,
    file_path: str | None = None, file_title: str | None = None,
    require_thread: bool = False,
) -> dict[str, Any]:
    """Send a message to a Slack channel, optionally with a file attachment.

    Args:
        channel: Channel name (with or without #) or channel ID.
        text: Message text (supports Slack mrkdwn formatting).
        thread_ts: Optional thread timestamp to reply in a thread.
        file_path: Optional path to a file to upload with the message.
        file_title: Optional title for the uploaded file.
        require_thread: Opt-in guard. When ``True`` and no ``thread_ts`` is
            provided, the send is refused (nothing is posted) and an error is
            returned. Defaults to ``False`` so every existing caller behaves
            exactly as before — this flag only affects callers that explicitly
            opt in (e.g. the oncall-ticket skill, which must only ever reply
            inside the triggering thread).

    Returns:
        JSON with channel, timestamp, and text of the sent message, or an error.
    """
    if not text or not text.strip():
        return {"error": "Message text cannot be empty."}

    if require_thread and not (thread_ts and thread_ts.strip()):
        return {
            "error": (
                "require_thread is set but no thread_ts was provided; refusing "
                "to post a top-level channel message. Pass --thread-ts to reply "
                "in a thread."
            )
        }

    client = WebClient(token=_get_bot_token())

    # Resolve channel name to ID
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
                "ok": True,
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
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        result = client.chat_postMessage(**kwargs)

        return {
            "ok": True,
            "method": "text_message",
            "channel": channel,
            "channel_id": channel_id,
            "timestamp": result.get("ts", ""),
            "message": result.get("message", {}).get("text", text),
        }

    except SlackApiError as e:
        error_msg = e.response.get("error", str(e))
        if error_msg == "channel_not_found":
            return {"error": f"Channel not found: {channel}. Make sure the bot is a member."}
        if error_msg == "not_in_channel":
            return {"error": f"Bot is not a member of {channel}. Invite the bot to the channel first."}
        if error_msg == "invalid_auth":
            return {"error": "Invalid bot token. Check SLACK_BOT_TOKEN."}
        if error_msg == "missing_scope":
            return {"error": "Bot is missing the chat:write scope. Update the bot's OAuth permissions."}
        if error_msg == "no_text":
            return {"error": "Message text cannot be empty."}
        return {"error": f"Slack API error: {error_msg}"}


def _parse_single(args: list[str], flag: str, default: str | None = None) -> str | None:
    """Extract a single value for a flag."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return default


def _print_usage():
    print("Usage:")
    print("  python3 tools/slack_reader.py channels [--query <name>]")
    print("    List accessible channels, optionally filtered by name")
    print()
    print("  python3 tools/slack_reader.py history <channel> [--limit N] [--thread-ts TS]")
    print("    Read recent messages from a channel (name or ID)")
    print("    Examples: history '#general'  or  history C12345ABC")
    print("    Options: --thread-ts <ts>  Read replies in a specific thread instead of channel history")
    print("    Default limit: 50 messages")
    print()
    print("  python3 tools/slack_reader.py send <channel> --text 'message' [--file PATH] [--file-title T]")
    print("    Send a message to a channel (name or ID), optionally with a file attachment")
    print("    Options: --thread-ts <ts>  Reply in a thread")
    print("             --require-thread  Refuse to post unless --thread-ts is set (no top-level post)")
    print("             --file <path>     Attach a file to the message")
    print("             --file-title <t>  Title for the uploaded file")
    print("    Stdin:   echo 'msg' | python3 tools/slack_reader.py send <channel>")
    sys.exit(1)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = sys.argv[2:]

    if command == "channels":
        query = _parse_single(rest, "--query")
        result = list_channels(query)
        print(json.dumps(result, indent=2))

    elif command == "history":
        if not rest or rest[0].startswith("--"):
            print("Error: history requires a channel name or ID")
            sys.exit(1)
        channel = rest[0]
        limit = int(_parse_single(rest[1:], "--limit", "50"))
        thread_ts = _parse_single(rest[1:], "--thread-ts")
        result = read_history(channel, limit, thread_ts=thread_ts)
        print(json.dumps(result, indent=2))

    elif command == "send":
        if not rest or rest[0].startswith("--"):
            print(json.dumps({"error": "send requires a channel name or ID"}))
            sys.exit(1)
        channel = rest[0]
        text = _parse_single(rest[1:], "--text")
        thread_ts = _parse_single(rest[1:], "--thread-ts")
        file_path = _parse_single(rest[1:], "--file")
        file_title = _parse_single(rest[1:], "--file-title")
        require_thread = "--require-thread" in rest[1:]

        # If --text not provided, read from stdin
        if not text:
            if not sys.stdin.isatty():
                text = sys.stdin.read().strip()
            else:
                print(json.dumps({"error": "No message text provided. Use --text 'message' or pipe via stdin."}))
                sys.exit(1)

        result = send_message(channel, text, thread_ts=thread_ts, file_path=file_path, file_title=file_title, require_thread=require_thread)
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}")
        _print_usage()
