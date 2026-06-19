import logging

from config.app_config import APP_NAME

logger = logging.getLogger(__name__)

PROMPT_SETTING_KEYS = ("identity_guidelines", "company_information")
PROMPT_SETTING_TITLES = {
    "identity_guidelines": "Identity & Guidelines",
    "company_information": "Company Information",
}

_prompt_settings_cache: dict[str, str] = {}


def set_prompt_settings_cache(settings: dict[str, str]) -> None:
    """Replace the in-memory prompt settings cache used by prompt builders."""
    global _prompt_settings_cache
    _prompt_settings_cache = {
        key: (settings.get(key) or "").strip()
        for key in PROMPT_SETTING_KEYS
    }


async def refresh_prompt_settings_from_db() -> None:
    """Load prompt settings from MongoDB into the in-memory prompt cache."""
    try:
        from observability.db import get_db

        db = get_db()
        if db is None:
            logger.warning("Prompt settings unavailable: DB not configured")
            return

        docs = await db.prompt_settings.find({"setting_key": {"$in": list(PROMPT_SETTING_KEYS)}}).to_list(10)
        set_prompt_settings_cache({
            doc.get("setting_key", ""): doc.get("content", "")
            for doc in docs
        })
        logger.info("Loaded %d prompt settings from MongoDB", len(docs))
    except Exception:
        logger.exception("Failed to load prompt settings from MongoDB")


_TOOLS_AND_SKILLS_SECTION = """
## Available Tools

This Loma deployment may provide optional MCP servers and CLI tools. Use connected tools when they are relevant, and clearly explain when a requested tool or integration is not connected.

Guidelines:
- Prefer read-only actions unless the user explicitly asks you to make a change.
- If a tool requires setup that is missing, tell the user which integration needs to be connected.
- Do not assume company-specific systems, repositories, databases, or runbooks unless they are present in configured company knowledge.

Optional personal tool examples, when configured:
- `send-email --to user@example.com --subject "Subject" --body "Body" [--attachments /path/to/file1 /path/to/file2]`
- `create-draft --to user@example.com --subject "Subject" --body "Body" [--attachments /path/to/file1]`
- `slack-personal send-message --channel CHANNEL --text "Message" [--file /path/to/file] [--file-title TITLE]`
""".strip()


_FORMATTING_SLACK = """
## Response Formatting (Slack mrkdwn)

You are responding in Slack. Use Slack mrkdwn:
- Bold section headers with single asterisks, like `*Summary*`.
- Use short bullets with `-`.
- Use backticks for inline code.
- Avoid Markdown tables and heading markers.
- Keep responses concise and readable in a thread.
""".strip()


_FORMATTING_DASHBOARD = """
## Response Formatting (Dashboard Chat UI)

You are responding in the dashboard. Use standard Markdown.
""".strip()


_FORMATTING_POOLED = f"""
## Response Formatting

Each message may specify its output channel with a `[Source: slack]`, `[Source: dashboard]`, or `[Source: github_webhook]` marker. Apply the formatting rules for the indicated source.

{_FORMATTING_SLACK}

{_FORMATTING_DASHBOARD}

### GitHub Webhook

Use standard GitHub Markdown. Be direct, technical, and actionable.
""".strip()


_GH_PR_SECTION = """
## GitHub Pull Requests

When creating GitHub pull request descriptions, use real line breaks rather than literal `\n` escape sequences.
""".strip()


SYSTEM_PROMPT_WRAPPER = """
{rulebook}

---

{tools_and_skills}

{formatting}

{gh_pr}
""".strip()


def load_rulebook() -> str:
    """Load core prompt settings from the Mongo-backed in-memory cache."""
    sections = []
    for key in PROMPT_SETTING_KEYS:
        content = (_prompt_settings_cache.get(key) or "").strip()
        if content:
            title = PROMPT_SETTING_TITLES[key]
            sections.append(f"# {title}\n\n{content}")

    if not sections:
        logger.warning("No prompt settings loaded from MongoDB")
        return f"You are {APP_NAME}, a helpful company assistant."

    logger.info("Loaded %d prompt settings from cache", len(sections))
    return "\n\n---\n\n".join(sections)


def build_system_prompt(source: str = "slack") -> str:
    """Build the system prompt from generic rules plus configured company knowledge."""
    formatting = _FORMATTING_DASHBOARD if source == "dashboard" else _FORMATTING_SLACK
    prompt = SYSTEM_PROMPT_WRAPPER.format(
        rulebook=load_rulebook(),
        tools_and_skills=_TOOLS_AND_SKILLS_SECTION,
        formatting=formatting,
        gh_pr=_GH_PR_SECTION,
    )
    logger.info("System prompt built - %d chars, source=%s", len(prompt), source)
    return prompt


def build_pooled_system_prompt() -> str:
    """Build a universal system prompt for pooled clients."""
    prompt = SYSTEM_PROMPT_WRAPPER.format(
        rulebook=load_rulebook(),
        tools_and_skills=_TOOLS_AND_SKILLS_SECTION,
        formatting=_FORMATTING_POOLED,
        gh_pr=_GH_PR_SECTION,
    )
    logger.info("Pooled system prompt built - %d chars", len(prompt))
    return prompt
