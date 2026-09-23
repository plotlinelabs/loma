"""Exact official CLI browser authorization endpoints; shared with image smoke tests."""
import re
from urllib.parse import urlsplit

AUTHORIZATION_ENDPOINTS = frozenset({
    ('claude.ai', '/oauth/authorize'),
    ('platform.claude.com', '/oauth/authorize'),
    ('console.anthropic.com', '/oauth/authorize'),
    ('claude.com', '/cai/oauth/authorize'),
})


def authorization_url(text):
    # CLI terminal output is never sent to a browser. Only its exact HTTPS OAuth
    # link on Anthropic's allowlist survives (no terminal escapes or HTML).
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    for raw in re.findall(r'https://[^\s\x1b<>]+', clean):
        parsed = urlsplit(raw)
        if ((parsed.hostname, parsed.path) in AUTHORIZATION_ENDPOINTS and not parsed.username
                and not parsed.password and parsed.port in (None, 443)
                and 'code_challenge=' in parsed.query and 'state=' in parsed.query):
            return raw
    return None

