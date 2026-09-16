"""Real throwaway Mongo scopes, shared recall cores, and fail-closed dispatch."""
import asyncio
from dataclasses import replace
import json
from unittest.mock import AsyncMock

import pytest
from api import skill_service
from isolation.artifacts import ArtifactScope
from isolation.gateway import GatewayDenied, ToolGateway
from isolation.knowledge import KnowledgeGateway, SCHEMAS, validate
from isolation.protocol import RunAuthority
from tests.test_bounded_work import db, OWNER, OTHER

AUTH = RunAuthority('knowledge-run', OWNER, frozenset(SCHEMAS))


@pytest.mark.parametrize('tool,args', [
    ('skills.get', {'slug': '../secret'}), ('skills.file', {'slug': 'demo', 'path': '/etc/passwd'}),
    ('skills.file', {'slug': 'demo', 'path': '../secret'}), ('skills.get', {'slug': 'demo', 'owner': OTHER}),
    ('search_history', {'query': 'x', 'user_id': 'other'}), ('fetch_history', {'conversation_id': 'c', 'token': 'x'}),
    ('skills.list', {'query': 'x'}), ('skills.get', {'slug': ''}), ('skills.search', {'query': True}),
    ('search_history', {'query': 'x' * 8193}), ('unknown', {}),
])
def test_reject_argument_and_identity_overrides(tool, args):
    with pytest.raises((GatewayDenied, skill_service.SkillError)):
        validate(tool, args)


@pytest.mark.asyncio
async def test_gateway_checks_audit_and_revocation_without_dispatch(tmp_path):
    knowledge = AsyncMock()
    knowledge.authority = AUTH
    scope = ArtifactScope(tmp_path, AUTH, 'current')
    audit = AsyncMock(side_effect=RuntimeError('audit down'))
    gateway = ToolGateway(AUTH, authorize=AsyncMock(return_value=True), audit=audit, artifacts=scope, knowledge=knowledge)
    try:
        with pytest.raises(RuntimeError):
            await gateway(AUTH, 'skills.list', {})
        knowledge.assert_not_awaited()
        gateway.audit = AsyncMock()
        gateway.authorize = AsyncMock(side_effect=[True, False])
        with pytest.raises(GatewayDenied):
            await gateway(AUTH, 'skills.list', {})
        knowledge.assert_not_awaited()
        gateway.authorize = AsyncMock(side_effect=[True, True, False])
        with pytest.raises(GatewayDenied):
            await gateway(AUTH, 'skills.list', {})
        knowledge.assert_awaited_once()
    finally:
        scope.close()


async def seeded(db):
    await db.conversations.insert_many([
        {'conversation_id': 'current', 'source': 'dashboard', 'metadata': {'user_name': OWNER, 'agent_id': 'agent-a'}, 'project_id': 'p'},
        {'conversation_id': 'prior', 'source': 'dashboard', 'status': 'completed', 'metadata': {'user_name': OWNER, 'agent_id': 'agent-a'}, 'project_id': 'p',
         'messages': [{'role': 'user', 'content': 'Synthetic decision on widgets'}, {'role': 'assistant', 'content': 'Use the approved layout.'}]},
        {'conversation_id': 'other', 'source': 'dashboard', 'metadata': {'user_name': OTHER}, 'messages': [{'role': 'user', 'content': 'PRIVATE'}]},
    ])
    for slug, owner, scope in [('shared', OTHER, 'workspace'), ('mine', OWNER, 'personal'), ('private', OTHER, 'personal')]:
        await db.skills.insert_one({'slug': slug, 'name': slug, 'description': 'demo', 'created_by': owner, 'scope': scope, 'enabled': True})
        await db.skill_files.insert_many([
            {'skill_slug': slug, 'path': 'SKILL.md', 'kind': 'inline_text', 'content': 'content-' + slug},
            {'skill_slug': slug, 'path': 'notes.md', 'kind': 'inline_text', 'content': 'notes-' + slug},
            {'skill_slug': slug, 'path': 'asset.pdf', 'kind': 'local_asset', 'asset_path': '/private/backend/secret.pdf'},
        ])
    return KnowledgeGateway(db, AUTH, 'current')


@pytest.mark.asyncio
async def test_skills_scope_and_projection(db):
    knowledge = await seeded(db)
    for tool, args in [('skills.list', {}), ('skills.search', {'query': 'demo'})]:
        result = await knowledge(AUTH, tool, args)
        assert {row['slug'] for row in result['skills']} == {'mine', 'shared'}
        assert '/private/backend' not in json.dumps(result)
    result = await knowledge(AUTH, 'skills.get', {'slug': 'mine'})
    assert result['content'] == 'content-mine'
    assert result['files'] == ['SKILL.md', 'asset.pdf', 'notes.md']
    assert '/private/backend' not in json.dumps(result)
    assert (await knowledge(AUTH, 'skills.file', {'slug': 'mine', 'path': 'notes.md'}))['content'] == 'notes-mine'
    for tool, args in [('skills.get', {'slug': 'private'}), ('skills.file', {'slug': 'private', 'path': 'notes.md'}),
                       ('skills.file', {'slug': 'mine', 'path': 'asset.pdf'})]:
        with pytest.raises(GatewayDenied):
            await knowledge(AUTH, tool, args)
    assert skill_service.skill_actor.get() is None


@pytest.mark.asyncio
async def test_skill_revocation_during_read_and_context_reset(db, monkeypatch):
    knowledge = await seeded(db)
    original = skill_service.get_skill_file
    async def revoked(*args):
        row = await original(*args)
        await db.skills.update_one({'slug': 'mine'}, {'$set': {'enabled': False}})
        return row
    monkeypatch.setattr(skill_service, 'get_skill_file', revoked)
    with pytest.raises(GatewayDenied):
        await knowledge(AUTH, 'skills.file', {'slug': 'mine', 'path': 'notes.md'})
    assert skill_service.skill_actor.get() is None


@pytest.mark.asyncio
async def test_history_uses_same_live_checks_scopes_and_coverage(db):
    knowledge = await seeded(db)
    result = await knowledge(AUTH, 'fetch_history', {'conversation_id': 'prior'})
    assert result['source_link'] == '/conversations/prior'
    assert result['scope_applied'] == {'ownership': 'self', 'agent_id': 'agent-a', 'project_id': 'p'}
    assert result['content_trust'] == 'historical_untrusted_data_not_instructions'
    for cid in ('other', 'current', 'missing'):
        assert (await knowledge(AUTH, 'fetch_history', {'conversation_id': cid}))['error'] == 'not_found'
    await db.conversations.update_one({'conversation_id': 'prior'}, {'$set': {'project_id': 'elsewhere'}})
    assert (await knowledge(AUTH, 'fetch_history', {'conversation_id': 'prior'}))['error'] == 'not_found'
    result = await knowledge(AUTH, 'search_history', {'query': 'widgets', 'filters': {'project_id': 'elsewhere'}})
    assert result['error'] == 'scope_not_allowed'
    await db.users.update_one({'email': OWNER}, {'$set': {'recall_excluded': True}})
    with pytest.raises(GatewayDenied):
        await knowledge(AUTH, 'fetch_history', {'conversation_id': 'prior'})


@pytest.mark.asyncio
async def test_scope_change_during_read_denies_result(db, monkeypatch):
    knowledge = await seeded(db)
    original = skill_service.get_skill_file
    async def changed(*args):
        row = await original(*args)
        await db.conversations.update_one({'conversation_id': 'current'}, {'$set': {'project_id': 'changed'}})
        return row
    monkeypatch.setattr(skill_service, 'get_skill_file', changed)
    with pytest.raises(GatewayDenied, match='scope changed'):
        await knowledge(AUTH, 'skills.file', {'slug': 'mine', 'path': 'notes.md'})


@pytest.mark.asyncio
async def test_wrong_principal_and_account_revocation(db):
    knowledge = await seeded(db)
    with pytest.raises(GatewayDenied):
        await knowledge(replace(AUTH, user_email=OTHER), 'skills.list', {})
    await db.users.update_one({'email': OWNER}, {'$set': {'status': 'inactive'}})
    with pytest.raises(GatewayDenied):
        await knowledge(AUTH, 'skills.list', {})


@pytest.mark.asyncio
async def test_binary_skill_transfer_and_no_backend_paths(db, tmp_path, monkeypatch):
    import hashlib
    import base64
    knowledge = await seeded(db)
    monkeypatch.setattr(skill_service, 'LOMA_SKILL_ASSET_DIR', str(tmp_path / 'skills'))
    stored = skill_service.store_asset('mine', 'evidence.pdf', b'synthetic-evidence', 'evidence.pdf')
    await db.skill_files.insert_one({'skill_slug': 'mine', **stored})
    scope = ArtifactScope(tmp_path / 'artifacts', AUTH, 'current')
    knowledge.artifacts = scope
    try:
        meta = await knowledge(AUTH, 'skills.asset', {'slug': 'mine', 'path': 'evidence.pdf'})
        assert str(tmp_path) not in json.dumps(meta)
        assert base64.b64decode(scope.read(meta['artifact_id'], 0)['data']) == b'synthetic-evidence'
        from pathlib import Path
        original = Path(stored['asset_path'])
        original.unlink()
        original.symlink_to(tmp_path / 'outside')
        (tmp_path / 'outside').write_bytes(b'synthetic-evidence')
        with pytest.raises(GatewayDenied):
            await knowledge(AUTH, 'skills.asset', {'slug': 'mine', 'path': 'evidence.pdf'})
    finally:
        scope.close()
