"""Actual OpenCode binary with synthetic provider replies only."""
from dataclasses import replace
import json
import shutil

from aiohttp import web
import pytest

from isolation.opencode_worker import OpenCodeRuntime
from tests.test_worker_models import AUTH, fixture, grant, close


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('opencode'), reason='Native OpenCode binary required')
async def test_native_opencode_reply(tmp_path):
    requests = []
    async def provider(request):
        requests.append(await request.json())
        data = [{'id': 'chat_test', 'object': 'chat.completion.chunk', 'model': 'test-model',
                 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'Synthetic response'}, 'finish_reason': None}]},
                {'id': 'chat_test', 'object': 'chat.completion.chunk', 'model': 'test-model',
                 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}]
        return web.Response(body=(''.join('data: ' + json.dumps(x) + '\n\n' for x in data) + 'data: [DONE]\n\n').encode(),
                            content_type='text/event-stream')
    relay, server, session, callbacks = await fixture(provider)
    relay.grant = replace(grant('chat'), max_output_tokens=8192)
    async def rpc(tool, args):
        return await relay(AUTH, tool, args)
    output = []
    async def emit(text): output.append(text)
    runtime = OpenCodeRuntime(tmp_path / 'runtime', rpc, emit, executable=shutil.which('opencode'))
    try:
        await runtime.start('test-model', 'Answer briefly.', [])
        await runtime.turn('hello', timeout=35)
        assert ''.join(output) == 'Synthetic response'
        assert len(requests) == 1
    finally:
        await runtime.close()
        await close(relay, server, session)
