"""Untrusted worker protocol; identity and authorization never come from frames."""
from dataclasses import dataclass
import json
import re

MAX_FRAME = 1024 * 1024
MAX_INPUT = 8 * MAX_FRAME
REQUEST_ID = re.compile(r'[a-zA-Z0-9_-]{1,64}\Z')


class ProtocolError(ValueError):
    pass


def decode(raw, *, limit=MAX_FRAME):
    if not isinstance(raw, (str, bytes)) or len(raw.encode('utf-8') if isinstance(raw, str) else raw) > limit:
        raise ProtocolError('Worker frame exceeds the limit')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError('Invalid worker JSON') from exc
    if not isinstance(value, dict):
        raise ProtocolError('Worker frame must be an object')
    return value


def worker_frame(raw):
    value = decode(raw)
    kind = value.get('type')
    if kind == 'text' and set(value) == {'type', 'text'} and isinstance(value['text'], str):
        return value
    if kind == 'tool_request' and set(value) == {'type', 'id', 'tool', 'arguments'}:
        if (isinstance(value['id'], str) and REQUEST_ID.fullmatch(value['id'])
                and isinstance(value['tool'], str) and len(value['tool']) <= 128
                and isinstance(value['arguments'], dict)):
            return value
    if kind == 'done' and set(value) == {'type'}:
        return value
    # No observer method calls, backend paths, principal overrides, OAuth tokens,
    # arbitrary artifacts or "approved" events cross this interface.
    raise ProtocolError('Unsupported worker frame')


def response_frame(value):
    if (not isinstance(value, dict) or set(value) != {'type', 'id', 'result'}
            or value.get('type') != 'tool_response'
            or not isinstance(value.get('id'), str)
            or not REQUEST_ID.fullmatch(value['id'])):
        raise ProtocolError('Invalid tool response')
    encoded = json.dumps(value, allow_nan=False)
    if len(encoded.encode()) > MAX_FRAME:
        raise ProtocolError('Tool response exceeds the limit')
    return encoded


@dataclass(frozen=True)
class RunAuthority:
    """Created by the authenticated backend, never deserialized from a worker."""
    run_id: str
    user_email: str
    allowed_tools: frozenset[str]
