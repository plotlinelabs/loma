"""Google Slides API client for the Loma agent.

Provides CLI commands to read and edit Google Slides presentations using a
user's personal Google OAuth tokens.

Commands:
  1. google_slides.py get-info --user-email EMAIL --presentation-id ID
  2. google_slides.py list-slides --user-email EMAIL --presentation-id ID
  3. google_slides.py read-slide --user-email EMAIL --presentation-id ID --slide-index N
  4. google_slides.py create-presentation --user-email EMAIL --title T
  5. google_slides.py add-slide --user-email EMAIL --presentation-id ID [--insertion-index N] [--layout L]
  6. google_slides.py replace-text --user-email EMAIL --presentation-id ID --find F --replacement R

Requires:
  - User must have connected their Google account via the Integrations page
  - OBSERVABILITY_MONGODB_URI, OAUTH_ENCRYPTION_KEY, GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET environment variables

Usage (called by the agent via Bash):
  python3 tools/google_slides.py get-info --user-email adarsh@example.com --presentation-id 1abc2def
  python3 tools/google_slides.py list-slides --user-email adarsh@example.com --presentation-id 1abc2def
  python3 tools/google_slides.py read-slide --user-email adarsh@example.com --presentation-id 1abc2def --slide-index 0
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


async def _get_service(user_email: str):
    """Build an authenticated Google Slides API service."""
    from googleapiclient.discovery import build

    creds = await get_google_credentials(user_email)
    return build("slides", "v1", credentials=creds)


def _extract_text_from_elements(elements: list[dict]) -> str:
    """Recursively extract all text content from slide page elements."""
    texts = []
    for el in elements:
        shape = el.get("shape", {})
        text_content = shape.get("text", {})
        for text_el in text_content.get("textElements", []):
            run = text_el.get("textRun", {})
            content = run.get("content", "")
            if content.strip():
                texts.append(content.strip())

        # Tables
        table = el.get("table", {})
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                cell_text = cell.get("text", {})
                for text_el in cell_text.get("textElements", []):
                    run = text_el.get("textRun", {})
                    content = run.get("content", "")
                    if content.strip():
                        texts.append(content.strip())

        # Groups (recursive)
        group = el.get("elementGroup", {})
        children = group.get("children", [])
        if children:
            texts.append(_extract_text_from_elements(children))

    return "\n".join(texts)


def _format_slide_summary(slide: dict, index: int) -> dict[str, Any]:
    """Format a slide into a compact summary."""
    layout = slide.get("slideProperties", {}).get("layoutProperties", {}).get("name", "")
    elements = slide.get("pageElements", [])
    text = _extract_text_from_elements(elements)
    return {
        "index": index,
        "objectId": slide.get("objectId", ""),
        "layout": layout,
        "elementCount": len(elements),
        "textPreview": text[:300] + ("..." if len(text) > 300 else ""),
    }


# ── Commands ──────────────────────────────────────────────────────────────


async def get_info(user_email: str, presentation_id: str) -> dict:
    """Get presentation metadata (title, slide count, dimensions)."""
    service = await _get_service(user_email)
    pres = service.presentations().get(presentationId=presentation_id).execute()
    page_size = pres.get("pageSize", {})
    width = page_size.get("width", {})
    height = page_size.get("height", {})
    return {
        "presentationId": pres.get("presentationId"),
        "title": pres.get("title", ""),
        "slideCount": len(pres.get("slides", [])),
        "width": width.get("magnitude", 0),
        "height": height.get("magnitude", 0),
        "locale": pres.get("locale", ""),
    }


async def list_slides(user_email: str, presentation_id: str) -> dict:
    """List all slides with text previews."""
    service = await _get_service(user_email)
    pres = service.presentations().get(presentationId=presentation_id).execute()
    slides = [
        _format_slide_summary(slide, i)
        for i, slide in enumerate(pres.get("slides", []))
    ]
    return {
        "title": pres.get("title", ""),
        "slideCount": len(slides),
        "slides": slides,
    }


async def read_slide(user_email: str, presentation_id: str, slide_index: int) -> dict:
    """Read full text content of a specific slide by index."""
    service = await _get_service(user_email)
    pres = service.presentations().get(presentationId=presentation_id).execute()
    slides = pres.get("slides", [])

    if slide_index < 0 or slide_index >= len(slides):
        return {"error": f"Slide index {slide_index} out of range (0-{len(slides) - 1})"}

    slide = slides[slide_index]
    elements = slide.get("pageElements", [])
    text = _extract_text_from_elements(elements)

    # Also extract notes
    notes_page = slide.get("slideProperties", {}).get("notesPage", {})
    notes_elements = notes_page.get("pageElements", [])
    notes_text = _extract_text_from_elements(notes_elements) if notes_elements else ""

    return {
        "index": slide_index,
        "objectId": slide.get("objectId", ""),
        "text": text,
        "speakerNotes": notes_text,
        "elementCount": len(elements),
    }


async def create_presentation(user_email: str, title: str) -> dict:
    """Create a new Google Slides presentation."""
    service = await _get_service(user_email)
    pres = service.presentations().create(body={"title": title}).execute()
    pres_id = pres.get("presentationId")
    return {
        "created": True,
        "presentationId": pres_id,
        "title": pres.get("title", ""),
        "url": f"https://docs.google.com/presentation/d/{pres_id}/edit",
        "slideCount": len(pres.get("slides", [])),
    }


async def add_slide(
    user_email: str, presentation_id: str, insertion_index: int = -1,
    layout: str = "BLANK",
) -> dict:
    """Add a new slide to a presentation.

    Args:
        insertion_index: 0-based index where to insert. -1 means append at end.
        layout: Predefined layout. One of: BLANK, CAPTION_ONLY, TITLE,
                TITLE_AND_BODY, TITLE_AND_TWO_COLUMNS, TITLE_ONLY,
                SECTION_HEADER, MAIN_POINT, BIG_NUMBER.
    """
    import uuid

    service = await _get_service(user_email)
    object_id = f"slide_{uuid.uuid4().hex[:12]}"

    request: dict = {
        "createSlide": {
            "objectId": object_id,
            "slideLayoutReference": {"predefinedLayout": layout},
        }
    }
    if insertion_index >= 0:
        request["createSlide"]["insertionIndex"] = insertion_index

    service.presentations().batchUpdate(
        presentationId=presentation_id,
        body={"requests": [request]},
    ).execute()

    return {
        "added": True,
        "presentationId": presentation_id,
        "slideObjectId": object_id,
        "insertionIndex": insertion_index,
        "layout": layout,
    }


async def replace_text_in_presentation(
    user_email: str, presentation_id: str, find: str, replacement: str,
    match_case: bool = True,
) -> dict:
    """Find and replace text across all slides in a presentation."""
    service = await _get_service(user_email)

    result = service.presentations().batchUpdate(
        presentationId=presentation_id,
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
        "presentationId": presentation_id,
        "find": find,
        "replacement": replacement,
        "occurrencesChanged": occurrences,
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Google Slides tool for Loma agent")
    parser.add_argument("--auth-token", required=True, help="HMAC-signed user auth token")
    sub = parser.add_subparsers(dest="command", required=True)

    # get-info
    p_info = sub.add_parser("get-info", help="Get presentation metadata")
    p_info.add_argument("--user-email", required=True)
    p_info.add_argument("--presentation-id", required=True)

    # list-slides
    p_list = sub.add_parser("list-slides", help="List all slides with previews")
    p_list.add_argument("--user-email", required=True)
    p_list.add_argument("--presentation-id", required=True)

    # read-slide
    p_read = sub.add_parser("read-slide", help="Read a specific slide's content")
    p_read.add_argument("--user-email", required=True)
    p_read.add_argument("--presentation-id", required=True)
    p_read.add_argument("--slide-index", type=int, required=True)

    # create-presentation
    p_create = sub.add_parser("create-presentation", help="Create a new presentation")
    p_create.add_argument("--user-email", required=True)
    p_create.add_argument("--title", required=True)

    # add-slide
    p_add = sub.add_parser("add-slide", help="Add a slide to a presentation")
    p_add.add_argument("--user-email", required=True)
    p_add.add_argument("--presentation-id", required=True)
    p_add.add_argument("--insertion-index", type=int, default=-1, help="0-based index (-1 for append)")
    p_add.add_argument("--layout", default="BLANK", help="Predefined layout name")

    # replace-text
    p_replace = sub.add_parser("replace-text", help="Find and replace text across all slides")
    p_replace.add_argument("--user-email", required=True)
    p_replace.add_argument("--presentation-id", required=True)
    p_replace.add_argument("--find", required=True)
    p_replace.add_argument("--replacement", required=True)
    p_replace.add_argument("--match-case", action="store_true", default=True)

    args = parser.parse_args()

    # Verify auth token matches the requested user
    from tools._auth_token import verify_user_auth_token
    if not verify_user_auth_token(args.auth_token, args.user_email):
        print(json.dumps({"error": "Authentication failed — user identity mismatch or expired token. "
                          "You can only access your own Google account."}))
        sys.exit(1)

    try:
        if args.command == "get-info":
            result = asyncio.run(get_info(args.user_email, args.presentation_id))
        elif args.command == "list-slides":
            result = asyncio.run(list_slides(args.user_email, args.presentation_id))
        elif args.command == "read-slide":
            result = asyncio.run(read_slide(
                args.user_email, args.presentation_id, args.slide_index,
            ))
        elif args.command == "create-presentation":
            result = asyncio.run(create_presentation(args.user_email, args.title))
        elif args.command == "add-slide":
            result = asyncio.run(add_slide(
                args.user_email, args.presentation_id,
                args.insertion_index, args.layout,
            ))
        elif args.command == "replace-text":
            result = asyncio.run(replace_text_in_presentation(
                args.user_email, args.presentation_id,
                args.find, args.replacement, args.match_case,
            ))
        else:
            parser.print_help()
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Google Slides API error: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
