"""Diagram generation tool using Mermaid.js via the mermaid.ink API.

Generates visual diagrams (flowcharts, architecture diagrams, sequence diagrams)
as PNG/SVG images from Mermaid syntax, and optionally uploads them to Google Drive
or embeds them in Google Docs.

Commands:
  1. diagrams.py render --output PATH [--type png|svg] [--theme default|neutral|dark|forest]
                        [--width N] [--height N] [--scale N] [--bg-color HEX]
     Reads Mermaid code from stdin and renders it to a file.

  2. diagrams.py upload --file PATH --name NAME --user-email EMAIL --auth-token TOKEN
     Uploads a rendered diagram to Google Drive and makes it publicly readable.
     Returns the file ID and embed URL.

  3. diagrams.py embed --doc-id DOC_ID --image-id DRIVE_FILE_ID
                       --replace-start TEXT --replace-end TEXT
                       [--width PT] [--height PT]
                       --user-email EMAIL --auth-token TOKEN
     Embeds a Google Drive image into a Google Doc, replacing content between
     two marker strings.

Usage (called by the agent via Bash):
  cat diagram.mmd | python3 tools/diagrams.py render --output /tmp/diagram.png --type png
  python3 tools/diagrams.py upload --file /tmp/diagram.png --name "My Diagram.png" --user-email user@co.com --auth-token TOKEN
  python3 tools/diagrams.py embed --doc-id DOC_ID --image-id FILE_ID --replace-start "HEADING_A" --replace-end "HEADING_B" --user-email user@co.com --auth-token TOKEN
"""

import asyncio
import base64
import json
import os
import sys
import zlib
from typing import Any, Optional

# Ensure tools directory is on path for _google_auth / _auth_token imports
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import aiohttp

MERMAID_INK_BASE = "https://mermaid.ink"


# -- Auth helpers (mirrors google_drive.py pattern) ----------------------------

def _verify_auth(user_email: str, auth_token: str) -> bool:
    """Verify the auth token matches the user email."""
    try:
        from _auth_token import verify_user_auth_token
        return verify_user_auth_token(auth_token, user_email)
    except ImportError:
        try:
            from tools._auth_token import verify_user_auth_token
            return verify_user_auth_token(auth_token, user_email)
        except ImportError:
            return False


async def _get_google_creds(user_email: str):
    """Get Google OAuth credentials for the user."""
    try:
        from _google_auth import get_google_credentials
    except ImportError:
        from tools._google_auth import get_google_credentials
    return await get_google_credentials(user_email)


# -- Mermaid encoding ----------------------------------------------------------

def _encode_pako(diagram_code: str, theme: str = "default") -> str:
    """Encode diagram using pako compression (same as mermaid live editor)."""
    payload = json.dumps({
        "code": diagram_code,
        "mermaid": {"theme": theme},
    })
    compress = zlib.compressobj(9, zlib.DEFLATED, 15)
    compressed = compress.compress(payload.encode("utf-8")) + compress.flush()
    encoded = base64.urlsafe_b64encode(compressed).decode("ascii")
    return "pako:" + encoded


def _encode_base64(diagram_code: str) -> str:
    """Encode diagram using plain base64 (fallback for simple diagrams)."""
    return base64.urlsafe_b64encode(diagram_code.encode("utf-8")).decode("ascii")


# -- Render command ------------------------------------------------------------

async def render_diagram(
    diagram_code: str,
    output_path: str,
    img_type: str = "png",
    theme: str = "default",
    width: Optional[int] = None,
    height: Optional[int] = None,
    scale: Optional[float] = None,
    bg_color: Optional[str] = None,
) -> dict[str, Any]:
    """Render a Mermaid diagram to an image file via mermaid.ink."""

    if not diagram_code.strip():
        return {"error": "No diagram code provided. Pipe Mermaid code via stdin."}

    encoded = _encode_pako(diagram_code, theme)

    if img_type == "svg":
        endpoint = "svg"
    else:
        endpoint = "img"

    url = MERMAID_INK_BASE + "/" + endpoint + "/" + encoded

    params = {}
    if endpoint == "img":
        params["type"] = img_type
    if width:
        params["width"] = str(width)
    if height:
        params["height"] = str(height)
    if scale and (width or height):
        params["scale"] = str(scale)
    if bg_color:
        params["bgColor"] = bg_color

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 400:
                    text = await resp.text()
                    return {"error": "Mermaid syntax error: " + text[:500]}
                if resp.status == 431:
                    encoded_b64 = _encode_base64(diagram_code)
                    url_b64 = MERMAID_INK_BASE + "/" + endpoint + "/" + encoded_b64
                    async with session.get(
                        url_b64, params=params,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp2:
                        if resp2.status != 200:
                            text = await resp2.text()
                            return {"error": "mermaid.ink error (HTTP " + str(resp2.status) + "): " + text[:500]}
                        data = await resp2.read()
                elif resp.status == 503:
                    return {"error": "mermaid.ink rendering timed out. Try a simpler diagram."}
                elif resp.status != 200:
                    text = await resp.text()
                    return {"error": "mermaid.ink error (HTTP " + str(resp.status) + "): " + text[:500]}
                else:
                    data = await resp.read()

        with open(output_path, "wb") as f:
            f.write(data)

        return {
            "success": True,
            "output": output_path,
            "size_bytes": len(data),
            "type": img_type,
        }

    except aiohttp.ClientError as e:
        return {"error": "Failed to connect to mermaid.ink: " + str(e)}


# -- Upload command (Google Drive) ---------------------------------------------

async def upload_to_drive(
    file_path: str,
    name: str,
    user_email: str,
) -> dict[str, Any]:
    """Upload a file to Google Drive and make it publicly readable."""
    try:
        creds = await _get_google_creds(user_email)
    except Exception as e:
        return {"error": "Authentication failed: " + str(e)}

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        drive_service = build("drive", "v3", credentials=creds)

        ext = os.path.splitext(file_path)[1].lower()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        file_metadata = {"name": name}
        media = MediaFileUpload(file_path, mimetype=mime_type)
        uploaded = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id,name,webViewLink",
        ).execute()

        file_id = uploaded["id"]

        drive_service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()

        return {
            "success": True,
            "file_id": file_id,
            "name": uploaded.get("name"),
            "web_view_link": uploaded.get("webViewLink"),
            "embed_url": "https://lh3.googleusercontent.com/d/" + file_id,
        }

    except Exception as e:
        return {"error": "Drive upload failed: " + str(e)}


# -- Embed command (Google Docs) -----------------------------------------------

async def embed_in_doc(
    doc_id: str,
    image_id: str,
    replace_start: str,
    replace_end: str,
    width_pt: float = 500,
    height_pt: float = 250,
    user_email: str = "",
) -> dict[str, Any]:
    """Embed a Drive image into a Google Doc, replacing content between markers."""
    try:
        creds = await _get_google_creds(user_email)
    except Exception as e:
        return {"error": "Authentication failed: " + str(e)}

    try:
        from googleapiclient.discovery import build

        docs_service = build("docs", "v1", credentials=creds)

        doc = docs_service.documents().get(documentId=doc_id).execute()
        content = doc.get("body", {}).get("content", [])

        # Build a flat text representation + track char offsets
        full_text = ""
        for element in content:
            if "paragraph" in element:
                for pe in element["paragraph"].get("elements", []):
                    if "textRun" in pe:
                        full_text += pe["textRun"]["content"]
                    elif "inlineObjectElement" in pe:
                        full_text += "\ufffc"
            elif "table" in element:
                full_text += "[table]"

        start_idx = full_text.find(replace_start)
        if start_idx == -1:
            return {"error": "Start marker '" + replace_start + "' not found in document."}

        end_idx = full_text.find(replace_end, start_idx + len(replace_start))
        if end_idx == -1:
            return {"error": "End marker '" + replace_end + "' not found after start marker."}

        # Map text offsets to Google Docs document indices
        char_count = 0
        doc_start = None
        doc_end = None

        for element in content:
            if "paragraph" in element:
                for pe in element["paragraph"].get("elements", []):
                    el_start = pe.get("startIndex", 0)
                    if "textRun" in pe:
                        text_len = len(pe["textRun"]["content"])
                    elif "inlineObjectElement" in pe:
                        text_len = 1
                    else:
                        continue

                    if doc_start is None and char_count + text_len > start_idx:
                        offset_in = start_idx - char_count
                        doc_start = el_start + offset_in + len(replace_start)

                    if doc_end is None and char_count + text_len > end_idx:
                        offset_in = end_idx - char_count
                        doc_end = el_start + offset_in

                    char_count += text_len

        if doc_start is None or doc_end is None:
            return {"error": "Could not map marker positions to document indices."}

        if doc_end <= doc_start:
            return {"error": "No content found between markers to replace."}

        embed_url = "https://lh3.googleusercontent.com/d/" + image_id
        requests = [
            {
                "deleteContentRange": {
                    "range": {
                        "startIndex": doc_start,
                        "endIndex": doc_end,
                    }
                }
            },
            {
                "insertInlineImage": {
                    "uri": embed_url,
                    "location": {"index": doc_start},
                    "objectSize": {
                        "width": {"magnitude": width_pt, "unit": "PT"},
                        "height": {"magnitude": height_pt, "unit": "PT"},
                    },
                }
            },
        ]

        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()

        return {
            "success": True,
            "doc_id": doc_id,
            "image_embedded": True,
            "replaced_range": str(doc_start) + "-" + str(doc_end),
        }

    except Exception as e:
        return {"error": "Embed failed: " + str(e)}


# -- CLI entry point -----------------------------------------------------------

def _parse_flag(args, flag, default=""):
    """Extract a --flag value from args list."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    args = sys.argv[1:]
    if not args:
        print(json.dumps({
            "error": "Usage: python3 tools/diagrams.py <command> [flags]\nCommands: render, upload, embed"
        }))
        sys.exit(1)

    command = args[0]

    if command == "render":
        # Render does not need auth
        diagram_code = sys.stdin.read()
        output = _parse_flag(args, "--output", "/tmp/diagram.png")
        img_type = _parse_flag(args, "--type", "png")
        theme = _parse_flag(args, "--theme", "default")
        width_s = _parse_flag(args, "--width")
        height_s = _parse_flag(args, "--height")
        scale_s = _parse_flag(args, "--scale")
        bg_color = _parse_flag(args, "--bg-color")

        width = int(width_s) if width_s else None
        height = int(height_s) if height_s else None
        scale = float(scale_s) if scale_s else None

        result = asyncio.run(render_diagram(
            diagram_code=diagram_code,
            output_path=output,
            img_type=img_type,
            theme=theme,
            width=width,
            height=height,
            scale=scale,
            bg_color=bg_color or None,
        ))

    elif command == "upload":
        file_path = _parse_flag(args, "--file")
        name = _parse_flag(args, "--name", "diagram.png")
        user_email = _parse_flag(args, "--user-email")
        auth_token = _parse_flag(args, "--auth-token")

        if not file_path:
            result = {"error": "Missing required flag: --file"}
        elif not user_email or not auth_token:
            result = {"error": "Missing required flags: --user-email and --auth-token"}
        elif not _verify_auth(user_email, auth_token):
            result = {"error": "Authentication failed - user identity mismatch or expired token."}
        else:
            result = asyncio.run(upload_to_drive(file_path, name, user_email))

    elif command == "embed":
        doc_id = _parse_flag(args, "--doc-id")
        image_id = _parse_flag(args, "--image-id")
        replace_start = _parse_flag(args, "--replace-start")
        replace_end = _parse_flag(args, "--replace-end")
        width_pt = float(_parse_flag(args, "--width", "500"))
        height_pt = float(_parse_flag(args, "--height", "250"))
        user_email = _parse_flag(args, "--user-email")
        auth_token = _parse_flag(args, "--auth-token")

        if not all([doc_id, image_id, replace_start, replace_end]):
            result = {"error": "Missing required flags: --doc-id, --image-id, --replace-start, --replace-end"}
        elif not user_email or not auth_token:
            result = {"error": "Missing required flags: --user-email and --auth-token"}
        elif not _verify_auth(user_email, auth_token):
            result = {"error": "Authentication failed - user identity mismatch or expired token."}
        else:
            result = asyncio.run(embed_in_doc(
                doc_id, image_id, replace_start, replace_end,
                width_pt, height_pt, user_email,
            ))

    else:
        result = {"error": "Unknown command: " + command + ". Use: render, upload, embed"}

    print(json.dumps(result, indent=2))
