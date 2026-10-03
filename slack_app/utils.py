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

# Earlier bot replies are fed back as thread context, where the model treats them
# as examples of the expected length and format. Keep the most recent reply whole
# (follow-ups like "confirm" depend on it) and trim older ones to this many chars.
ASSISTANT_CONTEXT_MAX_CHARS = 1200
ASSISTANT_CONTEXT_LABEL = "**Assistant (earlier reply; do not copy its length or format)**"


def format_assistant_context(text: str, keep_full: bool = False) -> str:
    """Label an earlier bot reply for the thread context, trimming older ones."""
    text = text or ""
    if not keep_full and len(text) > ASSISTANT_CONTEXT_MAX_CHARS:
        omitted = len(text) - ASSISTANT_CONTEXT_MAX_CHARS
        text = f"{text[:ASSISTANT_CONTEXT_MAX_CHARS].rstrip()} ... _(trimmed {omitted} chars)_"
    return f"{ASSISTANT_CONTEXT_LABEL}: {text}"


# Messages from other bots and integrations (Pylon, Zoho, alerts...) often put
# everything in attachments or blocks with an empty ``text`` field. Cap how
# much of one such message goes into the context.
MESSAGE_CONTEXT_MAX_CHARS = 8000


def _element_text(element) -> str:
    """Text of a Block Kit text object or element (mrkdwn / plain_text)."""
    if isinstance(element, dict):
        if isinstance(element.get("text"), str):
            return element["text"]
        if isinstance(element.get("text"), dict):
            return _element_text(element["text"])
    return ""


def _rich_text(elements) -> str:
    out = []
    for el in elements or []:
        kind = el.get("type")
        if kind in ("text",):
            out.append(el.get("text", ""))
        elif kind == "link":
            out.append(f"<{el.get('url', '')}|{el['text']}>" if el.get("text") else el.get("url", ""))
        elif kind == "user":
            out.append(f"<@{el.get('user_id', '')}>")
        elif kind == "channel":
            out.append(f"<#{el.get('channel_id', '')}>")
        elif kind == "emoji":
            out.append(f":{el.get('name', '')}:")
        elif "elements" in el:
            out.append(_rich_text(el["elements"]))
            if kind in ("rich_text_section", "rich_text_preformatted", "rich_text_quote"):
                out.append("\n")
    return "".join(out)


def blocks_to_text(blocks) -> str:
    """Flatten Slack Block Kit blocks to readable text (buttons and selects included)."""
    lines = []
    for block in blocks or []:
        kind = block.get("type")
        if kind in ("section", "header"):
            text = _element_text(block)
            if text:
                lines.append(text)
            for field in block.get("fields") or []:
                if _element_text(field):
                    lines.append(_element_text(field))
        elif kind == "context":
            text = " ".join(t.strip() for t in map(_element_text, block.get("elements") or []) if t.strip())
            if text:
                lines.append(text)
        elif kind == "rich_text":
            text = _rich_text(block.get("elements")).strip()
            if text:
                lines.append(text)
        elif kind == "actions":
            for el in block.get("elements") or []:
                label = _element_text(el) or _element_text(el.get("placeholder"))
                selected = _element_text((el.get("initial_option") or {}).get("text"))
                if selected:
                    lines.append(f"[{label or 'Select'}: {selected}]")
                elif el.get("url") and label:
                    lines.append(f"[{label}: {el['url']}]")
    return "\n".join(lines).strip()


def message_text(msg: dict) -> str:
    """Full readable text of a Slack message: text, blocks and attachments.

    ``text`` is often empty for integration posts (Pylon tickets, alerts),
    with the real content in ``attachments`` or ``blocks``.
    """
    parts = []
    text = (msg.get("text") or "").strip()
    if text:
        parts.append(text)
    elif msg.get("blocks"):
        block_text = blocks_to_text(msg["blocks"])
        if block_text:
            parts.append(block_text)
    for att in msg.get("attachments") or []:
        # Link unfurls just preview a URL that is already in the text.
        if att.get("from_url") or att.get("original_url"):
            continue
        att_parts = [att.get("pretext"), att.get("title"), att.get("author_name")]
        body = blocks_to_text(att.get("blocks")) or att.get("text") or att.get("fallback")
        att_parts.append(body)
        for field in att.get("fields") or []:
            if field.get("title") or field.get("value"):
                att_parts.append(f"{field.get('title', '')}: {field.get('value', '')}".strip(": "))
        att_text = "\n".join(p.strip() for p in att_parts if p and p.strip())
        if att_text and att_text not in text:
            parts.append(att_text)
    full = "\n".join(parts)
    if len(full) > MESSAGE_CONTEXT_MAX_CHARS:
        full = full[:MESSAGE_CONTEXT_MAX_CHARS].rstrip() + " ... _(message trimmed)_"
    return full


def _author(msg: dict) -> str:
    """Who wrote a message: a user ID, or the integration's bot name."""
    if msg.get("user"):
        return msg["user"]
    profile = msg.get("bot_profile") or {}
    return profile.get("name") or msg.get("username") or "bot"


# Bot ID of this Loma app, per Slack token, so only Loma's own earlier replies
# are labelled "Assistant". Other bots' posts (Pylon etc.) are thread content.
_own_bot_ids: dict = {}


async def _own_bot_id(client) -> str | None:
    key = getattr(client, "token", None) or id(client)
    if key in _own_bot_ids:
        return _own_bot_ids[key]
    try:
        bot_id = (await client.auth_test()).get("bot_id")
    except Exception:
        bot_id = None
    if isinstance(bot_id, str) and bot_id:
        _own_bot_ids[key] = bot_id
        return bot_id
    return None


def _is_own_reply(msg: dict, own_bot_id: str | None) -> bool:
    if not msg.get("bot_id"):
        return False
    # Unknown own ID (auth.test failed): keep the old behaviour, any bot = Loma.
    return own_bot_id is None or msg["bot_id"] == own_bot_id


def _last_bot_index(messages: list, own_bot_id: str | None = None) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if _is_own_reply(messages[index], own_bot_id):
            return index
    return -1


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
    own_bot_id = await _own_bot_id(client)
    last_bot_index = _last_bot_index(context_messages, own_bot_id)
    for index, msg in enumerate(context_messages):
        user = _author(msg)
        text = message_text(msg)
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

        if _is_own_reply(msg, own_bot_id):
            context_parts.append(format_assistant_context(text, keep_full=index == last_bot_index))
        else:
            label = f"Bot ({user})" if msg.get("bot_id") else f"User ({user})"
            context_parts.append(f"**{label}**: {text}")
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
    own_bot_id = await _own_bot_id(client)
    last_bot_index = _last_bot_index(context_messages, own_bot_id)
    for index, msg in enumerate(context_messages):
        user = _author(msg)
        text = message_text(msg)
        files = msg.get("files", [])

        if _is_own_reply(msg, own_bot_id):
            context_parts.append(format_assistant_context(text, keep_full=index == last_bot_index))
        else:
            label = f"Bot ({user})" if msg.get("bot_id") else f"User ({user})"
            context_parts.append(f"**{label}**: {text}")
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
