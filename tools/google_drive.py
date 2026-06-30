"""Google Drive API client for the Loma agent.

Provides CLI commands to search, read, and upload files to a user's personal
Google Drive using their OAuth tokens.

Commands:
  1. google_drive.py list-files --user-email EMAIL [--query Q] [--limit N]
  2. google_drive.py read-file --user-email EMAIL --file-id ID
  3. google_drive.py search --user-email EMAIL --query Q [--limit N]
  4. google_drive.py upload-file --user-email EMAIL --file-path PATH [--name N] [--folder-id FID] [--mime-type M]
  5. google_drive.py create-folder --user-email EMAIL --name N [--parent-id PID]
  6. google_drive.py copy-file --user-email EMAIL --file-id ID [--name N] [--folder-id FID]

Requires:
  - User must have connected their Google account via the Integrations page
  - OBSERVABILITY_MONGODB_URI, OAUTH_ENCRYPTION_KEY, GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET environment variables

Usage (called by the agent via Bash):
  python3 tools/google_drive.py list-files --user-email adarsh@example.com --limit 10
  python3 tools/google_drive.py search --user-email adarsh@example.com --query "name contains 'Q4 Report'"
  python3 tools/google_drive.py read-file --user-email adarsh@example.com --file-id 1abc2def3ghi
"""

import argparse
import asyncio
import io
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from tools._google_auth import get_google_credentials  # noqa: E402

# MIME types that can be exported from Google Docs native formats
_EXPORT_MIME_TYPES = {
    "application/vnd.google-apps.document": (
        "text/plain",
        "Google Doc",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "text/csv",
        "Google Sheet",
    ),
    "application/vnd.google-apps.presentation": (
        "text/plain",
        "Google Slides",
    ),
}

# MIME types where we can extract text from binary content
_EXTRACTABLE_BINARY_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Max content size to return (prevent huge files from overwhelming the agent)
MAX_CONTENT_SIZE = 50_000  # ~50KB


def _extract_text_from_binary(content: bytes, mime_type: str) -> str | None:
    """Extract text from PDF or DOCX bytes. Returns None on failure."""
    if mime_type == "application/pdf":
        try:
            import pymupdf
            doc = pymupdf.open(stream=content, filetype="pdf")
            pages = [page.get_text() for page in doc]
            doc.close()
            return "\n\n".join(pages).strip() or None
        except Exception:
            return None

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs).strip() or None
        except Exception:
            return None

    return None


async def _get_service(user_email: str):
    """Build an authenticated Google Drive API service."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("drive", "v3", credentials=creds)


def _format_file(f: dict) -> dict[str, Any]:
    """Format a Drive file into a compact summary."""
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "mimeType": f.get("mimeType", ""),
        "size": f.get("size"),
        "modifiedTime": f.get("modifiedTime"),
        "createdTime": f.get("createdTime"),
        "owners": [o.get("emailAddress", "") for o in f.get("owners", [])],
        "webViewLink": f.get("webViewLink"),
        "shared": f.get("shared", False),
    }


# ── Commands ──────────────────────────────────────────────────────────────


async def list_files(user_email: str, query: str = "", limit: int = 10) -> dict:
    """List files in the user's Drive."""
    service = await _get_service(user_email)

    q = query if query else None
    fields = "files(id,name,mimeType,size,modifiedTime,createdTime,owners,webViewLink,shared),nextPageToken"

    result = service.files().list(
        q=q,
        pageSize=limit,
        fields=fields,
        orderBy="modifiedTime desc",
    ).execute()

    files = [_format_file(f) for f in result.get("files", [])]
    return {
        "files": files,
        "total": len(files),
        "hasMore": bool(result.get("nextPageToken")),
    }


async def read_file(user_email: str, file_id: str) -> dict:
    """Read the content of a file from Drive.

    For Google Docs/Sheets/Slides, exports as text.
    For PDF and DOCX files, downloads and extracts text.
    For other text-like files, downloads content directly.
    """
    service = await _get_service(user_email)

    # Get file metadata first
    meta = service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,size,modifiedTime,owners,webViewLink",
    ).execute()

    mime_type = meta.get("mimeType", "")
    file_name = meta.get("name", "")
    content = ""

    # Google Docs native format — export
    if mime_type in _EXPORT_MIME_TYPES:
        export_mime, doc_type = _EXPORT_MIME_TYPES[mime_type]
        try:
            response = service.files().export(
                fileId=file_id, mimeType=export_mime,
            ).execute()
            if isinstance(response, bytes):
                content = response.decode("utf-8", errors="replace")
            else:
                content = str(response)
        except Exception as e:
            content = f"[Error exporting {doc_type}: {e}]"

    # Regular file — try to download (only text-like files)
    elif mime_type.startswith("text/") or mime_type in (
        "application/json", "application/xml", "application/javascript",
        "application/x-yaml", "application/csv",
    ):
        try:
            from googleapiclient.http import MediaIoBaseDownload

            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            content = fh.getvalue().decode("utf-8", errors="replace")
        except Exception as e:
            content = f"[Error downloading file: {e}]"

    # Extractable binary — download and extract text (PDF, DOCX)
    elif mime_type in _EXTRACTABLE_BINARY_MIME_TYPES:
        try:
            from googleapiclient.http import MediaIoBaseDownload

            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            extracted = _extract_text_from_binary(fh.getvalue(), mime_type)
            if extracted:
                content = extracted
            else:
                content = (
                    f"[Could not extract text from {mime_type} file. "
                    f"Use webViewLink to open in browser: {meta.get('webViewLink', 'N/A')}]"
                )
        except Exception as e:
            content = f"[Error downloading file: {e}]"

    else:
        content = (
            f"[Binary file ({mime_type}) — cannot display content. "
            f"Use webViewLink to open in browser: {meta.get('webViewLink', 'N/A')}]"
        )

    # Truncate if too large
    if len(content) > MAX_CONTENT_SIZE:
        content = content[:MAX_CONTENT_SIZE] + "\n\n... [truncated — file too large]"

    return {
        "id": meta.get("id"),
        "name": file_name,
        "mimeType": mime_type,
        "modifiedTime": meta.get("modifiedTime"),
        "webViewLink": meta.get("webViewLink"),
        "content": content,
    }


async def search_files(user_email: str, query: str, limit: int = 10) -> dict:
    """Search files in Drive using the Drive query syntax.

    Common query patterns:
      name contains 'report'
      mimeType='application/pdf'
      modifiedTime > '2026-01-01'
      fullText contains 'quarterly'
    """
    return await list_files(user_email, query=query, limit=limit)


async def upload_file(
    user_email: str,
    file_path: str,
    name: str = "",
    folder_id: str = "",
    mime_type: str = "",
) -> dict:
    """Upload a local file to the user's Google Drive."""
    import mimetypes
    from googleapiclient.http import MediaFileUpload

    service = await _get_service(user_email)

    local_path = os.path.abspath(file_path)
    if not os.path.isfile(local_path):
        return {"error": f"File not found: {file_path}"}

    file_name = name or os.path.basename(local_path)
    detected_mime = mime_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    file_metadata: dict = {"name": file_name}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(local_path, mimetype=detected_mime, resumable=True)
    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,mimeType,webViewLink,size",
    ).execute()

    return {
        "uploaded": True,
        "id": result.get("id"),
        "name": result.get("name"),
        "mimeType": result.get("mimeType"),
        "webViewLink": result.get("webViewLink"),
        "size": result.get("size"),
    }


async def create_folder(
    user_email: str, name: str, parent_id: str = "",
) -> dict:
    """Create a folder in the user's Google Drive."""
    service = await _get_service(user_email)

    metadata: dict = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    result = service.files().create(
        body=metadata,
        fields="id,name,webViewLink",
    ).execute()

    return {
        "created": True,
        "id": result.get("id"),
        "name": result.get("name"),
        "webViewLink": result.get("webViewLink"),
    }


async def copy_file(
    user_email: str, file_id: str, name: str = "", folder_id: str = "",
) -> dict:
    """Create a copy of a file in the user's Google Drive."""
    service = await _get_service(user_email)

    metadata: dict = {}
    if name:
        metadata["name"] = name
    if folder_id:
        metadata["parents"] = [folder_id]

    result = service.files().copy(
        fileId=file_id,
        body=metadata,
        fields="id,name,mimeType,webViewLink,size",
    ).execute()

    return {
        "copied": True,
        "id": result.get("id"),
        "name": result.get("name"),
        "mimeType": result.get("mimeType"),
        "webViewLink": result.get("webViewLink"),
        "size": result.get("size"),
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Google Drive tool for Loma agent")
    parser.add_argument("--auth-token", required=True, help="HMAC-signed user auth token")
    sub = parser.add_subparsers(dest="command", required=True)

    # list-files
    p_list = sub.add_parser("list-files", help="List recent Drive files")
    p_list.add_argument("--user-email", required=True)
    p_list.add_argument("--query", default="")
    p_list.add_argument("--limit", type=int, default=10)

    # read-file
    p_read = sub.add_parser("read-file", help="Read a Drive file's content")
    p_read.add_argument("--user-email", required=True)
    p_read.add_argument("--file-id", required=True)

    # search
    p_search = sub.add_parser("search", help="Search Drive files")
    p_search.add_argument("--user-email", required=True)
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=10)

    # upload-file
    p_upload = sub.add_parser("upload-file", help="Upload a file to Google Drive")
    p_upload.add_argument("--user-email", required=True)
    p_upload.add_argument("--file-path", required=True, help="Local path to file")
    p_upload.add_argument("--name", default="", help="File name in Drive (default: local filename)")
    p_upload.add_argument("--folder-id", default="", help="Target folder ID in Drive")
    p_upload.add_argument("--mime-type", default="", help="MIME type override")

    # create-folder
    p_folder = sub.add_parser("create-folder", help="Create a folder in Google Drive")
    p_folder.add_argument("--user-email", required=True)
    p_folder.add_argument("--name", required=True, help="Folder name")
    p_folder.add_argument("--parent-id", default="", help="Parent folder ID")

    # copy-file
    p_copy = sub.add_parser("copy-file", help="Copy a file in Google Drive")
    p_copy.add_argument("--user-email", required=True)
    p_copy.add_argument("--file-id", required=True, help="ID of the file to copy")
    p_copy.add_argument("--name", default="", help="Name for the copy (default: 'Copy of ...')")
    p_copy.add_argument("--folder-id", default="", help="Target folder ID for the copy")

    args = parser.parse_args()

    # Verify auth token matches the requested user
    from tools._auth_token import verify_user_auth_token
    if not verify_user_auth_token(args.auth_token, args.user_email):
        print(json.dumps({"error": "Authentication failed — user identity mismatch or expired token. "
                          "You can only access your own Google account."}))
        sys.exit(1)

    try:
        if args.command == "list-files":
            result = asyncio.run(list_files(args.user_email, args.query, args.limit))
        elif args.command == "read-file":
            result = asyncio.run(read_file(args.user_email, args.file_id))
        elif args.command == "search":
            result = asyncio.run(search_files(args.user_email, args.query, args.limit))
        elif args.command == "upload-file":
            result = asyncio.run(upload_file(
                args.user_email, args.file_path, args.name,
                args.folder_id, args.mime_type,
            ))
        elif args.command == "create-folder":
            result = asyncio.run(create_folder(
                args.user_email, args.name, args.parent_id,
            ))
        elif args.command == "copy-file":
            result = asyncio.run(copy_file(
                args.user_email, args.file_id, args.name, args.folder_id,
            ))
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Google Drive API error: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
