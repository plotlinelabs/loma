"""Gmail API client for the Loma agent.

Provides CLI commands to read, search, send emails, and create drafts using
a user's personal Google OAuth tokens.

Commands:
  1. gmail.py list-inbox --user-email EMAIL [--limit N] [--query Q]
  2. gmail.py read-email --user-email EMAIL --message-id ID
  3. gmail.py send-email --user-email EMAIL --to ADDR --subject S --body B [--cc ADDR] [--attachments PATH1,PATH2]
  4. gmail.py search --user-email EMAIL --query Q [--limit N]
  5. gmail.py create-draft --user-email EMAIL --body B [--to ADDR] [--subject S] [--cc ADDR] [--thread-id TID] [--in-reply-to MSG_ID] [--attachments PATH1,PATH2] [--html-body HTML | --html-body-file PATH]

  For rich, full-width emails with clickable links, pass --html-body-file (the HTML
  renders as the email; --body becomes the plain-text fallback). Use a file for large
  HTML to avoid shell-escaping issues.

Requires:
  - User must have connected their Google account via the Integrations page
  - OBSERVABILITY_MONGODB_URI, OAUTH_ENCRYPTION_KEY, GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET environment variables

Usage (called by the agent via Bash):
  python3 tools/gmail.py list-inbox --user-email adarsh@example.com --limit 10
  python3 tools/gmail.py read-email --user-email adarsh@example.com --message-id 18abc123
  python3 tools/gmail.py send-email --user-email adarsh@example.com --to bob@example.com --subject "Hello" --body "Hi Bob"
  python3 tools/gmail.py search --user-email adarsh@example.com --query "from:alice subject:invoice"
"""

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from dotenv import load_dotenv

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from tools._google_auth import get_google_credentials  # noqa: E402

# Lazy import to avoid loading google libs at module level
_gmail_service = None

# Maximum attachment size: 25 MB (Gmail API limit)
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024


async def _get_service(user_email: str):
    """Build an authenticated Gmail API service for the given user."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload: dict) -> str:
    """Extract plain text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart — look for text/plain part
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Nested multipart
        if part.get("parts"):
            result = _decode_body(part)
            if result:
                return result

    return ""


def _extract_headers(headers: list[dict], *names: str) -> dict[str, str]:
    """Extract specific headers from a Gmail message."""
    result = {}
    name_lower = {n.lower(): n for n in names}
    for h in headers:
        key = h.get("name", "").lower()
        if key in name_lower:
            result[name_lower[key]] = h.get("value", "")
    return result


def _format_message_summary(msg: dict) -> dict[str, Any]:
    """Format a Gmail message into a compact summary."""
    headers = msg.get("payload", {}).get("headers", [])
    extracted = _extract_headers(headers, "From", "To", "Subject", "Date")
    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "from": extracted.get("From", ""),
        "to": extracted.get("To", ""),
        "subject": extracted.get("Subject", "(no subject)"),
        "date": extracted.get("Date", ""),
        "snippet": msg.get("snippet", ""),
        "labelIds": msg.get("labelIds", []),
    }


_HTML_TAG_RE = re.compile(r"</[a-zA-Z][\w-]*>|<[a-zA-Z][\w-]*[^>]*/>|<(?:html|body|div|p|ul|ol|li|table|tr|td|h[1-6]|br|a|strong|span)\b", re.IGNORECASE)


def _looks_like_html(text: str) -> bool:
    """Heuristic: does this body appear to be HTML markup rather than plain text?

    True when it starts with a tag and contains real HTML structure. Guards
    against callers placing HTML in the plain-text `body` (which renders as raw
    markup in a narrow column instead of a full-width rich email).
    """
    stripped = (text or "").lstrip()
    return stripped.startswith("<") and bool(_HTML_TAG_RE.search(stripped))


def _html_to_plaintext(html: str) -> str:
    """Derive a crude plain-text fallback from an HTML body (strip tags, unescape)."""
    import html as _htmllib

    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|tr|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _htmllib.unescape(text)
    # Collapse excessive blank lines.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _build_message_with_attachments(
    body: str,
    attachment_paths: list[str] | None = None,
    html_body: str | None = None,
) -> MIMEMultipart | MIMEText:
    """Build an email message, optionally with HTML and/or file attachments.

    - Plain text only: returns MIMEText.
    - With html_body: returns a multipart/alternative (plain-text fallback +
      HTML) so clients render the rich version and it spans the full width.
    - With attachments: wraps the content in multipart/mixed.

    Safety net: if no html_body is given but `body` itself looks like HTML, it is
    promoted to the HTML part (with a stripped plain-text fallback) — so an HTML
    body never gets delivered as raw markup in a plain-text email.
    """
    if not html_body and _looks_like_html(body):
        html_body = body
        body = _html_to_plaintext(body)

    # Content part: plain text, or a plain+HTML alternative.
    if html_body:
        content: MIMEMultipart | MIMEText = MIMEMultipart("alternative")
        content.attach(MIMEText(body, "plain"))
        content.attach(MIMEText(html_body, "html"))
    else:
        content = MIMEText(body)

    if not attachment_paths:
        return content

    msg = MIMEMultipart("mixed")
    msg.attach(content)

    for file_path in attachment_paths:
        if not os.path.isfile(file_path):
            raise ValueError(f"Attachment file not found: {file_path}")

        file_size = os.path.getsize(file_path)
        if file_size > MAX_ATTACHMENT_SIZE:
            raise ValueError(
                f"Attachment too large: {file_path} ({file_size / 1024 / 1024:.1f} MB). "
                f"Gmail limit is 25 MB."
            )

        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = "application/octet-stream"

        main_type, sub_type = content_type.split("/", 1)

        with open(file_path, "rb") as f:
            part = MIMEBase(main_type, sub_type)
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=os.path.basename(file_path),
            )
            msg.attach(part)

    return msg


def _parse_attachments(attachments_str: str) -> list[str]:
    """Parse a comma-separated string of file paths into a list.

    Returns an empty list if the string is empty.
    """
    if not attachments_str:
        return []
    return [p.strip() for p in attachments_str.split(",") if p.strip()]


def _resolve_html_body(args) -> str:
    """Resolve the HTML body from either --html-body-file or --html-body.

    Prefers the file (avoids shell-escaping large HTML); falls back to inline.
    """
    path = getattr(args, "html_body_file", "") or ""
    if path:
        if not os.path.isfile(path):
            raise ValueError(f"HTML body file not found: {path}")
        with open(path, encoding="utf-8") as f:
            return f.read()
    return getattr(args, "html_body", "") or ""


# ── Commands ──────────────────────────────────────────────────────────────


async def list_inbox(user_email: str, limit: int = 10, query: str = "") -> dict:
    """List recent inbox messages."""
    service = await _get_service(user_email)
    q = "in:inbox"
    if query:
        q += f" {query}"

    result = service.users().messages().list(
        userId="me", q=q, maxResults=limit,
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return {"messages": [], "total": 0}

    # Fetch message details (batch)
    detailed = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        detailed.append(_format_message_summary(msg))

    return {
        "messages": detailed,
        "total": result.get("resultSizeEstimate", len(detailed)),
    }


async def read_email(user_email: str, message_id: str) -> dict:
    """Read a specific email message with full body."""
    service = await _get_service(user_email)
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full",
    ).execute()

    headers = msg.get("payload", {}).get("headers", [])
    extracted = _extract_headers(headers, "From", "To", "Cc", "Subject", "Date", "Message-ID")
    body = _decode_body(msg.get("payload", {}))

    # Truncate very long bodies
    if len(body) > 10000:
        body = body[:10000] + "\n\n... [truncated — message too long]"

    attachments = []
    for part in msg.get("payload", {}).get("parts", []):
        filename = part.get("filename")
        if filename:
            attachments.append({
                "filename": filename,
                "mimeType": part.get("mimeType", ""),
                "size": part.get("body", {}).get("size", 0),
            })

    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "from": extracted.get("From", ""),
        "to": extracted.get("To", ""),
        "cc": extracted.get("Cc", ""),
        "subject": extracted.get("Subject", "(no subject)"),
        "date": extracted.get("Date", ""),
        "messageId": extracted.get("Message-ID", ""),
        "body": body,
        "attachments": attachments,
        "labelIds": msg.get("labelIds", []),
    }


async def send_email(
    user_email: str, to: str, subject: str, body: str, cc: str = "",
    attachments: str = "", html_body: str = "",
) -> dict:
    """Send an email on behalf of the user, optionally with HTML and attachments.

    Args:
        attachments: Comma-separated file paths (e.g., "/tmp/a.pdf,/tmp/b.csv").
        html_body: Optional HTML body; `body` becomes the plain-text fallback.
    """
    service = await _get_service(user_email)

    attachment_paths = _parse_attachments(attachments)
    message = _build_message_with_attachments(body, attachment_paths, html_body or None)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    result = service.users().messages().send(
        userId="me", body={"raw": raw},
    ).execute()

    response: dict[str, Any] = {
        "sent": True,
        "messageId": result.get("id"),
        "threadId": result.get("threadId"),
    }
    if attachment_paths:
        response["attachments"] = [os.path.basename(p) for p in attachment_paths]

    return response


async def create_draft(
    user_email: str, to: str = "", subject: str = "", body: str = "",
    cc: str = "", thread_id: str = "", in_reply_to: str = "",
    attachments: str = "", html_body: str = "",
) -> dict:
    """Create a draft email in the user's Gmail, optionally with HTML/attachments.

    For threading (reply drafts), pass:
      - thread_id: Gmail thread ID to place the draft in
      - in_reply_to: Message-ID header of the message being replied to

    Args:
        attachments: Comma-separated file paths (e.g., "/tmp/a.pdf,/tmp/b.csv").
        html_body: Optional HTML body; `body` becomes the plain-text fallback.
    """
    service = await _get_service(user_email)

    attachment_paths = _parse_attachments(attachments)
    message = _build_message_with_attachments(body, attachment_paths, html_body or None)
    if to:
        message["to"] = to
    if subject:
        message["subject"] = subject
    if cc:
        message["cc"] = cc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    draft_body: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    result = service.users().drafts().create(
        userId="me", body=draft_body,
    ).execute()

    response: dict[str, Any] = {
        "created": True,
        "draftId": result.get("id"),
        "messageId": result.get("message", {}).get("id"),
        "threadId": result.get("message", {}).get("threadId", ""),
    }
    if attachment_paths:
        response["attachments"] = [os.path.basename(p) for p in attachment_paths]

    return response


async def search_emails(user_email: str, query: str, limit: int = 10) -> dict:
    """Search emails using Gmail query syntax."""
    service = await _get_service(user_email)

    result = service.users().messages().list(
        userId="me", q=query, maxResults=limit,
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return {"messages": [], "total": 0}

    detailed = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        detailed.append(_format_message_summary(msg))

    return {
        "messages": detailed,
        "total": result.get("resultSizeEstimate", len(detailed)),
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Gmail API tool for Loma agent")
    parser.add_argument("--auth-token", required=True, help="HMAC-signed user auth token")
    sub = parser.add_subparsers(dest="command", required=True)

    # list-inbox
    p_list = sub.add_parser("list-inbox", help="List recent inbox messages")
    p_list.add_argument("--user-email", required=True)
    p_list.add_argument("--limit", type=int, default=10)
    p_list.add_argument("--query", default="")

    # read-email
    p_read = sub.add_parser("read-email", help="Read a specific email")
    p_read.add_argument("--user-email", required=True)
    p_read.add_argument("--message-id", required=True)

    # send-email
    p_send = sub.add_parser("send-email", help="Send an email")
    p_send.add_argument("--user-email", required=True)
    p_send.add_argument("--to", required=True)
    p_send.add_argument("--subject", required=True)
    p_send.add_argument("--body", required=True)
    p_send.add_argument("--cc", default="")
    p_send.add_argument("--attachments", default="", help="Comma-separated file paths to attach")
    p_send.add_argument("--html-body", default="", help="HTML body (renders full-width with clickable links); --body is the plain-text fallback")
    p_send.add_argument("--html-body-file", default="", help="Path to a file containing the HTML body (avoids shell-escaping large HTML)")

    # create-draft
    p_draft = sub.add_parser("create-draft", help="Create a draft email")
    p_draft.add_argument("--user-email", required=True)
    p_draft.add_argument("--to", default="")
    p_draft.add_argument("--subject", default="")
    p_draft.add_argument("--body", required=True)
    p_draft.add_argument("--cc", default="")
    p_draft.add_argument("--thread-id", default="", help="Gmail thread ID for reply drafts")
    p_draft.add_argument("--in-reply-to", default="", help="Message-ID header of the message being replied to")
    p_draft.add_argument("--attachments", default="", help="Comma-separated file paths to attach")
    p_draft.add_argument("--html-body", default="", help="HTML body (renders full-width with clickable links); --body is the plain-text fallback")
    p_draft.add_argument("--html-body-file", default="", help="Path to a file containing the HTML body (avoids shell-escaping large HTML)")

    # search
    p_search = sub.add_parser("search", help="Search emails")
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
        if args.command == "list-inbox":
            result = asyncio.run(list_inbox(args.user_email, args.limit, args.query))
        elif args.command == "read-email":
            result = asyncio.run(read_email(args.user_email, args.message_id))
        elif args.command == "send-email":
            result = asyncio.run(send_email(
                args.user_email, args.to, args.subject, args.body, args.cc,
                args.attachments, _resolve_html_body(args),
            ))
        elif args.command == "create-draft":
            result = asyncio.run(create_draft(
                args.user_email, args.to, args.subject, args.body, args.cc,
                args.thread_id, args.in_reply_to, args.attachments,
                _resolve_html_body(args),
            ))
        elif args.command == "search":
            result = asyncio.run(search_emails(args.user_email, args.query, args.limit))
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Gmail API error: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
