"""Google Apps Script API client for the Loma agent.

Provides CLI commands to list, read, create, and update Apps Script projects —
including scripts bound to a Google Sheet, Doc, or Form — using a user's
personal Google OAuth tokens.

Commands:
  1. google_apps_script.py list-projects --user-email EMAIL [--query Q] [--limit N]
  2. google_apps_script.py get-content --user-email EMAIL --script-id ID [--file-name NAME] [--output-dir DIR]
  3. google_apps_script.py create-project --user-email EMAIL --title T [--parent-id FILE_ID]
  4. google_apps_script.py update-content --user-email EMAIL --script-id ID (--files-json J | --files-json-file PATH | --code-file PATH [--file-name NAME])
  5. google_apps_script.py create-version --user-email EMAIL --script-id ID [--description D]
  6. google_apps_script.py list-versions --user-email EMAIL --script-id ID

  Pass --parent-id (a Sheet/Doc/Form file ID) to create-project to create a
  BOUND script — it appears under Extensions → Apps Script in that file and can
  use simple triggers like onOpen/onEdit.

  get-content truncates each file's source at MAX_FILE_SOURCE chars by default
  (truncated files are flagged with "truncated": true). Use --file-name to get
  one file in full, or --output-dir to write every file in full to disk and
  print only a summary. Never push truncated source back with update-content.

  The API cannot execute scripts or grant their authorization; return the
  editorUrl so the user can run/authorize a script once in the editor.

Requires:
  - User must have connected their Google account via the Integrations page
    (connections made before Apps Script support need a one-time reconnect)
  - Each Google account must have the Apps Script API enabled once at
    https://script.google.com/home/usersettings
  - OBSERVABILITY_MONGODB_URI, OAUTH_ENCRYPTION_KEY, GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET environment variables

Usage (called by the agent via Bash):
  python3 tools/google_apps_script.py list-projects --user-email adarsh@example.com
  python3 tools/google_apps_script.py create-project --user-email adarsh@example.com --title "Report automation" --parent-id 1AbC...
  python3 tools/google_apps_script.py update-content --user-email adarsh@example.com --script-id 1XyZ... --code-file /tmp/code.gs
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

# Maximum source returned per script file (keeps agent context manageable)
MAX_FILE_SOURCE = 50000

APPS_SCRIPT_MIME = "application/vnd.google-apps.script"

# Apps Script file types by filename extension
_EXTENSION_TYPES = {
    ".gs": "SERVER_JS",
    ".js": "SERVER_JS",
    ".html": "HTML",
    ".json": "JSON",
}


async def _get_service(user_email: str):
    """Build an authenticated Apps Script API service for the given user."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("script", "v1", credentials=creds)


async def _get_drive_service(user_email: str):
    """Build an authenticated Drive API service (used to list script projects)."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("drive", "v3", credentials=creds)


def _editor_url(script_id: str) -> str:
    return f"https://script.google.com/d/{script_id}/edit"


def _translate_http_error(e: Exception) -> ValueError | None:
    """Map known Apps Script HTTP errors to user-facing messages.

    Returns a ValueError to raise, or None when the error is not recognized.
    """
    text = str(e)
    if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in text or "insufficient authentication scopes" in text.lower():
        return ValueError(
            "Your Google connection predates Apps Script support. "
            "Please reconnect Google on the Integrations page in the Loma dashboard, then retry."
        )
    if "script.google.com/home/usersettings" in text or "User has not enabled the Apps Script API" in text:
        return ValueError(
            "The Apps Script API is not enabled for this Google account. "
            "Ask the user to enable it at https://script.google.com/home/usersettings "
            "(one-time toggle), wait a minute, then retry."
        )
    return None


def _file_type_for(name: str) -> str:
    """Infer the Apps Script file type from a file name/path."""
    _, ext = os.path.splitext(name)
    return _EXTENSION_TYPES.get(ext.lower(), "SERVER_JS")


# ── Commands ──────────────────────────────────────────────────────────────


async def list_projects(user_email: str, query: str = "", limit: int = 20) -> dict:
    """List the user's Apps Script projects via Drive search.

    The Apps Script API has no list endpoint; script projects are Drive files
    with the Apps Script MIME type (this includes bound scripts).
    """
    service = await _get_drive_service(user_email)

    q = f"mimeType='{APPS_SCRIPT_MIME}' and trashed=false"
    if query:
        escaped = query.replace("\\", "\\\\").replace("'", "\\'")
        q += f" and name contains '{escaped}'"

    result = service.files().list(
        q=q,
        pageSize=limit,
        orderBy="modifiedTime desc",
        fields="files(id,name,modifiedTime,parents,webViewLink)",
    ).execute()

    projects = [
        {
            "scriptId": f.get("id"),
            "name": f.get("name"),
            "modifiedTime": f.get("modifiedTime", ""),
            "editorUrl": _editor_url(f.get("id", "")),
        }
        for f in result.get("files", [])
    ]
    return {"projects": projects, "total": len(projects)}


# Local file extension used when writing each Apps Script file type to disk
_TYPE_EXTENSIONS = {
    "SERVER_JS": ".gs",
    "HTML": ".html",
    "JSON": ".json",
}

TRUNCATION_HINT = (
    "One or more files were truncated. Do NOT push truncated source back with "
    "update-content. Re-run get-content with --file-name NAME to get a single file "
    "in full, or --output-dir DIR to write every file in full to disk."
)


def _local_filename(name: str, file_type: str) -> str:
    """Map an Apps Script file to a local filename (Code → Code.gs, appsscript → appsscript.json)."""
    return name + _TYPE_EXTENSIONS.get(file_type or "", ".gs")


def _find_file(files: list[dict], file_name: str) -> dict:
    """Find a script file by name, accepting an optional local extension (e.g. "Code.gs")."""
    for f in files:
        if f.get("name") == file_name:
            return f
    base, ext = os.path.splitext(file_name)
    if ext.lower() in _EXTENSION_TYPES:
        for f in files:
            if f.get("name") == base:
                return f
    available = ", ".join(str(f.get("name")) for f in files) or "(none)"
    raise ValueError(f"File not found in script project: {file_name!r}. Available files: {available}")


def _write_files(files: list[dict], output_dir: str) -> list[dict]:
    """Write each file's full source into output_dir; return a per-file summary."""
    root = os.path.realpath(output_dir)
    os.makedirs(root, exist_ok=True)
    summary = []
    for f in files:
        name = f.get("name") or ""
        source = f.get("source", "")
        path = os.path.realpath(os.path.join(root, _local_filename(name, f.get("type", ""))))
        # Apps Script names may contain "/" (pseudo-folders) — keep them inside root.
        if os.path.commonpath([root, path]) != root:
            raise ValueError(f"Refusing to write file outside --output-dir: {name!r}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as out:
            out.write(source)
        summary.append({
            "name": name,
            "type": f.get("type"),
            "chars": len(source),
            "path": path,
        })
    return summary


async def get_content(
    user_email: str,
    script_id: str,
    output_dir: str = "",
    file_name: str = "",
) -> dict:
    """Read the source files of an Apps Script project.

    Modes:
      - default: every file's source inline, each truncated at MAX_FILE_SOURCE
        chars (truncated files carry "truncated": true and "originalChars", and
        the result carries a top-level "truncated" flag and "hint").
      - file_name: only that file, with its full (untruncated) source.
      - output_dir: write the selected file(s) in full to disk and return only a
        summary (name, type, chars, path) — no source in the output.
    """
    service = await _get_service(user_email)
    content = service.projects().getContent(scriptId=script_id).execute()

    raw_files: list[dict] = content.get("files", [])
    if file_name:
        raw_files = [_find_file(raw_files, file_name)]

    result: dict[str, Any] = {"scriptId": content.get("scriptId", script_id)}

    if output_dir:
        result["outputDir"] = os.path.realpath(output_dir)
        result["files"] = _write_files(raw_files, output_dir)
        result["editorUrl"] = _editor_url(script_id)
        return result

    files = []
    any_truncated = False
    for f in raw_files:
        source = f.get("source", "")
        entry: dict[str, Any] = {
            "name": f.get("name"),
            "type": f.get("type"),
            "totalLength": len(source),
        }
        if not file_name and len(source) > MAX_FILE_SOURCE:
            entry["source"] = source[:MAX_FILE_SOURCE] + "\n\n... [truncated — file too large]"
            entry["truncated"] = True
            entry["originalChars"] = len(source)
            any_truncated = True
        else:
            entry["source"] = source
        files.append(entry)

    result["files"] = files
    result["editorUrl"] = _editor_url(script_id)
    if any_truncated:
        result["truncated"] = True
        result["hint"] = TRUNCATION_HINT
    return result


async def create_project(user_email: str, title: str, parent_id: str = "") -> dict:
    """Create a new Apps Script project, optionally bound to a Sheet/Doc/Form.

    Pass parent_id (the Drive file ID of a Google Sheet, Doc, Form, or Slides
    file) to create a bound script that can use simple triggers like onOpen.
    """
    service = await _get_service(user_email)

    body: dict[str, Any] = {"title": title}
    if parent_id:
        body["parentId"] = parent_id

    project = service.projects().create(body=body).execute()
    script_id = project.get("scriptId", "")

    result = {
        "created": True,
        "scriptId": script_id,
        "title": project.get("title", title),
        "editorUrl": _editor_url(script_id),
    }
    if parent_id:
        result["parentId"] = parent_id
        result["note"] = (
            "Bound script created. It appears under Extensions → Apps Script in the parent file. "
            "If the script needs authorization (e.g. it calls Gmail/Drive services), the user must "
            "open the editorUrl and run it once to approve access — no API can do that step."
        )
    return result


async def update_content(
    user_email: str,
    script_id: str,
    files_json: str = "",
    code_file: str = "",
    file_name: str = "Code",
) -> dict:
    """Update an Apps Script project's source files.

    Two modes:
      - files_json: full JSON array [{"name": ..., "type": ..., "source": ...}]
        replacing the entire file set (the appsscript.json manifest is preserved
        automatically if omitted).
      - code_file: path to a single .gs/.html file; it replaces (or adds) the
        file named file_name while all other files are preserved.

    updateContent replaces the whole file set, so both modes fetch the existing
    content first and merge — omitting the manifest would break the project.
    """
    service = await _get_service(user_email)

    existing = service.projects().getContent(scriptId=script_id).execute()
    existing_files: list[dict] = existing.get("files", [])

    if files_json:
        try:
            new_files = json.loads(files_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"--files-json is not valid JSON: {e}")
        if not isinstance(new_files, list) or not all(isinstance(f, dict) for f in new_files):
            raise ValueError('--files-json must be a JSON array of {"name", "type", "source"} objects')
        for f in new_files:
            if not f.get("name") or "source" not in f:
                raise ValueError('Each file needs at least "name" and "source"')
            f.setdefault("type", _file_type_for(f["name"]))
        # Preserve the manifest unless the caller supplied one
        if not any(f.get("name") == "appsscript" for f in new_files):
            manifest = next((f for f in existing_files if f.get("name") == "appsscript"), None)
            if manifest:
                new_files.append({
                    "name": "appsscript",
                    "type": "JSON",
                    "source": manifest.get("source", ""),
                })
    elif code_file:
        if not os.path.isfile(code_file):
            raise ValueError(f"Code file not found: {code_file}")
        with open(code_file, encoding="utf-8") as f:
            source = f.read()
        # "Code.gs" → name "Code"; the file type comes from the extension
        name = os.path.splitext(file_name)[0] or "Code"
        file_type = _file_type_for(file_name if os.path.splitext(file_name)[1] else code_file)
        new_files = [f for f in existing_files if f.get("name") != name]
        new_files.append({"name": name, "type": file_type, "source": source})
    else:
        raise ValueError("Provide either --files-json/--files-json-file or --code-file")

    updated = service.projects().updateContent(
        scriptId=script_id, body={"files": new_files},
    ).execute()

    return {
        "updated": True,
        "scriptId": updated.get("scriptId", script_id),
        "files": [{"name": f.get("name"), "type": f.get("type")} for f in updated.get("files", [])],
        "editorUrl": _editor_url(script_id),
    }


async def create_version(user_email: str, script_id: str, description: str = "") -> dict:
    """Create an immutable version snapshot of an Apps Script project."""
    service = await _get_service(user_email)

    body = {}
    if description:
        body["description"] = description

    version = service.projects().versions().create(
        scriptId=script_id, body=body,
    ).execute()

    return {
        "created": True,
        "scriptId": script_id,
        "versionNumber": version.get("versionNumber"),
        "description": version.get("description", ""),
        "createTime": version.get("createTime", ""),
    }


async def list_versions(user_email: str, script_id: str, limit: int = 20) -> dict:
    """List the version snapshots of an Apps Script project."""
    service = await _get_service(user_email)

    result = service.projects().versions().list(
        scriptId=script_id, pageSize=limit,
    ).execute()

    versions = [
        {
            "versionNumber": v.get("versionNumber"),
            "description": v.get("description", ""),
            "createTime": v.get("createTime", ""),
        }
        for v in result.get("versions", [])
    ]
    return {"scriptId": script_id, "versions": versions, "total": len(versions)}


# ── CLI ───────────────────────────────────────────────────────────────────


def _resolve_files_json(args) -> str:
    """Resolve the files JSON from either --files-json-file or --files-json."""
    path = getattr(args, "files_json_file", "") or ""
    if path:
        if not os.path.isfile(path):
            raise ValueError(f"Files JSON file not found: {path}")
        with open(path, encoding="utf-8") as f:
            return f.read()
    return getattr(args, "files_json", "") or ""


def main():
    parser = argparse.ArgumentParser(description="Google Apps Script API tool for Loma agent")
    parser.add_argument("--auth-token", required=True, help="HMAC-signed user auth token")
    sub = parser.add_subparsers(dest="command", required=True)

    # list-projects
    p_list = sub.add_parser("list-projects", help="List Apps Script projects (via Drive search)")
    p_list.add_argument("--user-email", required=True)
    p_list.add_argument("--query", default="", help="Filter by project name")
    p_list.add_argument("--limit", type=int, default=20)

    # get-content
    p_get = sub.add_parser("get-content", help="Read source files of a script project (default output truncates large files)")
    p_get.add_argument("--user-email", required=True)
    p_get.add_argument("--script-id", required=True)
    p_get.add_argument("--file-name", default="", help="Return only this file (e.g. Code or Code.gs), with full untruncated source")
    p_get.add_argument("--output-dir", default="", help="Write files in full to this directory and print only a summary (no source)")

    # create-project
    p_create = sub.add_parser("create-project", help="Create a script project (optionally bound to a Sheet/Doc/Form)")
    p_create.add_argument("--user-email", required=True)
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--parent-id", default="", help="Drive file ID of a Sheet/Doc/Form to bind the script to")

    # update-content
    p_update = sub.add_parser("update-content", help="Update a script project's source files")
    p_update.add_argument("--user-email", required=True)
    p_update.add_argument("--script-id", required=True)
    p_update.add_argument("--files-json", default="", help='Full JSON array of files: [{"name","type","source"}]')
    p_update.add_argument("--files-json-file", default="", help="Path to a file containing the files JSON (avoids shell-escaping)")
    p_update.add_argument("--code-file", default="", help="Path to a single .gs/.html source file to add or replace")
    p_update.add_argument("--file-name", default="Code", help="Script file name for --code-file (default: Code)")

    # create-version
    p_ver = sub.add_parser("create-version", help="Create an immutable version snapshot")
    p_ver.add_argument("--user-email", required=True)
    p_ver.add_argument("--script-id", required=True)
    p_ver.add_argument("--description", default="")

    # list-versions
    p_vers = sub.add_parser("list-versions", help="List version snapshots")
    p_vers.add_argument("--user-email", required=True)
    p_vers.add_argument("--script-id", required=True)
    p_vers.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    # Verify auth token matches the requested user
    from tools._auth_token import verify_user_auth_token
    if not verify_user_auth_token(args.auth_token, args.user_email):
        print(json.dumps({"error": "Authentication failed — user identity mismatch or expired token. "
                          "You can only access your own Google account."}))
        sys.exit(1)

    try:
        if args.command == "list-projects":
            result = asyncio.run(list_projects(args.user_email, args.query, args.limit))
        elif args.command == "get-content":
            result = asyncio.run(get_content(
                args.user_email, args.script_id, args.output_dir, args.file_name,
            ))
        elif args.command == "create-project":
            result = asyncio.run(create_project(args.user_email, args.title, args.parent_id))
        elif args.command == "update-content":
            result = asyncio.run(update_content(
                args.user_email, args.script_id, _resolve_files_json(args),
                args.code_file, args.file_name,
            ))
        elif args.command == "create-version":
            result = asyncio.run(create_version(args.user_email, args.script_id, args.description))
        elif args.command == "list-versions":
            result = asyncio.run(list_versions(args.user_email, args.script_id, args.limit))
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    except Exception as e:
        translated = _translate_http_error(e)
        if translated is not None:
            print(json.dumps({"error": str(translated)}))
        else:
            print(json.dumps({"error": f"Apps Script API error: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
