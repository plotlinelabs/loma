"""Google Docs API client for the Loma agent (personal OAuth).

Provides CLI commands to read and edit Google Docs using a user's personal
OAuth tokens.

Commands:
  1. google_docs_personal.py get-info --user-email EMAIL --document-id ID
  2. google_docs_personal.py read-doc --user-email EMAIL --document-id ID
  3. google_docs_personal.py create-doc --user-email EMAIL --title T [--content C]
  4. google_docs_personal.py append-text --user-email EMAIL --document-id ID --text T
  5. google_docs_personal.py insert-text --user-email EMAIL --document-id ID --text T --index N
  6. google_docs_personal.py replace-text --user-email EMAIL --document-id ID --find F --replacement R
  7. google_docs_personal.py copy-doc --user-email EMAIL --document-id ID [--title T]

Requires:
  - User must have connected their Google account via the Integrations page
  - OBSERVABILITY_MONGODB_URI, OAUTH_ENCRYPTION_KEY, GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET environment variables

Usage (called by the agent via Bash):
  python3 tools/google_docs_personal.py get-info --user-email adarsh@example.com --document-id 1abc2def
  python3 tools/google_docs_personal.py read-doc --user-email adarsh@example.com --document-id 1abc2def
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

MAX_CONTENT_SIZE = 50_000  # ~50KB


async def _get_service(user_email: str):
    """Build an authenticated Google Docs API service."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("docs", "v1", credentials=creds)


def _extract_text(body: dict) -> str:
    """Extract all text content from a Google Docs body."""
    texts = []
    for element in body.get("content", []):
        paragraph = element.get("paragraph", {})
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun", {})
            content = text_run.get("content", "")
            if content:
                texts.append(content)

        # Tables
        table = element.get("table", {})
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                cell_text = _extract_text(cell)
                if cell_text.strip():
                    texts.append(cell_text)

    return "".join(texts)


# ── Commands ──────────────────────────────────────────────────────────────


async def get_info(user_email: str, document_id: str) -> dict:
    """Get document metadata (title, revision)."""
    service = await _get_service(user_email)
    doc = service.documents().get(documentId=document_id).execute()
    body = doc.get("body", {})
    content = body.get("content", [])
    return {
        "documentId": doc.get("documentId"),
        "title": doc.get("title", ""),
        "revisionId": doc.get("revisionId", ""),
        "elementCount": len(content),
    }


async def read_doc(user_email: str, document_id: str) -> dict:
    """Read the full text content of a Google Doc."""
    service = await _get_service(user_email)
    doc = service.documents().get(documentId=document_id).execute()
    body = doc.get("body", {})
    text = _extract_text(body)

    if len(text) > MAX_CONTENT_SIZE:
        text = text[:MAX_CONTENT_SIZE] + "\n\n... [truncated — document too large]"

    return {
        "documentId": doc.get("documentId"),
        "title": doc.get("title", ""),
        "content": text,
    }


async def create_doc(user_email: str, title: str, content: str = "") -> dict:
    """Create a new Google Doc, optionally with initial content."""
    service = await _get_service(user_email)
    doc = service.documents().create(body={"title": title}).execute()
    doc_id = doc.get("documentId")

    if content:
        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [
                {"insertText": {"location": {"index": 1}, "text": content}},
            ]},
        ).execute()

    return {
        "created": True,
        "documentId": doc_id,
        "title": doc.get("title", ""),
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
    }


async def append_text(user_email: str, document_id: str, text: str) -> dict:
    """Append text to the end of a Google Doc."""
    service = await _get_service(user_email)

    # Get current document to find the end index
    doc = service.documents().get(documentId=document_id).execute()
    body = doc.get("body", {})
    content = body.get("content", [])
    end_index = content[-1]["endIndex"] - 1 if content else 1

    service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": [
            {"insertText": {"location": {"index": end_index}, "text": text}},
        ]},
    ).execute()

    return {
        "appended": True,
        "documentId": document_id,
        "title": doc.get("title", ""),
        "charsAdded": len(text),
    }


async def insert_text(
    user_email: str, document_id: str, text: str, index: int,
) -> dict:
    """Insert text at a specific index in a Google Doc."""
    service = await _get_service(user_email)

    service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": [
            {"insertText": {"location": {"index": index}, "text": text}},
        ]},
    ).execute()

    return {
        "inserted": True,
        "documentId": document_id,
        "index": index,
        "charsAdded": len(text),
    }


async def replace_text(
    user_email: str, document_id: str, find: str, replacement: str,
    match_case: bool = True,
) -> dict:
    """Find and replace text across a Google Doc."""
    service = await _get_service(user_email)

    result = service.documents().batchUpdate(
        documentId=document_id,
        body={"requests": [
            {"replaceAllText": {
                "containsText": {"text": find, "matchCase": match_case},
                "replaceText": replacement,
            }},
        ]},
    ).execute()

    replies = result.get("replies", [])
    occurrences = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0) if replies else 0

    return {
        "replaced": True,
        "documentId": document_id,
        "find": find,
        "replacement": replacement,
        "occurrencesChanged": occurrences,
    }


async def copy_doc(user_email: str, document_id: str, title: str = "") -> dict:
    """Create a copy of a Google Doc."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    drive = build("drive", "v3", credentials=creds)

    metadata: dict = {}
    if title:
        metadata["name"] = title

    result = drive.files().copy(
        fileId=document_id,
        body=metadata,
        fields="id,name,webViewLink",
    ).execute()

    return {
        "copied": True,
        "documentId": result.get("id"),
        "title": result.get("name", ""),
        "url": result.get("webViewLink", ""),
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Google Docs personal tool for Loma agent")
    parser.add_argument("--auth-token", required=True, help="HMAC-signed user auth token")
    sub = parser.add_subparsers(dest="command", required=True)

    # get-info
    p_info = sub.add_parser("get-info", help="Get document metadata")
    p_info.add_argument("--user-email", required=True)
    p_info.add_argument("--document-id", required=True)

    # read-doc
    p_read = sub.add_parser("read-doc", help="Read document content")
    p_read.add_argument("--user-email", required=True)
    p_read.add_argument("--document-id", required=True)

    # create-doc
    p_create = sub.add_parser("create-doc", help="Create a new Google Doc")
    p_create.add_argument("--user-email", required=True)
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--content", default="", help="Initial text content")

    # append-text
    p_append = sub.add_parser("append-text", help="Append text to end of doc")
    p_append.add_argument("--user-email", required=True)
    p_append.add_argument("--document-id", required=True)
    p_append.add_argument("--text", required=True)

    # insert-text
    p_insert = sub.add_parser("insert-text", help="Insert text at a specific index")
    p_insert.add_argument("--user-email", required=True)
    p_insert.add_argument("--document-id", required=True)
    p_insert.add_argument("--text", required=True)
    p_insert.add_argument("--index", type=int, required=True, help="1-based character index")

    # replace-text
    p_replace = sub.add_parser("replace-text", help="Find and replace text in doc")
    p_replace.add_argument("--user-email", required=True)
    p_replace.add_argument("--document-id", required=True)
    p_replace.add_argument("--find", required=True)
    p_replace.add_argument("--replacement", required=True)
    p_replace.add_argument("--match-case", action="store_true", default=True)

    # copy-doc
    p_copy = sub.add_parser("copy-doc", help="Create a copy of a Google Doc")
    p_copy.add_argument("--user-email", required=True)
    p_copy.add_argument("--document-id", required=True)
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
            result = asyncio.run(get_info(args.user_email, args.document_id))
        elif args.command == "read-doc":
            result = asyncio.run(read_doc(args.user_email, args.document_id))
        elif args.command == "create-doc":
            result = asyncio.run(create_doc(args.user_email, args.title, args.content))
        elif args.command == "append-text":
            result = asyncio.run(append_text(args.user_email, args.document_id, args.text))
        elif args.command == "insert-text":
            result = asyncio.run(insert_text(
                args.user_email, args.document_id, args.text, args.index,
            ))
        elif args.command == "replace-text":
            result = asyncio.run(replace_text(
                args.user_email, args.document_id, args.find, args.replacement,
                args.match_case,
            ))
        elif args.command == "copy-doc":
            result = asyncio.run(copy_doc(
                args.user_email, args.document_id, args.title,
            ))
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Google Docs API error: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
