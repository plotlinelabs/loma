"""Local native CLI with synthetic providers; no connected accounts."""
from dataclasses import replace
import json
import shutil
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from isolation.claude_worker import ClaudeRuntime
from tests.test_codex_worker import TOOL, sse
from tests.test_worker_models import AUTH, fixture, grant, close


def message_events(text):
    return [
        {'type': 'message_start', 'message': {'id': 'msg_test', 'type': 'message', 'role': 'assistant',
          'model': 'claude-sonnet-4-6', 'content': [], 'stop_reason': None, 'stop_sequence': None,
          'usage': {'input_tokens': 10, 'output_tokens': 0}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
         'usage': {'output_tokens': 5}}, {'type': 'message_stop'}]


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('claude'), reason='Native Claude binary required')
async def test_native_claude_reply(tmp_path):
    requests = []
    async def provider(request):
        requests.append(await request.json())
        return web.Response(body=sse(message_events('Synthetic response')), content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = replace(grant('messages'), model='claude-sonnet-4-6', max_output_tokens=8192, native_claude=True)
    async def rpc(tool, args):
        if tool.startswith('model.'):
            return await relay(AUTH, tool, args)
        raise AssertionError(tool)
    output = []
    async def emit(text): output.append(text)
    runtime = ClaudeRuntime(tmp_path / 'runtime', rpc, emit, executable=shutil.which('claude'))
    try:
        await runtime.start('claude-sonnet-4-6', 'Answer briefly.', [])
        await runtime.turn('hello', timeout=35)
        assert ''.join(output) == 'Synthetic response'
        assert len(requests) == 1
    finally:
        await runtime.close()
        await close(relay, server, session)
