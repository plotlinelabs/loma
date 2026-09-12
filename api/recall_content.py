"""Fail-closed visible-message projection for recall (never read raw turns)."""
import hashlib
import json
import re
from datetime import datetime

SANITIZER_VERSION = 1
# Legacy execution envelopes have no authenticated user-text delimiter. Exclude
# the whole message rather than trusting a spoofable 'Current Message' marker.
_ENVELOPE = re.compile(
    r'\[Source:|\[Authenticated User:|\[Personal Tools Auth Token:|'
    r'## Conversation Context|## Current Message|<environment_context>|'
    r'<system(?:_reminder|-reminder)?>|<developer>|<tool_result>|<thinking>|<analysis>', re.I,
)
_SECRETS = [
    re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)', re.S),
    re.compile(r'(?im)^.*(?:--auth-token|authorization\s*[:=]|["\']?(?:password|passwd|secret|api[-_]?key|access[-_]?token|refresh[-_]?token|auth[-_]?token|client[-_]?secret|(?:_token|_password|_secret|_key)|cookie)["\']?\s*[:=]).*$'),
    re.compile(r'(?i)\b(?:sk-(?:proj-)?[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9_]{10,}|github_pat_[a-z0-9_]{10,}|xox[baprs]-[a-z0-9-]+|AKIA[A-Z0-9]{16})\b'),
    re.compile(r'\beyJ[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+){0,2}={0,2}'),
    re.compile(r'(?i)\b(?:https?|mongodb(?:\+srv)?|postgres(?:ql)?|redis)://[^\s<>"\']+'),
]


def sanitize(text: str) -> tuple[str, bool]:
    original = text
    # Ambiguous multiline credential values: do not attempt to guess the end.
    if re.search(
        r'(?im)(?:--auth-token[ \t]*|(?:authorization|password|secret|api[-_]?key|'
        r'_(?:key|token))["\']?[ \t]*[:=][ \t]*)\\?\r?\n', text,
    ):
        return '[REDACTED]', True
    for pattern in _SECRETS:
        # URLs may embed credentials, signed queries, or secrets in paths.
        # Conservative v1 intentionally redacts URLs in prose/code too.
        text = pattern.sub('[REDACTED]', text)
    return text, text != original


def visible_messages(document: dict) -> tuple[list[dict], int]:
    messages, excluded = [], 0
    for index, raw in enumerate(document.get('messages') or []):
        if not isinstance(raw, dict) or raw.get('role') not in ('user', 'assistant'):
            excluded += 1
            continue
        content = raw.get('content')
        if not isinstance(content, str) or _ENVELOPE.search(content):
            excluded += 1
            continue
        content, redacted = sanitize(content)
        timestamp = raw.get('timestamp')
        messages.append({
            'message_id': f'm{index}', 'role': raw['role'], 'content': content,
            'message_at': timestamp.isoformat() if isinstance(timestamp, datetime) else None,
            'redacted': redacted,
            # Observer historically stored only the first 5000 assistant chars.
            'possibly_source_truncated': raw['role'] == 'assistant' and len(raw['content']) >= 5000,
        })
    return messages, excluded


def revision(document: dict) -> str:
    return hashlib.sha256(json.dumps(
        document, sort_keys=True, default=str, separators=(',', ':'),
    ).encode()).hexdigest()
