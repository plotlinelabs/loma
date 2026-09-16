"""Remote utility transformations never get tools, files, or chat history."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from isolation import utility
from isolation.context import ConversationContext, UtilityContext
from isolation.gateway import GatewayDenied
from isolation.protocol import RunAuthority
from tests.test_remote_entrypoint import synthetic_deployment
from tests.test_bounded_work import db, OWNER, OTHER  # noqa: F401


def fake_db(owner='owner@example.test'):
    database = MagicMock()
    database.conversations.find_one = AsyncMock(return_value={'metadata': {'user_name': owner}})
    return database


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv('LOMA_REMOTE_WORKERS', 'on')
    monkeypatch.setattr(utility, 'load_deployment', lambda: synthetic_deployment(
        tmp_path, default_model='test/model'))
    # A utility must never escape to any local model subprocess.
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(side_effect=AssertionError('local CLI')))


@pytest.mark.asyncio
async def test_assembly_has_no_tools_history_or_files(configured, monkeypatch):
    seen = {}
    async def stream(**kwargs):
        seen.update(kwargs)
        yield ' A short'
        yield ' title '
    monkeypatch.setattr(utility, 'stream_run', stream)
    database = fake_db()
    assert await utility.complete('Make a title', db=database, conversation_id='c') == 'A short title'
    assert seen['owner'] == 'owner@example.test'
    assert seen['utility'] is True
    assert seen['allowed_tools'] == frozenset()
    assert seen['grant'].history == ()
    assert seen['grant'].max_calls == seen['budget_spec'].max_calls == 2
    assert seen['grant'].max_output_tokens == 8192
    assert seen['cancelled'].is_set()
    assert 'attachments' not in seen and 'input_ids' not in seen


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [
    {'db': None}, {'conversation_id': None}, {'conversation_id': ''},
    {'message': ''}, {'message': 'x' * (32769)}, {'timeout': 0}, {'timeout': 121},
    {'db': fake_db(None)}, {'db': fake_db(42)},
])
async def test_bad_ingress_rejected(configured, monkeypatch, kwargs):
    stream = MagicMock(side_effect=AssertionError('must not start'))
    monkeypatch.setattr(utility, 'stream_run', stream)
    with pytest.raises((GatewayDenied, ValueError)):
        await utility.complete(**{'message': 'title', 'db': fake_db(), 'conversation_id': 'c', **kwargs})
    stream.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('chunks', [[], [''], [{'type': 'file'}], ['x' * 32769]])
async def test_invalid_output_closes_stream(configured, monkeypatch, chunks):
    closed = []
    async def stream(**kwargs):
        try:
            for chunk in chunks:
                yield chunk
        finally:
            closed.append(True)
    monkeypatch.setattr(utility, 'stream_run', stream)
    with pytest.raises((ValueError, GatewayDenied)):
        await utility.complete('title', db=fake_db(), conversation_id='c')
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_timeout_and_cancellation_close_stream(configured, monkeypatch, cancel):
    entered, closed = asyncio.Event(), asyncio.Event()
    async def stream(**kwargs):
        try:
            entered.set()
            await asyncio.Event().wait()
            yield 'never'
        finally:
            closed.set()
    monkeypatch.setattr(utility, 'stream_run', stream)
    task = asyncio.create_task(utility.complete('title', db=fake_db(), conversation_id='c', timeout=.05))
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_title_topic_and_compression_use_remote(configured, monkeypatch):
    from api.routes import _generate_title_llm, _classify_topic_llm, _VALID_TOPICS
    from slack_app.brevity import maybe_compress_slack_reply
    complete = AsyncMock(return_value='"A remote title"')
    monkeypatch.setattr(utility, 'complete', complete)
    database = fake_db()
    assert await _generate_title_llm('prompt', db=database, conversation_id='c') == 'A remote title'
    assert complete.await_args.kwargs == {'db': database, 'conversation_id': 'c', 'timeout': 45}
    topic = next(iter(_VALID_TOPICS))
    complete.return_value = topic
    assert await _classify_topic_llm('prompt', db=database, conversation_id='c') == topic
    complete.return_value = 'not a topic'
    assert await _classify_topic_llm('prompt', db=database, conversation_id='c') == 'other'
    complete.return_value = 'Short reply'
    assert await maybe_compress_slack_reply('Long reply ' * 100, db=database, conversation_id='c') == 'Short reply'
    assert complete.await_args.kwargs['conversation_id'] == 'c'


@pytest.mark.asyncio
async def test_failure_keeps_fallbacks_without_local_cli(configured, monkeypatch):
    from api.routes import _generate_title_llm, _classify_topic_llm
    from slack_app.brevity import maybe_compress_slack_reply
    monkeypatch.setattr(utility, 'complete', AsyncMock(side_effect=GatewayDenied('revoked')))
    assert await _generate_title_llm('Keep these title words') == 'Keep these title words'
    assert await _classify_topic_llm('prompt') == 'other'
    original = 'Long reply ' * 100
    assert await maybe_compress_slack_reply(original) == original
    asyncio.create_subprocess_exec.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['running', 'completed', 'error', 'interrupted', None])
async def test_utility_accepts_stored_states_without_history(db, status):
    await db.conversations.insert_one({'conversation_id': 'c', 'source': 'dashboard',
        'metadata': {'user_name': OWNER}, 'status': status,
        'messages': [{'role': 'user', 'content': 'SECRET OLD HISTORY'}]})
    auth = RunAuthority('run', OWNER, frozenset())
    context = UtilityContext(db, auth, 'c', cancelled=asyncio.Event(), check_access=AsyncMock(return_value=True))
    await context.load('Transform just this')
    assert context.history == ()
    assert await context.authorize(auth)
    if status != 'running':
        normal = ConversationContext(db, auth, 'c', cancelled=asyncio.Event(), check_access=AsyncMock(return_value=True))
        with pytest.raises(GatewayDenied):
            await normal.load('Normal run still needs running')
    assert (await db.conversations.find_one({'conversation_id': 'c'}))['status'] == status


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['owner', 'deleted', 'user', 'scope'])
async def test_utility_revocation_and_scope_changes(db, mutation):
    await db.conversations.insert_one({'conversation_id': 'c', 'source': 'task', 'metadata': {'user_name': OWNER}})
    auth = RunAuthority('run', OWNER, frozenset())
    context = UtilityContext(db, auth, 'c', cancelled=asyncio.Event(), check_access=AsyncMock(return_value=True))
    await context.load('Title')
    if mutation == 'user':
        await db.users.update_one({'email': OWNER}, {'$set': {'status': 'inactive'}})
    else:
        update = {'owner': {'metadata.user_name': OTHER}, 'deleted': {'deleted': True}, 'scope': {'project_id': 'changed'}}[mutation]
        await db.conversations.update_one({'conversation_id': 'c'}, {'$set': update})
    assert not await context.authorize(auth)


@pytest.mark.asyncio
async def test_real_assembly_denies_tools_and_omits_history(db, tmp_path, monkeypatch):
    from isolation import run as assembly
    from tests.test_worker_run import args, seed
    await seed(db, [{'role': 'user', 'content': 'private old history'}])
    await db.conversations.update_one({'conversation_id': 'current'}, {'$set': {'status': 'completed'}})
    seen = {}
    async def worker(**kwargs):
        seen.update(kwargs)
        assert kwargs['input']['tools'] == []
        assert kwargs['execute_tool'].models.grant.history == ()
        for name in ('workspace.exec', 'gmail.search', 'gmail.propose_send', 'artifacts.list', 'skills.list'):
            with pytest.raises(GatewayDenied):
                await kwargs['execute_tool'](kwargs['authority'], name, {})
        yield 'Utility title'
    monkeypatch.setattr(assembly, 'stream_worker', worker)
    assert [x async for x in assembly.stream_run(**args(db, tmp_path,
        allowed_tools=frozenset(), utility=True))] == ['Utility title']
    saved = await db.isolated_model_budgets.find_one({'_id': seen['authority'].run_id})
    assert not saved['active']
    assert await db.isolated_artifact_downloads.count_documents({}) == 0
    assert seen['session'].closed


@pytest.mark.asyncio
@pytest.mark.parametrize('overrides', [
    {'allowed_tools': frozenset({'gmail.search'})},
    {'attachments': (object(),)}, {'input_ids': ('file',)}, {'utility': 'yes'},
])
async def test_utility_cannot_enable_tools_or_files(tmp_path, overrides):
    from isolation import run as assembly
    from tests.test_worker_run import args
    with pytest.raises(ValueError, match='Utility runs cannot'):
        async for _ in assembly.stream_run(**args(None, tmp_path, **{'allowed_tools': frozenset(), 'utility': True, **overrides})):
            pass


@pytest.mark.asyncio
async def test_input_secret_redaction(configured, monkeypatch):
    seen = {}
    async def stream(**kwargs):
        seen.update(kwargs)
        yield 'Safe title'
    monkeypatch.setattr(utility, 'stream_run', stream)
    await utility.complete('Summarize: password=SYNTHETIC_PRIVATE_VALUE', db=fake_db(), conversation_id='c')
    assert 'SYNTHETIC_PRIVATE_VALUE' not in seen['prompt']
    assert '[REDACTED]' in seen['prompt']


@pytest.mark.asyncio
async def test_cancelled_compression_does_not_swallow_cancellation(configured, monkeypatch):
    from slack_app.brevity import maybe_compress_slack_reply
    monkeypatch.setattr(utility, 'complete', AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await maybe_compress_slack_reply('Long reply ' * 100)
