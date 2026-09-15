"""Run assembly against throwaway Mongo. Never calls paid/personal providers."""
import asyncio
import base64
from contextlib import aclosing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import ssl
from unittest.mock import AsyncMock

import pytest

from isolation import run as mod
from isolation.artifacts import ArtifactScope
from isolation.context import Attachment, ConversationContext
from isolation.gateway import GatewayDenied
from isolation.knowledge import KnowledgeGateway
from isolation.protocol import RunAuthority
from tests.test_bounded_work import db, OWNER, OTHER
from tests.test_worker_accounting import SPEC
from tests.test_worker_boundary import TOKEN
from tests.test_worker_models import grant

AUTH = RunAuthority('ingress-run', OWNER, frozenset())


async def seed(db, messages=None):
    await db.conversations.insert_one({'conversation_id': 'current', 'source': 'dashboard',
        'status': 'running', 'metadata': {'user_name': OWNER, 'agent_id': 'agent-a'},
        'project_id': 'project-a', 'messages': messages or []})


def context(db, **kw):
    return ConversationContext(db, AUTH, 'current', cancelled=kw.get('cancelled', asyncio.Event()),
        check_access=kw.get('check_access', AsyncMock(return_value=True)))


def args(db, tmp_path, **overrides):
    values = dict(db=db, owner=OWNER, conversation_id='current', prompt='Current message',
        instructions='Synthetic safe instructions', runtime='codex',
        grant=replace(grant(), native_codex=True, max_output_tokens=8192),
        budget_spec=replace(SPEC, output_ceiling=8192, budget_nusd=1_000_000),
        allowed_tools=frozenset({'workspace.list', 'workspace.publish'}),
        check_access=AsyncMock(return_value=True), cancelled=asyncio.Event(),
        url='https://worker.example.test', token=TOKEN, tls=ssl.create_default_context(),
        artifact_root=tmp_path)
    return {**values, **overrides}


@pytest.mark.parametrize('name,data', [('../private', b'a'), ('a', '/backend/file'), ('a', bytearray(b'a')),
                                      ('/etc/passwd', b'a'), ('a', b'x' * (20 * 1024 * 1024 + 1))])
def test_attachment_accepts_only_named_bounded_bytes(name, data):
    with pytest.raises(ValueError):
        Attachment(name, data)


@pytest.mark.asyncio
async def test_history_is_owned_visible_sanitized_and_not_duplicated(db):
    await seed(db, [
        {'role': 'user', 'content': 'Old question'}, {'role': 'assistant', 'content': 'Answer'},
        {'role': 'tool', 'content': 'PRIVATE'},
        {'role': 'user', 'content': '[Personal Tools Auth Token: PRIVATE]'},
        {'role': 'assistant', 'content': 'password=PRIVATE'},
        {'role': 'user', 'content': 'Current message'},
    ])
    result = await context(db).load('Current message')
    assert result.history == (('user', 'Old question'), ('assistant', 'Answer'), ('assistant', '[REDACTED]'))
    assert result.coverage['excluded_messages'] == 2 and result.coverage['redacted_messages'] == 1
    assert 'PRIVATE' not in json.dumps(result.history)
    assert await result.authorize(AUTH)
    assert not await result.authorize(replace(AUTH, user_email=OTHER))


@pytest.mark.asyncio
async def test_history_limits_are_reported_and_suffix_order_preserved(db):
    await seed(db, [{'role': 'user', 'content': str(i)} for i in range(110)])
    result = await context(db).load('Current message')
    assert len(result.history) == 100 and result.history[0] == ('user', '10')
    assert result.coverage['omitted_messages'] == 10
    await db.conversations.update_one({'conversation_id': 'current'}, {'$set': {'messages': [
        {'role': 'assistant', 'content': 'x' * 250000}, {'role': 'user', 'content': 'latest'}]}})
    result = await context(db).load('Current message')
    assert result.history == (('user', 'latest'),) and result.coverage['omitted_messages'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('update', [
    {'metadata.user_name': OTHER}, {'project_id': 'elsewhere'}, {'metadata.agent_id': 'other-agent'},
    {'deleted': True}, {'status': 'interrupted'}, {'source': 'slack'},
])
async def test_scope_changes_and_termination_revoke_run(db, update):
    await seed(db)
    result = await context(db).load('Current message')
    await db.conversations.update_one({'conversation_id': 'current'}, {'$set': update})
    assert not await result.authorize(AUTH)


@pytest.mark.asyncio
async def test_user_recreation_and_live_policy_revocation(db):
    await seed(db)
    policy = AsyncMock(return_value=True)
    result = await context(db, check_access=policy).load('Current message')
    policy.return_value = False
    assert not await result.authorize(AUTH)
    policy.return_value = True
    await db.users.delete_one({'email': OWNER})
    await db.users.insert_one({'email': OWNER, 'status': 'active'})
    assert not await result.authorize(AUTH)


@pytest.mark.asyncio
async def test_attachment_ingress_and_owned_reuse(db, tmp_path):
    await seed(db)
    result = await context(db).load('Current message')
    original = ArtifactScope(tmp_path, AUTH, 'current')
    receipt = original.ingest('old.txt', b'old')
    original.close()
    row = {'_id': receipt['artifact_id'], 'owner': OWNER, 'conversation_id': 'current',
        'metadata': receipt, 'expires_at': datetime.now(timezone.utc) + timedelta(days=1)}
    await db.isolated_artifact_downloads.insert_one(row)
    scope = ArtifactScope(tmp_path, AUTH, 'current')
    try:
        manifest = await result.stage(scope, [Attachment('new.txt', b'new')], [receipt['artifact_id']])
        assert {x['name'] for x in manifest} == {'old.txt', 'new.txt'}
        assert len(scope.inputs) == 2
        for item in manifest:
            assert base64.b64decode(scope.read(item['artifact_id'], 0)['data']) in (b'old', b'new')
    finally:
        scope.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('update', [{'owner': OTHER}, {'conversation_id': 'other'},
    {'expires_at': datetime.now(timezone.utc) - timedelta(days=1)}, {'metadata.name': 'changed.txt'}])
async def test_cross_scope_expired_or_changed_artifact_not_staged(db, tmp_path, update):
    await seed(db)
    result = await context(db).load('Current message')
    scope = ArtifactScope(tmp_path, AUTH, 'current')
    receipt = scope.ingest('source.txt', b'synthetic')
    scope.close()
    scope = ArtifactScope(tmp_path, AUTH, 'current')
    await db.isolated_artifact_downloads.insert_one({'_id': receipt['artifact_id'], 'owner': OWNER,
        'conversation_id': 'current', 'metadata': receipt, 'expires_at': datetime.now(timezone.utc) + timedelta(days=1)})
    await db.isolated_artifact_downloads.update_one({'_id': receipt['artifact_id']}, {'$set': update})
    try:
        with pytest.raises(GatewayDenied):
            await result.stage(scope, input_ids=[receipt['artifact_id']])
        assert not scope.inputs
    finally:
        scope.close()


@pytest.mark.asyncio
async def test_skill_allowlist_enforced_by_backend_not_prompt(db):
    from tests.test_worker_knowledge import seeded, AUTH as KNOWLEDGE_AUTH
    await seeded(db)
    knowledge = KnowledgeGateway(db, KNOWLEDGE_AUTH, 'current', allowed_skills=frozenset({'mine'}))
    result = await knowledge(KNOWLEDGE_AUTH, 'skills.list', {})
    assert [r['slug'] for r in result['skills']] == ['mine']
    with pytest.raises(GatewayDenied):
        await knowledge(KNOWLEDGE_AUTH, 'skills.get', {'slug': 'shared'})
    knowledge = KnowledgeGateway(db, KNOWLEDGE_AUTH, 'current', allowed_skills=frozenset())
    assert (await knowledge(KNOWLEDGE_AUTH, 'skills.list', {}))['skills'] == []


@pytest.mark.asyncio
async def test_assembled_run_history_files_budget_audit_and_cleanup(db, tmp_path, monkeypatch):
    await seed(db, [{'role': 'user', 'content': 'Prior question'}])
    captured = {}
    async def worker(**kw):
        captured.update(kw)
        gateway, authority = kw['execute_tool'], kw['authority']
        assert await kw['authorize'](authority)
        assert 'Prior question' not in kw['input']['prompt']
        assert gateway.models.grant.history == (('user', 'Prior question'),)
        assert 'Authorization' not in json.dumps(kw['input'])
        manifest = await gateway(authority, 'artifacts.list', {})
        assert manifest['files'][0]['name'] == 'input.txt'
        data = b'Synthetic generated output'
        reply = await gateway(authority, 'artifacts.begin', {'name': 'output.txt', 'size': len(data)})
        await gateway(authority, 'artifacts.write', {'artifact_id': reply['artifact_id'], 'offset': 0,
            'data': base64.b64encode(data).decode()})
        await gateway(authority, 'artifacts.commit', {'artifact_id': reply['artifact_id'], 'sha256': hashlib.sha256(data).hexdigest()})
        yield 'Done'
    monkeypatch.setattr(mod, 'stream_worker', worker)
    events = [e async for e in mod.stream_run(**args(db, tmp_path, attachments=[Attachment('input.txt', b'synthetic')]))]
    assert events[0]['type'] == 'file_artifact' and events[1] == 'Done'
    gateway = captured['execute_tool']
    assert gateway.artifacts.closed and gateway.models.closed and captured['session'].closed
    ledger = await db.isolated_model_budgets.find_one({'_id': captured['authority'].run_id})
    assert not ledger['active']
    assert await db.isolated_artifact_downloads.count_documents({'owner': OWNER}) == 1
    assert await db.isolated_worker_audit.count_documents({'stage': 'completed', 'runtime': 'codex'}) == 1


@pytest.mark.asyncio
async def test_no_fallback_on_transport_failure(db, tmp_path, monkeypatch):
    await seed(db)
    captured = []
    async def failed(**kw):
        captured.append(kw)
        raise RuntimeError('synthetic transport outage')
        yield
    monkeypatch.setattr(mod, 'stream_worker', failed)
    with pytest.raises(RuntimeError, match='transport outage'):
        _ = [e async for e in mod.stream_run(**args(db, tmp_path))]
    assert len(captured) == 1 and captured[0]['execute_tool'].artifacts.closed
    assert not (await db.isolated_model_budgets.find_one({}))['active']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['event', 'task', 'close'])
async def test_cancellation_and_generator_close_cleanup(db, tmp_path, monkeypatch, mode):
    await seed(db)
    captured = {}
    ready, stopped, cancellation = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def worker(**kw):
        captured.update(kw)
        try:
            yield 'First'
            ready.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(mod, 'stream_worker', worker)
    stream = mod.stream_run(**args(db, tmp_path, cancelled=cancellation))
    async def consume():
        async with aclosing(stream):
            async for item in stream:
                if mode == 'close':
                    break
    task = asyncio.create_task(consume())
    if mode != 'close':
        await asyncio.wait_for(ready.wait(), 5)
        if mode == 'event':
            cancellation.set()
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        await asyncio.wait_for(task, 5)
    assert stopped.is_set()
    assert captured['execute_tool'].artifacts.closed and captured['execute_tool'].models.closed
    assert captured['session'].closed
    assert not (await db.isolated_model_budgets.find_one({}))['active']


@pytest.mark.asyncio
@pytest.mark.parametrize('overrides', [dict(runtime='unknown'), dict(allowed_tools={'shell'}),
    dict(grant=replace(grant(), native_codex=False)), dict(budget_spec=replace(SPEC, model='wrong')),
    dict(allowed_tools=frozenset(), attachments=[Attachment('a', b'a')])])
async def test_invalid_contract_never_opens_worker(db, tmp_path, monkeypatch, overrides):
    worker = AsyncMock()
    monkeypatch.setattr(mod, 'stream_worker', worker)
    with pytest.raises(ValueError):
        _ = [e async for e in mod.stream_run(**args(db, tmp_path, **overrides))]
    worker.assert_not_called()
    assert await db.isolated_model_budgets.count_documents({}) == 0


@pytest.mark.asyncio
async def test_read_only_workspace_does_not_grant_upload(db, tmp_path, monkeypatch):
    await seed(db)
    async def worker(**kw):
        auth = kw['authority']
        assert 'artifacts.read' in auth.allowed_tools
        assert not {'artifacts.begin', 'artifacts.write', 'artifacts.commit'} & auth.allowed_tools
        with pytest.raises(GatewayDenied):
            await kw['execute_tool'](auth, 'artifacts.begin', {'name': 'x', 'size': 0})
        yield 'Read-only'
    monkeypatch.setattr(mod, 'stream_worker', worker)
    assert [x async for x in mod.stream_run(**args(db, tmp_path, allowed_tools={'workspace.read'}))] == ['Read-only']


@pytest.mark.asyncio
async def test_native_run_assembly_real_transport_files_and_usage(db, tmp_path, monkeypatch):
    import shutil
    import sys
    from pathlib import Path
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from isolation import supervisor
    from isolation.accounting import ModelBudget
    from tests.test_codex_worker import reply_events, sse
    from tests.test_worker_boundary import Transport, settings
    from tests.test_worker_models import SyntheticSession
    binary = shutil.which('codex')
    if not binary:
        pytest.skip('Pinned native Codex binary required')
    await seed(db, [{'role': 'user', 'content': 'Prior question'}])
    package = tmp_path / 'image' / 'isolation'
    package.mkdir(parents=True)
    for module in ('__init__.py', 'protocol.py', 'model_bridge.py', 'codex_worker.py', 'worker_entry.py',
                   'workspace_tools.py', 'workspace.py', 'artifacts.py'):
        shutil.copyfile(Path(__file__).parents[1] / 'isolation' / module, package / module)
    root = tmp_path / 'workspace'
    root.mkdir()
    script = tmp_path / 'image' / 'launch.py'
    script.write_text('''import asyncio,sys
from pathlib import Path
from isolation.worker_entry import run
async def main():
    reader=asyncio.StreamReader(limit=9*1024*1024)
    protocol=asyncio.StreamReaderProtocol(reader)
    transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin.buffer)
    def write(raw):
        sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    try: await run(reader,write,root=Path(sys.argv[1]),executable=sys.argv[2])
    finally: transport.close()
asyncio.run(main())
''')
    monkeypatch.setattr(supervisor, 'docker_command', lambda *_: [sys.executable, str(script), str(root), binary])
    monkeypatch.setattr(supervisor, 'command', AsyncMock(return_value=b''))
    host = supervisor.Supervisor(settings())
    application = web.Application()
    application.router.add_get('/v1/run', host.run)
    client = TestClient(TestServer(application))
    await client.start_server()
    requests = []
    async def provider(request):
        requests.append(await request.json())
        n = len(requests)
        if n <= 2:
            values = ({'command': 'find . -type f -name input.txt -exec cat {} \\; > report.txt'}
                      if n == 1 else {'path': 'report.txt'})
            item = {'id': 'fc_' + str(n), 'type': 'function_call', 'call_id': 'call_' + str(n),
                    'name': 'gateway_' + str(n - 1), 'arguments': json.dumps(values)}
            events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                {'type': 'response.completed', 'response': {'status': 'completed', 'output': [item]}}]
        else:
            events = reply_events('Verified assembled native run')
        for event in events:
            if event['type'] == 'response.completed':
                event['response'].update(id='receipt_' + str(n), model='gpt-5.4',
                    usage={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120})
        return web.Response(body=sse(events), content_type='text/event-stream')
    provider_app = web.Application()
    provider_app.router.add_post('/responses', provider)
    server = TestServer(provider_app)
    await server.start_server()
    original_relay, original_stream = ModelBudget.relay, mod.stream_worker
    def relay(self, grant, **kw):
        kw['session'] = SyntheticSession(kw['session'], str(server.make_url('/responses')))
        return original_relay(self, grant, **kw)
    def transport(**kw):
        kw['session'] = Transport(client)
        return original_stream(**kw)
    monkeypatch.setattr(ModelBudget, 'relay', relay)
    monkeypatch.setattr(mod, 'stream_worker', transport)
    try:
        spec = replace(SPEC, model='gpt-5.4', output_ceiling=8192, budget_nusd=1_000_000)
        events = [x async for x in mod.stream_run(**args(db, tmp_path / 'artifacts',
            grant=replace(grant(), model='gpt-5.4', max_output_tokens=8192, native_codex=True),
            budget_spec=spec, allowed_tools={'workspace.exec', 'workspace.publish'},
            attachments=[Attachment('input.txt', b'verified native input')], max_seconds=40))]
        assert ''.join(e for e in events if isinstance(e, str)) == 'Verified assembled native run'
        files = [e for e in events if isinstance(e, dict)]
        assert len(files) == 1 and files[0]['file_name'] == 'report.txt'
        assert len(requests) == 3 and 'Prior question' in json.dumps(requests[0])
        assert 'Authorization' not in json.dumps(requests)
        saved = await db.isolated_model_budgets.find_one({})
        assert saved['call_count'] == 3 and not saved['active']
        assert saved['recorded_nusd'] == saved['committed_nusd'] == 1500
        assert all(c['status'] == 'recorded' for c in saved['calls'])
        receipt = await db.isolated_artifact_downloads.find_one({})
        check = ArtifactScope(tmp_path / 'artifacts', AUTH, 'current', inputs=[receipt['_id']])
        try:
            assert base64.b64decode(check.read(receipt['_id'], 0)['data']) == b'verified native input'
        finally:
            check.close()
        assert not list(root.iterdir())
    finally:
        await client.close()
        await server.close()
    assert not host.active


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_settlement', [False, True])
async def test_native_followup_waits_for_durable_settlement(fail_settlement):
    import aiohttp
    from isolation.model_bridge import ModelBridge
    entered, release = asyncio.Event(), asyncio.Event()
    starts = []
    reads = 0
    async def rpc(tool, arguments):
        nonlocal reads
        if tool == 'model.start':
            starts.append(arguments)
            return {'stream_id': str(len(starts)), 'content_type': 'text/event-stream'}
        if tool == 'model.read':
            if arguments['stream_id'] == '1':
                reads += 1
                if reads == 1:
                    return {'data': base64.b64encode(b'data: [DONE]\n\n').decode(), 'eof': False}
                entered.set()
                await release.wait()
                if fail_settlement:
                    raise RuntimeError('synthetic ledger failure')
            return {'data': '', 'eof': True}
        return {'closed': True}
    bridge = ModelBridge('responses', rpc)
    await bridge.serve()
    pending = None
    try:
        async with aiohttp.ClientSession() as session:
            first = await session.post(bridge.origin + '/v1/responses', json={'input': 'first'})
            await first.content.readany()
            await asyncio.wait_for(entered.wait(), 2)
            pending = asyncio.create_task(session.post(bridge.origin + '/v1/responses', json={'input': 'second'}))
            for _ in range(100):
                if bridge.waiter is not None:
                    break
                await asyncio.sleep(.01)
            assert bridge.waiter is not None
            assert len(starts) == 1 and not pending.done()
            third = await session.post(bridge.origin + '/v1/responses', json={'input': 'third'})
            assert third.status == 409
            first.close()  # Native clients close after the terminal SSE event.
            release.set()
            second = await asyncio.wait_for(pending, 3)
            await second.read()
            assert second.status == (400 if fail_settlement else 200)
            assert len(starts) == (1 if fail_settlement else 2)
    finally:
        release.set()
        if pending and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await bridge.close()
