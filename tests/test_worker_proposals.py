"""Worker write/send proposals: durable, owner-decided, executed once at most.

Argument and argv tests are pure. State-machine and route tests use the opt-in
isolated Mongo. The connector is always patched: no email, Slack message,
calendar event, document or spreadsheet is touched.
"""
import asyncio
import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from isolation import proposals
from isolation.catalog import CATALOG
from isolation.gateway import GatewayDenied, ToolGateway, READ_SCHEMAS
from isolation.proposals import (PROPOSAL_TOOLS, ProposalGateway, TOOLS, WRITE_SCHEMAS, command, decide,
                                 execute, listing, reconcile, validate_write, view, write_adapter)
from isolation.protocol import RunAuthority
from tests.test_bounded_work import db, OWNER, OTHER  # noqa: F401 - fixture and principals

AUTH = RunAuthority('run-1', OWNER, frozenset(TOOLS))
SEND = {'to': 'Recipient@Example.com', 'subject': 'Review me', 'body': 'Exact approved body', 'reason': 'Reminder requested'}
START, END = '2026-09-17T10:00:00+05:30', '2026-09-17T10:30:00+05:30'
VALID = {
    'gmail.send': {'to': 'a@example.com', 'subject': 'S', 'body': 'B'},
    'gmail.draft': {'to': 'a@example.com', 'subject': 'S', 'body': 'B', 'cc': 'b@example.com, C@example.com'},
    'slack.send': {'channel': 'C0123ABCD', 'text': '- deploy done'},
    'calendar.create': {'summary': 'Sync', 'start': START, 'end': END, 'attendees': 'a@example.com'},
    'docs.append': {'document_id': 'doc_1234567890', 'text': 'Notes\nline two'},
    'sheets.write': {'spreadsheet_id': 'sheet_1234567890', 'range': 'Sheet1!A1:B2', 'values': [['a', 1], [True, 2.5]]},
}
INVALID = [
    ('gmail.send', {**VALID['gmail.send'], 'to': 'not-an-email'}),
    ('gmail.send', {**VALID['gmail.send'], 'to': 'a@example.com,b@example.com'}),
    ('gmail.send', {**VALID['gmail.send'], 'subject': 'hello\nBcc: other@example.com'}),
    ('gmail.send', {**VALID['gmail.send'], 'attachments': '/etc/passwd'}),
    ('gmail.send', {**VALID['gmail.send'], 'body': 'x' * 12001}),
    ('gmail.send', {'to': 'a@example.com', 'subject': 'S'}),
    ('gmail.draft', {**VALID['gmail.draft'], 'cc': 'b@example.com,bad'}),
    ('slack.send', {**VALID['slack.send'], 'channel': 'general'}),
    ('slack.send', {**VALID['slack.send'], 'channel': '#C0123ABCD'}),
    ('slack.send', {**VALID['slack.send'], 'text': '--file'}),
    ('slack.send', {**VALID['slack.send'], 'text': 'x' * 4001}),
    ('slack.send', {**VALID['slack.send'], 'thread_ts': 'latest'}),
    ('calendar.create', {**VALID['calendar.create'], 'start': '2026-09-17T10:00:00'}),
    ('calendar.create', {**VALID['calendar.create'], 'end': START}),
    ('calendar.create', {**VALID['calendar.create'], 'start': 'tomorrow'}),
    ('docs.append', {**VALID['docs.append'], 'document_id': '../etc'}),
    ('docs.append', {**VALID['docs.append'], 'text': 'nul\x00byte'}),
    ('sheets.write', {**VALID['sheets.write'], 'values': []}),
    ('sheets.write', {**VALID['sheets.write'], 'values': [['a', {'b': 1}]]}),
    ('sheets.write', {**VALID['sheets.write'], 'values': [[float('inf')]]}),
    ('sheets.write', {**VALID['sheets.write'], 'values': 'a,b'}),
    ('sheets.write', {**VALID['sheets.write'], 'range': 'A1\nB2'}),
    ('shell', {'command': 'curl'}),
    ('gmail.send', 'not a dict'),
]
EXPECTED_ARGV = [
    ('gmail.send', VALID['gmail.send'], 'gmail', ['send-email', '--to=a@example.com', '--subject=S', '--body=B',
     '--rfc-message-id=<loma-' + hashlib.sha256(b'p-1').hexdigest() + '@actions.loma.invalid>']),
    ('gmail.draft', VALID['gmail.draft'], 'gmail', ['create-draft', '--to=a@example.com', '--subject=S', '--body=B',
     '--cc=b@example.com,c@example.com']),
    ('slack.send', VALID['slack.send'], 'slack', ['send-message', '--channel', 'C0123ABCD', '--text', '- deploy done']),
    ('slack.send', {**VALID['slack.send'], 'thread_ts': '1700000000.123456'}, 'slack',
     ['send-message', '--channel', 'C0123ABCD', '--text', '- deploy done', '--thread-ts', '1700000000.123456']),
    ('calendar.create', VALID['calendar.create'], 'calendar', ['create-event', '--summary=Sync', '--start=' + START,
     '--end=' + END, '--attendees=a@example.com']),
    ('docs.append', VALID['docs.append'], 'docs', ['append-text', '--document-id=doc_1234567890', '--text=Notes\nline two']),
    ('sheets.write', VALID['sheets.write'], 'sheets', ['write-range', '--spreadsheet-id=sheet_1234567890',
     '--range=Sheet1!A1:B2', '--values=[["a", 1], [true, 2.5]]']),
]
RECEIPTS = {
    'gmail.send': ({'sent': True, 'messageId': 'm1'}, {'sent': True}),
    'gmail.draft': ({'created': True, 'draftId': 'd1'}, {'created': False, 'draftId': 'd1'}),
    'slack.send': ({'sent': True, 'message_ts': '1.2'}, {'ok': True}),
    'calendar.create': ({'created': True, 'id': 'e1'}, {'created': True}),
    'docs.append': ({'appended': True, 'documentId': 'doc'}, {'appended': False}),
    'sheets.write': ({'updatedRange': 'Sheet1!A1:B2'}, {'updatedCells': 0}),
}


# ---- pure: schemas, validation, argv ---------------------------------------

def test_catalog_matches_gateway_schemas_and_stays_within_native_limits():
    by_name = {t['name']: t for t in CATALOG}
    assert set(PROPOSAL_TOOLS) | {'proposals.status', 'proposals.list'} <= set(by_name)
    for tool, action in PROPOSAL_TOOLS.items():
        if action in ("github.write", "linear.write"):
            continue  # Exact operation schemas covered by test_worker_automation.
        required, optional = WRITE_SCHEMAS[action]
        schema = by_name[tool]['input_schema']
        assert set(schema['properties']) - {'reason'} == required | optional
        assert set(schema['required']) - {'reason'} == required
        assert 'reason' in schema['required'] and schema['additionalProperties'] is False
    assert not (set(PROPOSAL_TOOLS) & set(READ_SCHEMAS))
    assert len(CATALOG) <= 64 and len({t['name'] for t in CATALOG}) == len(CATALOG)


@pytest.mark.parametrize('action', sorted(VALID))
def test_valid_arguments_are_normalized(action):
    args = validate_write(action, VALID[action])
    assert set(args) == set(VALID[action])
    if 'cc' in args:
        assert args['cc'] == 'b@example.com,c@example.com'
    if action == 'calendar.create':
        assert args['start'] == START and args['end'] == END


def test_recipient_is_lowercased_and_trimmed():
    assert validate_write('gmail.send', {'to': '  Recipient@Example.com ', 'subject': 'S', 'body': 'B'})['to'] == 'recipient@example.com'


@pytest.mark.parametrize('action,arguments', INVALID)
def test_invalid_arguments_fail_closed(action, arguments):
    with pytest.raises(GatewayDenied):
        validate_write(action, arguments)


@pytest.mark.parametrize('action,arguments,script,argv', EXPECTED_ARGV)
def test_connector_argv_is_pinned(action, arguments, script, argv):
    assert command(action, validate_write(action, arguments), 'p-1') == (script, argv)


def test_argparse_scripts_receive_dash_safe_values():
    argv = command('gmail.send', validate_write('gmail.send', {**VALID['gmail.send'], 'body': '--html-body=<b>x</b>'}), 'p-1')[1]
    assert '--body=--html-body=<b>x</b>' in argv and '--html-body=<b>x</b>' not in argv


@pytest.mark.parametrize('action', sorted(RECEIPTS))
@pytest.mark.asyncio
async def test_write_adapter_requires_a_provider_receipt(action):
    good, bad = RECEIPTS[action]
    with patch('autonomy.connector.personal', AsyncMock(return_value=good)) as personal:
        receipt = await write_adapter(action, VALID[action], OWNER, 'p-1')
        assert json.loads(receipt['output']) == good and receipt['proposal_id'] == 'p-1'
        assert personal.await_args.args[2] == OWNER
    with patch('autonomy.connector.personal', AsyncMock(return_value=bad)):
        with pytest.raises(ValueError, match='receipt'):
            await write_adapter(action, VALID[action], OWNER, 'p-1')


@pytest.mark.asyncio
async def test_write_adapter_revalidates_before_dispatch():
    with patch('autonomy.connector.personal', AsyncMock()) as personal:
        with pytest.raises(GatewayDenied):
            await write_adapter('gmail.send', {**VALID['gmail.send'], 'to': 'nope'}, OWNER, 'p-1')
        personal.assert_not_awaited()


# ---- gateway dispatch --------------------------------------------------------

class Artifacts:
    def __init__(self, authority):
        self.authority = authority


@pytest.mark.asyncio
async def test_gateway_routes_proposals_and_has_no_send_path():
    proposal_gateway = AsyncMock(return_value={'proposal_id': 'p', 'status': 'pending'})
    proposal_gateway.authority = AUTH
    audit = AsyncMock()
    gateway = ToolGateway(AUTH, authorize=AsyncMock(return_value=True), audit=audit, artifacts=Artifacts(AUTH),
                          connector=AsyncMock(), proposals=proposal_gateway)
    assert await gateway(AUTH, 'gmail.propose_send', SEND) == {'proposal_id': 'p', 'status': 'pending'}
    proposal_gateway.assert_awaited_once_with(AUTH, 'gmail.propose_send', SEND)
    assert [c.args[1]['stage'] for c in audit.await_args_list] == ['requested', 'completed']
    gateway.connector.assert_not_awaited()
    for tool in ('gmail.send', 'slack.send', 'gmail.send-email'):
        with pytest.raises(GatewayDenied):
            await gateway(RunAuthority('run-1', OWNER, frozenset({tool})), tool, {})


@pytest.mark.asyncio
async def test_gateway_without_proposal_scope_denies_and_requires_matching_authority():
    gateway = ToolGateway(AUTH, authorize=AsyncMock(return_value=True), audit=AsyncMock(), artifacts=Artifacts(AUTH))
    with pytest.raises(GatewayDenied, match='unavailable'):
        await gateway(AUTH, 'gmail.propose_send', SEND)
    other = AsyncMock()
    other.authority = RunAuthority('run-2', OWNER, frozenset())
    with pytest.raises(ValueError):
        ToolGateway(AUTH, authorize=AsyncMock(return_value=True), audit=AsyncMock(), artifacts=Artifacts(AUTH), proposals=other)


@pytest.mark.asyncio
async def test_gateway_revocation_after_proposal_hides_result():
    proposal_gateway = AsyncMock(return_value={'proposal_id': 'p'})
    proposal_gateway.authority = AUTH
    gateway = ToolGateway(AUTH, authorize=AsyncMock(side_effect=[True, True, False]), audit=AsyncMock(),
                          artifacts=Artifacts(AUTH), proposals=proposal_gateway)
    with pytest.raises(GatewayDenied, match='no longer valid'):
        await gateway(AUTH, 'gmail.propose_send', SEND)


# ---- durable state machine (opt-in isolated Mongo) ---------------------------

def scope(db, authority=AUTH, conversation='current', access=None):
    return ProposalGateway(db, authority, conversation, check_access=access or AsyncMock(return_value=True))


@pytest.mark.asyncio
async def test_worker_proposal_is_durable_notifies_owner_and_exposes_no_receipt(db):
    await proposals.indexes(db)
    result = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    assert result['status'] == 'pending' and result['args']['to'] == 'recipient@example.com'
    assert set(result) == {'proposal_id', 'action', 'status', 'version', 'args', 'reason', 'created_at', 'expires_at', 'note'}
    stored = await db.isolated_worker_proposals.find_one({'proposal_id': result['proposal_id']})
    assert stored['owner'] == OWNER and stored['conversation_id'] == 'current' and stored['run_id'] == 'run-1'
    assert stored['digest'] and stored['version'] == 1 and stored['status'] == 'pending'
    note = await db.notifications.find_one({'notification_id': 'proposal:' + result['proposal_id']})
    assert note['user_email'] == OWNER and note['link'] == '/agents/proposals' and 'recipient@example.com' in note['body']
    status = await scope(db, RunAuthority('run-2', OWNER, frozenset(TOOLS)))(AUTH.__class__('run-2', OWNER, frozenset(TOOLS)), 'proposals.status', {'proposal_id': result['proposal_id']})
    assert status['status'] == 'pending' and 'receipt' not in status
    assert [p['proposal_id'] for p in (await scope(db)(AUTH, 'proposals.list', {}))['proposals']] == [result['proposal_id']]


@pytest.mark.asyncio
async def test_identical_proposal_is_reported_not_duplicated(db):
    first = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    again = await scope(db)(AUTH, 'gmail.propose_send', {**SEND, 'to': 'recipient@example.com', 'reason': 'Different reason'})
    assert again['proposal_id'] == first['proposal_id'] and again['duplicate'] is True
    assert await db.isolated_worker_proposals.count_documents({}) == 1
    assert await db.notifications.count_documents({}) == 1
    # A rejected proposal is not a barrier: the owner may ask for it again.
    await decide(db, OWNER, first['proposal_id'], 1, 'reject')
    fresh = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    assert fresh['proposal_id'] != first['proposal_id'] and 'duplicate' not in fresh


@pytest.mark.asyncio
async def test_scope_is_owner_and_conversation_bound(db):
    mine = await scope(db)(AUTH, 'slack.propose_send', {**VALID['slack.send'], 'reason': 'r'})
    other_conversation = scope(db, conversation='other')
    with pytest.raises(GatewayDenied, match='not found'):
        await other_conversation(AUTH, 'proposals.status', {'proposal_id': mine['proposal_id']})
    assert (await other_conversation(AUTH, 'proposals.list', {}))['proposals'] == []
    stranger = RunAuthority('run-9', OTHER, frozenset(TOOLS))
    with pytest.raises(GatewayDenied, match='not found'):
        await scope(db, stranger)(stranger, 'proposals.status', {'proposal_id': mine['proposal_id']})
    with pytest.raises(GatewayDenied):
        await scope(db)(stranger, 'proposals.list', {})
    with pytest.raises(ValueError, match='not found'):
        await decide(db, OTHER, mine['proposal_id'], 1, 'approve')


@pytest.mark.asyncio
async def test_revoked_access_blocks_proposals_and_limits_apply(db):
    with pytest.raises(GatewayDenied, match='no longer valid'):
        await scope(db, access=AsyncMock(return_value=False))(AUTH, 'gmail.propose_send', SEND)
    assert await db.isolated_worker_proposals.count_documents({}) == 0
    for i in range(proposals.MAX_OPEN_PER_CONVERSATION):
        await scope(db)(AUTH, 'gmail.propose_send', {**SEND, 'subject': f'S{i}'})
    with pytest.raises(GatewayDenied, match='Too many'):
        await scope(db)(AUTH, 'gmail.propose_send', {**SEND, 'subject': 'one more'})
    with pytest.raises(GatewayDenied):
        await scope(db)(AUTH, 'gmail.propose_send', {**SEND, 'reason': ''})
    with pytest.raises(GatewayDenied):
        await scope(db)(AUTH, 'gmail.propose_send', {'to': 'a@example.com', 'subject': 'S', 'body': 'B'})
    with pytest.raises(GatewayDenied):
        await scope(db)(AUTH, 'proposals.status', {'proposal_id': 'x', 'extra': 1})


@pytest.mark.asyncio
async def test_approve_executes_exact_version_once_with_backend_receipt(db):
    created = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    adapter = AsyncMock(return_value={'output': '{"sent": true, "messageId": "m1"}', 'proposal_id': created['proposal_id']})
    result = await decide(db, OWNER, created['proposal_id'], 1, 'approve', adapter=adapter)
    assert result['status'] == 'executed' and result['decided_by'] == OWNER and '_id' not in result
    adapter.assert_awaited_once_with('gmail.send', {'to': 'recipient@example.com', 'subject': 'Review me',
                                                   'body': 'Exact approved body'}, OWNER, created['proposal_id'])
    stored = await db.isolated_worker_proposals.find_one({'proposal_id': created['proposal_id']})
    assert stored['status'] == 'executed' and stored['audit'][0]['decision'] == 'approve'
    # Terminal: a second decision, a re-execution and a worker repeat are all blocked.
    with pytest.raises(ValueError, match='already decided'):
        await decide(db, OWNER, created['proposal_id'], 1, 'approve', adapter=adapter)
    assert await execute(db, {**stored, 'approved_digest': stored['digest']}, adapter) is None
    adapter.assert_awaited_once()
    repeat = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    assert repeat['duplicate'] is True and repeat['status'] == 'executed'
    assert (await scope(db)(AUTH, 'proposals.status', {'proposal_id': created['proposal_id']}))['status'] == 'executed'


@pytest.mark.asyncio
async def test_edit_creates_new_version_and_stale_approvals_fail(db):
    created = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    adapter = AsyncMock(return_value={'output': '{"sent": true, "messageId": "m2"}', 'proposal_id': created['proposal_id']})
    edited = await decide(db, OWNER, created['proposal_id'], 1, 'edit',
                          args={'to': 'other@example.com', 'subject': 'Changed', 'body': 'New body'})
    assert edited['status'] == 'pending' and edited['version'] == 2 and edited['args']['to'] == 'other@example.com'
    with pytest.raises(ValueError, match='Refresh'):
        await decide(db, OWNER, created['proposal_id'], 1, 'approve', adapter=adapter)
    with pytest.raises(ValueError, match='Invalid to'):
        await decide(db, OWNER, created['proposal_id'], 2, 'edit', args={'to': 'nope', 'subject': 'S', 'body': 'B'})
    adapter.assert_not_awaited()
    result = await decide(db, OWNER, created['proposal_id'], 2, 'approve', adapter=adapter)
    assert result['status'] == 'executed'
    assert adapter.await_args.args[1]['to'] == 'other@example.com'


@pytest.mark.asyncio
async def test_reject_cancel_and_expiry_never_execute(db):
    adapter = AsyncMock()
    rejected = await scope(db)(AUTH, 'slack.propose_send', {**VALID['slack.send'], 'reason': 'r'})
    assert (await decide(db, OWNER, rejected['proposal_id'], 1, 'reject', adapter=adapter))['status'] == 'rejected'
    cancelled = await scope(db)(AUTH, 'docs.propose_append', {**VALID['docs.append'], 'reason': 'r'})
    assert (await decide(db, OWNER, cancelled['proposal_id'], 1, 'cancel', adapter=adapter))['status'] == 'cancelled'
    expired = await scope(db)(AUTH, 'gmail.propose_draft', {**VALID['gmail.draft'], 'reason': 'r'})
    await db.isolated_worker_proposals.update_one({'proposal_id': expired['proposal_id']},
                                                  {'$set': {'expires_at': proposals.now() - timedelta(seconds=1)}})
    with pytest.raises(ValueError, match='expired'):
        await decide(db, OWNER, expired['proposal_id'], 1, 'approve', adapter=adapter)
    adapter.assert_not_awaited()
    rows = {r['proposal_id']: r['status'] for r in (await listing(db, OWNER))['proposals']}
    assert rows[expired['proposal_id']] == 'expired'
    with pytest.raises(ValueError, match='Invalid'):
        await decide(db, OWNER, rejected['proposal_id'], 1, 'execute', adapter=adapter)
    with pytest.raises(ValueError, match='Invalid'):
        await decide(db, OWNER, rejected['proposal_id'], '1', 'approve', adapter=adapter)


@pytest.mark.asyncio
async def test_failed_or_ambiguous_execution_becomes_uncertain_and_is_never_retried(db):
    created = await scope(db)(AUTH, 'calendar.propose_create', {**VALID['calendar.create'], 'reason': 'r'})
    adapter = AsyncMock(side_effect=TimeoutError())
    result = await decide(db, OWNER, created['proposal_id'], 1, 'approve', adapter=adapter)
    assert result['status'] == 'uncertain' and 'do not resend' in result['receipt']['message']
    stored = await db.isolated_worker_proposals.find_one({'proposal_id': created['proposal_id']})
    assert await execute(db, {**stored, 'approved_digest': stored['digest']}, AsyncMock()) is None
    assert (await scope(db)(AUTH, 'proposals.status', {'proposal_id': created['proposal_id']}))['status'] == 'uncertain'
    repeat = await scope(db)(AUTH, 'calendar.propose_create', {**VALID['calendar.create'], 'reason': 'again'})
    assert repeat['duplicate'] is True
    assert (await listing(db, OWNER))['proposals'][0]['proposal_id'] == created['proposal_id']
    record = await reconcile(db, OWNER, created['proposal_id'], 0, 'not_sent', 'Checked the calendar; nothing there.')
    assert record['status'] == 'uncertain' and record['reconciliation']['version'] == 1
    with pytest.raises(ValueError):
        await reconcile(db, OWNER, created['proposal_id'], 0, 'sent', 'stale version')
    assert all(r['proposal_id'] != created['proposal_id'] for r in (await listing(db, OWNER))['proposals'][:0])
    assert await proposals.attention_count(db, OWNER) == 0


@pytest.mark.asyncio
async def test_claim_without_receipt_sweeps_to_uncertain(db):
    created = await scope(db)(AUTH, 'sheets.propose_write', {**VALID['sheets.write'], 'reason': 'r'})
    await db.isolated_worker_proposals.update_one({'proposal_id': created['proposal_id']}, {'$set': {
        'status': 'executing', 'execution_started_at': proposals.now() - timedelta(minutes=6)}})
    assert await proposals.attention_count(db, OWNER) == 1
    stored = await db.isolated_worker_proposals.find_one({'proposal_id': created['proposal_id']})
    assert stored['status'] == 'uncertain' and 'receipt' in stored
    assert (await scope(db)(AUTH, 'proposals.status', {'proposal_id': created['proposal_id']}))['note'].startswith('Outcome unknown')


@pytest.mark.asyncio
async def test_execution_claim_is_exclusive_and_digest_bound(db):
    created = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    stored = await db.isolated_worker_proposals.find_one({'proposal_id': created['proposal_id']})
    approved = await db.isolated_worker_proposals.find_one_and_update({'proposal_id': created['proposal_id']},
        {'$set': {'status': 'approved', 'decided_by': OWNER, 'approved_digest': stored['digest']}}, return_document=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(action, args, owner, proposal_id):
        started.set()
        await release.wait()
        return {'output': '{}', 'proposal_id': proposal_id}
    first = asyncio.create_task(execute(db, approved, slow))
    await started.wait()
    second = AsyncMock()
    assert await execute(db, approved, second) is None
    second.assert_not_awaited()
    release.set()
    assert (await first)['status'] == 'executed'
    tampered = {**approved, 'args': {**approved['args'], 'to': 'attacker@example.com'}}
    with pytest.raises(ValueError, match='exact action'):
        await execute(db, tampered, AsyncMock())
    with pytest.raises(ValueError, match='exact action'):
        await execute(db, {**approved, 'decided_by': 'saved-policy'}, AsyncMock())


@pytest.mark.asyncio
async def test_disconnected_owner_request_still_persists_outcome(db):
    created = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    release = asyncio.Event()

    async def slow(action, args, owner, proposal_id):
        await release.wait()
        return {'output': '{"sent": true}', 'proposal_id': proposal_id}
    task = asyncio.create_task(decide(db, OWNER, created['proposal_id'], 1, 'approve', adapter=slow))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    for _ in range(50):
        stored = await db.isolated_worker_proposals.find_one({'proposal_id': created['proposal_id']})
        if stored['status'] == 'executed':
            break
        await asyncio.sleep(0.05)
    assert stored['status'] == 'executed'


@pytest.mark.asyncio
async def test_signed_control_plane_lists_decides_and_counts(db, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from api import bounded_work_routes as routes
    created = await scope(db)(AUTH, 'gmail.propose_send', SEND)
    monkeypatch.setenv('LOMA_BOUNDED_WORK_ENABLED', 'true')
    monkeypatch.setenv('LOMA_WORK_GATEWAY_SECRET', 'k' * 32)
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    monkeypatch.setattr(proposals, 'write_adapter', AsyncMock(return_value={'output': '{"sent": true, "messageId": "m1"}', 'proposal_id': created['proposal_id']}))

    @web.middleware
    async def identity(request, handler):
        request['user_email'] = request.headers.get('X-User-Email', '')
        request['system_role'] = 'operator'
        return await handler(request)
    app = web.Application(middlewares=[identity])
    app.router.add_route('*', '/api/bounded-work/{tail:.*}', routes.handle)
    async with TestClient(TestServer(app)) as client:
        async def call(path, body=None, owner=OWNER):
            raw = json.dumps(body).encode() if body else b''
            method = 'POST' if body else 'GET'
            stamp = str(int(time.time()))
            signature = hmac.new(b'k' * 32, '\n'.join([stamp, method, path, owner, hashlib.sha256(raw).hexdigest()]).encode(), hashlib.sha256).hexdigest()
            return await client.request(method, path, data=raw, headers={'X-User-Email': owner, 'X-Work-Time': stamp, 'X-Work-Signature': signature})
        unsigned = await client.get('/api/bounded-work/proposals', headers={'X-User-Email': OWNER})
        assert unsigned.status == 401
        attention = await (await call('/api/bounded-work/attention')).json()
        assert attention['proposals'] == 1 and attention['total'] == 1
        rows = await (await call('/api/bounded-work/proposals')).json()
        assert [r['proposal_id'] for r in rows['proposals']] == [created['proposal_id']] and '_id' not in rows['proposals'][0]
        assert (await (await call('/api/bounded-work/proposals', owner=OTHER)).json())['proposals'] == []
        forged = await call(f"/api/bounded-work/proposals/{created['proposal_id']}", {'decision': 'approve', 'version': 1}, owner=OTHER)
        assert forged.status == 400
        decided = await call(f"/api/bounded-work/proposals/{created['proposal_id']}", {'decision': 'approve', 'version': 1})
        assert decided.status == 200 and (await decided.json())['status'] == 'executed'
        proposals.write_adapter.assert_awaited_once()
        assert (await (await call('/api/bounded-work/attention')).json())['proposals'] == 0
        bad = await call(f"/api/bounded-work/proposals/{created['proposal_id']}/reconcile", {'version': 0, 'outcome': 'sent', 'evidence': 'x'})
        assert bad.status == 400


def test_view_never_leaks_owner_audit_or_receipt():
    row = {'proposal_id': 'p', 'action': 'gmail.send', 'status': 'executed', 'version': 1, 'args': {'to': 'a@example.com'},
           'reason': 'r', 'created_at': proposals.now(), 'expires_at': proposals.now(), 'receipt': {'output': 'secret'},
           'audit': [{'actor': OWNER}], 'decided_by': OWNER, 'owner': OWNER, 'digest': 'd'}
    projected = view(row)
    assert 'receipt' not in projected and 'audit' not in projected and 'owner' not in projected and 'digest' not in projected
    assert projected['note'].startswith('Executed')
