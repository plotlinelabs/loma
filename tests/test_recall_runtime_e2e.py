"""Opt-in full-stack session issuer tests. Requires logged-in browser storageState.

Never reads real customer data: the DB must be loma_local_* and all records this
fixture creates are synthetic. No signing key or provider account is supplied.
"""
import json
import os
import uuid
from pathlib import Path
import aiohttp
from bson import ObjectId
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.environ.get('LOMA_RECALL_RUNTIME_E2E') != '1', reason='requires isolated dashboard and backend')]


@pytest_asyncio.fixture
async def stack():
    config = dotenv_values(Path(__file__).parents[1] / '.env')
    assert config['OBSERVABILITY_DB_NAME'].startswith('loma_local_')
    assert config['WEBHOOK_PORT'] == '13000'
    assert config['LOMA_ENABLE_SLACK'] == config['LOMA_ENABLE_SCHEDULER'] == 'false'
    state = json.loads(Path(os.environ['LOMA_RECALL_BROWSER_STATE']).read_text())
    cookie = '; '.join(c['name']+'='+c['value'] for c in state['cookies'] if c['domain'] in ('localhost', '127.0.0.1'))
    client = AsyncIOMotorClient(config['OBSERVABILITY_MONGODB_URI'])
    db = client[config['OBSERVABILITY_DB_NAME']]
    owner = os.environ['LOMA_RECALL_TEST_EMAIL']
    user = await db.users.find_one({'email': owner})
    assert user
    ids = ['runtime-e2e-'+uuid.uuid4().hex for _ in range(3)]
    for cid, email in [(ids[0],owner),(ids[1],owner),(ids[2],'synthetic-other@example.com')]:
        await db.conversations.insert_one({'conversation_id':cid,'source':'dashboard','status':'completed',
            'metadata':{'user_name':email},'title':'Synthetic runtime history','messages':[
                {'role':'user','content':'A synthetic previous decision'},
                {'role':'assistant','content':'Use cobalt.\npassword=SYNTHETIC_PRIVATE_CANARY'}]})
    grants=[]
    async with aiohttp.ClientSession() as http:
        async def issuer(body, authenticated=False):
            async with http.post('http://localhost:13001/api/recall-session',json=body,
                    headers={'Cookie':cookie} if authenticated else {}) as response:
                result=await response.json()
                if result.get('grant'): grants.append(result['grant'])
                return response.status,result
        async def fetch(capability, cid):
            async with http.post('http://localhost:13000/api/recall/fetch',
                    headers={'Authorization':'Bearer '+capability},json={'conversation_id':cid}) as response:
                return response.status,await response.json()
        try:
            yield db, ids, issuer, fetch
        finally:
            for grant in grants: await issuer({'action':'revoke','grant':grant})
            await db.conversations.delete_many({'conversation_id':{'$in':ids}})
            client.close()


async def test_session_required_and_other_owner_hidden(stack):
    _,ids,issuer,_=stack
    assert (await issuer({'action':'launch','conversation_id':ids[0], 'email':'synthetic@example.com'}))[0]==401
    assert (await issuer({'action':'launch','conversation_id':ids[2]},True))[0]==404


async def test_issued_capability_fetch_redaction_and_ownership(stack):
    _,ids,issuer,fetch=stack
    status,session=await issuer({'action':'launch','conversation_id':ids[0]},True)
    assert status==200
    assert (await fetch(session['capability'],ids[0]))[0]==404
    assert (await fetch(session['capability'],ids[2]))[0]==404
    status,data=await fetch(session['capability'],ids[1])
    assert status==200 and 'cobalt' in json.dumps(data)
    assert 'SYNTHETIC_PRIVATE_CANARY' not in json.dumps(data)


async def test_renewal_revocation_and_forgery(stack):
    _,ids,issuer,fetch=stack
    _,session=await issuer({'action':'launch','conversation_id':ids[0]},True)
    status,renewed=await issuer({'action':'renew','grant':session['grant']})
    assert status==200 and (await fetch(renewed['capability'],ids[1]))[0]==200
    assert (await fetch(renewed['capability']+'x',ids[1]))[0]==401
    assert (await issuer({'action':'revoke','grant':session['grant']}))[0]==200
    assert (await fetch(renewed['capability'],ids[1]))[0]==401
    assert (await issuer({'action':'renew','grant':session['grant']}))[0]==401


async def test_scope_change_revokes_active_execution(stack):
    db,ids,issuer,fetch=stack
    _,session=await issuer({'action':'launch','conversation_id':ids[0]},True)
    await db.conversations.update_one({'conversation_id':ids[0]},{'$set':{'project_id':'new-project'}})
    assert (await fetch(session['capability'],ids[1]))[0]==401
    assert (await issuer({'action':'renew','grant':session['grant']}))[0]==401


async def test_excluded_execution_is_immediately_denied(stack):
    db,ids,issuer,fetch=stack
    _,session=await issuer({'action':'launch','conversation_id':ids[0]},True)
    await db.conversations.update_one({'conversation_id':ids[0]},{'$set':{'recall_excluded':True}})
    assert (await fetch(session['capability'],ids[1]))[0]==401
