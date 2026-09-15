"""Durable, default-deny agent work and exact-action authorizations.

The model is a JSON planner, not a privileged process. Only the broker can run
registered actions. All state transitions use Mongo CAS; writes with unknown
outcomes are never retried. The task document holds the bounded event log so a
checkpoint and its audit event commit together without a multi-doc transaction.
"""
import copy
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from api.agent_identity_routes import resolve_agent_for_chat
from scheduler.run_identity import require_execution_account

ACTIONS = {'gmail.search', 'gmail.read', 'gmail.send', 'slack.send', 'calendar.list'}
WRITE_ACTIONS = {'gmail.send', 'slack.send'}
TERMINAL = ('done', 'cancelled', 'failed')
MODES = ('allow', 'ask', 'deny')
MAX_STEPS = 30
DEFAULT_RUNTIME_MINUTES = 1440
DEADLINE_MESSAGE = "Run deadline reached. Waiting approvals and delegates cannot extend it."


def now():
    return datetime.now(timezone.utc)


def ident():
    return str(uuid.uuid4())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def text(value, name, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1 to {limit} characters')
    return value.strip()


def deadline(run):
    """Older queued runs also receive a finite deadline, anchored to creation."""
    value = run.get("deadline_at")
    if value is None:
        value = run["created_at"] + timedelta(minutes=run["snapshot"].get("max_runtime_minutes", DEFAULT_RUNTIME_MINUTES))
    return value.replace(tzinfo=timezone.utc)


def validate_limits(data):
    minutes = data.get("max_runtime_minutes", DEFAULT_RUNTIME_MINUTES)
    retries = data.get("max_planner_retries", 2)
    if type(minutes) is not int or not 1 <= minutes <= 43200:
        raise ValueError("Run deadline must be between 1 and 43200 minutes")
    if type(retries) is not int or not 0 <= retries <= 3:
        raise ValueError("Model retries must be between 0 and 3")
    return minutes, retries


def validate_policy(value):
    if not isinstance(value, dict) or set(value) - {'actions', 'recipients', 'channels'}:
        raise ValueError('Invalid permission policy')
    actions = value.get('actions', {})
    if not isinstance(actions, dict) or set(actions) - ACTIONS or any(v not in MODES for v in actions.values()):
        raise ValueError('Unknown action or permission')
    recipients = value.get('recipients', [])
    if not isinstance(recipients, list) or len(recipients) > 50:
        raise ValueError('Choose up to 50 exact recipient addresses')
    recipients = sorted(set(text(r, 'Recipient', 254).lower() for r in recipients))
    if any('*' in r or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', r) for r in recipients):
        raise ValueError('Use exact email addresses, not names or wildcards')
    channels = value.get('channels', [])
    if not isinstance(channels, list) or len(channels) > 20:
        raise ValueError('Choose up to 20 exact Slack channel IDs')
    channels = sorted(set(text(c, 'Channel', 30) for c in channels))
    if any(not re.fullmatch(r'[CDG][A-Z0-9]{4,25}', c) for c in channels):
        raise ValueError('Use exact Slack channel IDs (like C0123ABCD), not names or wildcards')
    return {'actions': {a: actions.get(a, 'deny') for a in sorted(ACTIONS)}, 'recipients': recipients,
            'channels': channels}


def intersect(left, right):
    rank = {'allow': 0, 'ask': 1, 'deny': 2}
    return {'actions': {a: max(left['actions'][a], right['actions'][a], key=rank.get) for a in ACTIONS},
            'recipients': sorted(set(left['recipients']) & set(right['recipients'])),
            'channels': sorted(set(left.get('channels', [])) & set(right.get('channels', [])))}


def validate_action(action, args, policy):
    if action not in ACTIONS or policy['actions'].get(action, 'deny') == 'deny':
        raise ValueError('This action is not permitted')
    if not isinstance(args, dict):
        raise ValueError('Action arguments must be an object')
    keys = {'gmail.send': {'to', 'subject', 'body'}, 'gmail.read': {'message_id'}, 'gmail.search': {'query'},
            'slack.send': {'channel', 'text'}, 'calendar.list': set()}[action]
    if set(args) != keys:
        raise ValueError('Unexpected or missing action arguments')
    args = {k: text(v, k, 12000 if k in ('body', 'text') else 1000) for k, v in args.items()}
    if action == 'gmail.send':
        args['to'] = args['to'].lower()
        if args['to'] not in policy['recipients']:
            raise ValueError('Recipient is outside the approved recipient list')
        if any(c in args['subject'] for c in '\r\n'):
            raise ValueError('Subject cannot contain line breaks')
    if action == 'slack.send':
        if len(args['text']) > 4000:
            raise ValueError('Slack messages are limited to 4000 characters')
        if args['channel'] not in policy.get('channels', []):
            raise ValueError('Channel is outside the approved channel list')
    return args


async def indexes(db):
    await db.flows.create_index('bounded_work_id', unique=True,
                                partialFilterExpression={'bounded_work_id': {'$type': 'string'}})
    await db.agent_work.create_index('work_id', unique=True)
    await db.agent_work.create_index([('owner', 1), ('created_at', -1)])
    await db.agent_runs.create_index('run_id', unique=True)
    await db.agent_runs.create_index([('work_id', 1), ('event_key', 1)], unique=True)
    await db.agent_runs.create_index([('status', 1), ('wake_at', 1)])
    await db.agent_runs.create_index([('status', 1), ('deadline_at', 1)])
    await db.agent_approvals.create_index('approval_id', unique=True)
    await db.agent_approvals.create_index([('run_id', 1), ('step', 1)], unique=True)
    await db.agent_knowledge.create_index('source_id', unique=True)
    await db.agent_notes.create_index([('owner', 1), ('agent_id', 1), ('note_id', 1)], unique=True)


async def authority_account(db, owner):
    await require_execution_account(db, {'run_as': owner})


async def authority(db, owner, agent_id):
    await authority_account(db, owner)
    agent = await resolve_agent_for_chat(db, agent_id, owner)
    if not agent:
        raise ValueError('Agent is disabled, deleted or no longer shared with your account')
    return agent


async def create_work(db, owner, data):
    agent = await authority(db, owner, text(data.get('agent_id'), 'Agent', 100))
    from autonomy import knowledge
    sources = knowledge.references(data.get('knowledge_ids', []))
    await knowledge.resolve(db, owner, sources)
    policy = validate_policy(data.get('policy', {}))
    peers = data.get('delegates', [])
    if not isinstance(peers, list) or len(peers) > 5 or not all(isinstance(p, str) for p in peers):
        raise ValueError('Choose at most five delegate agents')
    for peer in peers:
        await authority(db, owner, peer)
    budget = data.get('max_steps', 10)
    if type(budget) is not int or not 1 <= budget <= MAX_STEPS:
        raise ValueError('Step budget must be between 1 and 30')
    from autonomy.costs import validate_budget, DEFAULT_BUDGET_MICROUSD
    cost_budget = validate_budget(data.get('max_cost_microusd', DEFAULT_BUDGET_MICROUSD))
    minutes, retries = validate_limits(data)
    work = {'work_id': ident(), 'owner': owner, 'agent_id': agent['agent_id'],
            'title': text(data.get('title'), 'Job title', 120),
            'instructions': text(data.get('instructions'), 'Instructions'),
            'success': text(data.get('success'), 'Expected result', 2000),
            'knowledge_ids': sources, 'policy': policy, 'delegates': list(set(peers)), 'max_steps': budget,
            'max_runtime_minutes': minutes, 'max_planner_retries': retries, 'max_cost_microusd': cost_budget,
            'agent_snapshot': {'name': agent['name'], 'instructions': agent.get('identity_prompt', ''),
                               'version': str(agent.get('updated_at', ''))},
            'created_at': now(), 'paused': True, 'version': 1}
    await db.agent_work.insert_one(copy.deepcopy(work))
    return work


async def enqueue(db, work, event_key, *, dry_run=False, parent=None, note=None):
    await authority(db, work['owner'], work['agent_id'])
    if work.get('revoked'):
        raise ValueError('Work access has been revoked')
    event_key = text(event_key, 'Event ID', 200)
    if note is not None:
        note = text(note, 'Event note', 2000)
    if work.get('paused') and not dry_run and parent is None:
        raise ValueError('Enable this work before starting a run')
    # One stable identity per trigger; database unique index handles races.
    snapshot = copy.deepcopy(work)
    for secret_field in ('event_token_hash', 'event_token_rotated_at'):
        snapshot.pop(secret_field, None)
    run = {'run_id': str(uuid.uuid5(uuid.NAMESPACE_URL, work['work_id'] + ':' + event_key)), 'work_id': work['work_id'], 'owner': work['owner'],
           'event_key': event_key, 'snapshot': snapshot, 'status': 'queued',
           'dry_run': dry_run, 'step': 0, 'history': [], 'created_at': now(), 'wake_at': now(),
           'calls_used': 0, 'max_steps': work['max_steps'], 'parent_id': parent,
           'event_note': note, 'root_id': None, 'result': '', 'lease': None, 'lease_until': now()}
    run['root_id'] = parent or run['run_id']
    run['deadline_at'] = deadline(run)
    if parent:
        root = await db.agent_runs.find_one({'run_id': parent, 'owner': work['owner']})
        if not root or root['status'] in TERMINAL or deadline(root) <= now():
            raise ValueError('Parent work is no longer active')
        run['deadline_at'] = min(run['deadline_at'], deadline(root))
    existing = await db.agent_runs.find_one({'work_id': work['work_id'], 'event_key': event_key})
    if existing:
        return existing
    slot = await db.agent_work.find_one_and_update(
        {'work_id': work['work_id'], '$or': [{'active_run': None}, {'active_run': run['run_id']}]},
        {'$set': {'active_run': run['run_id'], 'reserved_at': now()}}, return_document=ReturnDocument.AFTER)
    if not slot:
        raise ValueError('This job already has active work. Finish or stop it before starting another run.')
    result = await db.agent_runs.find_one_and_update(
        {'work_id': work['work_id'], 'event_key': event_key}, {'$setOnInsert': run},
        upsert=True, return_document=ReturnDocument.AFTER)
    return result


async def current_authority(db, run, *, check_lease=True):
    current = await db.agent_runs.find_one({'run_id': run['run_id'], 'owner': run['owner']})
    if not current or current['status'] in TERMINAL:
        raise ValueError('Run is no longer active')
    if deadline(current) <= now():
        raise ValueError(DEADLINE_MESSAGE)
    if check_lease and run.get('lease') and (current.get('lease') != run['lease'] or current['lease_until'].replace(tzinfo=timezone.utc) <= now()):
        raise ValueError('Worker lease was lost')
    work = await db.agent_work.find_one({'work_id': run['work_id'], 'owner': run['owner']})
    if not work or work.get('revoked'):
        raise ValueError('Work access has been revoked')
    await authority(db, run['owner'], run['snapshot']['agent_id'])
    root = await db.agent_runs.find_one({'run_id': run['root_id']})
    if not root or root.get('owner') != run['owner'] or root['status'] in ('cancelled', 'failed'):
        raise ValueError('Parent work was cancelled or failed')
    if deadline(root) <= now():
        raise ValueError(DEADLINE_MESSAGE)
    if run.get('parent_id'):
        root_work = await db.agent_work.find_one({'work_id': root['work_id'], 'owner': run['owner']})
        if not root_work or root_work.get('revoked') or root['status'] in TERMINAL:
            raise ValueError('Parent work is no longer active')
        await authority(db, run['owner'], root['snapshot']['agent_id'])
    from autonomy import knowledge
    await knowledge.resolve(db, run['owner'], run['snapshot'].get('knowledge_ids', []))
    return work


async def effective_policy(db, run, *, check_lease=True):
    """Saved grants are a ceiling, not a way to bypass current revocation.

    Delegates also intersect the root's current grant. No peer identity, later
    edit or stale snapshot can increase the principal's original authority.
    """
    work = await current_authority(db, run, check_lease=check_lease)
    policy = intersect(validate_policy(run['snapshot']['policy']), validate_policy(work['policy']))
    if run.get('parent_id'):
        root = await db.agent_runs.find_one({'run_id': run['root_id'], 'owner': run['owner']})
        if not root or root['status'] in TERMINAL:
            raise ValueError('Parent work is no longer active')
        root_work = await current_authority(db, root, check_lease=False)
        policy = intersect(policy, intersect(validate_policy(root['snapshot']['policy']),
                                            validate_policy(root_work['policy'])))
    return policy


async def advance(db, run, fields, event=None):
    update = {'$set': {**fields, 'updated_at': now(), 'notification_status': ''}}
    if event:
        update['$push'] = {'history': {'at': now(), **event}}
    result = await db.agent_runs.update_one(
        {'run_id': run['run_id'], 'lease': run['lease'], 'status': 'running'}, update)
    if result.modified_count != 1:
        raise ValueError('Run changed or worker lease was lost')


async def propose(db, run, action, args, reason):
    args = validate_action(action, args, await effective_policy(db, run))
    proposal = {'approval_id': ident(), 'run_id': run['run_id'], 'work_id': run['work_id'],
                'owner': run['owner'], 'step': run['step'], 'action': action, 'args': args,
                'reason': text(reason, 'Reason', 2000), 'version': 1,
                'status': 'pending', 'created_at': now(), 'expires_at': min(now() + timedelta(hours=24), deadline(run))}
    proposal['digest'] = digest({'action': action, 'args': args, 'owner': run['owner']})
    repeated = await db.agent_approvals.find_one({'run_id': run['run_id'], 'step': {'$ne': run['step']},
        'digest': proposal['digest'], 'status': {'$in': ['rejected', 'executed', 'uncertain', 'executing']}})
    uncertain = await db.agent_approvals.find_one({'work_id': run['work_id'], 'digest': proposal['digest'],
        'status': {'$in': ['uncertain', 'executing']}, 'run_id': {'$ne': run['run_id']}})
    if repeated or uncertain:
        raise ValueError('This action was already handled or has an unknown outcome. Review history instead of repeating it.')
    # Same step always reuses its saved proposal, even after a worker crash.
    return await db.agent_approvals.find_one_and_update(
        {'run_id': run['run_id'], 'step': run['step']}, {'$setOnInsert': proposal},
        upsert=True, return_document=ReturnDocument.AFTER)


async def decide(db, owner, approval_id, version, decision, args=None):
    if decision not in ('approve', 'reject', 'edit', 'cancel') or type(version) is not int:
        raise ValueError('Invalid approval decision')
    proposal = await db.agent_approvals.find_one({'approval_id': approval_id, 'owner': owner})
    if not proposal:
        raise ValueError('Approval not found')
    run = await db.agent_runs.find_one({'run_id': proposal['run_id'], 'owner': owner})
    if not run or run['status'] in TERMINAL:
        raise ValueError('The parent run is no longer active')
    if decision in ('approve', 'edit'):
        policy = await effective_policy(db, run, check_lease=False)
        validate_action(proposal['action'], args if decision == 'edit' else proposal['args'], policy)
    changes = {'updated_at': now(), 'decided_by': owner}
    if decision == 'edit':
        args = validate_action(proposal['action'], args, run['snapshot']['policy'])
        changes.update(args=args, version=version + 1, status='pending',
                       digest=digest({'action': proposal['action'], 'args': args, 'owner': owner}),
                       expires_at=min(now() + timedelta(hours=24), deadline(run)))
    else:
        changes['status'] = {'approve': 'approved', 'reject': 'rejected', 'cancel': 'cancelled'}[decision]
        if decision == 'approve':
            changes['approved_digest'] = proposal['digest']
    result = await db.agent_approvals.find_one_and_update(
        {'approval_id': approval_id, 'owner': owner, 'version': version,
         'status': 'pending', 'expires_at': {'$gt': now()}},
        {'$set': changes, '$push': {'audit': {'at': now(), 'actor': owner, 'decision': decision, 'version': version,
                                             'action': proposal['action'], 'args': proposal['args'],
                                             'digest': proposal['digest']}}},
        return_document=ReturnDocument.AFTER)
    if not result:
        raise ValueError('Approval changed, expired or was already decided. Refresh and review again.')
    if decision != 'edit':
        await db.agent_runs.update_one({'run_id': run['run_id'], 'status': 'waiting_approval'},
                                       {'$set': {'status': 'queued', 'wake_at': now()}})
    return result


async def execute_proposal(db, proposal, run, adapter):
    if run.get('dry_run'):
        raise ValueError('Safe tests cannot execute connected-account actions')
    policy = await effective_policy(db, run)
    validate_action(proposal['action'], proposal['args'], policy)
    if policy['actions'][proposal['action']] == 'ask' and proposal.get('decided_by') != run['owner']:
        raise ValueError('Current policy requires explicit human approval')
    expected = digest({'action': proposal['action'], 'args': proposal['args'], 'owner': run['owner']})
    if expected != proposal.get('approved_digest'):
        raise ValueError('Approval does not match the exact action')
    from autonomy.reconciliation import message_id
    correlation = {'provider_message_id': message_id(proposal['approval_id'])} if proposal['action'] == 'gmail.send' else {}
    claimed = await db.agent_approvals.find_one_and_update(
        {'approval_id': proposal['approval_id'], 'status': 'approved',
         'expires_at': {'$gt': now()}, 'digest': expected, 'approved_digest': expected},
        {'$set': {'status': 'executing', 'execution_started_at': now(), **correlation}},
        return_document=ReturnDocument.AFTER)
    if not claimed:
        return None
    # Never put executing back into approved. A timeout/crash may be a successful
    # provider write. An operator must inspect the receipt/provider before any retry.
    try:
        policy = await effective_policy(db, run)
        validate_action(proposal['action'], proposal['args'], policy)
        if policy['actions'][proposal['action']] == 'ask' and claimed.get('decided_by') != run['owner']:
            raise ValueError('Current policy requires explicit human approval')
        receipt = await adapter(proposal['action'], proposal['args'], run['owner'], proposal['approval_id'])
        status = 'executed'
    except Exception:
        receipt = {'message': 'Delivery outcome unknown. Check the provider; do not resend automatically.'}
        status = 'uncertain'
    await db.agent_approvals.update_one({'approval_id': proposal['approval_id'], 'status': 'executing'},
        {'$set': {'status': status, 'receipt': receipt, 'finished_at': now()}})
    return {'status': status, 'receipt': receipt}


async def reconcile(db, owner, approval_id, version, outcome, evidence):
    """Record a human investigation, never infer delivery or authorize a retry.

    The uncertain status and original receipt stay intact as the replay barrier.
    Only the authenticated owner may attest, even after run expiry/revocation or
    agent deletion. No agent authority or connector grant is needed for this
    local audit write. Later corrections append history under a version CAS.
    """
    await authority_account(db, owner)
    if type(version) is not int or version < 0 or outcome not in ('sent', 'not_sent', 'unknown'):
        raise ValueError('Choose a valid investigation outcome and review version')
    evidence = text(evidence, 'Provider evidence or investigation notes', 2000)
    proposal = await db.agent_approvals.find_one({'approval_id': approval_id, 'owner': owner})
    if not proposal or proposal['action'] not in WRITE_ACTIONS:
        raise ValueError('Unknown external write action not found')
    record = {'outcome': outcome, 'evidence': evidence, 'actor': owner, 'at': now(),
              'source': 'owner_report', 'version': version + 1, 'digest': proposal['digest']}
    # One-document commit: never lose the receipt or a competing review. Do not
    # change run state, proposal version, approval digest or execution status.
    result = await db.agent_approvals.find_one_and_update(
        {'approval_id': approval_id, 'owner': owner, 'status': 'uncertain',
         '$expr': {'$eq': [{'$ifNull': ['$reconciliation.version', 0]}, version]}},
        {'$set': {'reconciliation': record}, '$push': {'reconciliation_history': record}},
        return_document=ReturnDocument.AFTER)
    if not result:
        raise ValueError('Outcome changed or is not awaiting investigation. Close and review again.')
    return result


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def rotate_event_token(db, owner, work_id):
    """One secret per job; rotation invalidates the old one immediately.

    Only the hash is stored. The plaintext is shown once to the owner and is
    scoped to waking THIS job under its saved grant - it is not a session, an
    approval or any other authority.
    """
    work = await db.agent_work.find_one({'work_id': work_id, 'owner': owner})
    if not work or work.get('revoked') or work.get('parent_only'):
        raise ValueError('Work not found or revoked')
    await authority(db, owner, work['agent_id'])
    token = secrets.token_urlsafe(32)
    await db.agent_work.update_one({'work_id': work_id, 'owner': owner},
        {'$set': {'event_token_hash': token_hash(token), 'event_token_rotated_at': now()}})
    return token


async def event_wake(db, work_id, token, event_key, note=None):
    """External wake-up. The payload is untrusted data, never instructions.

    Fails uniformly (not-found) for unknown work, missing tokens and bad
    tokens so callers cannot probe which jobs exist. The event cannot change
    the grant, instructions or budgets; it only queues one deduplicated run.
    """
    work = await db.agent_work.find_one({'work_id': work_id})
    stored = (work or {}).get('event_token_hash') or ''
    supplied = token_hash(token) if isinstance(token, str) and token else ''
    if not work or not stored or not hmac.compare_digest(stored, supplied):
        raise LookupError('Unknown work or event token')
    if work.get('revoked') or work.get('paused'):
        raise ValueError('This job is paused or revoked. Enable it before sending events.')
    return await enqueue(db, work, 'hook:' + text(event_key, 'Event ID', 150), note=note)


async def review_cost(db, owner, run_id, version, outcome, notes):
    """Owner attestation for model charges still reserved after a run ended.

    Audit-only: never releases funds, changes budget math, unblocks a
    provider-violation stop or restarts work. Corrections append history.
    """
    await authority_account(db, owner)
    if type(version) is not int or version < 0 or outcome not in ('billed', 'not_billed', 'unknown'):
        raise ValueError('Choose a valid charge outcome and review version')
    notes = text(notes, 'Billing evidence or investigation notes', 2000)
    run = await db.agent_runs.find_one({'run_id': run_id, 'owner': owner})
    if not run or run.get('parent_id') or run['status'] not in TERMINAL:
        raise ValueError('Charge review applies to ended top-level runs only')
    if not any(e.get('status') == 'held' for e in run.get('cost_ledger', [])):
        raise ValueError('This run has no unresolved model charge reservations')
    record = {'outcome': outcome, 'notes': notes, 'actor': owner, 'at': now(),
              'source': 'owner_report', 'version': version + 1}
    result = await db.agent_runs.find_one_and_update(
        {'run_id': run_id, 'owner': owner, 'status': {'$in': list(TERMINAL)},
         '$expr': {'$eq': [{'$ifNull': ['$cost_review.version', 0]}, version]}},
        {'$set': {'cost_review': record}, '$push': {'cost_review_history': record}},
        return_document=ReturnDocument.AFTER)
    if not result:
        raise ValueError('Charge review changed. Close and review again.')
    return result


async def cancel(db, owner, run_id):
    run = await db.agent_runs.find_one({'run_id': run_id, 'owner': owner})
    if not run:
        raise ValueError('Run not found')
    ids = [run_id]
    children = await db.agent_runs.find({'parent_id': run_id, 'owner': owner}, {'run_id': 1}).to_list(10)
    ids += [c['run_id'] for c in children]
    await db.agent_runs.update_many({'run_id': {'$in': ids}, 'status': {'$nin': list(TERMINAL)}},
        {'$set': {'status': 'cancelled', 'finished_at': now(), 'lease': None}})
    await db.agent_approvals.update_many({'run_id': {'$in': ids}, 'status': {'$in': ['pending', 'approved']}},
        {'$set': {'status': 'cancelled'}})
