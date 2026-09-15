"""Trusted streaming model relay for networkless workers.

Only server-created grants select the upstream, credentials and selected model.
Workers receive bounded response bytes, never credentials or provider headers.
This module is not a chat cutover: runtime/account adapters must construct the
budget and authorization callbacks and close the relay at the end of the run.
"""
import asyncio
import base64
from dataclasses import dataclass, field
import json
import re
from urllib.parse import urlsplit
import uuid

import aiohttp

from isolation.protocol import MAX_FRAME

CHUNK = 32 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
SCHEMAS = {'model.start': {'body'}, 'model.read': {'stream_id'}, 'model.close': {'stream_id'}}
FIELDS = {
    'responses': {'model', 'input', 'instructions', 'tools', 'tool_choice', 'stream',
                  'max_output_tokens', 'reasoning', 'text', 'parallel_tool_calls', 'store'},
    'messages': {'model', 'messages', 'system', 'tools', 'tool_choice', 'stream',
                 'max_tokens', 'temperature', 'top_p', 'stop_sequences', 'thinking'},
    'chat': {'model', 'messages', 'tools', 'tool_choice', 'stream', 'max_tokens',
             'max_completion_tokens', 'temperature', 'top_p', 'stop', 'parallel_tool_calls'},
}
# Never allow the model service to become an alternate connector, remote-MCP,
# file-store or cross-conversation retrieval path. Text inside tool results is
# not interpreted as authority. New provider features need explicit adapters.
CONTENT_TYPES = {
    'responses': {'message', 'input_text', 'output_text', 'function_call', 'function_call_output'},
    'messages': {'text', 'tool_use', 'tool_result', 'thinking', 'redacted_thinking'},
    'chat': {'text'},
}


class ModelDenied(ValueError):
    pass


@dataclass(frozen=True)
class ModelGrant:
    protocol: str
    endpoint: str
    model: str
    headers: dict = field(repr=False)
    max_output_tokens: int = 8192
    max_calls: int = 32

    def __post_init__(self):
        target = urlsplit(self.endpoint)
        if (self.protocol not in FIELDS or target.scheme != 'https' or not target.hostname
                or target.username or target.password or target.query or target.fragment):
            raise ValueError('A fixed HTTPS model endpoint is required')
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 200:
            raise ValueError('A fixed model is required')
        if (type(self.max_calls) is not int or not 1 <= self.max_calls <= 128
                or type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 131072):
            raise ValueError('Invalid model limits')
        if not isinstance(self.headers, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) or '\r' in k + v or '\n' in k + v
                for k, v in self.headers.items()):
            raise ValueError('Invalid provider credentials')
        # Copy: callers must replace the grant rather than mutate shared headers.
        object.__setattr__(self, 'headers', dict(self.headers))


def _content(value, protocol, depth=0):
    if depth > 32:
        raise ModelDenied('Model content is too deeply nested')
    if isinstance(value, list):
        for item in value:
            _content(item, protocol, depth + 1)
    elif isinstance(value, dict):
        if any(key in value for key in ('file_id', 'image_url', 'file_url', 'url', 'source',
                                        'previous_response_id', 'conversation', 'server_url')):
            raise ModelDenied('Remote content references are not supported')
        if 'type' in value and value['type'] not in CONTENT_TYPES[protocol]:
            raise ModelDenied('Unsupported model content')
        for key, child in value.items():
            # Tool input dictionaries and JSON output strings are ordinary data,
            # not provider-recognized content. Still bounded by the request size.
            if key not in {'input', 'arguments'}:
                _content(child, protocol, depth + 1)


def request_body(grant, body):
    if not isinstance(body, dict) or set(body) - FIELDS[grant.protocol]:
        raise ModelDenied('Unsupported model request fields')
    try:
        encoded = json.dumps(body, allow_nan=False)
        if len(encoded.encode()) > MAX_FRAME - 1024:
            raise ModelDenied('Model request is too large')
        body = json.loads(encoded)  # no shared mutable request objects
    except (TypeError, ValueError, RecursionError):
        raise ModelDenied('Invalid model request') from None
    if body.get('model') != grant.model or body.get('stream') is not True:
        raise ModelDenied('Model and streaming mode are fixed for this run')
    if grant.protocol == 'responses':
        if 'input' not in body:
            raise ModelDenied('Model input is required')
        _content(body['input'], grant.protocol)
        body['store'] = False
        cap = 'max_output_tokens'
    else:
        if not isinstance(body.get('messages'), list) or not body['messages']:
            raise ModelDenied('Messages are required')
        _content(body['messages'], grant.protocol)
        if grant.protocol == 'messages':
            _content(body.get('system'), grant.protocol)
        cap = 'max_tokens' if grant.protocol == 'messages' else 'max_completion_tokens'
        if grant.protocol == 'chat' and 'max_tokens' in body:
            cap = 'max_tokens'
            if 'max_completion_tokens' in body:
                raise ModelDenied('Conflicting output limits')
    limit = body.get(cap, grant.max_output_tokens)
    if type(limit) is not int or not 1 <= limit <= grant.max_output_tokens:
        raise ModelDenied('Output limit exceeds the run grant')
    body[cap] = limit
    tools = body.get('tools', [])
    if not isinstance(tools, list) or len(tools) > 128:
        raise ModelDenied('Invalid model tool definitions')
    for tool in tools:
        if not isinstance(tool, dict):
            raise ModelDenied('Invalid model tool definition')
        if grant.protocol == 'messages':
            if set(tool) - {'name', 'description', 'input_schema', 'type'} or tool.get('type', 'custom') != 'custom':
                raise ModelDenied('Only local function tools are supported')
            name = tool.get('name')
        elif grant.protocol == 'responses':
            if tool.get('type') != 'function' or set(tool) - {'type', 'name', 'description', 'parameters', 'strict'}:
                raise ModelDenied('Only local function tools are supported')
            name = tool.get('name')
        else:
            if set(tool) != {'type', 'function'} or tool.get('type') != 'function' or not isinstance(tool['function'], dict):
                raise ModelDenied('Only local function tools are supported')
            if set(tool['function']) - {'name', 'description', 'parameters', 'strict'}:
                raise ModelDenied('Invalid local function tool')
            name = tool['function'].get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name):
            raise ModelDenied('Invalid local function name')
    return body


class ModelRelay:
    def __init__(self, authority, grant, *, session, authorize, audit, reserve, settle):
        """Callbacks are trusted backend adapters, never selected by the worker.

        reserve(authority, call_id, grant, body) durably reserves spend BEFORE
        dispatch. settle(authority, call_id, outcome) persists completion metadata;
        outcome is NOT a billing receipt and must never itself release a hold.
        Authoritative usage reconciliation remains the budget adapter's job.
        """
        if any(not callable(f) for f in (authorize, audit, reserve, settle)):
            raise ValueError('Model policy, audit and budget callbacks are required')
        self.authority, self.grant, self.session = authority, grant, session
        self.authorize, self.audit, self.reserve, self.settle = authorize, audit, reserve, settle
        self.lock = asyncio.Lock()
        self.response = None
        self.stream_id = None
        self.bytes_read = 0
        self.calls = 0
        self.closed = False

    async def _authorized(self, authority):
        if authority != self.authority or not await self.authorize(authority):
            raise ModelDenied('Model access is no longer valid')

    async def _finish(self, outcome):
        response, stream_id = self.response, self.stream_id
        self.response, self.stream_id = None, None
        if response is not None:
            response.close()
        if stream_id is not None:
            # Clear first: failed accounting blocks this relay rather than
            # settling twice or replaying a potentially billable request.
            try:
                await self.settle(self.authority, stream_id, outcome)
            except BaseException:
                self.closed = True
                raise

    async def __call__(self, authority, tool, arguments):
        async with self.lock:
            if self.closed:
                raise ModelDenied('Model relay is closed')
            try:
                await self._authorized(authority)
                if tool not in SCHEMAS or tool not in authority.allowed_tools or not isinstance(arguments, dict) or set(arguments) != SCHEMAS[tool]:
                    raise ModelDenied('Invalid model operation')
                if tool == 'model.start':
                    if self.stream_id is not None or self.calls >= self.grant.max_calls:
                        raise ModelDenied('Model call limit reached or stream still active')
                    body = request_body(self.grant, arguments['body'])
                    call_id = uuid.uuid4().hex
                    await self.audit(authority, {'tool': tool, 'stage': 'requested', 'call_id': call_id})
                    await self.reserve(authority, call_id, self.grant, body)
                    self.calls += 1
                    self.stream_id, self.bytes_read = call_id, 0
                    await self._authorized(authority)
                    # Do not inherit ambient proxies, cookies or redirect to a
                    # worker-chosen destination. The caller owns session lifetime.
                    if self.session.trust_env or not isinstance(self.session.cookie_jar, aiohttp.DummyCookieJar):
                        raise ModelDenied('Model relay requires a private cookie-free session')
                    self.response = await self.session.post(self.grant.endpoint, json=body,
                        headers=self.grant.headers, allow_redirects=False,
                        timeout=aiohttp.ClientTimeout(total=300, sock_read=60))
                    if self.response.status != 200 or self.response.content_type != 'text/event-stream':
                        raise ModelDenied('Model provider did not return a valid stream')
                    await self._authorized(authority)
                    return {'stream_id': call_id, 'content_type': 'text/event-stream'}
                if not isinstance(arguments['stream_id'], str) or arguments['stream_id'] != self.stream_id:
                    raise ModelDenied('Unknown model stream')
                if tool == 'model.close':
                    await self._finish('interrupted')
                    return {'closed': True}
                chunk = await self.response.content.read(CHUNK)
                self.bytes_read += len(chunk)
                if self.bytes_read > MAX_RESPONSE:
                    raise ModelDenied('Model response exceeds the limit')
                await self._authorized(authority)
                if not chunk:
                    await self._finish('stream_ended')
                return {'data': base64.b64encode(chunk).decode(), 'eof': not chunk}
            except BaseException:
                await self._finish('unknown')
                raise

    async def close(self):
        async with self.lock:
            self.closed = True
            await self._finish('interrupted')
