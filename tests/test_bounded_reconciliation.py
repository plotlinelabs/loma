"""No live Google calls. Exact-match proof and read-only, bounded repair."""
import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from autonomy import core, worker, reconciliation as recon, connector
from tests.test_bounded_work import db, setup, OWNER, OTHER, SEND


def candidate(proposal):
    return {'outcome': 'candidate', 'rfc_message_id': proposal['provider_message_id'],
            'message_id': 'gmail-id', 'cc': '', 'bcc': '', **proposal['args']}


async def uncertain(db):
    _, run = await setup(db)
    await worker.tick(db, planner=AsyncMock(return_value=SEND))
    proposal = await db.agent_approvals.find_one({'run_id': run['run_id']})
    await core.decide(db, OWNER, proposal['approval_id'], 1, 'approve')
    await worker.tick(db, broker=AsyncMock(side_effect=TimeoutError()))
    proposal = await db.agent_approvals.find_one({'run_id': run['run_id']})
    assert proposal['status'] == 'uncertain'
    assert proposal['provider_message_id'] == recon.message_id(proposal['approval_id'])
    return proposal


@pytest.mark.asyncio
async def test_positive_reconciliation_preserves_replay_barrier_and_owner_report(db):
    p = await uncertain(db)
    await core.reconcile(db, OWNER, p['approval_id'], 0, 'not_sent', 'Owner believed it was not sent')
    provider = AsyncMock(return_value=candidate(p))
    await recon.sweep(db, provider)
    saved = await db.agent_approvals.find_one({'approval_id': p['approval_id']})
    assert saved['provider_check']['outcome'] == 'sent'
    assert saved['status'] == 'uncertain'
    assert saved['receipt'] == p['receipt']
    assert saved['reconciliation']['outcome'] == 'not_sent'
    assert (await db.agent_runs.find_one({'run_id': p['run_id']}))['status'] == 'failed'
    await recon.sweep(db, provider)
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_provider_checks_only_one_claim(db):
    p = await uncertain(db)
    provider = AsyncMock(return_value=candidate(p))
    await asyncio.gather(*[recon.check_one(db, p, provider) for _ in range(8)])
    provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['inactive', 'revoked', 'read_denied', 'read_ask', 'dry_run', 'legacy', 'wrong_owner'])
async def test_reconciliation_rejects_missing_authority(db, change):
    p = await uncertain(db)
    if change == 'inactive':
        await db.users.update_one({'email': OWNER}, {'$set': {'status': 'inactive'}})
    elif change == 'revoked':
        await db.agent_work.update_one({'work_id': p['work_id']}, {'$set': {'revoked': True}})
    elif change.startswith('read_'):
        work = await db.agent_work.find_one({'work_id': p['work_id']})
        work['policy']['actions']['gmail.read'] = 'ask' if change == 'read_ask' else 'deny'
        await db.agent_work.update_one({'work_id': p['work_id']}, {'$set': {'policy': work['policy']}})
    elif change == 'dry_run':
        await db.agent_runs.update_one({'run_id': p['run_id']}, {'$set': {'dry_run': True}})
    elif change == 'legacy':
        p.pop('provider_message_id')
    else:
        p['owner'] = OTHER
    provider = AsyncMock(return_value=candidate({**p, 'provider_message_id': 'unused'}))
    assert not await recon.check_one(db, p, provider)
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_result_never_proves_not_sent_and_checks_are_bounded(db):
    p = await uncertain(db)
    provider = AsyncMock(return_value={'outcome': 'not_found'})
    for _ in range(9):
        await db.agent_approvals.update_one({'approval_id': p['approval_id']}, {'$unset': {'provider_check.next_at': ''}})
        await recon.sweep(db, provider)
    assert provider.await_count == 5
    saved = await db.agent_approvals.find_one({'approval_id': p['approval_id']})
    assert saved['status'] == 'uncertain'
    assert saved['provider_check']['outcome'] == 'unresolved'
    assert len(saved['provider_check_history']) == 5


@pytest.mark.asyncio
async def test_revocation_while_checking_discards_provider_result(db):
    p = await uncertain(db)
    async def provider(*args):
        await db.users.update_one({'email': OWNER}, {'$set': {'status': 'inactive'}})
        return candidate(p)
    await recon.check_one(db, p, provider)
    saved = await db.agent_approvals.find_one({'approval_id': p['approval_id']})
    assert saved['provider_check']['outcome'] == 'unavailable'
    assert saved['status'] == 'uncertain'


@pytest.mark.parametrize('field,value', [('to','wrong@example.com'), ('cc','hidden@example.com'),
    ('bcc','hidden@example.com'), ('body','changed'), ('subject','changed'),
    ('rfc_message_id','wrong'), ('message_id',''), ('outcome','ambiguous')])
def test_exact_match_only(field, value):
    p = {'provider_message_id': recon.message_id('a'), 'args': SEND['args']}
    value_dict = candidate(p)
    assert recon.matches(value_dict, p)
    value_dict[field] = value
    assert not recon.matches(value_dict, p)


def test_connector_environment_excludes_runtime_and_proxy_secrets(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'must-not-leak')
    monkeypatch.setenv('GITHUB_API_KEY', 'must-not-leak')
    monkeypatch.setenv('HTTPS_PROXY', 'must-not-leak')
    monkeypatch.setenv('PYTHONPATH', '/malicious')
    env = connector.environment()
    assert not {'ANTHROPIC_API_KEY', 'GITHUB_API_KEY', 'HTTPS_PROXY', 'PYTHONPATH'} & set(env)
    assert env['PYTHON_DOTENV_DISABLED'] == '1'


@pytest.mark.asyncio
async def test_gmail_send_stamps_id_before_provider_call(monkeypatch):
    from tools import gmail
    from email import message_from_bytes
    service = MagicMock()
    service.users().messages().send().execute.return_value = {'id': 'real-id'}
    monkeypatch.setattr(gmail, '_get_service', AsyncMock(return_value=service))
    mid = recon.message_id('exact-action')
    await gmail.send_email(OWNER, 'recipient@example.com', 'Subject', 'Body', rfc_message_id=mid)
    raw = service.users().messages().send.call_args.kwargs['body']['raw']
    assert message_from_bytes(base64.urlsafe_b64decode(raw))['Message-ID'] == mid
    with pytest.raises(ValueError):
        await gmail.send_email(OWNER, 'r@example.com', 'Subject', 'Body', rfc_message_id='bad\nBcc: stolen')


@pytest.mark.asyncio
async def test_gmail_check_is_read_only_and_searches_sent(monkeypatch):
    from tools import gmail
    service = MagicMock()
    messages = service.users().messages()
    messages.list().execute.return_value = {'messages': []}
    monkeypatch.setattr(gmail, '_get_service', AsyncMock(return_value=service))
    mid = recon.message_id('exact-action')
    assert await gmail.check_sent(OWNER, mid) == {'outcome': 'not_found'}
    assert messages.list.call_args.kwargs == {'userId': 'me', 'q': 'rfc822msgid:' + mid, 'labelIds': ['SENT'], 'maxResults': 2}
    messages.send.assert_not_called()
    messages.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('response,expected', [
    ({'messages': [], 'nextPageToken': 'next'}, 'ambiguous'),
    ({'messages': [{'id': 'a'}, {'id': 'b'}]}, 'ambiguous'),
])
async def test_gmail_check_ambiguous_never_fetches_or_sends(monkeypatch, response, expected):
    from tools import gmail
    service = MagicMock()
    messages = service.users().messages()
    messages.list().execute.return_value = response
    monkeypatch.setattr(gmail, '_get_service', AsyncMock(return_value=service))
    assert (await gmail.check_sent(OWNER, recon.message_id('a')))['outcome'] == expected
    messages.get.assert_not_called()
    messages.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('labels,header,expected', [(['SENT'], True, 'candidate'), ([], True, 'mismatch'), (['SENT'], False, 'mismatch')])
async def test_gmail_candidate_requires_sent_label_and_exact_header(monkeypatch, labels, header, expected):
    from tools import gmail
    service = MagicMock()
    messages = service.users().messages()
    mid = recon.message_id('a')
    messages.list().execute.return_value = {'messages': [{'id': 'a'}]}
    messages.get().execute.return_value = {'id': 'a', 'labelIds': labels, 'payload': {
        'headers': [{'name': 'Message-ID', 'value': mid if header else 'wrong'}],
        'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(b'Body').decode()}}}
    monkeypatch.setattr(gmail, '_get_service', AsyncMock(return_value=service))
    assert (await gmail.check_sent(OWNER, mid))['outcome'] == expected
    messages.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('oversize', [False, True])
async def test_connector_dispatch_is_fixed_bounded_and_uses_isolated_environment(monkeypatch, oversize):
    from tools import _auth_token
    from pathlib import Path
    monkeypatch.setattr(_auth_token, 'create_user_auth_token', lambda owner: 'QA-TOKEN')
    proc = MagicMock()
    proc.returncode = None
    proc.stdout.read = AsyncMock(side_effect=[b'x' * (connector.MAX_OUTPUT + 1)] if oversize else [b'{"ok":true}', b''])
    async def wait():
        proc.returncode = 0
    proc.wait = AsyncMock(side_effect=wait)
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    if oversize:
        with pytest.raises(ValueError, match='size limit'):
            await connector.gmail(['search', '--query', 'hello'], OWNER)
        proc.kill.assert_called_once()
    else:
        assert await connector.gmail(['search', '--query', 'hello'], OWNER) == {'ok': True}
    call = spawn.call_args
    assert call.args[1] == '-I'
    assert call.args[-2:] == ('--user-email', OWNER)
    assert call.kwargs['stdin'] == asyncio.subprocess.DEVNULL
    assert call.kwargs['stderr'] == asyncio.subprocess.DEVNULL
    assert call.kwargs['env'] == connector.environment()
    assert not Path(call.kwargs['cwd']).exists()  # private cwd cleaned up


@pytest.mark.asyncio
async def test_late_claim_cannot_overwrite_newer_provider_evidence(db):
    p = await uncertain(db)
    async def provider(*args):
        await db.agent_approvals.update_one({'approval_id': p['approval_id']}, {'$set': {
            'provider_check.claim': 'new-claim', 'provider_check.outcome': 'unresolved'}})
        return candidate(p)
    await recon.check_one(db, p, provider)
    saved = await db.agent_approvals.find_one({'approval_id': p['approval_id']})
    assert saved['provider_check']['outcome'] == 'unresolved'
    assert 'provider_check_history' not in saved
