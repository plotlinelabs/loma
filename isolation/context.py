"""Trusted ingress state for one disposable run; no legacy execution envelopes."""
from dataclasses import dataclass
from datetime import datetime, timezone

from api.recall_content import visible_messages
from isolation.artifacts import MAX_FILE, MAX_FILES, MAX_RUN, identifier, name
from isolation.gateway import GatewayDenied


@dataclass(frozen=True)
class Attachment:
    """Bytes already accepted by the authenticated ingress, never a server path."""
    name: str
    data: bytes

    def __post_init__(self):
        name(self.name)
        if not isinstance(self.data, bytes) or len(self.data) > MAX_FILE:
            raise ValueError('Invalid attachment bytes')


class ConversationContext:
    def __init__(self, db, authority, conversation_id, *, cancelled, check_access):
        if (not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 128
                or not callable(check_access)):
            raise ValueError('An authenticated conversation and live policy are required')
        self.db, self.authority, self.conversation_id = db, authority, conversation_id
        self.cancelled, self.check_access = cancelled, check_access
        self.scope = None
        self.history = ()
        self.coverage = {}

    async def _read(self, *, include_messages=False):
        if self.cancelled.is_set() or not await self.check_access(self.authority):
            raise GatewayDenied('Run access is no longer valid')
        user = await self.db.users.find_one({'email': self.authority.user_email,
            'status': {'$in': [None, 'active']}, 'deleted': {'$ne': True}}, {'_id': 1})
        # Reviewed ingress sources whose metadata.user_name is the creator's
        # authenticated email (dashboard/task chat, scheduled and webhook
        # flows, Telegram DMs, Slack channels). Ownership still requires that
        # email to match the run authority and resolve to an active user, so a
        # source whose user_name is a display name or bot ID fails closed.
        row = await self.db.conversations.find_one({'conversation_id': self.conversation_id,
            'metadata.user_name': self.authority.user_email, 'deleted': {'$ne': True},
            '$or': [{'source': {'$in': ['dashboard', 'task', 'flow', 'webhook', 'telegram']}},
                    {'source': {'$regex': '^slack'}}],
            'status': 'running'},
            {'project_id': 1, 'metadata.agent_id': 1, **({'messages': 1} if include_messages else {})})
        if not user or not row:
            raise GatewayDenied('Conversation access is no longer valid')
        scope = (str(user['_id']), row.get('project_id'), (row.get('metadata') or {}).get('agent_id'))
        if self.scope is not None and self.scope != scope:
            raise GatewayDenied('Conversation scope changed')
        self.scope = scope
        return row

    async def authorize(self, authority):
        if authority != self.authority:
            return False
        try:
            await self._read()
        except GatewayDenied:
            return False
        return not self.cancelled.is_set()

    async def load(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 512 * 1024:
            raise ValueError('Invalid current message')
        row = await self._read(include_messages=True)
        # The observer appends the ingress message before launching the turn.
        # Remove only that exact final user message, not every repeated message.
        raw = list(row.get('messages') or [])
        if raw and isinstance(raw[-1], dict) and raw[-1].get('role') == 'user' and raw[-1].get('content') == prompt:
            raw.pop()
        messages, excluded = visible_messages({'messages': raw})
        kept, size = [], 0
        for message in reversed(messages):
            cost = len(message['content'].encode())
            if len(kept) == 100 or size + cost > 240 * 1024:
                break
            kept.append((message['role'], message['content']))
            size += cost
        self.history = tuple(reversed(kept))
        self.coverage = {'excluded_messages': excluded, 'omitted_messages': len(messages) - len(kept),
            'redacted_messages': sum(m['redacted'] for m in messages),
            'possibly_truncated_messages': sum(m['possibly_source_truncated'] for m in messages)}
        if not await self.authorize(self.authority):
            raise GatewayDenied('Conversation access is no longer valid')
        return self

    async def stage(self, artifacts, attachments=(), input_ids=()):
        if artifacts.authority != self.authority or artifacts.conversation_id != self.conversation_id:
            raise GatewayDenied('Artifact scope mismatch')
        if artifacts.inputs or artifacts.created or artifacts.pending:
            raise GatewayDenied('Input grants must be selected by authenticated ingress')
        if not isinstance(attachments, (tuple, list)) or any(not isinstance(a, Attachment) for a in attachments):
            raise ValueError('Only accepted attachment bytes are supported')
        if not isinstance(input_ids, (tuple, list)):
            raise ValueError('Invalid input selection')
        ids = frozenset(identifier(i) for i in input_ids)
        if len(attachments) + len(ids) > MAX_FILES or sum(len(a.data) for a in attachments) > MAX_RUN:
            raise ValueError('Attachment quota exceeded')
        if not await self.authorize(self.authority):
            raise GatewayDenied('Conversation access is no longer valid')
        total = sum(len(a.data) for a in attachments)
        # Download IDs are not authority. Reuse requires a live registry receipt
        # for this same owner AND conversation, not just a blob in storage.
        for artifact_id in ids:
            row = await self.db.isolated_artifact_downloads.find_one({'_id': artifact_id,
                'owner': self.authority.user_email, 'conversation_id': self.conversation_id,
                'expires_at': {'$gt': datetime.now(timezone.utc)}})
            if not row:
                raise GatewayDenied('Input artifact is unavailable')
            # Temporarily select one authenticated receipt; compare immutable
            # metadata before exposing the completed selection to the worker.
            artifacts.inputs = frozenset({artifact_id})
            try:
                meta = artifacts.metadata(artifact_id)
            finally:
                artifacts.inputs = frozenset()
            if meta != row['metadata']:
                raise GatewayDenied('Input artifact changed')
            total += meta['size']
        if total > MAX_RUN:
            raise ValueError('Attachment quota exceeded')
        if not await self.authorize(self.authority):
            raise GatewayDenied('Conversation access is no longer valid')
        artifacts.inputs = ids
        artifacts.created = len(ids)
        artifacts.bytes_reserved = total - sum(len(a.data) for a in attachments)
        for attachment in attachments:
            meta = artifacts.ingest(attachment.name, attachment.data)
            artifacts.inputs |= {meta['artifact_id']}
        return artifacts.manifest()
