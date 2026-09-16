"""Provider usage, not model/worker claims; no real network or accounts."""
import json
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from isolation.usage import UsageCollector, MAX_EVENT
from tests.test_worker_models import AUTH, body, fixture, close


def frame(value):
    return b'data: ' + (value if isinstance(value, str) else json.dumps(value)).encode() + b'\n\n'


def events(protocol):
    usage = {'input_tokens': 10, 'output_tokens': 3, 'total_tokens': 13,
             'input_tokens_details': {'cached_tokens': 2}}
    if protocol == 'responses':
        return [{'type': 'response.completed', 'response': {'id': 'r-1', 'model': 'test-model',
                 'status': 'completed', 'usage': usage}}]
    if protocol == 'chat':
        return [{'id': 'r-1', 'model': 'test-model', 'choices': [],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13,
                           'prompt_tokens_details': {'cached_tokens': 2}}}, '[DONE]']
    return [{'type': 'message_start', 'message': {'id': 'r-1', 'model': 'test-model',
             'usage': {'input_tokens': 10, 'output_tokens': 1, 'cache_read_input_tokens': 2,
                       'cache_creation_input_tokens': 4}}},
            {'type': 'message_delta', 'delta': {'stop_reason': None}, 'usage': {'output_tokens': 2}},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 3}},
            {'type': 'message_stop'}]


@pytest.mark.parametrize('protocol', ['responses', 'messages', 'chat'])
@pytest.mark.parametrize('size', [1, 7, 65536])
def test_fragmented_provider_usage(protocol, size):
    collector = UsageCollector(protocol)
    raw = b''.join(frame(event) for event in events(protocol))
    for i in range(0, len(raw), size):
        collector.feed(raw[i:i + size])
    receipt = collector.finish()
    assert receipt.input_tokens == 10 and receipt.output_tokens == 3
    assert receipt.cache_read_tokens == 2
    assert receipt.cache_write_tokens == (4 if protocol == 'messages' else 0)
    assert receipt.response_id == 'r-1' and receipt.model == 'test-model'


@pytest.mark.parametrize('protocol', ['responses', 'messages', 'chat'])
def test_eof_is_not_a_receipt(protocol):
    collector = UsageCollector(protocol)
    collector.feed(b''.join(frame(event) for event in events(protocol))[:-2])
    assert collector.finish() is None
    collector = UsageCollector(protocol)
    collector.feed(frame({'text': 'I used 13 tokens', 'usage': None}))
    assert collector.finish() is None


@pytest.mark.parametrize('protocol', ['responses', 'messages', 'chat'])
def test_error_or_duplicate_after_terminal_discards_receipt(protocol):
    collector = UsageCollector(protocol)
    raw = b''.join(frame(event) for event in events(protocol))
    collector.feed(raw)
    assert collector.finish() is not None
    collector.feed(frame({'type': 'error', 'error': {'message': 'failed'}}))
    assert collector.finish() is None
    collector = UsageCollector(protocol)
    collector.feed(raw + raw)
    assert collector.finish() is None


@pytest.mark.parametrize('value', [-1, True, 1.1, '10', None, 100_000_001])
def test_invalid_counts_do_not_authorize_settlement(value):
    event = events('responses')[0]
    event['response']['usage']['input_tokens'] = value
    collector = UsageCollector('responses')
    collector.feed(frame(event))
    assert collector.finish() is None


def test_totals_cache_and_incomplete_response():
    for key, value in [('total_tokens', 12), ('input_tokens_details', {'cached_tokens': 11})]:
        event = events('responses')[0]
        event['response']['usage'][key] = value
        collector = UsageCollector('responses')
        collector.feed(frame(event))
        assert collector.finish() is None
    event['response']['status'] = 'incomplete'
    collector = UsageCollector('responses')
    collector.feed(frame(event))
    assert collector.finish() is None


def test_messages_require_final_delta_and_monotonic_output():
    for stream in ([events('messages')[0], events('messages')[-1]],
                   [*events('messages')[:-1], {'type': 'message_delta', 'usage': {'output_tokens': 1}}, events('messages')[-1]]):
        collector = UsageCollector('messages')
        collector.feed(b''.join(frame(event) for event in stream))
        assert collector.finish() is None


def test_bounded_bad_data_and_crlf():
    for data in [b'data: ' + b'x' * (MAX_EVENT + 1), b'data: \xff\n\n', frame([]), b'data: invalid\n\n']:
        collector = UsageCollector('responses')
        collector.feed(data)
        assert collector.finish() is None and collector.failed
    collector = UsageCollector('responses')
    collector.feed(frame(events('responses')[0]).replace(b'\n', b'\r\n'))
    assert collector.finish() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('complete', [True, False])
async def test_relay_records_provider_usage_once(complete):
    raw = frame(events('responses')[0]) if complete else frame({'type': 'response.output_text.delta', 'delta': 'hi'})
    async def provider(request):
        return web.Response(body=raw, content_type='text/event-stream')
    record = AsyncMock()
    relay, server, session, callbacks = await fixture(provider, record_usage=record)
    try:
        started = await relay(AUTH, 'model.start', {'body': body()})
        while not (await relay(AUTH, 'model.read', {'stream_id': started['stream_id']}))['eof']:
            pass
        assert record.await_count == 1
        authority, call_id, receipt = record.call_args.args
        assert authority == AUTH and call_id == started['stream_id']
        assert (receipt is not None) == complete
        assert callbacks['settle'].await_count == 1
        await relay.close()
        assert record.await_count == 1
    finally:
        await close(relay, server, session)


@pytest.mark.asyncio
async def test_accounting_failure_closes_relay_without_replay():
    async def provider(request):
        return web.Response(body=frame(events('responses')[0]), content_type='text/event-stream')
    record = AsyncMock(side_effect=RuntimeError('ledger unavailable'))
    relay, server, session, callbacks = await fixture(provider, record_usage=record)
    try:
        started = await relay(AUTH, 'model.start', {'body': body()})
        with pytest.raises(RuntimeError, match='ledger unavailable'):
            while not (await relay(AUTH, 'model.read', {'stream_id': started['stream_id']}))['eof']:
                pass
        assert relay.closed and record.await_count == 1
        assert callbacks['settle'].await_count == 0
    finally:
        await close(relay, server, session)
