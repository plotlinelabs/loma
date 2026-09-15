"""Bounded provider-side SSE accounting; never consume worker-reported usage.

A receipt is token evidence, not a dollar invoice. Pricing and durable,
idempotent settlement remain the trusted account adapter's responsibility.
Unknown, truncated or contradictory streams never produce a receipt.
"""
from dataclasses import dataclass
import json

MAX_EVENT = 1024 * 1024
MAX_TOKENS = 100_000_000


@dataclass(frozen=True)
class UsageReceipt:
    protocol: str
    response_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def count(value):
    if type(value) is not int or not 0 <= value <= MAX_TOKENS:
        raise ValueError('Invalid provider token count')
    return value


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError('Invalid provider identifier')
    return value


class UsageCollector:
    def __init__(self, protocol):
        if protocol not in {'responses', 'messages', 'chat'}:
            raise ValueError('Unsupported usage protocol')
        self.protocol = protocol
        self.buffer = b''
        self.data = []
        self.event_size = 0
        self.failed = False
        self.terminal = False
        self.receipt = None
        self.started = None
        self.output = None
        self.final_delta = False

    def feed(self, chunk):
        if self.failed:
            return
        try:
            self.buffer += chunk
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                line = line.removesuffix(b'\r')
                self.event_size += len(line)
                if self.event_size > MAX_EVENT:
                    raise ValueError('Oversized provider event')
                if not line:
                    if self.data:
                        self._event(b'\n'.join(self.data).decode('utf-8'))
                    self.data, self.event_size = [], 0
                elif line.startswith(b'data:'):
                    self.data.append(line[5:].removeprefix(b' '))
            if len(self.buffer) + self.event_size > MAX_EVENT:
                raise ValueError('Oversized provider event')
        except (ValueError, TypeError, KeyError, RecursionError, AttributeError):
            self.failed = True
            self.buffer, self.data = b'', []
            self.receipt = None

    def finish(self):
        # Require a complete event separator and protocol terminal event, not
        # TCP EOF alone. Never guess usage after cancellation or missing data.
        if self.failed or self.buffer or self.data or not self.terminal:
            return None
        return self.receipt

    def _event(self, text):
        if self.terminal:
            # Responses sometimes also emits the generic SSE sentinel.
            if self.protocol == 'responses' and text == '[DONE]':
                return
            raise ValueError('Data after terminal event')
        if text == '[DONE]':
            if self.protocol != 'chat' or self.receipt is None:
                raise ValueError('Missing provider receipt')
            self.terminal = True
            return
        event = json.loads(text)
        if not isinstance(event, dict) or 'error' in event:
            raise ValueError('Provider error')
        kind = event.get('type')
        if kind in {'error', 'response.failed', 'response.incomplete'}:
            raise ValueError('Provider did not complete')
        if self.protocol == 'responses':
            if kind != 'response.completed':
                return
            response = event['response']
            if response.get('status') != 'completed':
                raise ValueError('Incomplete response')
            self.receipt = self._openai(response, response['usage'], 'input_tokens', 'output_tokens')
            self.terminal = True
        elif self.protocol == 'chat':
            usage = event.get('usage')
            if usage is not None:
                if self.receipt is not None or event.get('choices') != []:
                    raise ValueError('Not a final usage chunk')
                self.receipt = self._openai(event, usage, 'prompt_tokens', 'completion_tokens')
        elif kind == 'message_start':
            if self.started is not None:
                raise ValueError('Duplicate message start')
            msg = event['message']
            usage = msg['usage']
            self.started = (identifier(msg['id']), identifier(msg['model']),
                            count(usage['input_tokens']), count(usage.get('cache_read_input_tokens', 0)),
                            count(usage.get('cache_creation_input_tokens', 0)))
            self.output = count(usage['output_tokens'])
        elif kind == 'message_delta':
            if self.started is None:
                raise ValueError('Missing message start')
            usage = event['usage']
            output = count(usage['output_tokens'])
            if output < self.output:
                raise ValueError('Decreasing cumulative usage')
            # New input/cache fields cannot silently change the original bill.
            for field, original in zip(('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'), self.started[2:]):
                if field in usage and count(usage[field]) != original:
                    raise ValueError('Conflicting input usage')
            self.output = output
            self.final_delta = bool(event.get("delta", {}).get("stop_reason"))
        elif kind == 'message_stop':
            if self.started is None or self.output is None or not self.final_delta:
                raise ValueError('Missing message usage')
            response_id, model, inputs, reads, writes = self.started
            self.receipt = UsageReceipt(self.protocol, response_id, model, inputs, self.output, reads, writes)
            self.terminal = True

    def _openai(self, response, usage, inputs, outputs):
        incoming, outgoing = count(usage[inputs]), count(usage[outputs])
        if count(usage['total_tokens']) != incoming + outgoing:
            raise ValueError('Conflicting total usage')
        detail_key = 'input_tokens_details' if inputs == 'input_tokens' else 'prompt_tokens_details'
        details = usage.get(detail_key) or {}
        cached = count(details.get('cached_tokens', 0))
        if cached > incoming:
            raise ValueError('Cache tokens exceed input tokens')
        return UsageReceipt(self.protocol, identifier(response['id']), identifier(response['model']),
                            incoming, outgoing, cached)
