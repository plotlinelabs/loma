"""Data Room Management — Create, Share & Track Data Rooms.

Provides CLI commands for the Loma agent:
  1.  dataroom.py create-room "Name"                — Create a new data room
  2.  dataroom.py list-rooms [--search "q"]          — List / search data rooms
  3.  dataroom.py get-room <id>                      — Get data room details
  4.  dataroom.py delete-room <id>                   — Delete a data room
  5.  dataroom.py update-room <id> [options]          — Update data room settings
  6.  dataroom.py list-docs [--search "q"]           — List team documents
  7.  dataroom.py add-doc <room_id> <doc_id> [--folder "/path"] — Add doc to room
  8.  dataroom.py list-room-docs <room_id>           — List docs in a data room
  9.  dataroom.py create-folder <room_id> "Name"     — Create folder in data room
 10.  dataroom.py create-doc "Name" --url "URL"      — Create a link-type document in library
 11.  dataroom.py create-link <target_id> --type dataroom|document — Create sharing link
 12.  dataroom.py update-link <link_id> [options]     — Update a sharing link
 13.  dataroom.py delete-link <link_id>              — Delete (soft) a sharing link
 14.  dataroom.py get-link <link_id>                 — Get link details + shareable URL
 15.  dataroom.py list-links <room_id>               — List all links for a data room
 16.  dataroom.py viewers <room_id>                  — View data room viewers
 17.  dataroom.py team-viewers [--search "email"]    — View team-wide viewers
 18.  dataroom.py get-branding <room_id>             — Get data room branding
 19.  dataroom.py set-branding <room_id> [options]   — Set data room branding

Requires environment variables:
  DATAROOM_API_TOKEN  — Bearer token (e.g. pmk_...)
  DATAROOM_BASE_URL   — Base URL of the data room platform
  DATAROOM_TEAM_ID    — Team identifier

Usage (called by the agent via Bash):
  python3 tools/dataroom.py create-room "Example Corp Deal Room"
  python3 tools/dataroom.py create-link room_abc123 --type dataroom
  python3 tools/dataroom.py viewers room_abc123
"""

import asyncio
import json
import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default watermark configuration (matches platform UI defaults)
# ---------------------------------------------------------------------------

DEFAULT_WATERMARK_CONFIG: dict[str, Any] = {
    "text": "Confidential {{email}} {{date}} {{time}} {{link}} {{ipAddress}}",
    "isTiled": False,
    "color": "#000000",
    "fontSize": 24,
    "rotation": 45,
    "opacity": 0.1,
    "position": "middle-center",
}

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _get_api_token() -> str:
    token = os.environ.get("DATAROOM_API_TOKEN", "")
    if not token:
        raise ValueError(
            "DATAROOM_API_TOKEN environment variable is not set. "
            "Please configure it before using data room tools."
        )
    return token


def _get_base_url() -> str:
    url = os.environ.get("DATAROOM_BASE_URL", "")
    if not url:
        raise ValueError(
            "DATAROOM_BASE_URL environment variable is not set. "
            "Please configure it before using data room tools."
        )
    return url.rstrip("/")


def _get_team_id() -> str:
    team_id = os.environ.get("DATAROOM_TEAM_ID", "")
    if not team_id:
        raise ValueError(
            "DATAROOM_TEAM_ID environment variable is not set. "
            "Please configure it before using data room tools."
        )
    return team_id


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_token()}",
        "Content-Type": "application/json",
    }


def _team_url(path: str = "") -> str:
    """Build a URL scoped to the team: {base}/api/teams/{teamId}{path}."""
    return f"{_get_base_url()}/api/teams/{_get_team_id()}{path}"


def _shareable_url(link_id: str) -> str:
    """Construct the shareable URL for a link.

    The shareable URL format is: {DATAROOM_BASE_URL}/view/{linkId}
    e.g. https://dataroom-loma.vercel.app/view/cmmdm04y10001jy04hhmml96p
    """
    return f"{_get_base_url()}/view/{link_id}"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _request(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make an HTTP request and return parsed JSON or an error dict."""
    try:
        async with aiohttp.ClientSession() as session:
            kwargs: dict[str, Any] = {
                "headers": _headers(),
                "timeout": aiohttp.ClientTimeout(total=30),
            }
            if json_body is not None:
                kwargs["json"] = json_body
            if params:
                kwargs["params"] = params

            async with session.request(method, url, **kwargs) as resp:
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "unknown")
                    return {"error": f"Rate limit reached. Retry after {retry_after} seconds."}
                if resp.status == 401:
                    return {"error": "API token is invalid or expired. Check DATAROOM_API_TOKEN."}
                if resp.status == 403:
                    return {"error": "Permission denied. You may not have access to this resource."}
                if resp.status == 404:
                    return {"error": "Resource not found. Check the ID and try again."}
                if resp.status == 204:
                    return {"success": True}
                if resp.status >= 400:
                    error_text = await resp.text()
                    return {"error": f"API error (HTTP {resp.status}): {error_text[:500]}"}

                # Some DELETE endpoints return empty body
                text = await resp.text()
                if not text:
                    return {"success": True}
                return await resp.json() if resp.content_type == "application/json" else json.loads(text)
    except aiohttp.ClientError as e:
        return {"error": f"Failed to connect to data room API: {e}"}


# ---------------------------------------------------------------------------
# Data Room CRUD
# ---------------------------------------------------------------------------


async def create_room(name: str) -> dict[str, Any]:
    """Create a new data room."""
    return await _request("POST", _team_url("/datarooms"), json_body={"name": name})


async def list_rooms(search: str | None = None) -> dict[str, Any]:
    """List data rooms, optionally filtered by search query."""
    params = {}
    if search:
        params["search"] = search
    return await _request("GET", _team_url("/datarooms"), params=params or None)


async def get_room(room_id: str) -> dict[str, Any]:
    """Get details for a specific data room."""
    return await _request("GET", _team_url(f"/datarooms/{room_id}"))


async def delete_room(room_id: str) -> dict[str, Any]:
    """Delete a data room (admin/manager only)."""
    return await _request("DELETE", _team_url(f"/datarooms/{room_id}"))


async def update_room(
    room_id: str,
    *,
    name: str | None = None,
    internal_name: str | None = None,
    set_internal_name: bool = False,
    allow_bulk_download: bool | None = None,
    show_last_updated: bool | None = None,
    enable_change_notifications: bool | None = None,
    default_permission_strategy: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update data room properties (PATCH — partial update, send only changed fields).

    Supported fields:
      - name: string
      - internalName: string | null (set null to clear)
      - allowBulkDownload: boolean
      - showLastUpdated: boolean
      - enableChangeNotifications: boolean (requires datarooms-plus/premium plan)
      - defaultPermissionStrategy: "INHERIT_FROM_PARENT" | "ASK_EVERY_TIME" | "HIDDEN_BY_DEFAULT"
      - tags: string[] of tag IDs (replaces all existing tags)
    """
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if set_internal_name:
        body["internalName"] = internal_name  # Can be None to clear
    if allow_bulk_download is not None:
        body["allowBulkDownload"] = allow_bulk_download
    if show_last_updated is not None:
        body["showLastUpdated"] = show_last_updated
    if enable_change_notifications is not None:
        body["enableChangeNotifications"] = enable_change_notifications
    if default_permission_strategy is not None:
        body["defaultPermissionStrategy"] = default_permission_strategy
    if tags is not None:
        body["tags"] = tags
    if not body:
        return {"error": "No update fields provided. Use --name, --internal-name, --bulk-download, --show-last-updated, --notifications, --permission-strategy, or --tags."}
    return await _request("PATCH", _team_url(f"/datarooms/{room_id}"), json_body=body)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def list_docs(search: str | None = None) -> dict[str, Any]:
    """List all documents in the team library."""
    params = {}
    if search:
        params["search"] = search
    return await _request("GET", _team_url("/documents"), params=params or None)


async def add_doc(room_id: str, document_id: str, folder: str | None = None) -> dict[str, Any]:
    """Add a document to a data room, optionally in a specific folder."""
    body: dict[str, Any] = {"documentId": document_id}
    if folder:
        body["folderPathName"] = folder
    return await _request("POST", _team_url(f"/datarooms/{room_id}/documents"), json_body=body)


async def list_room_docs(room_id: str) -> dict[str, Any]:
    """List all documents inside a data room."""
    return await _request("GET", _team_url(f"/datarooms/{room_id}/documents"))


async def create_doc(name: str, url: str) -> dict[str, Any]:
    """Create a link-type document in the team library.

    Used for adding external links (Google Sheets, Gamma decks, etc.)
    to the document library so they can be added to data rooms.
    """
    body: dict[str, Any] = {
        "name": name,
        "url": url,
        "type": "link",
    }
    return await _request("POST", _team_url("/documents"), json_body=body)


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


async def create_folder(room_id: str, name: str, parent_path: str | None = None) -> dict[str, Any]:
    """Create a folder inside a data room.

    API expects:
      - name (required): folder name string
      - path (optional): parent folder path without leading/trailing slashes
        e.g. "parent-folder/sub" to nest inside an existing folder

    If path is omitted, folder is created at the data room root.
    """
    body: dict[str, Any] = {"name": name}
    if parent_path:
        # Strip leading/trailing slashes — API uses slugified composite key
        body["path"] = parent_path.strip("/")
    return await _request("POST", _team_url(f"/datarooms/{room_id}/folders"), json_body=body)


# ---------------------------------------------------------------------------
# Sharing Links
# ---------------------------------------------------------------------------


async def create_link(
    target_id: str,
    link_type: str,
    *,
    # Security defaults — all enabled by default
    email_protected: bool = True,
    email_authenticated: bool = True,
    enable_watermark: bool = True,
    enable_screenshot_protection: bool = True,
    enable_notification: bool = True,
    # Expiry — 14 days default (matches platform UI default)
    expiry_days: int = 14,
    # Optional overrides
    password: str | None = None,
    allow_download: bool = False,
    name: str | None = None,
    # Access control
    allow_list: list[str] | None = None,
    deny_list: list[str] | None = None,
    # Watermark customization (if None, uses DEFAULT_WATERMARK_CONFIG when watermark enabled)
    watermark_config: dict[str, Any] | None = None,
    # Agreement/NDA
    enable_agreement: bool = False,
    agreement_id: str | None = None,
    # Display
    show_banner: bool | None = None,
    domain: str | None = None,
    slug: str | None = None,
    # Feedback
    enable_feedback: bool = False,
    # Audience
    audience_type: str = "GENERAL",
    group_id: str | None = None,
) -> dict[str, Any]:
    """Create a sharing link with secure defaults.

    Defaults enforced (matching platform UI defaults):
      - teamId = DATAROOM_TEAM_ID (required by API)
      - emailProtected = True (viewers must provide email to view)
      - emailAuthenticated = True (viewers must verify email via auth)
      - enableWatermark = True (with default watermark config)
      - enableScreenshotProtection = True (blur on tab-switch + hotkey blocking)
      - enableNotification = True (notify team on new views)
      - expiresAt = now + 14 days
      - allowDownload = False
      - showBanner = True for DOCUMENT_LINK, False for DATAROOM_LINK
    """
    type_map = {
        "dataroom": "DATAROOM_LINK",
        "document": "DOCUMENT_LINK",
    }
    mapped_type = type_map.get(link_type.lower())
    if not mapped_type:
        return {"error": f"Invalid link type '{link_type}'. Use 'dataroom' or 'document'."}

    expires_at = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Resolve showBanner default: True for document links, False for dataroom links
    resolved_show_banner = show_banner
    if resolved_show_banner is None:
        resolved_show_banner = mapped_type == "DOCUMENT_LINK"

    # Build the full request body matching the API schema
    body: dict[str, Any] = {
        "targetId": target_id,
        "linkType": mapped_type,
        "teamId": _get_team_id(),  # REQUIRED by the API
        # Access control
        "emailProtected": email_protected,
        "emailAuthenticated": email_authenticated,
        # Security
        "enableWatermark": enable_watermark,
        "enableScreenshotProtection": enable_screenshot_protection,
        # Notifications & feedback
        "enableNotification": enable_notification,
        "enableFeedback": enable_feedback,
        # Expiry & downloads
        "expiresAt": expires_at,
        "allowDownload": allow_download,
        # Display
        "showBanner": resolved_show_banner,
        # Audience
        "audienceType": audience_type,
        # Defaults for optional fields
        "enableCustomMetatag": False,
        "metaTitle": None,
        "metaDescription": None,
        "metaImage": None,
        "metaFavicon": None,
        "welcomeMessage": None,
        "enableQuestion": False,
        "questionText": None,
        "questionType": None,
        "enableConversation": False,
        "enableAIAgents": False,
        "enableUpload": False,
        "isFileRequestOnly": False,
        "uploadFolderId": None,
        "enableIndexFile": False,
        "customFields": [],
        "tags": [],
        "allowList": allow_list or [],
        "denyList": deny_list or [],
    }

    # Watermark config — always include the config object
    body["watermarkConfig"] = watermark_config or DEFAULT_WATERMARK_CONFIG

    # Optional fields
    if password:
        body["password"] = password
    if name:
        body["name"] = name
    if domain:
        body["domain"] = domain
    if slug:
        body["slug"] = slug

    # Agreement
    body["enableAgreement"] = enable_agreement
    if enable_agreement and agreement_id:
        body["agreementId"] = agreement_id
    else:
        body["agreementId"] = None

    # Group
    if group_id:
        body["groupId"] = group_id
        body["audienceType"] = "GROUP"
    else:
        body["groupId"] = None

    result = await _request("POST", f"{_get_base_url()}/api/links", json_body=body)

    if "error" not in result:
        # Add the shareable URL so the agent can immediately share it
        link_id = result.get("id", "")
        if link_id:
            result["shareableUrl"] = _shareable_url(link_id)
        result["_security_defaults_applied"] = {
            "emailProtected": email_protected,
            "emailAuthenticated": email_authenticated,
            "enableWatermark": enable_watermark,
            "enableScreenshotProtection": enable_screenshot_protection,
            "enableNotification": enable_notification,
            "expiresAt": expires_at,
            "expiry_days": expiry_days,
            "allowDownload": allow_download,
            "showBanner": resolved_show_banner,
        }

    return result


async def update_link(
    link_id: str,
    *,
    # All fields for full-replace PUT semantics
    target_id: str | None = None,
    link_type: str | None = None,
    expiry_days: int | None = None,
    password: str | None = None,
    enable_watermark: bool | None = None,
    enable_screenshot_protection: bool | None = None,
    email_protected: bool | None = None,
    email_authenticated: bool | None = None,
    enable_notification: bool | None = None,
    allow_download: bool | None = None,
    allow_list: list[str] | None = None,
    deny_list: list[str] | None = None,
    watermark_config: dict[str, Any] | None = None,
    enable_agreement: bool | None = None,
    agreement_id: str | None = None,
    show_banner: bool | None = None,
    enable_feedback: bool | None = None,
    name: str | None = None,
    domain: str | None = None,
    slug: str | None = None,
    audience_type: str | None = None,
    group_id: str | None = None,
) -> dict[str, Any]:
    """Update an existing sharing link.

    IMPORTANT: The API uses PUT (full replace), NOT PATCH. When updating,
    we first GET the existing link to preserve current values, then merge
    the user's changes on top before sending the full object.

    The GET response wraps link fields inside a "link" key:
      { "linkType": "...", "link": { "id": "...", "dataroom": { "id": "ROOM_ID" }, ... } }
    We must unwrap this to read current values correctly.
    """
    # Step 1: Fetch the existing link to get all current values
    raw = await _request("GET", f"{_get_base_url()}/api/links/{link_id}")
    if "error" in raw:
        return raw

    # Step 2: Unwrap the "link" key — the GET response nests all link
    # fields inside raw["link"], with the dataroom/document info nested further.
    existing = raw.get("link", raw)

    # Step 3: Resolve targetId — the critical fix.
    # The GET response does NOT include a top-level "targetId". The room ID
    # is at existing["dataroom"]["id"] (for DATAROOM_LINK) or
    # existing["document"]["id"] (for DOCUMENT_LINK).
    resolved_target_id = target_id
    if not resolved_target_id:
        dr = existing.get("dataroom")
        if isinstance(dr, dict):
            resolved_target_id = dr.get("id")
        if not resolved_target_id:
            doc = existing.get("document")
            if isinstance(doc, dict):
                resolved_target_id = doc.get("id")
    if not resolved_target_id:
        return {"error": "Could not resolve targetId from existing link. The link may be corrupted. Pass --target-id explicitly or recreate the link."}

    # Step 4: Build the full body from existing values, overriding with provided changes
    body: dict[str, Any] = {
        "targetId": resolved_target_id,
        "linkType": link_type or existing.get("linkType") or raw.get("linkType", "DATAROOM_LINK"),
        "teamId": existing.get("teamId") or _get_team_id(),
        "password": password if password is not None else existing.get("password"),
        "name": name if name is not None else existing.get("name"),
        "emailProtected": email_protected if email_protected is not None else existing.get("emailProtected", True),
        "emailAuthenticated": email_authenticated if email_authenticated is not None else existing.get("emailAuthenticated", True),
        "allowDownload": allow_download if allow_download is not None else existing.get("allowDownload", False),
        "enableNotification": enable_notification if enable_notification is not None else existing.get("enableNotification", True),
        "enableFeedback": enable_feedback if enable_feedback is not None else existing.get("enableFeedback", False),
        "enableScreenshotProtection": enable_screenshot_protection if enable_screenshot_protection is not None else existing.get("enableScreenshotProtection", True),
        "enableWatermark": enable_watermark if enable_watermark is not None else existing.get("enableWatermark", True),
        "showBanner": show_banner if show_banner is not None else existing.get("showBanner", False),
        "enableAgreement": enable_agreement if enable_agreement is not None else existing.get("enableAgreement", False),
        "agreementId": agreement_id if agreement_id is not None else existing.get("agreementId"),
        "enableCustomMetatag": existing.get("enableCustomMetatag", False),
        "metaTitle": existing.get("metaTitle"),
        "metaDescription": existing.get("metaDescription"),
        "metaImage": existing.get("metaImage"),
        "metaFavicon": existing.get("metaFavicon"),
        "welcomeMessage": existing.get("welcomeMessage"),
        "enableQuestion": existing.get("enableQuestion", False),
        "questionText": existing.get("questionText"),
        "questionType": existing.get("questionType"),
        "audienceType": audience_type or existing.get("audienceType", "GENERAL"),
        "groupId": group_id if group_id is not None else existing.get("groupId"),
        "enableConversation": existing.get("enableConversation", False),
        "enableAIAgents": existing.get("enableAIAgents", False),
        "enableUpload": existing.get("enableUpload", False),
        "isFileRequestOnly": existing.get("isFileRequestOnly", False),
        "uploadFolderId": existing.get("uploadFolderId"),
        "enableIndexFile": existing.get("enableIndexFile", False),
        "allowList": allow_list if allow_list is not None else existing.get("allowList", []),
        "denyList": deny_list if deny_list is not None else existing.get("denyList", []),
        "domain": domain if domain is not None else existing.get("domainSlug"),
        "slug": slug if slug is not None else existing.get("slug"),
        "customFields": existing.get("customFields", []),
        "tags": [t["id"] if isinstance(t, dict) else t for t in existing.get("tags", [])],
    }

    # Handle expiry
    if expiry_days is not None:
        body["expiresAt"] = (
            datetime.now(timezone.utc) + timedelta(days=expiry_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        body["expiresAt"] = existing.get("expiresAt")

    # Handle watermark config
    resolved_watermark = watermark_config if watermark_config is not None else existing.get("watermarkConfig", DEFAULT_WATERMARK_CONFIG)
    body["watermarkConfig"] = resolved_watermark

    return await _request("PUT", f"{_get_base_url()}/api/links/{link_id}", json_body=body)


async def delete_link(link_id: str) -> dict[str, Any]:
    """Soft-delete a sharing link."""
    return await _request("DELETE", f"{_get_base_url()}/api/links/{link_id}")


async def get_link(link_id: str) -> dict[str, Any]:
    """Get details for a specific sharing link, including its shareable URL.

    Returns the full link object from the API with an added
    'shareableUrl' field containing the URL to share with recipients.
    """
    result = await _request("GET", f"{_get_base_url()}/api/links/{link_id}")
    if "error" not in result:
        result["shareableUrl"] = _shareable_url(link_id)
    return result


async def list_links(room_id: str) -> dict[str, Any]:
    """List all sharing links for a data room.

    Note: The Papermark API does not expose a list-links-by-room endpoint
    with the current API token scope. This function attempts the call but
    may return a 401 error. If that happens, use get-link with specific
    link IDs instead (link IDs are returned when creating links).
    """
    result = await _request("GET", _team_url(f"/datarooms/{room_id}/links"))
    if "error" not in result:
        # result may be a list or a dict with a list inside
        links = result if isinstance(result, list) else result.get("links", result.get("data", []))
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict) and "id" in link:
                    link["shareableUrl"] = _shareable_url(link["id"])
    return result


# ---------------------------------------------------------------------------
# Viewers / Analytics
# ---------------------------------------------------------------------------


async def viewers(room_id: str) -> dict[str, Any]:
    """Get viewers for a specific data room."""
    return await _request("GET", _team_url(f"/datarooms/{room_id}/viewers"))


async def team_viewers(search: str | None = None) -> dict[str, Any]:
    """Get all viewers across the team, optionally filtered by email."""
    params = {}
    if search:
        params["search"] = search
    return await _request("GET", _team_url("/viewers"), params=params or None)


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------


async def get_branding(room_id: str) -> dict[str, Any]:
    """Get branding settings for a data room."""
    return await _request("GET", _team_url(f"/datarooms/{room_id}/branding"))


async def set_branding(
    room_id: str,
    *,
    logo: str | None = None,
    banner: str | None = None,
    brand_color: str | None = None,
    accent_color: str | None = None,
    welcome_message: str | None = None,
) -> dict[str, Any]:
    """Set branding for a data room (logo, banner, colors, welcome message)."""
    body: dict[str, Any] = {}
    if logo is not None:
        body["logo"] = logo
    if banner is not None:
        body["banner"] = banner
    if brand_color is not None:
        body["brandColor"] = brand_color
    if accent_color is not None:
        body["accentColor"] = accent_color
    if welcome_message is not None:
        body["welcomeMessage"] = welcome_message
    if not body:
        return {"error": "No branding fields provided. Use --logo, --banner, --brand-color, --accent-color, or --welcome-message."}
    return await _request("POST", _team_url(f"/datarooms/{room_id}/branding"), json_body=body)


# ---------------------------------------------------------------------------
# CLI argument helpers (following apollo.py pattern)
# ---------------------------------------------------------------------------


def _parse_single(args: list[str], flag: str, default: str | None = None) -> str | None:
    """Extract a single value for a flag."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return default


def _parse_list(args: list[str], flag: str) -> list[str] | None:
    """Extract a comma-separated list value for a flag."""
    val = _parse_single(args, flag)
    if val:
        return [v.strip() for v in val.split(",") if v.strip()]
    return None


def _has_flag(args: list[str], flag: str) -> bool:
    return flag in args


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in ("true", "1", "yes")


def _parse_json(value: str | None) -> dict[str, Any] | None:
    """Parse a JSON string into a dict."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _print_usage():
    print("Usage:")
    print("  python3 tools/dataroom.py create-room \"Room Name\"")
    print("    Create a new data room")
    print()
    print("  python3 tools/dataroom.py list-rooms [--search \"query\"]")
    print("    List data rooms, optionally filtered")
    print()
    print("  python3 tools/dataroom.py get-room <room_id>")
    print("    Get data room details")
    print()
    print("  python3 tools/dataroom.py delete-room <room_id>")
    print("    Delete a data room")
    print()
    print("  python3 tools/dataroom.py update-room <room_id> [options]")
    print("    Update data room settings")
    print("    Options:")
    print("      --name \"Name\"                   Room display name")
    print("      --internal-name \"Label\"          Internal-only label (use \"null\" to clear)")
    print("      --bulk-download true|false       Allow bulk downloads")
    print("      --show-last-updated true|false   Show last-updated timestamps")
    print("      --notifications true|false       Enable change notifications (premium)")
    print("      --permission-strategy INHERIT_FROM_PARENT|ASK_EVERY_TIME|HIDDEN_BY_DEFAULT")
    print("      --tags \"tag-id-1,tag-id-2\"      Replace tags (comma-separated IDs)")
    print()
    print("  python3 tools/dataroom.py list-docs [--search \"query\"]")
    print("    List documents in the team library")
    print()
    print("  python3 tools/dataroom.py add-doc <room_id> <document_id> [--folder \"/path\"]")
    print("    Add a document to a data room")
    print()
    print("  python3 tools/dataroom.py list-room-docs <room_id>")
    print("    List documents in a data room")
    print()
    print("  python3 tools/dataroom.py create-doc \"Doc Name\" --url \"https://example.com\"")
    print("    Create a link-type document in the team library (for Google Sheets, Gamma decks, etc.)")
    print()
    print("  python3 tools/dataroom.py create-folder <room_id> \"Folder Name\" [--path \"parent/path\"]")
    print("    Create a folder in a data room")
    print("    --path is the PARENT folder path (without leading/trailing slashes)")
    print("    Omit --path to create at root level")
    print()
    print("  python3 tools/dataroom.py create-link <target_id> --type dataroom|document [options]")
    print("    Create a sharing link with secure defaults")
    print("    Security defaults: email verification + watermark + screenshot protection + notifications + 14-day expiry")
    print("    Options:")
    print("      --password \"pass\"             Password-protect the link")
    print("      --allow-download true          Allow document downloads")
    print("      --name \"Link Name\"            Display name for the link")
    print("      --expiry-days N                Set expiry (default: 14)")
    print("      --no-email-protection          Disable email requirement")
    print("      --no-email-auth                Disable email verification/auth")
    print("      --no-watermark                 Disable watermark")
    print("      --no-screenshot-protection     Disable screenshot protection")
    print("      --no-notification              Disable view notifications")
    print("      --allow-list \"a@x.com,b@y.com\" Restrict to these emails/domains")
    print("      --deny-list \"c@z.com\"          Block these emails/domains")
    print("      --watermark-config '{...}'     Custom watermark JSON settings")
    print("      --enable-agreement             Require agreement/NDA acceptance")
    print("      --agreement-id <id>            Agreement ID (with --enable-agreement)")
    print("      --show-banner true|false       Show powered-by banner (default: true for doc, false for dataroom)")
    print("      --domain \"custom-domain\"      Custom domain slug")
    print("      --slug \"custom-slug\"          Custom URL slug")
    print("      --enable-feedback              Enable viewer feedback")
    print()
    print("  python3 tools/dataroom.py update-link <link_id> [options]")
    print("    Update a sharing link (fetches existing link first, merges changes)")
    print("    Options: --expiry-days, --password, --watermark true|false,")
    print("      --screenshot-protection true|false, --email-protection true|false,")
    print("      --email-auth true|false, --notification true|false,")
    print("      --allow-download true|false, --allow-list, --deny-list,")
    print("      --watermark-config, --enable-agreement true|false, --agreement-id,")
    print("      --show-banner true|false, --enable-feedback true|false, --name")
    print()
    print("  python3 tools/dataroom.py delete-link <link_id>")
    print("    Delete (soft) a sharing link")
    print()
    print("  python3 tools/dataroom.py get-link <link_id>")
    print("    Get link details including shareable URL")
    print()
    print("  python3 tools/dataroom.py list-links <room_id>")
    print("    List all sharing links for a data room (with shareable URLs)")
    print()
    print("  python3 tools/dataroom.py viewers <room_id>")
    print("    View data room viewers and activity")
    print()
    print("  python3 tools/dataroom.py team-viewers [--search \"email\"]")
    print("    View team-wide viewers, optionally filtered by email")
    print()
    print("  python3 tools/dataroom.py get-branding <room_id>")
    print("    Get data room branding settings")
    print()
    print("  python3 tools/dataroom.py set-branding <room_id> [--logo \"url\"] [--banner \"url\"]")
    print("    [--brand-color \"#hex\"] [--accent-color \"#hex\"] [--welcome-message \"text\"]")
    print("    Set data room branding")
    sys.exit(1)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        _print_usage()

    command = sys.argv[1]
    rest = sys.argv[2:]

    if command == "create-room":
        if not rest:
            print("Error: create-room requires a room name")
            sys.exit(1)
        name = rest[0]
        result = asyncio.run(create_room(name))
        print(json.dumps(result, indent=2))

    elif command == "list-rooms":
        search = _parse_single(rest, "--search")
        result = asyncio.run(list_rooms(search=search))
        print(json.dumps(result, indent=2))

    elif command == "get-room":
        if not rest:
            print("Error: get-room requires a room_id")
            sys.exit(1)
        result = asyncio.run(get_room(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "delete-room":
        if not rest:
            print("Error: delete-room requires a room_id")
            sys.exit(1)
        result = asyncio.run(delete_room(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "update-room":
        if not rest:
            print("Error: update-room requires a room_id")
            sys.exit(1)
        room_id = rest[0]
        internal_name_val = _parse_single(rest, "--internal-name")
        set_internal = _has_flag(rest, "--internal-name")
        if internal_name_val == "null":
            internal_name_val = None  # Explicit null to clear
        result = asyncio.run(update_room(
            room_id,
            name=_parse_single(rest, "--name"),
            internal_name=internal_name_val,
            set_internal_name=set_internal,
            allow_bulk_download=_parse_bool(_parse_single(rest, "--bulk-download")),
            show_last_updated=_parse_bool(_parse_single(rest, "--show-last-updated")),
            enable_change_notifications=_parse_bool(_parse_single(rest, "--notifications")),
            default_permission_strategy=_parse_single(rest, "--permission-strategy"),
            tags=_parse_list(rest, "--tags"),
        ))
        print(json.dumps(result, indent=2))

    elif command == "list-docs":
        search = _parse_single(rest, "--search")
        result = asyncio.run(list_docs(search=search))
        print(json.dumps(result, indent=2))

    elif command == "add-doc":
        if len(rest) < 2:
            print("Error: add-doc requires <room_id> <document_id>")
            sys.exit(1)
        room_id = rest[0]
        doc_id = rest[1]
        folder = _parse_single(rest, "--folder")
        result = asyncio.run(add_doc(room_id, doc_id, folder=folder))
        print(json.dumps(result, indent=2))

    elif command == "list-room-docs":
        if not rest:
            print("Error: list-room-docs requires a room_id")
            sys.exit(1)
        result = asyncio.run(list_room_docs(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "create-doc":
        if not rest:
            print("Error: create-doc requires a document name")
            sys.exit(1)
        doc_name = rest[0]
        doc_url = _parse_single(rest, "--url")
        if not doc_url:
            print('Error: create-doc requires --url <URL>')
            sys.exit(1)
        result = asyncio.run(create_doc(doc_name, doc_url))
        print(json.dumps(result, indent=2))

    elif command == "create-folder":
        if len(rest) < 2:
            print("Error: create-folder requires <room_id> \"Folder Name\"")
            sys.exit(1)
        room_id = rest[0]
        folder_name = rest[1]
        parent_path = _parse_single(rest, "--path")
        result = asyncio.run(create_folder(room_id, folder_name, parent_path=parent_path))
        print(json.dumps(result, indent=2))

    elif command == "create-link":
        if not rest:
            print("Error: create-link requires a target_id")
            sys.exit(1)
        target_id = rest[0]
        link_type = _parse_single(rest, "--type")
        if not link_type:
            print("Error: create-link requires --type dataroom|document")
            sys.exit(1)
        result = asyncio.run(create_link(
            target_id,
            link_type,
            password=_parse_single(rest, "--password"),
            allow_download=_parse_bool(_parse_single(rest, "--allow-download")) or False,
            name=_parse_single(rest, "--name"),
            expiry_days=int(_parse_single(rest, "--expiry-days", "14")),
            email_protected=not _has_flag(rest, "--no-email-protection"),
            email_authenticated=not _has_flag(rest, "--no-email-auth"),
            enable_watermark=not _has_flag(rest, "--no-watermark"),
            enable_screenshot_protection=not _has_flag(rest, "--no-screenshot-protection"),
            enable_notification=not _has_flag(rest, "--no-notification"),
            allow_list=_parse_list(rest, "--allow-list"),
            deny_list=_parse_list(rest, "--deny-list"),
            watermark_config=_parse_json(_parse_single(rest, "--watermark-config")),
            enable_agreement=_has_flag(rest, "--enable-agreement"),
            agreement_id=_parse_single(rest, "--agreement-id"),
            show_banner=_parse_bool(_parse_single(rest, "--show-banner")),
            domain=_parse_single(rest, "--domain"),
            slug=_parse_single(rest, "--slug"),
            enable_feedback=_has_flag(rest, "--enable-feedback"),
        ))
        print(json.dumps(result, indent=2))

    elif command == "update-link":
        if not rest:
            print("Error: update-link requires a link_id")
            sys.exit(1)
        link_id = rest[0]
        expiry_str = _parse_single(rest, "--expiry-days")
        result = asyncio.run(update_link(
            link_id,
            expiry_days=int(expiry_str) if expiry_str else None,
            password=_parse_single(rest, "--password"),
            enable_watermark=_parse_bool(_parse_single(rest, "--watermark")),
            enable_screenshot_protection=_parse_bool(_parse_single(rest, "--screenshot-protection")),
            email_protected=_parse_bool(_parse_single(rest, "--email-protection")),
            email_authenticated=_parse_bool(_parse_single(rest, "--email-auth")),
            enable_notification=_parse_bool(_parse_single(rest, "--notification")),
            allow_download=_parse_bool(_parse_single(rest, "--allow-download")),
            allow_list=_parse_list(rest, "--allow-list"),
            deny_list=_parse_list(rest, "--deny-list"),
            watermark_config=_parse_json(_parse_single(rest, "--watermark-config")),
            enable_agreement=_parse_bool(_parse_single(rest, "--enable-agreement")),
            agreement_id=_parse_single(rest, "--agreement-id"),
            show_banner=_parse_bool(_parse_single(rest, "--show-banner")),
            enable_feedback=_parse_bool(_parse_single(rest, "--enable-feedback")),
            name=_parse_single(rest, "--name"),
        ))
        print(json.dumps(result, indent=2))

    elif command == "delete-link":
        if not rest:
            print("Error: delete-link requires a link_id")
            sys.exit(1)
        result = asyncio.run(delete_link(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "get-link":
        if not rest:
            print("Error: get-link requires a link_id")
            sys.exit(1)
        result = asyncio.run(get_link(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "list-links":
        if not rest:
            print("Error: list-links requires a room_id")
            sys.exit(1)
        result = asyncio.run(list_links(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "viewers":
        if not rest:
            print("Error: viewers requires a room_id")
            sys.exit(1)
        result = asyncio.run(viewers(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "team-viewers":
        search = _parse_single(rest, "--search")
        result = asyncio.run(team_viewers(search=search))
        print(json.dumps(result, indent=2))

    elif command == "get-branding":
        if not rest:
            print("Error: get-branding requires a room_id")
            sys.exit(1)
        result = asyncio.run(get_branding(rest[0]))
        print(json.dumps(result, indent=2))

    elif command == "set-branding":
        if not rest:
            print("Error: set-branding requires a room_id")
            sys.exit(1)
        room_id = rest[0]
        result = asyncio.run(set_branding(
            room_id,
            logo=_parse_single(rest, "--logo"),
            banner=_parse_single(rest, "--banner"),
            brand_color=_parse_single(rest, "--brand-color"),
            accent_color=_parse_single(rest, "--accent-color"),
            welcome_message=_parse_single(rest, "--welcome-message"),
        ))
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}")
        _print_usage()
