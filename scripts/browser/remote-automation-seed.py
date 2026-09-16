"""Synthetic proposal/skill fixtures; refuses non-throwaway databases."""
import asyncio,os,sys,uuid,json
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient
from unittest.mock import AsyncMock
sys.path.insert(0,os.getcwd())
from isolation.protocol import RunAuthority
from isolation.proposals import ProposalGateway
async def main():
 assert len(sys.argv)==2 and sys.argv[1] in ("desktop","mobile")
 env=dotenv_values('.env');dash=dotenv_values('dashboard/.env');owner=dash['USER_NAME']
 assert env['OBSERVABILITY_DB_NAME'].startswith('loma_local_utility_')
 os.environ['LOMA_REMOTE_AUTOMATION_GRANTS']=json.dumps({owner:{'github':['qa/remote-worker']}})
 client=AsyncIOMotorClient(env['OBSERVABILITY_MONGODB_URI'],tz_aware=True);db=client[env['OBSERVABILITY_DB_NAME']]
 await db.skills.update_one({'slug':'remote-utility-qa'},{'$set':{'name':'Remote utility QA','description':'Synthetic utility browser fixture','folder':None,'enabled':True,'created_by':owner}},upsert=True)
 authority=RunAuthority(uuid.uuid4().hex,owner,frozenset({'github.propose_write'}))
 gateway=ProposalGateway(db,authority,'browser-'+sys.argv[1],check_access=AsyncMock(return_value=True))
 await gateway(authority,'github.propose_write',{'operation':'comment','repo':'qa/remote-worker','number':191,'body':'QA approved comment','reason':'Browser '+sys.argv[1]})
 client.close()
asyncio.run(main())
