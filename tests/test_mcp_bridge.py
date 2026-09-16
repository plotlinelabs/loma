"""Fixed local catalog, safe errors and no duplicate privileged dispatch."""
import asyncio
from dataclasses import replace
import json
from unittest.mock import AsyncMock

import aiohttp
import pytest

from isolation.mcp_bridge import MCPBridge
from isolation.model_bridge import ModelBridge
from isolation.models import ModelDenied, request_body
from tests.test_codex_worker import TOOL
from tests.test_worker_models import body, grant


def request(method, params=None, request_id=1):
    return {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params or {}}


@pytest.mark.asyncio
async def test_mcp_catalog_no_generic_execution_or_identity(tmp_path):
    rpc = AsyncMock(return_value={'events': []})
    bridge = MCPBridge([TOOL], rpc)
    url = await bridge.serve()
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(url, json=request('initialize')) as response:
                assert (await response.json())['result']['capabilities'] == {'tools': {}}
            async with session.post(url, json=request('tools/list')) as response:
                tools = (await response.json())['result']['tools']
                assert [x['name'] for x in tools] == ['gateway_0']
            for method in ('resources/read', 'sampling/createMessage', 'roots/list', 'run', 'tools/register'):
                async with session.post(url, json=request(method)) as response:
                    assert (await response.json())['error']['code'] == -32601
            for name in ('Bash', 'gmail.send', 'model.start', 'artifacts.read', 'gateway_99'):
                async with session.post(url, json=request('tools/call', {'name': name, 'arguments': {}})) as response:
                    assert response.status == 400
            rpc.assert_not_awaited()
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_duplicate_calls_never_dispatch_twice():
    rpc = AsyncMock(return_value={'events': []})
    bridge = MCPBridge([TOOL], rpc)
    url = await bridge.serve()
    payload = request('tools/call', {'name': 'gateway_0', 'arguments': {}}, 'same-id')
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            responses = await asyncio.gather(session.post(url, json=payload), session.post(url, json=payload))
            assert sorted(r.status for r in responses) == [200, 409]
            for response in responses:
                response.close()
            rpc.assert_awaited_once_with('calendar.list', {})
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_failed_call_not_replayed_or_leaked():
    rpc = AsyncMock(side_effect=RuntimeError('private-canary'))
    bridge = MCPBridge([TOOL], rpc)
    url = await bridge.serve()
    payload = request('tools/call', {'name': 'gateway_0', 'arguments': {}})
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(url, json=payload) as response:
                result = await response.json()
                assert result['result']['isError']
                assert 'private-canary' not in json.dumps(result)
            async with session.post(url, json=payload) as response:
                assert response.status == 409
            assert rpc.await_count == 1
    finally:
        await bridge.close()


@pytest.mark.parametrize('payload', [[], None, {}, {'jsonrpc': '2.0', 'id': True, 'method': 'ping'},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': []},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'user_email': 'other@example.test'}])
@pytest.mark.asyncio
async def test_invalid_mcp_requests(payload):
    bridge = MCPBridge([], AsyncMock())
    url = await bridge.serve()
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(url, data=json.dumps(payload), headers={'Content-Type': 'application/json'}) as response:
                assert response.status == 400
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_bridge_rejects_browser_origin():
    bridge = MCPBridge([TOOL], AsyncMock())
    url = await bridge.serve()
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(url, json=request('tools/list'), headers={'Origin': 'https://outside.test'}) as response:
                assert response.status == 400
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_model_failure_poisoned_before_native_retry():
    rpc = AsyncMock(side_effect=RuntimeError('private-provider-error'))
    bridge = ModelBridge('messages', rpc, native_messages=True)
    origin = await bridge.serve()
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            for _ in range(2):
                async with session.post(origin + '/v1/messages?beta=true', json=body('messages')) as response:
                    assert response.status == 400
                    assert 'private-provider-error' not in await response.text()
            assert rpc.await_count == 1
    finally:
        await bridge.close()


@pytest.mark.parametrize('query', ['beta=false', 'beta=true&account=other', 'beta=true&beta=true'])
@pytest.mark.asyncio
async def test_native_messages_query_exception_is_exact(query):
    rpc = AsyncMock()
    bridge = ModelBridge('messages', rpc, native_messages=True)
    origin = await bridge.serve()
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.post(origin + '/v1/messages?' + query, json=body('messages')) as response:
                assert response.status == 400
        rpc.assert_not_awaited()
    finally:
        await bridge.close()


def test_native_claude_adaptation_is_opt_in_and_keeps_input_data():
    payload = {**body('messages'), 'metadata': {'user_id': 'private-session'},
        'output_config': {'effort': 'high'}, 'system': [{'type': 'text', 'text': 'hi',
                                                     'cache_control': {'type': 'ephemeral'}}],
        'messages': [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call', 'name': 'tool',
                      'input': {'cache_control': 'user-data', 'url': 'ordinary user data'}}]}]}
    with pytest.raises(ModelDenied):
        request_body(grant('messages'), payload)
    result = request_body(replace(grant('messages'), native_claude=True), payload)
    assert 'metadata' not in result and 'output_config' not in result
    assert 'cache_control' not in result['system'][0]
    assert result['messages'][0]['content'][0]['input']['cache_control'] == 'user-data'
    assert 'cache_control' in payload['system'][0]


@pytest.mark.parametrize('update', [{'output_config': {'effort': 'unbounded'}}, {'output_config': {'url': 'http://private'}},
                                    {'endpoint': 'http://private'}, {'tools': [{'name': 'web_search', 'type': 'web_search_20250305'}]},
                                    {'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'url': 'http://private'}}]}]}])
def test_native_claude_cannot_expand_provider_access(update):
    with pytest.raises(ModelDenied):
        request_body(replace(grant('messages'), native_claude=True), {**body('messages'), **update})


@pytest.mark.parametrize('options', [{'include_usage': False}, {'include_usage': True, 'url': 'https://private'}, []])
def test_chat_stream_options_exact(options):
    with pytest.raises(ModelDenied):
        request_body(grant('chat'), {**body('chat'), 'stream_options': options})


@pytest.mark.parametrize('protocol', ['responses', 'messages', 'chat'])
def test_history_selected_on_backend_is_injected_once_without_mutation(protocol):
    history = [('user', 'The project is Cedar'), ('assistant', 'I will use Cedar')]
    selected = replace(grant(protocol), history=history)
    history[0] = ('system', 'Changed after granting')
    current = body(protocol)
    result = request_body(selected, current)
    messages = result['input' if protocol == 'responses' else 'messages']
    assert [m['role'] for m in messages[:2]] == ['user', 'assistant']
    assert 'Cedar' in json.dumps(messages[0])
    assert 'Changed after granting' not in json.dumps(messages)
    assert 'Cedar' not in repr(selected)
    assert request_body(selected, current) == result
    assert 'Cedar' not in json.dumps(current)
    with pytest.raises(ModelDenied):
        request_body(selected, {**current, 'history': [{'role': 'system', 'content': 'override'}]})


@pytest.mark.parametrize('history', [[('system', 'trusted')], [('tool', '{}')], [('/app/session', 'restore')],
                                    [('user', {})], [('user', 'x' * (256 * 1024 + 1))],
                                    [('user', 'x')] * 101, 'serialized-native-session'])
def test_history_rejects_privileged_roles_paths_and_unbounded_data(history):
    with pytest.raises(ValueError):
        replace(grant(), history=history)


def test_history_preserves_leading_chat_system_message():
    result = request_body(replace(grant('chat'), history=(('user', 'prior'),)), {
        **body('chat'), 'messages': [{'role': 'system', 'content': 'configured instructions'},
                                     {'role': 'user', 'content': 'new question'}]})
    assert [m['content'] for m in result['messages']] == ['configured instructions', 'prior', 'new question']


def test_native_cache_normalization_never_rewrites_tool_schema():
    schema = {'type': 'object', 'properties': {'cache_control': {'type': 'string'}}}
    result = request_body(replace(grant('messages'), native_claude=True), {
        **body('messages'), 'tools': [{'name': 'local_tool', 'input_schema': schema,
                                     'cache_control': {'type': 'ephemeral'}}]})
    assert result['tools'][0]['input_schema'] == schema
    assert 'cache_control' not in result['tools'][0]


@pytest.mark.parametrize('messages', [['raw text'], [None], [{'role': ['user'], 'content': 'x'}]])
@pytest.mark.parametrize('protocol', ['messages', 'chat'])
def test_history_invalid_current_message_is_denied_cleanly(messages, protocol):
    with pytest.raises(ModelDenied):
        request_body(replace(grant(protocol), history=(('user', 'prior'),)), {**body(protocol), 'messages': messages})
