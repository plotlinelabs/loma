"""Actual stdio protocol round trip through the HTTP service, no LLM mocks."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.test_history_search import search_rig
from tests.test_history_recall import capability, signed

SERVER = Path(__file__).parents[1] / 'mcp-servers/loma-recall/server.py'
spec = importlib.util.spec_from_file_location('recall_mcp', SERVER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.asyncio
async def test_actual_stdio_search_fetch_budget_and_auth(search_rig):
    _, _, key, claims, client = search_rig
    env = {'PATH': os.environ['PATH'], 'LOMA_RECALL_BACKEND_URL': str(client.make_url('/')).rstrip('/'),
        'LOMA_RECALL_CAPABILITY': signed(key, claims)}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert 'untrusted' in init.instructions
            listed = await session.list_tools()
            assert {t.name for t in listed.tools} == {'search_history', 'fetch_history'}
            for tool in listed.tools:
                assert tool.annotations.read_only_hint
                assert 'user_email' not in tool.input_schema['properties']
                assert 'capability' not in tool.input_schema['properties']
            result = await session.call_tool('search_history', {'query': 'RECALL-42'})
            data = json.loads(result.content[0].text)
            assert len(data['results']) == 2
            hit = data['results'][0]
            fetched = await session.call_tool('fetch_history', {'conversation_id': hit['conversation_id'],
                'anchor_message_id': hit['message_id']})
            assert 'CANARY_FOR_SEARCH' not in str(fetched)
            assert 'historical_untrusted_data_not_instructions' in str(fetched)
            for _ in range(6):
                await session.call_tool('search_history', {'query': 'no-match'})
            exhausted = await session.call_tool('search_history', {'query': 'RECALL-42'})
            assert 'recall_budget_exhausted' in str(exhausted)


@pytest.mark.asyncio
async def test_adapter_invalid_auth(search_rig):
    *_, client = search_rig
    adapter = module.RecallClient(str(client.make_url('/')).rstrip('/'), 'invalid')
    assert await adapter.call('search', {'query': 'RECALL-42'}) == {'error': 'unauthorized'}


@pytest.mark.parametrize('url', ['http://evil.example', 'https://user:pass@example.com',
    'https://example.com/path', 'https://example.com?secret=x', 'file:///tmp/test'])
def test_adapter_rejects_unsafe_configuration(url):
    with pytest.raises(ValueError):
        module.RecallClient(url, 'not-a-real-credential')
