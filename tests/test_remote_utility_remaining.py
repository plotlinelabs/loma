"""Remaining utility callers use owner-bound remote completions, never local CLIs."""
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from isolation import utility
from isolation.gateway import GatewayDenied

@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv('LOMA_REMOTE_WORKERS', 'on')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(side_effect=AssertionError('local')))
    mock = AsyncMock()
    monkeypatch.setattr(utility, 'complete', mock)
    return mock

@pytest.mark.asyncio
async def test_extraction_and_quality(remote):
    from api.dashboard_ingestion import _extract_thread_refs_llm
    from observability.review_quality import assess_review_quality
    db = MagicMock()
    remote.return_value = '{"linear_issue":["TEST-123"]}'
    assert await _extract_thread_refs_llm('input', db=db, conversation_id='c') == {'linear_issue':['TEST-123']}
    assert remote.await_args.kwargs == dict(db=db, conversation_id='c', timeout=15)
    remote.return_value = '{"quality":"effective", "learnings":[]}'
    assert (await assess_review_quality(db, 'c', {}, {}))['quality'] == 'effective'
    assert remote.await_args.kwargs == dict(db=db, conversation_id='c', timeout=60)

@pytest.mark.asyncio
@pytest.mark.parametrize('answer,expected', [('yes',True),('YES.',True),('no',False),('not yes',False)])
async def test_dedup_exact_answer(remote, answer, expected):
    from observability.org_learnings import _haiku_confirm_duplicate
    db = MagicMock(); remote.return_value = answer
    assert await _haiku_confirm_duplicate('a','b',db=db,conversation_id='c') is expected
    assert remote.await_args.kwargs['conversation_id'] == 'c'

@pytest.mark.asyncio
async def test_verifier_remote_even_when_api_provider_forced(remote, monkeypatch):
    from gate.verifier import get_verifier, RemoteVerifier
    monkeypatch.setenv('GATE_VERIFIER','openai')
    verifier = get_verifier()
    assert isinstance(verifier, RemoteVerifier)
    remote.return_value = '{"confidence_score":0.5,"claims_verified":false,"risk_flags":["no_evidence"]}'
    result = await verifier.assess_remote(db=MagicMock(), conversation_id='c', ticket_context='t', draft_reply='d', evidence_kinds=[])
    assert result.confidence_score == .5 and not result.claims_verified
    with pytest.raises(RuntimeError):
        verifier.assess(ticket_context='t',draft_reply='d',evidence_kinds=[])

@pytest.mark.asyncio
async def test_failure_fallbacks(remote):
    from api.dashboard_ingestion import _extract_thread_refs_llm
    from observability.review_quality import assess_review_quality
    from observability.org_learnings import _haiku_confirm_duplicate
    remote.side_effect = GatewayDenied('revoked')
    assert await _extract_thread_refs_llm('text') == {}
    assert await assess_review_quality(MagicMock(),'c',{}, {}) is None
    assert not await _haiku_confirm_duplicate('a','b')
    asyncio.create_subprocess_exec.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['chatter',None,'maintainer','admin'])
async def test_maintenance_attribution_cleanup(remote, role):
    db = MagicMock()
    db.users.find_one = AsyncMock(return_value={'system_role':role,'status':'active'})
    db.conversations.insert_one = AsyncMock(); db.conversations.delete_one = AsyncMock()
    remote.return_value = '{}'
    if role not in ('maintainer','admin'):
        with pytest.raises(GatewayDenied):
            await utility.complete_maintenance('text',db=db,owner='u@example.test',purpose='skill-organization')
        remote.assert_not_called(); db.conversations.insert_one.assert_not_called()
    else:
        assert await utility.complete_maintenance('text',db=db,owner='u@example.test',purpose='skill-organization') == '{}'
        row = db.conversations.insert_one.await_args.args[0]
        assert row['metadata']['user_name'] == 'u@example.test' and row['messages'] == []
        assert remote.await_args.kwargs['conversation_id'] == row['conversation_id']
        db.conversations.delete_one.assert_awaited_once()

@pytest.mark.asyncio
async def test_maintenance_cleanup_on_failure(remote):
    db = MagicMock(); db.users.find_one = AsyncMock(return_value={'system_role':'admin'})
    db.conversations.insert_one=AsyncMock(); db.conversations.delete_one=AsyncMock()
    remote.side_effect=GatewayDenied('failed')
    with pytest.raises(GatewayDenied):
        await utility.complete_maintenance('text',db=db,owner='u@example.test',purpose='skill-organization')
    db.conversations.delete_one.assert_awaited_once()

from tests.test_bounded_work import db, OWNER  # noqa: E402,F811
@pytest.mark.asyncio
async def test_maintenance_real_context_and_revocation(db,monkeypatch):
    from isolation.context import UtilityContext
    from isolation.protocol import RunAuthority
    await db.users.update_one({'email':OWNER},{'$set':{'system_role':'maintainer'}})
    async def complete(message,**kwargs):
        authority=RunAuthority('maintenance',OWNER,frozenset())
        context=UtilityContext(db,authority,kwargs['conversation_id'],cancelled=asyncio.Event(),check_access=AsyncMock(return_value=True))
        await context.load(message)
        assert context.history==()
        await db.users.update_one({'email':OWNER},{'$set':{'system_role':'chatter'}})
        assert not await context.authorize(authority)
        return 'result'
    monkeypatch.setattr(utility,'complete',complete)
    assert await utility.complete_maintenance('text',db=db,owner=OWNER,purpose='skill-organization')=='result'
    assert await db.conversations.count_documents({'source':'utility'})==0

@pytest.mark.asyncio
async def test_skill_organization_routes_authenticated_owner(remote,monkeypatch):
    from api.skill_service import auto_organize_skills
    db=MagicMock()
    db.skills.find.return_value.to_list=AsyncMock(return_value=[{'slug':'test-skill','name':'Test','description':'Test'}])
    db.skills.update_one=AsyncMock(return_value=MagicMock(modified_count=1))
    maintenance=AsyncMock(return_value='{"test-skill":"Engineering"}')
    monkeypatch.setattr(utility,'complete_maintenance',maintenance)
    result=await auto_organize_skills(db,owner='owner@example.test')
    assert result['organized']==1
    assert maintenance.await_args.kwargs=={'db':db,'owner':'owner@example.test','purpose':'skill-organization','timeout':120}


def test_explicit_heuristic_stays_tool_and_model_free(remote,monkeypatch):
    from gate.verifier import get_verifier, HeuristicVerifier
    monkeypatch.setenv('GATE_VERIFIER','heuristic')
    assert isinstance(get_verifier(),HeuristicVerifier)
    remote.assert_not_called()
