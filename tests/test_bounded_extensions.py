"""Broader actions, external event wake-ups and held-charge reviews.

Pure policy/broker units plus opt-in isolated-Mongo state tests
(LOMA_LOCAL_E2E=1), matching test_bounded_work.py conventions.
"""
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

from autonomy import core, connector, worker

OWNER = 'qa-owner@example.com'
POLICY = {'actions': {'gmail.search': 'allow', 'gmail.read': 'allow', 'gmail.send': 'ask',
                      'slack.send': 'ask', 'calendar.list': 'allow'},
          'recipients': ['recipient@example.com'], 'channels': ['C0123ABCD']}


# ── Policy units ─────────────────────────────────────────────────────────

def test_channels_validated_as_exact_ids():
    assert core.validate_policy(POLICY)['channels'] == ['C0123ABCD']
    for bad in (['#general'], ['general'], ['C*'], ['c0123abcd'], ['C0123ABCD '] * 21, [42]):
        with pytest.raises(ValueError):
            core.validate_policy({**POLICY, 'channels': bad})


def test_legacy_policy_defaults_new_actions_to_deny():
    legacy = core.validate_policy({'actions': {'gmail.send': 'allow'}, 'recipients': ['a@b.co']})
    assert legacy['actions']['slack.send'] == 'deny'
    assert legacy['actions']['calendar.list'] == 'deny'
    assert legacy['channels'] == []


def test_intersect_channels_never_widens():
    other = core.validate_policy({**POLICY, 'channels': ['C0123ABCD', 'C0999ZZZZ']})
    assert core.intersect(core.validate_policy(POLICY), other)['channels'] == ['C0123ABCD']
    empty = core.validate_policy({**POLICY, 'channels': []})
    assert core.intersect(core.validate_policy(POLICY), empty)['channels'] == []


def test_slack_send_requires_approved_channel():
    policy = core.validate_policy(POLICY)
    args = core.validate_action('slack.send', {'channel': 'C0123ABCD', 'text': 'hello'}, policy)
    assert args == {'channel': 'C0123ABCD', 'text': 'hello'}
    for bad in ({'channel': 'C0999ZZZZ', 'text': 'x'}, {'channel': 'C0123ABCD'},
                {'channel': 'C0123ABCD', 'text': 'x', 'thread_ts': '1'},
                {'channel': 'C0123ABCD', 'text': 'y' * 4001}):
        with pytest.raises(ValueError):
            core.validate_action('slack.send', bad, policy)


def test_calendar_list_takes_no_arguments():
    policy = core.validate_policy(POLICY)
    assert core.validate_action('calendar.list', {}, policy) == {}
    with pytest.raises(ValueError):
        core.validate_action('calendar.list', {'query': 'x'}, policy)
    with pytest.raises(ValueError):
        core.validate_action('calendar.list', {}, core.validate_policy({}))


# ── Connector units ──────────────────────────────────────────────────────

def test_connector_argv_per_tool_and_allowlist():
    slack = connector.build_argv('slack', ['send-message', '--channel', 'C1', '--text', 'hi'], OWNER, 'tok')
    assert slack[:2] == [sys.executable, '-I'] and slack[2].endswith('tools/slack_user.py')
    assert slack.index('--user-email') < slack.index('send-message')
    cal = connector.build_argv('calendar', ['list-events', '--limit', '10'], OWNER, 'tok')
    assert cal[2].endswith('tools/google_calendar.py')
    assert cal.index('list-events') < cal.index('--user-email')
    gmail = connector.build_argv('gmail', ['search', '--query', 'q', '--limit', '5'], OWNER, 'tok')
    assert gmail[2].endswith('tools/gmail.py')
    with pytest.raises(KeyError):
        connector.build_argv('shell', ['ls'], OWNER, 'tok')


def test_connector_sandbox_prefix_is_operator_only():
    with patch.dict(os.environ, {'LOMA_CONNECTOR_SANDBOX': '/usr/bin/bwrap --unshare-net'}):
        argv = connector.build_argv('gmail', ['search', '--query', 'q'], OWNER, 'tok')
        assert argv[:2] == ['/usr/bin/bwrap', '--unshare-net']
    with patch.dict(os.environ, {'LOMA_CONNECTOR_SANDBOX': ''}):
        assert connector.build_argv('gmail', ['search', '--query', 'q'], OWNER, 'tok')[0] == sys.executable


@pytest.mark.asyncio
async def test_adapter_routes_actions_and_requires_receipts():
    calls = []

    async def fake(tool, command, owner):
        calls.append((tool, command[0], owner))
        return {'sent': True, 'message_ts': '1.2'} if tool == 'slack' else {'events': []}

    with patch.object(connector, 'personal', fake):
        await worker.adapter('slack.send', {'channel': 'C1', 'text': 'hi'}, OWNER, 'a1')
        await worker.adapter('calendar.list', {}, OWNER, 'a2')
    assert calls == [('slack', 'send-message', OWNER), ('calendar', 'list-events', OWNER)]

    async def no_receipt(tool, command, owner):
        return {'ok': True}

    with patch.object(connector, 'personal', no_receipt):
        with pytest.raises(ValueError, match='receipt'):
            await worker.adapter('slack.send', {'channel': 'C1', 'text': 'hi'}, OWNER, 'a3')


# ── Opt-in isolated Mongo state tests ────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    if os.getenv('LOMA_LOCAL_E2E') != '1':
        pytest.skip('Requires opt-in isolated Mongo')
    env = dotenv_values(Path(__file__).parents[1] / '.env')
    assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    name = 'loma_local_bounded_ext_' + uuid.uuid4().hex
    client = AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'], tz_aware=True)
    database = client[name]
    await core.indexes(database)
    await database.users.insert_one({'email': OWNER, 'status': 'active'})
    await database.agent_identities.insert_one(
        {'agent_id': 'agent-a', 'name': 'Finance', 'description': 'Test', 'created_by': OWNER,
         'status': 'active', 'visibility': 'private'})
    try:
        yield database
    finally:
        assert name.startswith('loma_local_bounded_ext_')
        await client.drop_database(name)
        client.close()


async def make_work(db):
    work = await core.create_work(db, OWNER, {'agent_id': 'agent-a', 'title': 'Triage',
        'instructions': 'Review the event', 'success': 'Summary', 'policy': POLICY, 'max_steps': 5})
    return work


@pytest.mark.asyncio
async def test_event_token_rotation_and_wake(db):
    work = await make_work(db)
    with pytest.raises(LookupError):
        await core.event_wake(db, work['work_id'], 'anything', 'evt-1')
    token = await core.rotate_event_token(db, OWNER, work['work_id'])
    saved = await db.agent_work.find_one({'work_id': work['work_id']})
    assert saved['event_token_hash'] != token  # only the hash is stored
    with pytest.raises(ValueError, match='paused'):
        await core.event_wake(db, work['work_id'], token, 'evt-1')
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'paused': False}})
    run = await core.event_wake(db, work['work_id'], token, 'evt-1', note='ticket 42 (untrusted)')
    assert run['event_note'] == 'ticket 42 (untrusted)'
    dup = await core.event_wake(db, work['work_id'], token, 'evt-1')
    assert dup['run_id'] == run['run_id']
    assert await db.agent_runs.count_documents({}) == 1
    # Rotation invalidates the old token; revocation stops wake-ups entirely.
    fresh = await core.rotate_event_token(db, OWNER, work['work_id'])
    with pytest.raises(LookupError):
        await core.event_wake(db, work['work_id'], token, 'evt-2')
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'revoked': True}})
    with pytest.raises(ValueError, match='revoked'):
        await core.event_wake(db, work['work_id'], fresh, 'evt-3')
    with pytest.raises(ValueError, match='not found or revoked'):
        await core.rotate_event_token(db, OWNER, work['work_id'])


@pytest.mark.asyncio
async def test_event_wake_rejects_wrong_or_missing_token_uniformly(db):
    work = await make_work(db)
    await core.rotate_event_token(db, OWNER, work['work_id'])
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'paused': False}})
    for token in ('', None, 'wrong', 'x' * 64):
        with pytest.raises(LookupError):
            await core.event_wake(db, work['work_id'], token, 'evt-1')
    with pytest.raises(LookupError):
        await core.event_wake(db, 'missing-work', 'wrong', 'evt-1')
    assert await db.agent_runs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_cost_review_requires_terminal_root_with_held_funds(db):
    work = await make_work(db)
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'paused': False}})
    work['paused'] = False
    run = await core.enqueue(db, work, 'evt-1')
    held = {'reservation_id': 'r1', 'run_id': run['run_id'], 'status': 'held', 'reserved_nusd': 5000}
    with pytest.raises(ValueError, match='ended top-level'):
        await core.review_cost(db, OWNER, run['run_id'], 0, 'unknown', 'still running')
    await db.agent_runs.update_one({'run_id': run['run_id']},
        {'$set': {'status': 'failed', 'cost_ledger': [held], 'cost_committed_nusd': 5000}})
    with pytest.raises(ValueError):
        await core.review_cost(db, OWNER, run['run_id'], 0, 'refunded', 'invalid outcome')
    first = await core.review_cost(db, OWNER, run['run_id'], 0, 'unknown', 'invoice not posted yet')
    assert first['cost_review']['version'] == 1
    # Stale version CAS: a concurrent reviewer must reread before correcting.
    with pytest.raises(ValueError, match='review changed'):
        await core.review_cost(db, OWNER, run['run_id'], 0, 'billed', 'stale review')
    second = await core.review_cost(db, OWNER, run['run_id'], 1, 'billed', 'found on invoice')
    assert [r['outcome'] for r in second['cost_review_history']] == ['unknown', 'billed']
    # Audit-only: reservations and committed funds are untouched.
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['cost_ledger'][0]['status'] == 'held'
    assert saved['cost_committed_nusd'] == 5000


@pytest.mark.asyncio
async def test_cost_review_rejects_other_users_and_clean_runs(db):
    work = await make_work(db)
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'paused': False}})
    work['paused'] = False
    run = await core.enqueue(db, work, 'evt-1')
    await db.agent_runs.update_one({'run_id': run['run_id']}, {'$set': {'status': 'done'}})
    with pytest.raises(ValueError, match='no unresolved'):
        await core.review_cost(db, OWNER, run['run_id'], 0, 'billed', 'nothing held')
    await db.users.insert_one({'email': 'other@example.com', 'status': 'active'})
    with pytest.raises(ValueError, match='ended top-level'):
        await core.review_cost(db, 'other@example.com', run['run_id'], 0, 'billed', 'not my run')


@pytest.mark.asyncio
async def test_slack_send_reconciliation_and_uncertainty(db):
    work = await core.create_work(db, OWNER, {'agent_id': 'agent-a', 'title': 'Notify',
        'instructions': 'Tell the channel', 'success': 'Receipt', 'policy': POLICY, 'max_steps': 5})
    await db.agent_work.update_one({'work_id': work['work_id']}, {'$set': {'paused': False}})
    work['paused'] = False
    run = await core.enqueue(db, work, 'evt-1')
    slack = {'op': 'action', 'action': 'slack.send',
             'args': {'channel': 'C0123ABCD', 'text': 'Deploy done'}, 'reason': 'Notify'}
    planner = AsyncMock(side_effect=[slack])

    async def broken(action, args, owner, action_id):
        raise TimeoutError('provider timeout')

    await worker.tick(db, planner, broken)
    approval = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, approval['approval_id'], 1, 'approve')
    await worker.tick(db, planner, broken)
    approval = await db.agent_approvals.find_one({'approval_id': approval['approval_id']})
    assert approval['status'] == 'uncertain'
    assert 'provider_message_id' not in approval  # correlation IDs are Gmail-only
    saved = await db.agent_runs.find_one({'run_id': run['run_id']})
    assert saved['status'] == 'failed'
    # Owner investigation applies to Slack sends too and never unblocks replay.
    result = await core.reconcile(db, OWNER, approval['approval_id'], 0, 'not_sent',
                                  'Channel checked at 10:02; no matching message')
    assert result['status'] == 'uncertain'
    with pytest.raises(ValueError):
        await core.reconcile(db, OWNER, approval['approval_id'], 0, 'sent', 'stale version')
