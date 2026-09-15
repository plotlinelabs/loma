"""Lossless, deliberately small Google Docs instruction format.

Only selected-tab body content is executable. Unsupported structures fail closed.
The adapter does not own credentials: callers supply the acting user's access token.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from urllib.parse import urlparse

import aiohttp
from markdown_it import MarkdownIt

from api.skill_service import SkillError

MAX_BYTES = 200_000


class SourceError(SkillError):
    def __init__(self, message, *, status=400, code="invalid_source"):
        super().__init__(message, status=status)
        self.code = code


def parse_url(url):
    if not isinstance(url, str):
        raise SourceError("Paste a Google Docs document URL.")
    parsed = urlparse(url)
    match = re.fullmatch(r"/document/d/([\w-]+)(?:/.*)?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or not match:
        raise SourceError("Paste a Google Docs document URL.")
    return match[1]


def utf16(text):
    return len(text.encode("utf-16-le")) // 2


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def all_tabs(doc):
    def walk(tabs):
        for tab in tabs:
            yield tab
            yield from walk(tab.get("childTabs", []))
    return list(walk(doc.get("tabs", [])))


def reject_suggestions(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.startswith("suggested") and item:
                raise SourceError("Resolve suggestions in the selected tab before syncing.")
            reject_suggestions(item)
    elif isinstance(value, list):
        for item in value:
            reject_suggestions(item)


def _escape(text):
    return re.sub(r"([\\`*_\[\]<>#!|~])", r"\\\1", text)


def _markdown(block):
    pieces = []
    for run in block["runs"]:
        text = run["text"]
        if run.get("code"):
            fence = "`" * (max([len(m[0]) for m in re.finditer(r"`+", text)] or [0]) + 1)
            text = fence + " " + text + " " + fence
        else:
            text = _escape(text)
            # Keep whitespace outside emphasis delimiters so CommonMark parses it.
            match = re.fullmatch(r"(\s*)(.*?)(\s*)", text, re.S)
            lead, core, tail = match.groups()
            if core:
                if run.get("bold"):
                    core = "**" + core + "**"
                if run.get("italic"):
                    core = "*" + core + "*"
                if run.get("url"):
                    core = "[" + core + "](<" + run["url"] + ">)"
            text = lead + core + tail
        pieces.append(text)
    prefix = "#" * block["heading"] + " " if block.get("heading") else ""
    if block.get("bullet"):
        prefix = "  " * block.get("depth", 0) + ("1. " if block["bullet"] == "number" else "- ")
    value = "".join(pieces)
    if not prefix:
        value = re.sub(r"^([-+]|\d+\.) ", lambda m: m[1].replace(".", "\\.") + " " if m[1][0].isdigit() else "\\" + m[0], value)
    return prefix + value


def _merge_runs(runs):
    result = []
    for run in runs:
        if not run["text"]:
            continue
        style = {k: v for k, v in run.items() if k != "text" and v}
        if result and {k: v for k, v in result[-1].items() if k != "text"} == style:
            result[-1]["text"] += run["text"]
        else:
            result.append({"text": run["text"], **style})
    return result


def read_tab(doc, tab_id=None):
    tabs = all_tabs(doc)
    if not tab_id and len(tabs) == 1:
        tab_id = tabs[0]["tabProperties"]["tabId"]
    tab = next((t for t in tabs if t["tabProperties"]["tabId"] == tab_id), None)
    if not tab:
        raise SourceError("The selected document tab no longer exists." if tab_id else "Select an existing document tab.", code="access_revoked" if tab_id else "invalid_source")
    data = tab["documentTab"]
    reject_suggestions(data)
    if data.get("positionedObjects") or data.get("footnotes"):
        raise SourceError("Drawings and footnotes are not supported in linked instructions.")
    blocks = []
    for element in data.get("body", {}).get("content", []):
        if "sectionBreak" in element:
            if blocks:
                raise SourceError("Section breaks within instructions are not supported.")
            continue
        if "paragraph" not in element:
            raise SourceError("Tables and embedded content are not supported in linked instructions.")
        para = element["paragraph"]
        if para.get("positionedObjectIds"):
            raise SourceError("Positioned images are not supported.")
        runs = []
        for part in para.get("elements", []):
            if "textRun" not in part:
                raise SourceError("Images, smart chips, and non-text elements are not supported.")
            text_run = part["textRun"]
            style = text_run.get("textStyle", {})
            if style.get("strikethrough") or style.get("baselineOffset", "NONE") != "NONE":
                raise SourceError("Strikethrough, superscript and subscript are not supported.")
            link = style.get("link", {})
            if link and (not link.get("url") or urlparse(link["url"]).scheme not in ("https", "http", "mailto")):
                raise SourceError("Only ordinary http, https and mailto links are supported.")
            runs.append({"text": text_run.get("content", ""), "bold": style.get("bold", False),
                         "italic": style.get("italic", False), "url": link.get("url")})
        # The structural paragraph terminator is not editable content.
        if runs and runs[-1]["text"].endswith("\n"):
            runs[-1]["text"] = runs[-1]["text"][:-1]
        if any("\n" in r["text"] for r in runs):
            raise SourceError("Soft line breaks are not supported; use separate paragraphs.")
        heading = para.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        if heading not in ("NORMAL_TEXT", "TITLE", "SUBTITLE") and not re.fullmatch(r"HEADING_[1-6]", heading):
            raise SourceError("Unsupported heading style.")
        block = {"runs": _merge_runs(runs), "heading": int(heading[-1]) if heading.startswith("HEADING_") else 0,
                 "start": element["startIndex"], "end": element["endIndex"]}
        if heading in ("TITLE", "SUBTITLE"):
            raise SourceError("Use headings rather than title/subtitle styles for linked instructions.")
        bullet = para.get("bullet")
        if bullet:
            depth = bullet.get("nestingLevel", 0)
            levels = data.get("lists", {}).get(bullet["listId"], {}).get("listProperties", {}).get("nestingLevels", [])
            if depth >= len(levels):
                raise SourceError("Cannot resolve list formatting.")
            glyph = levels[depth].get("glyphType", "GLYPH_TYPE_UNSPECIFIED")
            if glyph not in ("GLYPH_TYPE_UNSPECIFIED", "DECIMAL") or depth:
                raise SourceError("Only single-level bullet and decimal numbered lists are supported in v1.")
            block.update(bullet="number" if glyph == "DECIMAL" else "bullet", depth=depth)
        blocks.append(block)
    if not blocks:
        raise SourceError("The selected tab has no instructions.")
    content = "\n".join(_markdown(b) for b in blocks) + "\n"
    if not content.strip() or len(content.encode()) > MAX_BYTES:
        raise SourceError("Instructions must be nonempty and at most 200 KB.")
    if [block_key(b) for b in parse_markdown(content)] != [block_key(b) for b in blocks]:
        raise SourceError("This combination of text and formatting cannot round-trip safely. Simplify the selected tab's formatting.")
    return {"tab_id": tab_id, "tab_title": tab["tabProperties"].get("title", ""), "content": content,
            "hash": digest(content), "revision": doc.get("revisionId"), "title": doc.get("title", ""), "blocks": blocks}


def parse_markdown(content):
    """Parse the editable subset, preserving blank lines and literal whitespace."""
    if not content.strip() or len(content.encode()) > MAX_BYTES:
        raise SourceError("Instructions must be nonempty and at most 200 KB.")
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", content):
        raise SourceError("Unsupported control characters in instructions.")
    parser = MarkdownIt("commonmark", {"html": False})
    blocks = []
    fenced = False
    for line in content.removesuffix("\n").split("\n"):
        if line.startswith("```"):
            fenced = not fenced
            # Fences are literal text in Docs, not a lossy code-block conversion.
        heading, bullet = 0, None
        value = line
        if not fenced and not line.startswith("```"):
            match = re.match(r"^(#{1,6}) (.*)$", line)
            if match:
                heading, value = len(match[1]), match[2]
            else:
                match = re.match(r"^([-+*]|\d+\.) (.*)$", line)
                if match:
                    bullet, value = ("number" if match[1][0].isdigit() else "bullet"), match[2]
        runs, style = [], {}
        # Code and indentation remain literal; no automatic whitespace stripping.
        if fenced or line.startswith("```") or value.startswith((" ", "\t")):
            runs = [{"text": value}]
        else:
            for token in parser.parseInline(value)[0].children or []:
                if token.type in ("strong_open", "em_open"):
                    style["bold" if token.type == "strong_open" else "italic"] = True
                elif token.type in ("strong_close", "em_close"):
                    style.pop("bold" if token.type == "strong_close" else "italic", None)
                elif token.type == "link_open":
                    url = token.attrGet("href")
                    if urlparse(url).scheme not in ("http", "https", "mailto"):
                        raise SourceError("Only ordinary http, https and mailto links are supported.")
                    style["url"] = url
                elif token.type == "link_close":
                    style.pop("url", None)
                elif token.type == "text":
                    runs.append({"text": token.content, **style})
                elif token.type == "code_inline":
                    # Keep literal code delimiters; Docs does not have an inline-code semantic type.
                    runs.append({"text": "`" + token.content + "`", **style})
                else:
                    raise SourceError("Unsupported Markdown element: " + token.type)
        block = {"runs": _merge_runs(runs), "heading": heading}
        if bullet:
            block.update(bullet=bullet, depth=0)
        blocks.append(block)
    return blocks


def block_key(block):
    normalized = {k: v for k, v in block.items() if k not in ("start", "end", "runs")}
    runs = []
    for run in block["runs"]:
        match = re.fullmatch(r"(\s*)(.*?)(\s*)", run["text"], re.S)
        lead, core, tail = match.groups()
        runs.extend([{"text": lead}, {**run, "text": core}, {"text": tail}])
    normalized["runs"] = _merge_runs(runs)
    return json.dumps(normalized, sort_keys=True)


def build_requests(snapshot, content):
    """Replace changed paragraph spans back-to-front; retain untouched paragraphs/tabs."""
    old, new = snapshot["blocks"], parse_markdown(content)
    tab_id = snapshot["tab_id"]
    requests = []
    def span(start, end):
        return {"startIndex": start, "endIndex": end, "tabId": tab_id}
    matcher = difflib.SequenceMatcher(a=list(map(block_key, old)), b=list(map(block_key, new)), autojunk=False)
    for op, a, b, c, d in reversed(matcher.get_opcodes()):
        if op == "equal":
            continue
        start = old[a]["start"] if a < len(old) else old[-1]["end"] - 1
        end = old[b-1]["end"] if b > a else start
        terminal = b == len(old)
        if terminal:
            end = min(end, old[-1]["end"] - 1)  # never delete Docs' final newline
        if terminal and c == d and a > 0:
            start -= 1  # remove the preceding separator, retaining the final terminator
        if end > start:
            requests.append({"deleteContentRange": {"range": span(start, end)}})
        added = new[c:d]
        text = "\n".join("".join(r["text"] for r in block["runs"]) for block in added)
        if added and not terminal:
            text += "\n"
        if a == len(old) and added:
            # Insertion at the terminal newline starts a new paragraph.
            text = "\n" + text
            start_offset = 1
        else:
            start_offset = 0
        if text:
            requests.append({"insertText": {"location": {"index": start, "tabId": tab_id}, "text": text}})
        index = start + start_offset
        for block in added:
            length = utf16("".join(r["text"] for r in block["runs"]))
            rng = span(index, index + max(length, 1))
            requests.append({"deleteParagraphBullets": {"range": rng}})
            requests.append({"updateParagraphStyle": {"range": rng, "paragraphStyle": {
                "namedStyleType": "HEADING_" + str(block["heading"]) if block["heading"] else "NORMAL_TEXT"}, "fields": "namedStyleType"}})
            if length:
                requests.append({"updateTextStyle": {"range": span(index, index+length),
                    "textStyle": {"bold": False, "italic": False}, "fields": "bold,italic,link"}})
            offset = index
            for run in block["runs"]:
                size = utf16(run["text"])
                style = {k: run[k] for k in ("bold", "italic") if run.get(k)}
                if run.get("url"):
                    style["link"] = {"url": run["url"]}
                if style and size:
                    requests.append({"updateTextStyle": {"range": span(offset, offset+size), "textStyle": style, "fields": ",".join(style)}})
                offset += size
            if block.get("bullet"):
                requests.append({"createParagraphBullets": {"range": rng, "bulletPreset":
                    "NUMBERED_DECIMAL_ALPHA_ROMAN" if block["bullet"] == "number" else "BULLET_DISC_CIRCLE_SQUARE"}})
            index += length + 1
    if len(requests) > 1000:
        raise SourceError("This edit is too large for one safe batch. Make smaller edits.")
    return requests


class GoogleDocsSource:
    def __init__(self, token):
        self.token = token

    async def _request(self, method, url, **kwargs):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.request(method, url, headers={"Authorization": "Bearer " + self.token}, **kwargs) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    code = "access_revoked" if resp.status in (401, 403, 404) else "google_error"
                    if resp.status == 400 and "revision" in str(data).lower():
                        code = "conflict"
                    error = SourceError("Google Docs access failed. Reconnect or check document permissions." if code == "access_revoked"
                                      else "Google Docs changed during this edit. Reload the source and retry." if code == "conflict"
                                      else "Google Docs request failed. Try again later.", status=409 if code == "conflict" else 502, code=code)
                    error.google_message = data.get("error", {}).get("message", "")
                    raise error
                return data

    async def read(self, document_id):
        return await self._request("GET", f"https://docs.googleapis.com/v1/documents/{document_id}", params={"includeTabsContent": "true", "suggestionsViewMode": "SUGGESTIONS_INLINE"})

    async def can_edit(self, document_id):
        data = await self._request("GET", f"https://www.googleapis.com/drive/v3/files/{document_id}", params={"fields": "capabilities(canEdit),trashed,mimeType", "supportsAllDrives": "true"})
        if data.get("trashed") or data.get("mimeType") != "application/vnd.google-apps.document":
            raise SourceError("Source is deleted or is not a Google Doc.", code="access_revoked")
        return data.get("capabilities", {}).get("canEdit", False)

    async def write(self, document_id, snapshot, requests):
        if not snapshot.get("revision"):
            raise SourceError("Google did not return an editable revision. Check edit access.")
        if requests:
            await self._request("POST", f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate", json={"writeControl": {"requiredRevisionId": snapshot["revision"]}, "requests": requests})
