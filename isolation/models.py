"""Trusted streaming model relay for networkless workers.

Only server-created grants select the upstream, credentials and selected model.
Workers receive bounded response bytes, never credentials or provider headers.
This module is not a chat cutover: runtime/account adapters must construct the
budget and authorization callbacks and close the relay at the end of the run.
"""
import asyncio
import base64
from dataclasses import dataclass, field, replace
import json
import re
from urllib.parse import urlsplit
import uuid

import aiohttp

from isolation.protocol import MAX_FRAME
from isolation.usage import UsageCollector

CHUNK = 32 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
SCHEMAS = {'model.start': {'body'}, 'model.read': {'stream_id'}, 'model.close': {'stream_id'}}
FIELDS = {
    'responses': {'model', 'input', 'instructions', 'tools', 'tool_choice', 'stream',
                  'max_output_tokens', 'reasoning', 'text', 'parallel_tool_calls', 'store'},
    'messages': {'model', 'messages', 'system', 'tools', 'tool_choice', 'stream',
                 'max_tokens', 'temperature', 'top_p', 'stop_sequences', 'thinking'},
    'chat': {'model', 'messages', 'tools', 'tool_choice', 'stream', 'max_tokens',
             'max_completion_tokens', 'temperature', 'top_p', 'stop', 'parallel_tool_calls', 'stream_options'},
}
# Never allow the model service to become an alternate connector, remote-MCP,
# file-store or cross-conversation retrieval path. Text inside tool results is
# not interpreted as authority. New provider features need explicit adapters.
CONTENT_TYPES = {
    'responses': {'message', 'input_text', 'output_text', 'function_call', 'function_call_output'},
    'messages': {'text', 'tool_use', 'tool_result', 'thinking', 'redacted_thinking'},
    'chat': {'text', 'function'},
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
    native_codex: bool = False
    native_claude: bool = False
    history: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def __post_init__(self):
        if type(self.native_codex) is not bool or (self.native_codex and self.protocol != 'responses'):
            raise ValueError('Native Codex requires the Responses protocol')
        if type(self.native_claude) is not bool or (self.native_claude and self.protocol != 'messages'):
            raise ValueError('Native Claude requires the Messages protocol')
        # Only authenticated backend code may select conversation history.
        # Never restore a native HOME, account file, model-side conversation ID
        # or untrusted serialized tool call to resume a disposable worker.
        if not isinstance(self.history, (list, tuple)) or len(self.history) > 100:
            raise ValueError('Invalid conversation history')
        history = []
        for item in self.history:
            if (not isinstance(item, (list, tuple)) or len(item) != 2
                    or item[0] not in ('user', 'assistant') or not isinstance(item[1], str)):
                raise ValueError('Only text conversation history is supported')
            history.append(tuple(item))
        if sum(len(text.encode()) for _, text in history) > 256 * 1024:
            raise ValueError('Conversation history is too large')
        object.__setattr__(self, 'history', tuple(history))
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


def _without_cache(value, depth=0):
    if depth > 32:
        raise ModelDenied('Model content is too deeply nested')
    if isinstance(value, list):
        return [_without_cache(item, depth + 1) for item in value]
    if isinstance(value, dict):
        # Tool inputs are data, not provider fields; do not rewrite user data.
        return {key: child if key in {'input', 'arguments', 'input_schema'} else _without_cache(child, depth + 1)
                for key, child in value.items() if key != 'cache_control'}
    return value


def request_body(grant, body):
    if grant.native_claude and isinstance(body, dict):
        body = {key: value for key, value in body.items() if key != 'metadata'}
        output_config = body.get('output_config', {})
        if (not isinstance(output_config, dict) or set(output_config) - {'effort'}
                or output_config.get('effort', 'high') not in ('low', 'medium', 'high', 'max')):
            raise ModelDenied('Unsupported native effort configuration')
        # No provider session/cache namespaces supplied by a native runtime.
        body = {key: _without_cache(value) if key in {'messages', 'system', 'tools'} else value
                for key, value in body.items()}
        # The current worker API has no effort selection. Do not let a CLI
        # default silently change the backend model grant's spending contract.
        body.pop('output_config', None)

    if grant.native_codex and isinstance(body, dict):
        # Native clients attach local cache/session diagnostics. Do not forward
        # those as provider conversation identifiers or cache namespaces. No
        # endpoint/header/content fields are silently dropped by this adapter.
        body = {key: value for key, value in body.items()
                if key not in {'client_metadata', 'prompt_cache_key'}}
        if 'include' in body:
            if body['include'] != ['reasoning.encrypted_content']:
                raise ModelDenied('Unsupported native response expansion')
            body = {key: value for key, value in body.items() if key != 'include'}
        # The initial isolated adapter exposes only gateway functions. Native
        # local tools are withheld until their file/sandbox parity is verified.
        # Reject rather than silently translate unknown hosted capabilities.
        tools = body.get('tools', [])
        if not isinstance(tools, list):
            raise ModelDenied('Invalid native tool list')
        filtered = []
        for tool in tools:
            if not isinstance(tool, dict):
                raise ModelDenied('Invalid native tool')
            if tool.get('type') == 'custom' and tool.get('name') == 'apply_patch':
                continue
            if tool.get('type') != 'function':
                raise ModelDenied('Unsupported native tool')
            if isinstance(tool.get('name'), str) and re.fullmatch(r'gateway_[0-9]{1,2}', tool['name']):
                filtered.append(tool)
        body = {**body, 'tools': filtered}
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
    if 'stream_options' in body and body['stream_options'] != {'include_usage': True}:
        raise ModelDenied('Only final usage may be requested')
    if grant.protocol == 'responses':
        if 'input' not in body:
            raise ModelDenied('Model input is required')
        _content(body['input'], grant.protocol)
        body['store'] = False
        cap = 'max_output_tokens'
    else:
        if not isinstance(body.get('messages'), list) or not body['messages']:
            raise ModelDenied('Messages are required')
        roles = ('user', 'assistant') if grant.protocol == 'messages' else ('system', 'developer', 'user', 'assistant', 'tool')
        if any(not isinstance(message, dict) or message.get('role') not in roles for message in body['messages']):
            raise ModelDenied('Invalid message role or structure')
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
    if grant.history:
        if grant.protocol == 'responses':
            current = body['input']
            if isinstance(current, str):
                current = [{'role': 'user', 'content': [{'type': 'input_text', 'text': current}]}]
            if not isinstance(current, list):
                raise ModelDenied('Unsupported response history input')
            prior = [{'role': role, 'content': [{'type': 'input_text' if role == 'user' else 'output_text',
                                               'text': text}]} for role, text in grant.history]
            body['input'] = prior + current
        else:
            current = body['messages']
            leading = 0
            if grant.protocol == 'chat':
                while leading < len(current) and current[leading].get('role') == 'system':
                    leading += 1
            body['messages'] = (current[:leading] + [{'role': role, 'content': text}
                               for role, text in grant.history] + current[leading:])
        if len(json.dumps(body, allow_nan=False).encode()) > MAX_FRAME - 1024:
            raise ModelDenied('Model request including history exceeds the limit')
    return body


class ModelRelay:
    def __init__(self, authority, grant, *, session, authorize, audit, reserve, settle, record_usage=None, resolve_headers=None):
        """Callbacks are trusted backend adapters, never selected by the worker.

        reserve(authority, call_id, grant, body) durably reserves spend BEFORE
        dispatch. settle(authority, call_id, outcome) persists completion metadata;
        outcome is NOT a billing receipt and must never itself release a hold.
        record_usage(authority, call_id, receipt_or_none), when configured,
        persists provider-side token evidence before settlement. Missing evidence
        must retain the reservation. Neither callback receives worker usage claims.
        Pricing and durable idempotent settlement remain the budget adapter's job.
        resolve_headers(authority), if supplied, refreshes credentials for the
        pinned account before EACH call. It cannot change model, endpoint, budget
        or history. Errors never fall back to the grant's original credentials.
        """
        if any(not callable(f) for f in (authorize, audit, reserve, settle)):
            raise ValueError('Model policy, audit and budget callbacks are required')
        if record_usage is not None and not callable(record_usage):
            raise ValueError("Usage recorder must be a trusted callback")
        if resolve_headers is not None and not callable(resolve_headers):
            raise ValueError("Credential resolver must be a trusted callback")
        self.resolve_headers = resolve_headers
        self.record_usage = record_usage
        self.usage = None
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
        response, stream_id, usage = self.response, self.stream_id, self.usage
        self.usage = None
        self.response, self.stream_id = None, None
        if response is not None:
            response.close()
        if stream_id is not None:
            # Clear first: failed accounting blocks this relay rather than
            # settling twice or replaying a potentially billable request.
            try:
                if self.record_usage is not None:
                    receipt = usage.finish() if usage is not None and outcome == "stream_ended" else None
                    await self.record_usage(self.authority, stream_id, receipt)
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
                    headers = self.grant.headers
                    if self.resolve_headers is not None:
                        try:
                            async with asyncio.timeout(30):
                                headers = await self.resolve_headers(authority)
                            # Reuse grant validation without persisting refreshed
                            # secrets on the grant or serializing them to workers.
                            headers = replace(self.grant, headers=headers).headers
                            if not headers:
                                raise ValueError('Empty account credentials')
                        except Exception:
                            self.closed = True
                            raise ModelDenied('Account credentials unavailable') from None
                        # Refresh may yield while an account/user is revoked.
                        await self._authorized(authority)
                    call_id = uuid.uuid4().hex
                    await self.audit(authority, {'tool': tool, 'stage': 'requested', 'call_id': call_id})
                    await self.reserve(authority, call_id, self.grant, body)
                    self.calls += 1
                    self.stream_id, self.bytes_read = call_id, 0
                    self.usage = UsageCollector(self.grant.protocol)
                    await self._authorized(authority)
                    # Do not inherit ambient proxies, cookies or redirect to a
                    # worker-chosen destination. The caller owns session lifetime.
                    if self.session.trust_env or not isinstance(self.session.cookie_jar, aiohttp.DummyCookieJar):
                        raise ModelDenied('Model relay requires a private cookie-free session')
                    self.response = await self.session.post(self.grant.endpoint, json=body,
                        headers=headers, allow_redirects=False,
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
                if chunk:
                    self.usage.feed(chunk)
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
