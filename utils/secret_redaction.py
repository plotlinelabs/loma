"""Best-effort log redaction. Not a substitute for isolating credential access."""
import base64
import json
import re

REDACTED = "[REDACTED]"
_SECRET_KEYS = {
    "auth_token", "access_token", "refresh_token", "id_token", "client_secret",
    "api_key", "password", "password_hash", "local_auth", "authorization",
}
_BASE64 = re.compile(r"[A-Za-z0-9_-]{40,}={0,2}")
_ASSIGNMENT = re.compile(
    r'''(?i)((?:--(?:auth-token|api-key|access-token|refresh-token|client-secret)|'''
    r'''(?:auth_token|access_token|refresh_token|client_secret|api_key|password))'''
    r'''["']?\s*(?:[:=]\s*|\s+))(?:"[^"\n]*"|'[^'\n]*'|[^\s,;\]}"']+)'''
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
# Run-scoped execution capabilities and gateway proxy tokens must never
# survive into logs/transcripts.
_RUN_CAPABILITY = re.compile(r"\b(?:loma_run_v1|loma_mcpproxy)_[A-Za-z0-9_-]+")


def _redact_personal_token(match):
    text = match.group()
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
        if isinstance(data, dict) and {"email", "ts", "sig"} <= data.keys():
            return REDACTED
    except (ValueError, UnicodeError):
        pass
    return text


def redact_secrets(value):
    """Return a sanitized copy, including historical personal-tool token formats."""
    if isinstance(value, str):
        value = _RUN_CAPABILITY.sub(REDACTED, value)
        value = _BASE64.sub(_redact_personal_token, value)
        value = _ASSIGNMENT.sub(lambda m: m[1] + REDACTED, value)
        return _BEARER.sub("Bearer " + REDACTED, value)
    if isinstance(value, dict):
        return {k: REDACTED if str(k).lower().replace("-", "_") in _SECRET_KEYS
                else redact_secrets(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(v) for v in value]
    return value
