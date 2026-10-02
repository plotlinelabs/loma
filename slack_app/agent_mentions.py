"""Pick a Loma agent from a Slack message.

A Slack message can name an agent up front, like the dashboard composer's
agent picker. Punctuation and brackets around the name are optional:

    @Loma AR Agent: can you check invoice INV-123?
    @Loma - AR agent - can you check invoice INV-123?
    @Loma [AR Agent] can you check invoice INV-123?
    @Loma ar-agent can you check invoice INV-123?

Single-word agent names need a bracket or a ':' / ',' / '-' after them, so a
sentence that merely starts with a word like "Finance" never switches agents.

The agent is then pinned to the Slack thread (conversation metadata), so
follow-ups in that thread keep talking to it until another agent is named.
Only agents the sender can see (their own plus workspace-shared) match.
"""

import re

from api.agent_identity_routes import list_agents_for_chat

LIST_AGENTS_COMMANDS = {"agents", "list agents", "show agents"}

_SEPARATORS = r"[\s_-]+"


def agent_handle(name: str) -> str:
    """'AR Agent' -> 'ar-agent'."""
    return "-".join(w for w in re.split(_SEPARATORS, (name or "").strip()) if w).lower()


# Leading noise before the name: spaces, dashes, colons, slashes, Slack
# formatting (* _ ~). Slack sends "<" and ">" in message text as &lt; / &gt;.
_LEAD = r"^[\s\-\u2013\u2014:,;./\\*_~]*"
_OPEN = r"(?:\[|\(|\{|&lt;|<)"
_CLOSE = r"(?:\]|\)|\}|&gt;|>)"
_FMT = r"[*_~]*"
_TRAIL = r"[\s\-\u2013\u2014:,;.!?/\\]*"


def _name_patterns(name: str) -> list[re.Pattern]:
    words = [re.escape(w) for w in re.split(_SEPARATORS, (name or "").strip()) if w]
    if not words:
        return []
    body = rf"{_FMT}@?{_SEPARATORS.join(words)}{_FMT}"
    patterns = [
        # "[AR Agent]", "<ar agent>", "(Finance)" - bracketed, any length.
        re.compile(rf"{_LEAD}{_OPEN}\s*{body}\s*{_CLOSE}{_TRAIL}", re.IGNORECASE),
        # "AR Agent:", "- Finance -", "/finance/", "Finance," - name then punctuation.
        re.compile(rf"{_LEAD}{body}\s*(?:[:,]|[/\-\u2013\u2014](?=\s|$)){_TRAIL}", re.IGNORECASE),
    ]
    # "AR agent what do we..." - a multi-word name is deliberate enough to
    # need no punctuation at all.
    if len(words) > 1:
        patterns.append(re.compile(rf"{_LEAD}{body}(?![\w-]){_TRAIL}", re.IGNORECASE))
    return patterns


def match_agent_reference(text: str, agents: list[dict]) -> tuple[dict | None, str]:
    """Return (agent, remaining text) when ``text`` starts by naming an agent."""
    if not text:
        return None, text
    # Longest name first, so "AR Agent Pro" wins over "AR Agent".
    for agent in sorted(agents, key=lambda a: len(a.get("name") or ""), reverse=True):
        for pattern in _name_patterns(agent.get("name")):
            m = pattern.match(text)
            if m:
                return agent, text[m.end():]
    return None, text


async def find_agent_reference(db, text: str, user_email: str) -> tuple[dict | None, str]:
    """Match ``text`` against the agents ``user_email`` may chat with."""
    if db is None or not user_email or not text:
        return None, text
    return match_agent_reference(text, await list_agents_for_chat(db, user_email))


def is_list_agents_command(text: str) -> bool:
    return (text or "").strip().lower().rstrip("?") in LIST_AGENTS_COMMANDS


def format_agent_list(agents: list[dict]) -> str:
    if not agents:
        return "No agents are shared with you yet. Create one on the Agents page in Loma."
    lines = ["*Agents you can chat with*"]
    for agent in agents:
        desc = (agent.get("description") or "").strip()
        lines.append(f"- *{agent['name']}* (`{agent_handle(agent['name'])}`)" + (f": {desc}" if desc else ""))
    lines.append(
        f"Start your message with the agent's name, e.g. `@Loma {agents[0]['name']}: your question` "
        f"or `@Loma [{agents[0]['name']}] your question`. The thread then stays with that agent."
    )
    return "\n".join(lines)
