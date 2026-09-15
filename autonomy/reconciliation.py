"""Bounded read-only reconciliation. Never refunds, resends or resumes work."""
import asyncio
import hashlib
from datetime import timedelta
from email.utils import getaddresses

from pymongo import ReturnDocument
from autonomy import core

MAX_CHECKS = 5


def message_id(action_id):
    return '<loma-' + hashlib.sha256(action_id.encode()).hexdigest() + '@actions.loma.invalid>'


async def lookup(owner, rfc_message_id):
    from autonomy.connector import gmail
    return await gmail(['check-sent', '--rfc-message-id', rfc_message_id], owner)


def matches(result, proposal):
    """Only positive, exact evidence resolves uncertainty; no fuzzy matching."""
    args = proposal['args']
    return (isinstance(result, dict) and result.get('outcome') == 'candidate'
            and result.get('rfc_message_id') == proposal['provider_message_id']
            and isinstance(result.get('message_id'), str) and bool(result['message_id'])
            and getaddresses([result.get('to', '')]) == [('', args['to'])]
            and not result.get('cc') and not result.get('bcc')
            and result.get('subject') == args['subject']
            and isinstance(result.get('body'), str)
            and result['body'].replace('\r\n', '\n') == args['body'].replace('\r\n', '\n'))


async def check_one(db, proposal, provider=lookup):
    """No new read authority: both Gmail reads must already be auto-allowed.

    Recheck the active account, current work grant and agent sharing, including
    after the provider returns. Ended runs may be inspected, revoked work may
    not. Missing historical correlation IDs never trigger speculative lookup.
    """
    if (proposal.get('status') != 'uncertain' or proposal.get('action') != 'gmail.send'
            or proposal.get('provider_message_id') != message_id(proposal['approval_id'])):
        return False
    owner = proposal['owner']

    async def authorized():
        await core.authority(db, owner, proposal.get('agent_id') or run['snapshot']['agent_id'])
        work = await db.agent_work.find_one({'work_id': proposal['work_id'], 'owner': owner})
        if not work or work.get('revoked') or run.get('dry_run'):
            raise ValueError('Work access revoked or preview only')
        for policy in (work['policy'], run['snapshot']['policy']):
            if any(policy['actions'].get(a) != 'allow' for a in ('gmail.search', 'gmail.read')):
                raise ValueError('Automatic provider checks need saved read permission')

    run = await db.agent_runs.find_one({'run_id': proposal['run_id'], 'owner': owner})
    if not run:
        return False
    try:
        await authorized()
    except ValueError:
        # Do not let inaccessible old records starve later eligible checks.
        await db.agent_approvals.update_one({'approval_id': proposal['approval_id'], 'owner': owner,
            'provider_check.outcome': {'$ne': 'sent'}}, {'$set': {'provider_check.next_at': core.now() + timedelta(hours=1)}})
        return False
    at, claim = core.now(), core.ident()
    claimed = await db.agent_approvals.find_one_and_update({
        'approval_id': proposal['approval_id'], 'owner': owner, 'status': 'uncertain',
        'provider_check.outcome': {'$ne': 'sent'},
        '$and': [
            {'$or': [{'provider_check.attempts': {'$exists': False}}, {'provider_check.attempts': {'$lt': MAX_CHECKS}}]},
            {'$or': [{'provider_check.next_at': {'$exists': False}}, {'provider_check.next_at': {'$lte': at}}]},
        ]}, {'$set': {'provider_check.claim': claim, 'provider_check.next_at': at + timedelta(minutes=5)},
             '$inc': {'provider_check.attempts': 1}}, return_document=ReturnDocument.AFTER)
    if not claimed:
        return False
    try:
        result = await asyncio.wait_for(provider(owner, proposal['provider_message_id']), 50)
        await authorized()
        outcome = 'sent' if matches(result, proposal) else 'unresolved'
        evidence = {'outcome': outcome, 'checked_at': core.now(), 'source': 'gmail',
                    'message_id': result['message_id'] if outcome == 'sent' else None}
    except Exception:
        evidence = {'outcome': 'unavailable', 'checked_at': core.now(), 'source': 'gmail'}
    # Preserve original unknown receipt, owner reports, and the resend barrier.
    # A late result cannot overwrite a later worker's claim.
    await db.agent_approvals.update_one({'approval_id': proposal['approval_id'], 'status': 'uncertain',
        'provider_check.claim': claim}, {'$set': {f'provider_check.{k}': v for k, v in evidence.items()},
        '$push': {'provider_check_history': evidence}})
    return True


async def sweep(db, provider=lookup):
    candidates = await db.agent_approvals.find({'status': 'uncertain', 'action': 'gmail.send',
        'provider_message_id': {'$type': 'string'}, 'provider_check.outcome': {'$ne': 'sent'},
        '$and': [
            {'$or': [{'provider_check.attempts': {'$exists': False}}, {'provider_check.attempts': {'$lt': MAX_CHECKS}}]},
            {'$or': [{'provider_check.next_at': {'$exists': False}}, {'provider_check.next_at': {'$lte': core.now()}}]},
        ]}).sort('created_at', 1).limit(10).to_list(10)
    for proposal in candidates:
        await check_one(db, proposal, provider)
