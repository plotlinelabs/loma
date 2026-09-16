"""ACL, revision, revocation and planner boundaries against isolated Mongo."""
from unittest.mock import AsyncMock
import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from autonomy import core, knowledge, worker
from tests.test_bounded_work import db, OWNER, OTHER, POLICY, SEND


async def source(db, **kwargs):
    return await knowledge.save(db, OWNER, {'title': 'Approved process', 'content': 'Do not auto-send.', **kwargs})


async def job(db, ids, owner=OWNER, delegates=None):
    return await core.create_work(db, owner, {'agent_id': 'agent-b', 'title': 'Research', 'instructions': 'Summarize',
        'success': 'Summary', 'policy': POLICY, 'knowledge_ids': ids, 'delegates': delegates or []})


@pytest.mark.parametrize('ids', [None, 'x', [None], ['x'] * 11, ['']])
def test_invalid_references(ids):
    with pytest.raises(ValueError):
        knowledge.references(ids)


@pytest.mark.asyncio
async def test_private_shared_and_revoked(db):
    s = await source(db)
    assert await knowledge.listing(db, OTHER) == []
    with pytest.raises(ValueError, match='accessible'):
        await job(db, [s['source_id']], OTHER)
    s = await knowledge.save(db, OWNER, {**s, 'readers': [OTHER]}, s['source_id'])
    assert len(await knowledge.listing(db, OTHER)) == 1
    assert 'readers' not in (await knowledge.listing(db, OTHER))[0]
    w = await job(db, [s['source_id']], OTHER)
    await knowledge.save(db, OWNER, {**s, 'readers': []}, s['source_id'])
    with pytest.raises(ValueError, match='accessible'):
        await knowledge.resolve(db, OTHER, w['knowledge_ids'])


@pytest.mark.asyncio
async def test_only_author_edits_and_revision_conflicts(db):
    s = await source(db, readers=[OTHER])
    for actor in (OTHER, OWNER):
        with pytest.raises(ValueError, match='changed'):
            await knowledge.save(db, actor, {**s, 'version': 2}, s['source_id'])
    with pytest.raises(ValueError, match='changed'):
        await knowledge.save(db, OTHER, s, s['source_id'])
    with pytest.raises(ValueError):
        await knowledge.delete(db, OTHER, s['source_id'], 1)
    await knowledge.save(db, OWNER, {**s, 'content': 'Revision two'}, s['source_id'])
    with pytest.raises(ValueError):
        await knowledge.delete(db, OWNER, s['source_id'], 1)
    assert (await knowledge.resolve(db, OWNER, [s['source_id']]))[0]['content'] == 'Revision two'
    await knowledge.delete(db, OWNER, s['source_id'], 2)
    assert not await knowledge.listing(db, OWNER)
    assert (await db.agent_knowledge.find_one({'source_id': s['source_id']}))['content'] == ''


@pytest.mark.asyncio
async def test_inactive_author_and_reader(db):
    s = await source(db, readers=[OTHER])
    await db.users.update_one({'email': OWNER}, {'$set': {'status': 'inactive'}})
    assert not await knowledge.listing(db, OTHER)
    with pytest.raises(ValueError):
        await knowledge.resolve(db, OTHER, [s['source_id']])
    with pytest.raises(ValueError):
        await knowledge.save(db, OTHER, {'title': 'x', 'content': 'x', 'readers': [OWNER]})


@pytest.mark.asyncio
async def test_only_selected_sources_reach_planner(db):
    s = await source(db)
    await source(db, title='Not attached', content='Do not leak')
    w = await job(db, [s['source_id']])
    run = await core.enqueue(db, w, 'one', dry_run=True)
    planner = AsyncMock(return_value={'op': 'done', 'result': 'ok'})
    await worker.tick(db, planner, AsyncMock())
    context = planner.call_args.args[0]
    assert len(context['playbooks']) == 1
    assert context['playbooks'][0]['source_id'] == s['source_id']
    assert 'content' not in run['snapshot']
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'done'


@pytest.mark.asyncio
async def test_revocation_blocks_previously_approved_action(db):
    s = await source(db)
    w = await job(db, [s['source_id']]); w['paused'] = False
    await db.agent_work.update_one({'work_id': w['work_id']}, {'$set': {'paused': False}})
    run = await core.enqueue(db, w, 'one')
    await worker.tick(db, AsyncMock(return_value=SEND), AsyncMock())
    approval = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, approval['approval_id'], 1, 'approve')
    await knowledge.delete(db, OWNER, s['source_id'], 1)
    broker = AsyncMock()
    await worker.tick(db, AsyncMock(), broker)
    broker.assert_not_awaited()
    assert (await db.agent_runs.find_one({'run_id': run['run_id']}))['status'] == 'failed'


@pytest.mark.asyncio
async def test_overview_preserves_old_slack_and_charge_attention(db, monkeypatch):
    from api import bounded_work_routes as routes
    await db.agent_runs.insert_many([{'work_id': f'new-{n}', 'event_key': 'fixture', 'run_id': f'new-{n}', 'owner': OWNER, 'created_at': core.now()} for n in range(110)])
    old = core.now() - timedelta(days=100)
    await db.agent_runs.insert_many([
        {'work_id': 'held', 'event_key': 'fixture', 'run_id': 'held', 'owner': OWNER, 'parent_id': None, 'created_at': old, 'status': 'failed', 'cost_ledger': [{'status': 'held'}]},
        {'work_id': 'question', 'event_key': 'fixture', 'run_id': 'question', 'owner': OWNER, 'created_at': old, 'status': 'needs_input'}])
    await db.agent_approvals.insert_many([{'run_id': f'new-{n}', 'step': 0, 'approval_id': f'new-{n}', 'owner': OWNER, 'created_at': core.now(), 'status': 'executed'} for n in range(110)])
    await db.agent_approvals.insert_one({'run_id': 'slack', 'step': 0, 'approval_id': 'old-slack', 'owner': OWNER, 'created_at': old, 'status': 'uncertain', 'action': 'slack.send'})
    monkeypatch.setattr(routes, 'verify', AsyncMock(return_value=(db, OWNER, {})))
    response = await routes.handle(SimpleNamespace(match_info={'tail': 'overview'}, method='GET'))
    data = json.loads(response.text)
    assert {'held', 'question'} <= {r['run_id'] for r in data['runs']}
    assert 'old-slack' in {a['approval_id'] for a in data['approvals']}


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,passes', [
    ('POST', '/api/bounded-work-hooks/job-123', True),
    ('GET', '/api/bounded-work-hooks/job-123', False),
    ('POST', '/api/bounded-work-hooks/job-123/enable', False),
    ('POST', '/api/bounded-work/work/job-123/enable', False),
])
async def test_only_event_post_bypasses_session_middleware(monkeypatch, method, path, passes):
    from aiohttp.test_utils import make_mocked_request
    from aiohttp import web
    import api.auth_middleware as auth
    monkeypatch.setattr(auth, '_IS_DEV', False)
    monkeypatch.setattr(auth, '_IS_PREVIEW', False)
    handler = AsyncMock(return_value=web.json_response({'token_required': True}, status=404))
    response = await auth.auth_middleware(make_mocked_request(method, path), handler)
    assert response.status == (404 if passes else 401)
    assert handler.await_count == int(passes)


@pytest.mark.asyncio
async def test_board_is_owner_scoped_metadata_only(db, monkeypatch):
    from api import bounded_work_routes as routes
    w = await job(db, [])
    await job(db, [], OTHER)
    run = await core.enqueue(db, w, 'board', dry_run=True)
    monkeypatch.setattr(routes, 'verify', AsyncMock(return_value=(db, OWNER, {})))
    response = await routes.handle(SimpleNamespace(match_info={'tail': 'board'}, method='GET'))
    data = json.loads(response.text)
    assert len(data['work']) == 1
    assert data['work'][0]['work_id'] == w['work_id']
    assert data['work'][0]['run_status'] == 'queued'
    assert data['work'][0]['dry_run'] is True
    assert not {'instructions', 'policy', 'history', 'knowledge_ids', 'content'} & set(data['work'][0])


@pytest.mark.asyncio
async def test_delegated_sources_use_same_principal_and_revocation(db):
    s = await source(db)
    w = await job(db, [s['source_id']], delegates=['agent-a'])
    w['paused'] = False
    await db.agent_work.update_one({'work_id': w['work_id']}, {'$set': {'paused': False}})
    run = await core.enqueue(db, w, 'parent')
    await worker.tick(db, AsyncMock(return_value={'op': 'delegate', 'agent_id': 'agent-a',
        'instructions': 'Summarize', 'success': 'Summary'}), AsyncMock())
    child = await db.agent_runs.find_one({'parent_id': run['run_id']})
    assert child['owner'] == OWNER and child['snapshot']['knowledge_ids'] == [s['source_id']]
    await knowledge.delete(db, OWNER, s['source_id'], 1)
    planner, broker = AsyncMock(), AsyncMock()
    await worker.tick(db, planner, broker)
    planner.assert_not_awaited(); broker.assert_not_awaited()
