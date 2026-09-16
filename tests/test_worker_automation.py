import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from isolation import automation as a
from isolation.gateway import GatewayDenied

OWNER='owner@example.test'; TEAM='12345678-1234-1234-1234-123456789abc'

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv('LOMA_REMOTE_AUTOMATION_GRANTS',json.dumps({OWNER:{'github':['org/repo'],'linear':[TEAM]}}))
    db=MagicMock(); db.users.find_one=AsyncMock(return_value={'status':'active'})
    request=AsyncMock(return_value={'id':123,'head':{'sha':'a'*40}})
    monkeypatch.setattr(a,'request',request)
    return db,request

@pytest.mark.asyncio
@pytest.mark.parametrize('operation,suffix', [('pr','/pulls/1'),('files','/pulls/1/files?per_page=30&page=1'),('comments','/issues/1/comments?per_page=30&page=1'),('reviews','/pulls/1/reviews?per_page=30&page=1')])
async def test_github_reads(env,operation,suffix):
    db,request=env
    result=await a.read(db,OWNER,'github.read',{'operation':operation,'repo':'org/repo','number':1})
    assert result['data']['id']==123
    request.assert_awaited_once_with('github','GET','/repos/org/repo'+suffix)

@pytest.mark.asyncio
async def test_content_is_fixed_origin(env):
    db,request=env
    await a.read(db,OWNER,'github.read',{'operation':'content','repo':'org/repo','path':'src/file.py','ref':'loma/test'})
    assert request.await_args.args[2]=='/repos/org/repo/contents/src/file.py?ref=loma%2Ftest'

@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'repo':'other/private'},{'repo':'org/repo/../../secrets'},{'number':True},{'url':'https://evil.test'},{'owner':'admin@example.test'},{'operation':'delete'}])
async def test_denied_before_provider(env,change):
    db,request=env
    with pytest.raises(GatewayDenied):
        await a.read(db,OWNER,'github.read',{'operation':'pr','repo':'org/repo','number':1,**change})
    request.assert_not_called()

@pytest.mark.asyncio
async def test_revocation_during_read(env):
    db,request=env
    db.users.find_one.side_effect=[{'status':'active'},{'status':'disabled'}]
    with pytest.raises(GatewayDenied):
        await a.read(db,OWNER,'github.read',{'operation':'pr','repo':'org/repo','number':1})

@pytest.mark.asyncio
async def test_linear_issue_resolves_team(env):
    db,request=env
    request.side_effect=[{'issue':{'team':{'id':TEAM}}},{'issue':{'id':'id','title':'t','team':{'id':TEAM}}}]
    result=await a.read(db,OWNER,'linear.read',{'operation':'issue','issue_id':'TEST-1'})
    assert result['data']['issue']['title']=='t'
    assert request.await_args.args[2]=='/graphql'
    assert request.await_args.args[3]['variables']=={'id':'TEST-1'}

@pytest.mark.asyncio
async def test_linear_other_team_denied(env):
    db,request=env;request.return_value={'issue':{'team':{'id':'other'}}}
    with pytest.raises(GatewayDenied):
        await a.read(db,OWNER,'linear.read',{'operation':'issue','issue_id':'TEST-1'})
    assert request.await_count==1

@pytest.mark.asyncio
async def test_github_review_pins_head_and_receipt(env):
    db,request=env
    result=await a.write(db,OWNER,'github.write',{'operation':'review','repo':'org/repo','number':1,'commit_id':'a'*40,'event':'COMMENT','body':'review'},'p')
    assert result['id']==123
    assert request.await_args.args[1]=='POST'
    assert request.await_args.args[3]['commit_id']=='a'*40
    request.reset_mock();request.return_value={'head':{'sha':'b'*40}}
    with pytest.raises(GatewayDenied):
        await a.write(db,OWNER,'github.write',{'operation':'review','repo':'org/repo','number':1,'commit_id':'a'*40,'event':'COMMENT','body':'review'},'p')
    assert request.await_count==1

@pytest.mark.asyncio
async def test_github_draft_pr(env):
    db,request=env
    await a.write(db,OWNER,'github.write',{'operation':'create_pr','repo':'org/repo','head':'loma/fix','base':'main','title':'Fix','body':'Details'},'p')
    assert request.await_args.args[3]['draft'] is True

@pytest.mark.asyncio
async def test_file_write_no_main_or_workflow(env):
    db,request=env
    for update in ({'branch':'main'},{'path':'.github/workflows/ci.yml'},{'path':'../secret'}):
        with pytest.raises(GatewayDenied):
            await a.write(db,OWNER,'github.write',{'operation':'file','repo':'org/repo','branch':'loma/fix','path':'src/a.py','content':'text','message':'fix',**update},'p')
    request.assert_not_called()
    request.return_value={'commit':{'sha':'b'*40}}
    result=await a.write(db,OWNER,'github.write',{'operation':'file','repo':'org/repo','branch':'loma/fix','path':'src/a.py','content':'text','message':'fix','sha':'a'*40},'p')
    assert result['id']=='b'*40
    assert request.await_args.args[1]=='PUT'
    assert request.await_args.args[3]['sha']=='a'*40

@pytest.mark.asyncio
async def test_linear_create_success_required(env):
    db,request=env
    args={'operation':'create','team_id':TEAM,'title':'Title','description':'Desc'}
    request.return_value={'issueCreate':{'success':True,'issue':{'id':'receipt'}}}
    assert (await a.write(db,OWNER,'linear.write',args,'p'))['id']=='receipt'
    request.return_value={'issueCreate':{'success':False}}
    with pytest.raises(GatewayDenied):
        await a.write(db,OWNER,'linear.write',args,'p')

@pytest.mark.asyncio
async def test_write_not_available_from_read(env):
    db,request=env
    with pytest.raises(GatewayDenied):
        await a.read(db,OWNER,'github.write',{'operation':'comment','repo':'org/repo','number':1,'body':'text'})
    request.assert_not_called()


def test_catalog_fixed_schemas_and_proposal_validation():
    from isolation.catalog import CATALOG
    from isolation.proposals import validate_write, PROPOSAL_TOOLS
    names={t['name'] for t in CATALOG}
    assert len(names)==len(CATALOG) <= 64
    assert {'github.read','linear.read','github.propose_write','linear.propose_write'} <= names
    for tool,operations in a.SCHEMAS.items():
        name=tool.replace('.write','.propose_write')
        spec=next(t for t in CATALOG if t['name']==name)['input_schema']
        assert set(spec['properties']['operation']['enum'])==set(operations)
        assert set(spec['properties'])=={'operation'} | ({'reason'} if tool.endswith('.write') else set()) | set().union(*(r|o for r,o in operations.values()))
    args={'operation':'comment','repo':'org/repo','number':1,'body':'text'}
    assert validate_write('github.write',args)==args
    assert PROPOSAL_TOOLS['github.propose_write']=='github.write'

@pytest.mark.asyncio
async def test_webhook_owner_explicit_mapping(env,monkeypatch):
    db,request=env
    db.conversations.find_one=AsyncMock(return_value={'source':'github_webhook','metadata':{'github_repo':'org/repo'}})
    db.conversations.update_one=AsyncMock(return_value=MagicMock(matched_count=1))
    with pytest.raises(GatewayDenied): await a.webhook_owner(db,'c')
    monkeypatch.setenv('LOMA_REMOTE_WEBHOOK_OWNERS',json.dumps({'github':{'org/repo':OWNER}}))
    assert await a.webhook_owner(db,'c')==(OWNER,'github','org/repo')
    assert db.conversations.update_one.await_args.args[0]['metadata.user_name'] is None
    db.conversations.find_one.return_value['metadata']['user_name']='other@example.test'
    with pytest.raises(GatewayDenied): await a.webhook_owner(db,'c')
    request.assert_not_called()

@pytest.mark.asyncio
async def test_webhook_revocation(env,monkeypatch):
    db,request=env
    monkeypatch.setenv('LOMA_REMOTE_WEBHOOK_OWNERS',json.dumps({'github':{'org/repo':OWNER}}))
    await a.authorize_webhook(db,OWNER,'github','org/repo')
    monkeypatch.setenv('LOMA_REMOTE_WEBHOOK_OWNERS','{}')
    with pytest.raises(GatewayDenied): await a.authorize_webhook(db,OWNER,'github','org/repo')

# Real Mongo proposal state-machine tests; shared fixture uses a fresh DB only.
from tests.test_bounded_work import db, OWNER as DB_OWNER  # noqa: E402,F811
from isolation.protocol import RunAuthority
from isolation.proposals import ProposalGateway, decide, indexes

@pytest.mark.asyncio
async def test_proposal_approval_scoped_and_no_worker_write(db,monkeypatch):
    monkeypatch.setenv('LOMA_REMOTE_AUTOMATION_GRANTS',json.dumps({DB_OWNER:{'github':['org/repo']}}))
    request=AsyncMock(return_value={'id':456})
    monkeypatch.setattr(a,'request',request)
    await indexes(db)
    authority=RunAuthority('run',DB_OWNER,frozenset({'github.propose_write'}))
    gateway=ProposalGateway(db,authority,'c',check_access=AsyncMock(return_value=True))
    args={'operation':'comment','repo':'org/repo','number':1,'body':'Approved content','reason':'User requested'}
    proposal=await gateway(authority,'github.propose_write',args)
    request.assert_not_called()
    duplicate=await gateway(authority,'github.propose_write',args)
    assert duplicate['duplicate'] and duplicate['proposal_id']==proposal['proposal_id']
    with pytest.raises(ValueError): await decide(db,'other@example.test',proposal['proposal_id'],1,'approve')
    result=await decide(db,DB_OWNER,proposal['proposal_id'],1,'approve')
    assert result['status']=='executed' and result['receipt']['id']==456
    request.assert_awaited_once()
    with pytest.raises(ValueError): await decide(db,DB_OWNER,proposal['proposal_id'],1,'approve')
    assert request.await_count==1

@pytest.mark.asyncio
async def test_proposal_revoked_or_edited_scope_never_sends(db,monkeypatch):
    monkeypatch.setenv('LOMA_REMOTE_AUTOMATION_GRANTS',json.dumps({DB_OWNER:{'github':['org/repo']}}))
    request=AsyncMock(return_value={'id':456});monkeypatch.setattr(a,'request',request)
    authority=RunAuthority('run',DB_OWNER,frozenset({'github.propose_write'}))
    gateway=ProposalGateway(db,authority,'c',check_access=AsyncMock(return_value=True))
    args={'operation':'comment','repo':'org/repo','number':1,'body':'text','reason':'needed'}
    proposal=await gateway(authority,'github.propose_write',args)
    monkeypatch.setenv('LOMA_REMOTE_AUTOMATION_GRANTS','{}')
    result=await decide(db,DB_OWNER,proposal['proposal_id'],1,'approve')
    assert result['status']=='uncertain'
    request.assert_not_called()

@pytest.mark.asyncio
async def test_branch_receipt_and_tree(env):
    db,request=env
    request.return_value={'object':{'sha':'a'*40}}
    result=await a.write(db,OWNER,'github.write',{'operation':'branch','repo':'org/repo','branch':'loma/new','sha':'a'*40},'p')
    assert result['id']=='a'*40
    await a.read(db,OWNER,'github.read',{'operation':'tree','repo':'org/repo','ref':'main'})
    assert request.await_args.args[2]=='/repos/org/repo/git/trees/main'

@pytest.mark.asyncio
async def test_linear_update_state_and_identity(env):
    db,request=env
    issue_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    state_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
    request.side_effect=[{'issue':{'team':{'id':TEAM}}},
        {'issue':{'id':issue_id,'team':{'id':TEAM}}},
        {'workflowState':{'team':{'id':TEAM}}},
        {'issue':{'team':{'id':TEAM}}},
        {'issueUpdate':{'success':True,'issue':{'id':issue_id}}}]
    result=await a.write(db,OWNER,'linear.write',{'operation':'update','issue_id':'TEST-1','title':'Title','description':'Desc','state_id':state_id},'p')
    assert result['id']==issue_id
    assert request.await_args.args[3]['variables']=={'id':issue_id,'input':{'title':'Title','description':'Desc','stateId':state_id}}

@pytest.mark.asyncio
async def test_bound_webhook_context_and_live_revocation(db,monkeypatch):
    import asyncio
    from isolation.context import ConversationContext
    monkeypatch.setenv('LOMA_REMOTE_AUTOMATION_GRANTS',json.dumps({DB_OWNER:{'github':['org/repo']}}))
    monkeypatch.setenv('LOMA_REMOTE_WEBHOOK_OWNERS',json.dumps({'github':{'org/repo':DB_OWNER}}))
    await db.conversations.insert_one({'conversation_id':'hook','source':'github_webhook','status':'running','metadata':{'github_repo':'org/repo'},'messages':[]})
    assert await a.webhook_owner(db,'hook')==(DB_OWNER,'github','org/repo')
    authority=RunAuthority('hook',DB_OWNER,frozenset())
    context=ConversationContext(db,authority,'hook',cancelled=asyncio.Event(),check_access=AsyncMock(return_value=True))
    await context.load('Review this PR')
    assert await context.authorize(authority)
    monkeypatch.setenv('LOMA_REMOTE_WEBHOOK_OWNERS','{}')
    assert not await context.authorize(authority)


@pytest.mark.asyncio
async def test_linear_team_move_during_read_never_releases_data(env):
    db,request=env
    request.side_effect=[{'issue':{'team':{'id':TEAM}}},{'issue':{'title':'private','team':{'id':'other'}}}]
    with pytest.raises(GatewayDenied):
        await a.read(db,OWNER,'linear.read',{'operation':'issue','issue_id':'TEST-1'})
