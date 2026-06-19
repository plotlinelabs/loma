"""File serving routes for binary artifact previews (GO-91).

Serves files from allowed directories (e.g. /tmp) so the dashboard can
preview PDFs, DOCX, PPTX, and XLSX files that the agent generates.
"""
import base64
import logging
import mimetypes
import os
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

# Only serve files from these directories (security)
ALLOWED_PREFIXES = ["/tmp/"]


def encode_file_id(file_path: str) -> str:
    """Encode an absolute file path as a URL-safe base64 file ID."""
    return base64.urlsafe_b64encode(file_path.encode()).decode()


def _decode_file_id(file_id: str) -> str | None:
    """Decode a file ID back to an absolute path."""
    try:
        # Add padding if needed
        padded = file_id + "=" * (-len(file_id) % 4)
        path = base64.urlsafe_b64decode(padded.encode()).decode()
        # Security: only allow files from allowed prefixes
        if not any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            logger.warning("[FILE] Rejected path outside allowed prefix: %s", path)
            return None
        if ".." in path:
            logger.warning("[FILE] Rejected path with traversal: %s", path)
            return None
        return path
    except Exception:
        return None


async def serve_file(request: web.Request) -> web.StreamResponse:
    """Serve a file by its encoded file ID."""
    file_id = request.match_info["file_id"]
    file_path = _decode_file_id(file_id)

    if not file_path or not os.path.isfile(file_path):
        raise web.HTTPNotFound(text="File not found")

    content_type, _ = mimetypes.guess_type(file_path)
    if not content_type:
        content_type = "application/octet-stream"

    # For PDFs, serve inline; for others, suggest download
    ext = Path(file_path).suffix.lower()
    disposition = "inline" if ext == ".pdf" else "attachment"
    filename = Path(file_path).name

    file_size = os.path.getsize(file_path)

    response = web.StreamResponse()
    response.content_type = content_type
    response.headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response.headers["Content-Length"] = str(file_size)
    response.headers["Cache-Control"] = "private, max-age=3600"
    # Allow cross-origin for iframe embedding
    response.headers["Access-Control-Allow-Origin"] = "*"

    await response.prepare(request)

    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            await response.write(chunk)

    return response


def setup_file_routes(app: web.Application):
    """Register file serving routes."""
    app.router.add_get("/api/files/{file_id}", serve_file)
