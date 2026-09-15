"""Backend-only skills and recall, bound to an authenticated run/conversation.

No tokens, source credentials, asset paths or database handles cross the wire.
The caller supplies the conversation and grants; worker arguments cannot change
them. Existing recall sanitization, live ownership checks and durable limits are
shared with the HTTP endpoints, not reimplemented in an alternate retrieval path.
"""
import asyncio
import json
import hashlib
import os
from pathlib import Path
import re
import stat

from api import skill_service
from api.recall_auth import RecallIdentity
from api.recall_controls import RecallError, admit_request
from api.recall_routes import fetch_history
from api.recall_search import search_history
from config.recall import recall_enabled
from isolation.gateway import GatewayDenied
from isolation.artifacts import MAX_FILE


SCHEMAS = {
    'skills.list': (set(), set()),
    'skills.search': ({'query'}, {'query'}),
    'skills.get': ({'slug'}, {'slug'}),
    'skills.file': ({'slug', 'path'}, {'slug', 'path'}),
    'skills.asset': ({'slug', 'path'}, {'slug', 'path'}),
    'search_history': ({'query'}, {'query', 'match_mode', 'limit', 'filters', 'cursor'}),
    'fetch_history': ({'conversation_id'}, {'conversation_id', 'anchor_message_id', 'before', 'after', 'max_chars', 'cursor'}),
}
MAX_RESULT = 256 * 1024


def validate(tool, arguments):
    if tool not in SCHEMAS or not isinstance(arguments, dict):
        raise GatewayDenied('Invalid knowledge request')
    required, allowed = SCHEMAS[tool]
    if not required <= set(arguments) <= allowed:
        raise GatewayDenied('Invalid knowledge arguments')
    try:
        if len(json.dumps(arguments, allow_nan=False).encode()) > 8192:
            raise ValueError()
    except (ValueError, TypeError):
        raise GatewayDenied('Invalid knowledge arguments') from None
    if tool.startswith('skills.'):
        for value in arguments.values():
            if not isinstance(value, str) or not value.strip() or len(value) > 1000 or '\x00' in value:
                raise GatewayDenied('Invalid skill arguments')
        if 'slug' in arguments and not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', arguments['slug']):
            raise GatewayDenied('Invalid skill slug')
        if 'path' in arguments:
            skill_service.normalize_file_path(arguments['path'])


class KnowledgeGateway:
    def __init__(self, db, authority, conversation_id, *, artifacts=None):
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 128:
            raise ValueError('An authorized conversation is required')
        self.db, self.authority, self.conversation_id = db, authority, conversation_id
        self.identity = None
        if artifacts is not None and (artifacts.authority != authority or artifacts.conversation_id != conversation_id):
            raise ValueError('A matching artifact scope is required')
        self.artifacts = artifacts

    @staticmethod
    def _asset_bytes(row):
        # DB metadata selects the storage object, never the worker's path. Pin
        # every component to avoid symlink races and similar-prefix root escapes.
        root = skill_service._asset_root().absolute()
        relative = Path(row.get('asset_path') or '').relative_to(root)
        if not relative.parts or any(part in ('.', '..') for part in relative.parts):
            raise ValueError('Invalid skill asset')
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                    raise ValueError('Invalid skill asset')
                data = handle.read(MAX_FILE + 1)
            if len(data) != row.get('size_bytes') or hashlib.sha256(data).hexdigest() != row.get('content_hash'):
                raise ValueError('Skill asset changed')
            return data
        finally:
            os.close(directory)

    async def _context(self):
        owner = self.authority.user_email
        query = {'email': owner, 'status': {'$in': [None, 'active']}, 'deleted': {'$ne': True}}
        user = await self.db.users.find_one(query, {'_id': 1, 'recall_excluded': 1})
        conversation = await self.db.conversations.find_one({
            'conversation_id': self.conversation_id, 'metadata.user_name': owner,
            'deleted': {'$ne': True}, 'source': {'$in': ['dashboard', 'task']},
        }, {'project_id': 1, 'metadata.agent_id': 1})
        if not user or not conversation:
            raise GatewayDenied('Knowledge access is no longer valid')
        identity = RecallIdentity(str(user['_id']), owner, self.conversation_id,
            conversation.get('project_id'), (conversation.get('metadata') or {}).get('agent_id'))
        if self.identity is not None and self.identity != identity:
            raise GatewayDenied('Knowledge scope changed during retrieval')
        self.identity = identity
        return identity, {**query, '_id': user['_id'], 'recall_excluded': {'$ne': True}}, user

    async def _skill(self, slug):
        # Recheck even ordinary skills: an explicit personal scope is private,
        # regardless of the older shared CLI's backward-compatible defaults.
        row = await self.db.skills.find_one({'slug': slug, 'enabled': {'$ne': False}})
        if (not row or (row.get('scope') == 'personal' and row.get('created_by') != self.authority.user_email)):
            raise GatewayDenied('Skill not found')
        await skill_service.check_linked_access(self.db, slug)
        return row

    @staticmethod
    def _summary(row):
        return {k: row[k] for k in ('slug', 'name', 'description', 'tags', 'files') if k in row}

    async def __call__(self, authority, tool, arguments):
        if authority != self.authority or tool not in authority.allowed_tools:
            raise GatewayDenied('Knowledge tool is not allowed')
        validate(tool, arguments)
        # Explicitly override both ContextVars so concurrent owners and calls
        # originating in a dashboard handler cannot inherit elevated visibility.
        actor = skill_service.skill_actor.set(authority.user_email)
        dashboard = skill_service.skill_dashboard.set(False)
        try:
            async with asyncio.timeout(10):
                identity, user_query, user = await self._context()
                if tool in ('search_history', 'fetch_history'):
                    if not recall_enabled() or user.get('recall_excluded') is True:
                        raise GatewayDenied('History recall is unavailable')
                    await admit_request(self.db, identity)
                    handler = search_history if tool == 'search_history' else fetch_history
                    result = await handler(self.db, identity, user_query, arguments)
                elif tool in ('skills.list', 'skills.search'):
                    rows = (await skill_service.list_skills(self.db) if tool == 'skills.list'
                            else await skill_service.search_skills(self.db, arguments['query']))
                    visible = []
                    for row in rows:
                        try:
                            await self._skill(row['slug'])
                        except (GatewayDenied, skill_service.SkillError):
                            continue
                        visible.append(self._summary(row))
                    result = {'skills': visible}
                else:
                    slug = arguments['slug']
                    await self._skill(slug)
                    if tool == 'skills.get':
                        row = await skill_service.get_skill(self.db, slug)
                        # files from get_skill contain internal asset paths;
                        # expose only their logical package names.
                        result = {**self._summary(row), 'content': row.get('content', ''),
                                  'files': [f['path'] for f in row.get('files', [])]}
                    else:
                        row = await skill_service.get_skill_file(self.db, slug, arguments['path'])
                        if tool == 'skills.asset':
                            if row.get('kind') != 'local_asset' or self.artifacts is None:
                                raise GatewayDenied('Skill asset transfer is unavailable')
                            try:
                                data = self._asset_bytes(row)
                            except (ValueError, OSError):
                                raise GatewayDenied('Skill asset is unavailable') from None
                            await self._skill(slug)
                            # Receipt contains bytes metadata only. Import it
                            # with workspace.import; never expose asset_path.
                            result = self.artifacts.ingest(Path(arguments['path']).name, data)
                        elif row.get('kind') != 'inline_text':
                            raise GatewayDenied('Binary skills require artifact transfer')
                        else:
                            result = {k: row[k] for k in ('path', 'content', 'content_type', 'content_hash') if k in row}
                    await self._skill(slug)
                latest, _, _ = await self._context()
                if latest != identity:
                    raise GatewayDenied('Knowledge scope changed during retrieval')
                if len(json.dumps(result, allow_nan=False).encode()) > MAX_RESULT:
                    raise GatewayDenied('Knowledge response too large; read individual files')
                return result
        except RecallError as exc:
            # Preserve useful coverage/cursor errors without backend diagnostics.
            return {'error': exc.code}
        except (skill_service.SkillError, TimeoutError, OSError):
            raise GatewayDenied('Knowledge request unavailable') from None
        finally:
            skill_service.skill_dashboard.reset(dashboard)
            skill_service.skill_actor.reset(actor)
