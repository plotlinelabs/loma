import io
import re
import base64
import logging
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

IMAGE_MIMETYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DOCUMENT_MIMETYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
}
TEXT_EXTENSIONS = {
    ".txt", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".md", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb", ".rs",
    ".c", ".cpp", ".h", ".css", ".sql", ".sh", ".toml", ".ini", ".cfg", ".env",
}
ARCHIVE_MIMETYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
}
BINARY_EXTENSIONS = {
    ".xlsx", ".xlsm", ".xls", ".pptx",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".tgz",
}

# Matches <@U12345ABC> patterns used for Slack user mentions
BOT_MENTION_RE = re.compile(r"<@[\w]+>\s*")

# Slack's chat.postMessage text field supports up to 40,000 characters.
# We use a slightly lower limit to leave room for the truncation notice.
SLACK_MAX_LENGTH = 40000


def strip_bot_mention(text: str) -> str:
    """Remove the @bot mention from the beginning of a message."""
    return BOT_MENTION_RE.sub("", text).strip()


def truncate_for_slack(text: str, max_length: int = SLACK_MAX_LENGTH) -> str:
    """Truncate text to fit within Slack's message limits."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 100] + "\n\n_(response truncated due to length)_"


async def get_thread_context(
    client, channel: str, thread_ts: str, current_ts: str = "",
    limit: int = 150,
) -> tuple[str, list]:
    """
    Fetch previous messages in a thread and format them as conversation context.

    Also collects file metadata from earlier messages so images/files
    shared earlier in the thread are available to the agent.

    Args:
        client: Slack WebClient
        channel: Channel ID
        thread_ts: Thread timestamp (parent message ts)
        current_ts: Timestamp of the current message to exclude
        limit: Max number of messages to fetch

    Returns:
        Tuple of (formatted conversation context string, list of raw Slack
        file objects from earlier messages).
    """
    try:
        result = await client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=limit,
        )
        messages = result.get("messages", [])
    except Exception as e:
        logger.warning(f"Failed to fetch thread context: {e}")
        return "", []

    if len(messages) <= 1:
        return "", []

    # Exclude the current message we're responding to
    if current_ts:
        context_messages = [m for m in messages if m.get("ts") != current_ts]
    else:
        context_messages = messages[:-1]

    context_parts = []
    thread_files = []
    for msg in context_messages:
        user = msg.get("user", "bot")
        text = msg.get("text", "")
        files = msg.get("files", [])
        subtype = msg.get("subtype", "")

        # Debug: log every message's keys and file-related fields
        logger.debug(
            "[THREAD] msg keys=%s subtype=%r files=%d",
            list(msg.keys()), subtype, len(files),
        )
        if files:
            logger.info("[THREAD] Found %d file(s) in message from user=%s", len(files), user)
        elif subtype == "file_share" or "uploaded a file" in text or "file" in msg.keys():
            logger.warning(
                "[THREAD] Message looks like a file share but files array is empty. "
                "subtype=%r, keys=%s, text=%.100s",
                subtype, list(msg.keys()), text,
            )

        if msg.get("bot_id"):
            context_parts.append(f"**Assistant**: {text}")
        else:
            context_parts.append(f"**User ({user})**: {text}")
            if files:
                context_parts.append(f"  _(attached {len(files)} file(s))_")
                thread_files.extend(files)

    return "\n".join(context_parts), thread_files


async def get_dm_context(
    client, channel: str, limit: int = 10,
) -> tuple[str, list]:
    """
    Fetch recent DM history for context.

    Args:
        client: Slack WebClient
        channel: DM channel ID
        limit: Max number of messages to fetch

    Returns:
        Tuple of (formatted conversation context string, list of raw Slack
        file objects from earlier messages).
    """
    try:
        result = await client.conversations_history(
            channel=channel,
            limit=limit,
        )
        messages = result.get("messages", [])
    except Exception as e:
        logger.warning(f"Failed to fetch DM context: {e}")
        return "", []

    if len(messages) <= 1:
        return "", []

    # Messages come newest-first, reverse for chronological order, skip current
    context_messages = list(reversed(messages))[:-1]

    context_parts = []
    thread_files = []
    for msg in context_messages:
        user = msg.get("user", "bot")
        text = msg.get("text", "")
        files = msg.get("files", [])

        if msg.get("bot_id"):
            context_parts.append(f"**Assistant**: {text}")
        else:
            context_parts.append(f"**User ({user})**: {text}")
            if files:
                context_parts.append(f"  _(attached {len(files)} file(s))_")
                thread_files.extend(files)

    return "\n".join(context_parts), thread_files


def _extract_document_text(content: bytes, name: str, mimetype: str) -> str | None:
    """Extract text from PDF or DOCX bytes. Returns None on failure."""
    ext = Path(name).suffix.lower()

    if mimetype == "application/pdf" or ext == ".pdf":
        try:
            import pymupdf
            doc = pymupdf.open(stream=content, filetype="pdf")
            pages = [page.get_text() for page in doc]
            doc.close()
            return "\n\n".join(pages).strip() or None
        except Exception as e:
            logger.warning("[FILES] pymupdf failed for %s: %s", name, e)
            return None

    if mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or ext == ".docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs).strip() or None
        except Exception as e:
            logger.warning("[FILES] python-docx failed for %s: %s", name, e)
            return None

    return None


async def download_slack_files(bot_token: str, files: list) -> list:
    """
    Download files attached to a Slack message.

    Returns a list of dicts, each with:
      - name: filename
      - mimetype: MIME type
      - type: "image" or "text"
      - data: base64 string (images) or decoded text (text files)
    """
    downloaded = []

    for f in files:
        file_url = f.get("url_private_download") or f.get("url_private")
        if not file_url:
            continue

        name = f.get("name", "unknown")
        mimetype = f.get("mimetype", "")
        size = f.get("size", 0)

        # Skip files larger than 10MB
        if size > 10 * 1024 * 1024:
            logger.warning("[FILES] Skipping %s — too large (%d bytes)", name, size)
            continue

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {bot_token}"}
                async with session.get(file_url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning("[FILES] Failed to download %s — HTTP %d", name, resp.status)
                        continue
                    content = await resp.read()
        except Exception as e:
            logger.warning("[FILES] Failed to download %s: %s", name, e)
            continue

        if mimetype in IMAGE_MIMETYPES:
            downloaded.append({
                "name": name,
                "mimetype": mimetype,
                "type": "image",
                "data": base64.standard_b64encode(content).decode("ascii"),
            })
            logger.info("[FILES] Downloaded image: %s (%s, %d bytes)", name, mimetype, len(content))

        elif mimetype in DOCUMENT_MIMETYPES or Path(name).suffix.lower() in (".pdf", ".docx"):
            text_content = _extract_document_text(content, name, mimetype)
            if text_content:
                if len(text_content) > 50000:
                    text_content = text_content[:50000] + "\n\n... (truncated)"
                downloaded.append({
                    "name": name,
                    "mimetype": mimetype,
                    "type": "text",
                    "data": text_content,
                })
                logger.info("[FILES] Extracted text from document: %s (%d chars)", name, len(text_content))
            else:
                logger.warning("[FILES] Failed to extract text from %s", name)

        elif Path(name).suffix.lower() in TEXT_EXTENSIONS or mimetype.startswith("text/"):
            try:
                text_content = content.decode("utf-8", errors="replace")
                # Truncate very large text files
                if len(text_content) > 50000:
                    text_content = text_content[:50000] + "\n\n... (truncated)"
                downloaded.append({
                    "name": name,
                    "mimetype": mimetype,
                    "type": "text",
                    "data": text_content,
                })
                logger.info("[FILES] Downloaded text file: %s (%d chars)", name, len(text_content))
            except Exception as e:
                logger.warning("[FILES] Failed to decode %s as text: %s", name, e)
        elif (mimetype in ARCHIVE_MIMETYPES
              or Path(name).suffix.lower() in BINARY_EXTENSIONS):
            # Binary files (archives, spreadsheets) — base64 encode for agent
            downloaded.append({
                "name": name,
                "mimetype": mimetype or "application/octet-stream",
                "type": "binary",
                "data": base64.standard_b64encode(content).decode("ascii"),
            })
            logger.info("[FILES] Downloaded binary file: %s (%s, %d bytes)", name, mimetype, len(content))
        else:
            logger.info("[FILES] Skipping unsupported file type: %s (%s)", name, mimetype)

    return downloaded
