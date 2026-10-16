"""One plain line per board task: what happened, or what Loma needs.

Stored on the conversation when a run ends (`task_summary`); older tasks fall
back to the first part of their reply, which the board query projects.
"""
import json
import re

from api.task_attention import (
    APPROVAL, CLARIFY_MARKER, FAILED, INFORMATION, PLAN, PLAN_FENCE, QUESTION,
    STOPPED, STOPPED_BY_YOU,
)

MAX_LEN = 160
# Characters of the reply the board query projects for the fallback.
REPLY_HEAD_CHARS = 1500

_CLARIFY_RE = re.compile(r":::clarify\s*\n(.*?)\n:::", re.DOTALL)
_PLAN_RE = re.compile(r"```plan\s*\n(.*?)(?:\n```|$)", re.DOTALL)
_MARKDOWN = [
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),          # images
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),       # links -> text
    (re.compile(r"`{1,3}([^`]*)`{1,3}"), r"\1"),         # inline code
    (re.compile(r"(\*\*|__|\*|_|~~)(.+?)\1"), r"\2"),    # emphasis
    (re.compile(r"^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)"), ""),  # block markers
]


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 1].rstrip() + "…"


def _plain(line: str) -> str:
    for pattern, repl in _MARKDOWN:
        line = pattern.sub(repl, line)
    return line.strip()


def first_line(text: str | None) -> str:
    """The first readable line of a reply, without markdown or blocks."""
    if not text:
        return ""
    text = _CLARIFY_RE.sub("", text)
    text = _PLAN_RE.sub("", text)
    in_fence = False
    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or raw.strip().startswith("|"):
            continue
        line = _plain(raw)
        if line and set(line) - set("-=*_ "):
            return _clip(line)
    return ""


def first_question(text: str | None) -> str:
    """The first question of a :::clarify block, if it parses."""
    match = _CLARIFY_RE.search(text or "")
    if not match:
        return ""
    try:
        questions = json.loads(match.group(1)).get("questions") or []
    except (ValueError, AttributeError):
        return ""
    for question in questions:
        if isinstance(question, dict) and str(question.get("question") or "").strip():
            return _clip(str(question["question"]))
    return ""


def plan_title(text: str | None) -> str:
    """The `# Title` line of a ```plan block."""
    match = _PLAN_RE.search(text or "")
    body = match.group(1) if match else ""
    for raw in body.splitlines():
        if raw.lstrip().startswith("#"):
            return _clip(raw.lstrip("# ").strip())
    return first_line(body)


def summarize_reply(final_response: str | None, reason: str | None = None) -> str:
    """Summary of a finished run's reply, worded for why it stopped."""
    if reason == QUESTION or (reason is None and CLARIFY_MARKER in (final_response or "")):
        question = first_question(final_response)
        if question:
            return f"Loma asks: {question}"
    if reason == PLAN or (reason is None and PLAN_FENCE in (final_response or "")):
        title = plan_title(final_response)
        if title:
            return f"Plan ready: {title}"
    return first_line(final_response)


def task_summary(task: dict, reason: str | None) -> str | None:
    """The line shown under a board task's title, or None."""
    human = task.get("human_task") or {}
    if reason in (APPROVAL, INFORMATION):
        return _clip(str(human.get("title") or human.get("details") or "")) or None
    if reason in (FAILED, STOPPED):
        error = first_line(task.get("error"))
        if reason == STOPPED:
            return f"Stopped: {error[0].lower()}{error[1:]}" if error else "Stopped before finishing"
        return error or "The run failed"
    if reason == STOPPED_BY_YOU:
        return "You stopped it"
    stored = task.get("task_summary")
    if stored:
        return stored
    if task.get("status") is None:  # a draft: its instructions
        return first_line(task.get("prompt")) or None
    return summarize_reply(task.get("final_response_head"), reason) or None


_TOOL_VERBS = {
    "websearch": "Searching the web",
    "webfetch": "Reading a web page",
    "read": "Reading a file",
    "write": "Writing a file",
    "edit": "Editing a file",
    "grep": "Searching files",
    "glob": "Finding files",
    "bash": "Running a command",
    "task": "Working on a sub-task",
    "agent": "Working on a sub-task",
}


def step_label(tool_name: str | None, tool_input) -> str:
    """Plain words for the step a running task is on."""
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            tool_input = {}
    if isinstance(tool_input, dict):
        description = str(tool_input.get("description") or "").strip()
        if description:
            return _clip(description)
    name = (tool_name or "").strip()
    return _TOOL_VERBS.get(name.lower(), f"Using {name}" if name else "Working")
