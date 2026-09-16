"""Durable write/send proposals from remote workers.

A worker can only *propose* an external write. The exact arguments and a
digest are stored on the trusted backend; the model receives a proposal ID and
status, never a receipt or credential. Only the authenticated owner decides,
through the signed control plane, and only the backend executes the exact
approved version through the fixed personal connector. Every transition is a
Mongo CAS; an execution with an unknown outcome is never retried.
"""
import asyncio
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.write_concern import WriteConcern

from isolation.gateway import GatewayDenied

# Model-visible proposal tool -> durable action name.
PROPOSAL_TOOLS = {
    'gmail.propose_send': 'gmail.send',
    'gmail.propose_draft': 'gmail.draft',
    'slack.propose_send': 'slack.send',
    'calendar.propose_create': 'calendar.create',
    'docs.propose_append': 'docs.append',
    'sheets.propose_write': 'sheets.write',
}
STATUS_TOOLS = frozenset({'proposals.status', 'proposals.list'})
TOOLS = frozenset(PROPOSAL_TOOLS) | STATUS_TOOLS
# action: (required argument names, optional argument names).
WRITE_SCHEMAS = {
    'gmail.send': ({'to', 'subject', 'body'}, {'cc'}),
    'gmail.draft': ({'to', 'subject', 'body'}, {'cc'}),
    'slack.send': ({'channel', 'text'}, {'thread_ts'}),
    'calendar.create': ({'summary', 'start', 'end'}, {'description', 'attendees', 'location'}),
    'docs.append': ({'document_id', 'text'}, set()),
    'sheets.write': ({'spreadsheet_id', 'range', 'values'}, set()),
}
LIMITS = {'to': 254, 'cc': 1000, 'subject': 1000, 'body': 12000, 'channel': 30, 'text': 12000,
          'thread_ts': 30, 'summary': 300, 'start': 40, 'end': 40, 'description': 4000,
          'attendees': 1000, 'location': 500, 'document_id': 200, 'spreadsheet_id': 200, 'range': 200}
EMAIL = r'[^\s<>@,;"()]+@[^\s<>@,;"()]+\.[^\s<>@,;"()]+'
CHANNEL = r'[CDG][A-Z0-9]{4,25}'
THREAD_TS = r'\d{10}\.\d{6}'
RESOURCE_ID = r'[A-Za-z0-9_-]{10,200}'
OPEN = ('pending', 'approved', 'executing')
# Statuses that make an identical re-proposal a repeat, not a new request.
HANDLED = ('pending', 'approved', 'executing', 'executed', 'uncertain')
TERMINAL = ('executed', 'uncertain', 'rejected', 'expired', 'cancelled')
MAX_OPEN_PER_CONVERSATION = 10
MAX_PER_RUN = 30
PROPOSAL_TTL = timedelta(hours=24)
EXECUTION_GRACE = timedelta(minutes=5)
NOTES = {
    'pending': 'Waiting for the owner to review this exact proposal in Loma. Do not propose it again; check proposals.status later.',
    'approved': 'Approved by the owner; execution is being claimed. Do not repeat.',
    'executing': 'Execution is in progress on the backend. Do not repeat.',
    'executed': 'Executed. A receipt was saved. Do not repeat this action.',
    'uncertain': 'Outcome unknown. The owner must check the provider; never repeat this action.',
    'rejected': 'Rejected by the owner. Propose a changed action only if the owner asks for one.',
    'expired': 'Expired before a decision. Propose again only if the owner still wants it.',
    'cancelled': 'Cancelled by the owner.',
}
LABELS = {'gmail.send': 'Send email', 'gmail.draft': 'Create Gmail draft', 'slack.send': 'Send Slack message',
          'calendar.create': 'Create calendar event', 'docs.append': 'Append to Google Doc',
          'sheets.write': 'Write to Google Sheet'}


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def _text(value, name, limit, *, multiline=True):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value
            or (not multiline and any(c in value for c in '\r\n'))):
        raise GatewayDenied(f'Invalid {name}')
    return value.strip()


def _emails(value, name, limit, *, maximum):
    parts = [p.strip().lower() for p in _text(value, name, limit, multiline=False).split(',')]
    if not parts or len(parts) > maximum or any(not re.fullmatch(EMAIL, p) for p in parts):
        raise GatewayDenied(f'Invalid {name}')
    return ','.join(parts)


def _datetime(value, name):
    value = _text(value, name, LIMITS[name], multiline=False)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise GatewayDenied(f'Invalid {name}') from None
    if parsed.tzinfo is None:
        raise GatewayDenied(f'{name} must include a UTC offset')
    return parsed


def _cells(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 200:
        raise GatewayDenied('Invalid values')
    rows = []
    for row in value:
        if not isinstance(row, list) or not 1 <= len(row) <= 50:
            raise GatewayDenied('Invalid values')
        cells = []
        for cell in row:
            if isinstance(cell, bool) or (isinstance(cell, int) and abs(cell) < 2 ** 53):
                cells.append(cell)
            elif isinstance(cell, float) and math.isfinite(cell):
                cells.append(cell)
            elif isinstance(cell, str) and len(cell) <= 2000 and '\x00' not in cell:
                cells.append(cell)
            else:
                raise GatewayDenied('Invalid values')
        rows.append(cells)
    return rows


def validate_write(action, arguments):
    """Exact, typed arguments for one durable write action. Fails closed."""
    if action not in WRITE_SCHEMAS or not isinstance(arguments, dict):
        raise GatewayDenied('Unknown action or invalid arguments')
    required, optional = WRITE_SCHEMAS[action]
    if not required <= set(arguments) <= required | optional:
        raise GatewayDenied('Unexpected or missing action arguments')
    args = {}
    for name, value in arguments.items():
        if name in ('to', 'cc', 'attendees'):
            args[name] = _emails(value, name, LIMITS[name], maximum={'to': 1, 'cc': 10, 'attendees': 20}[name])
        elif name in ('start', 'end'):
            args[name] = _datetime(value, name).isoformat()
        elif name == 'values':
            args[name] = _cells(value)
        elif name == 'channel':
            args[name] = _text(value, name, LIMITS[name], multiline=False)
            if not re.fullmatch(CHANNEL, args[name]):
                raise GatewayDenied('Use an exact Slack channel ID (like C0123ABCD)')
        elif name == 'thread_ts':
            args[name] = _text(value, name, LIMITS[name], multiline=False)
            if not re.fullmatch(THREAD_TS, args[name]):
                raise GatewayDenied('Invalid thread_ts')
        elif name in ('document_id', 'spreadsheet_id'):
            args[name] = _text(value, name, LIMITS[name], multiline=False)
            if not re.fullmatch(RESOURCE_ID, args[name]):
                raise GatewayDenied(f'Invalid {name}')
        elif name in ('subject', 'summary', 'location', 'range'):
            args[name] = _text(value, name, LIMITS[name], multiline=False)
        else:
            args[name] = _text(value, name, LIMITS[name])
    if action == 'slack.send':
        if len(args['text']) > 4000:
            raise GatewayDenied('Slack messages are limited to 4000 characters')
        if args['text'].startswith('--'):
            # slack_user.py parses flags by exact token match; keep text unambiguous.
            raise GatewayDenied('Slack message text cannot start with --')
    if action == 'calendar.create' and _datetime(args['end'], 'end') <= _datetime(args['start'], 'start'):
        raise GatewayDenied('Event end must be after its start')
    return args


def receipt_ok(action, value):
    checks = {
        'gmail.send': lambda v: v.get('sent') is True and bool(v.get('messageId')),
        'gmail.draft': lambda v: v.get('created') is True and bool(v.get('draftId')),
        'slack.send': lambda v: v.get('sent') is True and bool(v.get('message_ts')),
        'calendar.create': lambda v: v.get('created') is True and bool(v.get('id')),
        'docs.append': lambda v: v.get('appended') is True,
        'sheets.write': lambda v: bool(v.get('updatedRange')),
    }
    return isinstance(value, dict) and checks[action](value)


def command(action, args, proposal_id):
    """Fixed connector argv for one validated action. Never model-supplied."""
    from autonomy.reconciliation import message_id
    if action == 'gmail.send':
        argv = ['send-email', '--to=' + args['to'], '--subject=' + args['subject'], '--body=' + args['body'],
                '--rfc-message-id=' + message_id(proposal_id)]
        if args.get('cc'):
            argv.append('--cc=' + args['cc'])
        return 'gmail', argv
    if action == 'gmail.draft':
        argv = ['create-draft', '--to=' + args['to'], '--subject=' + args['subject'], '--body=' + args['body']]
        if args.get('cc'):
            argv.append('--cc=' + args['cc'])
        return 'gmail', argv
    if action == 'slack.send':
        argv = ['send-message', '--channel', args['channel'], '--text', args['text']]
        if args.get('thread_ts'):
            argv += ['--thread-ts', args['thread_ts']]
        return 'slack', argv
    if action == 'calendar.create':
        argv = ['create-event', '--summary=' + args['summary'], '--start=' + args['start'], '--end=' + args['end']]
        for name in ('description', 'attendees', 'location'):
            if args.get(name):
                argv.append(f'--{name}=' + args[name])
        return 'calendar', argv
    if action == 'docs.append':
        return 'docs', ['append-text', '--document-id=' + args['document_id'], '--text=' + args['text']]
    if action == 'sheets.write':
        return 'sheets', ['write-range', '--spreadsheet-id=' + args['spreadsheet_id'], '--range=' + args['range'],
                          '--values=' + json.dumps(args['values'], allow_nan=False)]
    raise ValueError('Unknown action')


async def write_adapter(action, args, owner, proposal_id):
    """Only validated, literal arguments reach a first-party personal CLI."""
    from autonomy.connector import personal
    args = validate_write(action, args)
    tool, argv = command(action, args, proposal_id)
    value = await personal(tool, argv, owner)
    if not receipt_ok(action, value):
        raise ValueError('No provider receipt was returned')
    return {'output': json.dumps(value)[:16000], 'proposal_id': proposal_id}


async def indexes(db):
    await db.isolated_worker_proposals.create_index('proposal_id', unique=True)
    await db.isolated_worker_proposals.create_index([('owner', 1), ('conversation_id', 1), ('created_at', -1)])
    await db.isolated_worker_proposals.create_index([('owner', 1), ('status', 1), ('created_at', 1)])


def _collection(db):
    return db.isolated_worker_proposals.with_options(write_concern=WriteConcern(w='majority'))


def summary(proposal):
    args = proposal['args']
    action = proposal['action']
    target = {'gmail.send': args.get('to'), 'gmail.draft': args.get('to'), 'slack.send': args.get('channel'),
              'calendar.create': args.get('summary'), 'docs.append': args.get('document_id'),
              'sheets.write': args.get('spreadsheet_id')}.get(action) or ''
    return f"{LABELS.get(action, action)}: {target}"[:200]


def view(proposal, *, duplicate=False):
    """Model-visible projection: no owner audit, receipts or provider output."""
    result = {'proposal_id': proposal['proposal_id'], 'action': proposal['action'],
              'status': proposal['status'], 'version': proposal['version'], 'args': proposal['args'],
              'reason': proposal['reason'], 'created_at': proposal['created_at'].isoformat(),
              'expires_at': proposal['expires_at'].isoformat(),
              'note': NOTES.get(proposal['status'], 'Unknown state; do not repeat.')}
    if duplicate:
        result['duplicate'] = True
        result['note'] = 'An identical proposal already exists in this conversation. ' + result['note']
    return result


class ProposalGateway:
    """Run-bound proposal creation and status reads for one conversation."""

    def __init__(self, db, authority, conversation_id, *, check_access):
        if (not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 128
                or not callable(check_access)):
            raise ValueError('An authenticated conversation and live policy are required')
        self.db, self.authority, self.conversation_id = db, authority, conversation_id
        self.check_access = check_access

    def _scope(self):
        return {'owner': self.authority.user_email, 'conversation_id': self.conversation_id}

    async def __call__(self, authority, tool, arguments):
        if authority != self.authority or not await self.check_access(self.authority):
            raise GatewayDenied('Run access is no longer valid')
        if tool not in TOOLS or not isinstance(arguments, dict):
            raise GatewayDenied('Unknown tool or invalid arguments')
        if tool == 'proposals.list':
            if arguments:
                raise GatewayDenied('Unknown tool or invalid arguments')
            rows = await self.db.isolated_worker_proposals.find(self._scope()).sort('created_at', -1).limit(20).to_list(20)
            return {'proposals': [view(row) for row in rows]}
        if tool == 'proposals.status':
            if set(arguments) != {'proposal_id'}:
                raise GatewayDenied('Unknown tool or invalid arguments')
            proposal_id = _text(arguments['proposal_id'], 'proposal_id', 64, multiline=False)
            row = await self.db.isolated_worker_proposals.find_one({**self._scope(), 'proposal_id': proposal_id})
            if not row:
                raise GatewayDenied('Proposal not found')
            return view(row)
        return await self.propose(PROPOSAL_TOOLS[tool], arguments)

    async def propose(self, action, arguments):
        reason = _text(arguments.get('reason'), 'reason', 2000)
        args = validate_write(action, {k: v for k, v in arguments.items() if k != 'reason'})
        owner = self.authority.user_email
        fingerprint = digest({'action': action, 'args': args, 'owner': owner, 'conversation_id': self.conversation_id})
        existing = await self.db.isolated_worker_proposals.find_one(
            {**self._scope(), 'digest': fingerprint, 'status': {'$in': list(HANDLED)}})
        if existing:
            return view(existing, duplicate=True)
        if await self.db.isolated_worker_proposals.count_documents(
                {**self._scope(), 'status': {'$in': list(OPEN)}}) >= MAX_OPEN_PER_CONVERSATION:
            raise GatewayDenied('Too many proposals are waiting for the owner. Ask them to review before proposing more.')
        if await self.db.isolated_worker_proposals.count_documents({'run_id': self.authority.run_id}) >= MAX_PER_RUN:
            raise GatewayDenied('Proposal limit reached for this run')
        proposal = {'proposal_id': str(uuid.uuid4()), 'owner': owner, 'conversation_id': self.conversation_id,
                    'run_id': self.authority.run_id, 'action': action, 'args': args, 'reason': reason,
                    'digest': fingerprint, 'version': 1, 'status': 'pending', 'created_at': now(),
                    'expires_at': now() + PROPOSAL_TTL, 'audit': []}
        await _collection(self.db).insert_one(dict(proposal))
        # Idempotent owner notification: the model cannot address or repeat it.
        await self.db.notifications.update_one({'notification_id': 'proposal:' + proposal['proposal_id']}, {'$setOnInsert': {
            'notification_id': 'proposal:' + proposal['proposal_id'], 'user_email': owner,
            'title': 'Agent proposed an action that needs your approval', 'body': summary(proposal),
            'conversation_id': self.conversation_id, 'link': '/agents/proposals', 'source': 'agent',
            'read': False, 'dismissed': False, 'created_at': now()}}, upsert=True)
        return view(proposal)


# ---- Owner control plane (signed dashboard session; never a worker path) ----

async def sweep(db, owner=None):
    scope = {'owner': owner} if owner else {}
    await _collection(db).update_many({**scope, 'status': 'pending', 'expires_at': {'$lte': now()}},
                                      {'$set': {'status': 'expired'}})
    await _collection(db).update_many({**scope, 'status': 'approved', 'expires_at': {'$lte': now()}},
                                      {'$set': {'status': 'expired'}})
    # A claim without a saved receipt is an unknown outcome, never a retry.
    await _collection(db).update_many(
        {**scope, 'status': 'executing', 'execution_started_at': {'$lte': now() - EXECUTION_GRACE}},
        {'$set': {'status': 'uncertain', 'finished_at': now(), 'receipt': {'message':
            'Execution stopped before a receipt was saved. Check the provider before resending.'}}})


def attention_filter(owner):
    return {'owner': owner, '$or': [{'status': 'pending', 'expires_at': {'$gt': now()}},
            {'status': 'uncertain', 'reconciliation.outcome': {'$nin': ['sent', 'not_sent']}}]}


async def listing(db, owner):
    await sweep(db, owner)
    attention = attention_filter(owner)
    pending = await db.isolated_worker_proposals.find(attention, {'_id': 0}).sort('created_at', 1).limit(200).to_list(200)
    recent = await db.isolated_worker_proposals.find({'owner': owner, '$nor': [attention]}, {'_id': 0}).sort('created_at', -1).limit(100).to_list(100)
    return {'proposals': pending + recent, 'attention_limited': len(pending) == 200}


async def attention_count(db, owner):
    await sweep(db, owner)
    return await db.isolated_worker_proposals.count_documents(attention_filter(owner))


def _owner_text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1 to {limit} characters')
    return value.strip()


async def decide(db, owner, proposal_id, version, decision, args=None, adapter=None):
    """Owner decision under a version CAS. Approval executes the exact version."""
    if decision not in ('approve', 'reject', 'edit', 'cancel') or type(version) is not int:
        raise ValueError('Invalid proposal decision')
    proposal = await db.isolated_worker_proposals.find_one({'proposal_id': proposal_id, 'owner': owner})
    if not proposal:
        raise ValueError('Proposal not found')
    changes = {'updated_at': now(), 'decided_by': owner}
    if decision == 'edit':
        try:
            args = validate_write(proposal['action'], args)
        except GatewayDenied as exc:
            raise ValueError(str(exc)) from None
        changes.update(args=args, version=version + 1, status='pending',
                       digest=digest({'action': proposal['action'], 'args': args, 'owner': owner,
                                      'conversation_id': proposal['conversation_id']}),
                       expires_at=now() + PROPOSAL_TTL)
    else:
        changes['status'] = {'approve': 'approved', 'reject': 'rejected', 'cancel': 'cancelled'}[decision]
        if decision == 'approve':
            try:
                validate_write(proposal['action'], proposal['args'])
            except GatewayDenied as exc:
                raise ValueError(str(exc)) from None
            changes['approved_digest'] = proposal['digest']
    result = await _collection(db).find_one_and_update(
        {'proposal_id': proposal_id, 'owner': owner, 'version': version, 'status': 'pending',
         'expires_at': {'$gt': now()}},
        {'$set': changes, '$push': {'audit': {'at': now(), 'actor': owner, 'decision': decision, 'version': version,
                                             'action': proposal['action'], 'args': proposal['args'],
                                             'digest': proposal['digest']}}},
        projection={'_id': 0}, return_document=ReturnDocument.AFTER)
    if not result:
        raise ValueError('Proposal changed, expired or was already decided. Refresh and review again.')
    if decision == 'approve':
        # Shielded so a dropped dashboard request cannot abandon a claimed
        # execution without persisting its outcome.
        outcome = await asyncio.shield(execute(db, result, adapter))
        if outcome:
            result.update(outcome)
        else:
            result = await db.isolated_worker_proposals.find_one({'proposal_id': proposal_id}, {'_id': 0})
    return result


async def execute(db, proposal, adapter=None):
    adapter = adapter or write_adapter
    expected = digest({'action': proposal['action'], 'args': proposal['args'], 'owner': proposal['owner'],
                       'conversation_id': proposal['conversation_id']})
    if expected != proposal.get('approved_digest') or proposal.get('decided_by') != proposal['owner']:
        raise ValueError('Approval does not match the exact action')
    claimed = await _collection(db).find_one_and_update(
        {'proposal_id': proposal['proposal_id'], 'status': 'approved', 'expires_at': {'$gt': now()},
         'digest': expected, 'approved_digest': expected, 'decided_by': proposal['owner']},
        {'$set': {'status': 'executing', 'execution_started_at': now()}},
        return_document=ReturnDocument.AFTER)
    if not claimed:
        return None
    # Never put executing back into approved. A timeout or crash may be a
    # successful provider write; an operator must check before any retry.
    try:
        receipt = await adapter(proposal['action'], proposal['args'], proposal['owner'], proposal['proposal_id'])
        # Persist only a plain JSON receipt so the outcome write cannot fail.
        receipt = json.loads(json.dumps(receipt, default=str))
        if not isinstance(receipt, dict):
            receipt = {'output': receipt}
        status = 'executed'
    except Exception:
        receipt = {'message': 'Delivery outcome unknown. Check the provider; do not resend automatically.'}
        status = 'uncertain'
    await _collection(db).update_one({'proposal_id': proposal['proposal_id'], 'status': 'executing'},
                                     {'$set': {'status': status, 'receipt': receipt, 'finished_at': now()}})
    return {'status': status, 'receipt': receipt}


async def reconcile(db, owner, proposal_id, version, outcome, evidence):
    """Record a human investigation; never infer delivery or authorize a retry."""
    if type(version) is not int or version < 0 or outcome not in ('sent', 'not_sent', 'unknown'):
        raise ValueError('Choose a valid investigation outcome and review version')
    evidence = _owner_text(evidence, 'Provider evidence or investigation notes', 2000)
    proposal = await db.isolated_worker_proposals.find_one({'proposal_id': proposal_id, 'owner': owner})
    if not proposal:
        raise ValueError('Proposal not found')
    record = {'outcome': outcome, 'evidence': evidence, 'actor': owner, 'at': now(),
              'source': 'owner_report', 'version': version + 1, 'digest': proposal['digest']}
    result = await _collection(db).find_one_and_update(
        {'proposal_id': proposal_id, 'owner': owner, 'status': 'uncertain',
         '$expr': {'$eq': [{'$ifNull': ['$reconciliation.version', 0]}, version]}},
        {'$set': {'reconciliation': record}, '$push': {'reconciliation_history': record}},
        projection={'_id': 0}, return_document=ReturnDocument.AFTER)
    if not result:
        raise ValueError('Outcome changed or is not awaiting investigation. Close and review again.')
    return result
