"""Fixed GitHub/Linear operations with operator-scoped, revocable owner grants.

No URLs, raw GraphQL, credentials, shell commands, merge or delete operations
are accepted. Workers can read or propose; writes run only after durable owner
approval. Provider responses and repository content are untrusted data.
"""
import json
import os
import re
from urllib.parse import quote

import aiohttp
from isolation.gateway import GatewayDenied

READ_TOOLS = {'github.read', 'linear.read'}
WRITE_ACTIONS = {'github.write', 'linear.write'}
REPO = r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'
UUID = r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}'
SHA = r'[a-f0-9]{40}'
# Exact schemas for each provider operation; additional keys fail closed.
SCHEMAS = {
    'github.read': {
        'pr': ({'repo','number'}, set()),
        'files': ({'repo','number'}, {'page'}),
        'reviews': ({'repo','number'}, {'page'}),
        'comments': ({'repo','number'}, {'page'}),
        'content': ({'repo','path','ref'}, set()),
        'tree': ({'repo','ref'}, set()),
    },
    'github.write': {
        'comment': ({'repo','number','body'}, set()),
        'review': ({'repo','number','body','commit_id','event'}, set()),
        'create_pr': ({'repo','head','base','title','body'}, set()),
        'branch': ({'repo','branch','sha'}, set()),
        'file': ({'repo','branch','path','content','message'}, {'sha'}),
    },
    'linear.read': {
        'issue': ({'issue_id'}, set()),
        'comments': ({'issue_id'}, {'cursor'}),
        'issues': ({'team_id'}, {'cursor'}),
        'states': ({'team_id'}, set()),
    },
    'linear.write': {
        'comment': ({'issue_id','body'}, set()),
        'create': ({'team_id','title','description'}, set()),
        'update': ({'issue_id','title','description'}, {'state_id'}),
    },
}


def validate(tool, arguments):
    if not isinstance(arguments, dict):
        raise GatewayDenied('Invalid automation arguments')
    operation = arguments.get('operation')
    if not isinstance(operation, str) or operation not in SCHEMAS.get(tool, {}):
        raise GatewayDenied('Unsupported automation operation')
    required, optional = SCHEMAS[tool][operation]
    required = required | {'operation'}
    if not required <= set(arguments) <= required | optional:
        raise GatewayDenied('Unexpected or missing automation arguments')
    for key, value in arguments.items():
        if key in ('number','page'):
            if type(value) is not int or not 1 <= value <= (100 if key == 'page' else 10**8):
                raise GatewayDenied('Invalid automation number')
            continue
        limit = 12000 if key in ('body','description','content') else 1000
        if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
            raise GatewayDenied('Invalid automation text')
        if key not in ('body','description','content') and any(c in value for c in '\r\n'):
            raise GatewayDenied('Invalid automation text')
        pattern = {'repo':REPO,'team_id':UUID,'state_id':UUID,'issue_id':rf'(?:{UUID}|[A-Z][A-Z0-9]*-[1-9][0-9]*)','sha':SHA,'commit_id':SHA}.get(key)
        if pattern and not re.fullmatch(pattern,value):
            raise GatewayDenied('Invalid automation identifier')
        if key in ('path','ref','head','base','branch'):
            if (value.startswith(('/', '-')) or any(p in ('','..','.') for p in value.split('/'))
                    or not re.fullmatch(r'[A-Za-z0-9_./-]+',value) or value.endswith(('/', '.lock'))):
                raise GatewayDenied('Invalid repository path or ref')
        if key == 'branch' and not value.startswith('loma/'):
            raise GatewayDenied('Writes require an owner-approved loma/ branch')
        if key == 'head' and not value.startswith('loma/'):
            raise GatewayDenied('Pull requests require a loma/ head branch')
        if key == 'path' and tool == 'github.write' and value.lower().startswith('.github/'):
            raise GatewayDenied('Workflow changes are not available through workers')
        if key == 'event' and value not in ('COMMENT','REQUEST_CHANGES','APPROVE'):
            raise GatewayDenied('Invalid review event')
    return dict(arguments)


async def authorize(db, owner, provider, resource):
    """Operator policy uses exact owner->provider resource lists, never wildcards."""
    user = await db.users.find_one({'email':owner,'deleted':{'$ne':True}}, {'status':1})
    try:
        policy = json.loads(os.environ.get('LOMA_REMOTE_AUTOMATION_GRANTS', '{}'))
        resources = policy.get(owner, {}).get(provider, [])
        allowed = isinstance(resources, list) and all(isinstance(v,str) for v in resources) and resource in resources
    except (ValueError, AttributeError, TypeError):
        allowed = False
    if not user or user.get('status','active') != 'active' or not allowed:
        raise GatewayDenied('Automation resource is not granted to this owner')


async def request(provider, method, path, payload=None):
    """Only module-generated paths reach fixed origins. No redirects or retries."""
    from tools._integration_key import get_integration_key
    key = os.environ.get('GITHUB_API_KEY' if provider == 'github' else 'LINEAR_API_KEY') or get_integration_key(provider)
    if not key:
        raise GatewayDenied('Automation integration is not configured')
    origin = 'https://api.github.com' if provider == 'github' else 'https://api.linear.app'
    headers = {'Authorization':('Bearer ' if provider == 'github' else '') + key}
    if provider == 'github':
        headers.update({'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'})
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
            trust_env=False, cookie_jar=aiohttp.DummyCookieJar()) as session:
        async with session.request(method, origin+path, json=payload, headers=headers, allow_redirects=False) as response:
            if not 200 <= response.status < 300:
                raise GatewayDenied('Automation provider request failed')
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(16384):
                size += len(chunk)
                if size > 512*1024:
                    raise GatewayDenied('Automation response too large')
                chunks.append(chunk)
            value = json.loads(b''.join(chunks))
            if provider == 'linear':
                if not isinstance(value,dict) or value.get('errors') or not isinstance(value.get('data'),dict):
                    raise GatewayDenied('Linear operation failed')
                value = value['data']
            return value


async def gql(query, variables):
    return await request('linear','POST','/graphql',{'query':query,'variables':variables})


async def check_scope(db, owner, tool, args):
    """Re-resolve an issue's team on every request and again before a write."""
    provider = tool.split('.')[0]
    if provider == 'github':
        await authorize(db,owner,provider,args['repo'])
        return args['repo']
    if 'team_id' in args:
        team = args['team_id']
    else:
        # Check there is a grant BEFORE touching a shared provider credential.
        try:
            teams = json.loads(os.environ.get('LOMA_REMOTE_AUTOMATION_GRANTS','{}')).get(owner,{}).get('linear',[])
        except (ValueError,AttributeError):
            teams = []
        if not isinstance(teams,list) or not teams:
            raise GatewayDenied('No Linear grant for this owner')
        await authorize(db,owner,provider,teams[0])
        value = await gql('query($id:String!){issue(id:$id){team{id}}}',{'id':args['issue_id']})
        team = ((value.get('issue') or {}).get('team') or {}).get('id')
    await authorize(db,owner,provider,team)
    return team


async def read(db, owner, tool, arguments):
    args = validate(tool,arguments)
    if tool not in READ_TOOLS:
        raise GatewayDenied('Read operation required')
    scope = await check_scope(db,owner,tool,args)
    op = args['operation']
    if tool == 'github.read':
        prefix = '/repos/'+args['repo']
        if op == 'tree':
            path = prefix+'/git/trees/'+quote(args['ref'],safe='')
        elif op == 'content':
            path = prefix+'/contents/'+quote(args['path'],safe='/')+'?ref='+quote(args['ref'],safe='')
        else:
            path = prefix+('/issues/' if op == 'comments' else '/pulls/')+str(args['number'])
            if op != 'pr':
                path += '/'+op+'?per_page=30&page='+str(args.get('page',1))
        value = await request('github','GET',path)
    elif op == 'issue':
        value = await gql('query($id:String!){issue(id:$id){id identifier title description url team{id} state{id name}}}',{'id':args['issue_id']})
    elif op == 'comments':
        value = await gql('query($id:String!,$after:String){issue(id:$id){team{id} comments(first:30,after:$after){nodes{id body url} pageInfo{hasNextPage endCursor}}}}',{'id':args['issue_id'],'after':args.get('cursor')})
    elif op == 'issues':
        value = await gql('query($id:String!,$after:String){team(id:$id){issues(first:30,after:$after){nodes{id identifier title url} pageInfo{hasNextPage endCursor}}}}',{'id':args['team_id'],'after':args.get('cursor')})
    else:
        value = await gql('query($id:String!){team(id:$id){states{nodes{id name type}}}}',{'id':args['team_id']})
    if tool == 'linear.read' and 'issue_id' in args:
        returned_team = ((value.get('issue') or {}).get('team') or {}).get('id')
        if returned_team != scope:
            raise GatewayDenied('Issue team changed during the read')
    await authorize(db,owner,tool.split('.')[0],scope)
    return {'data':value, 'coverage':'One bounded page; repository patches/content may be truncated by the provider.'}


async def write(db, owner, action, arguments, proposal_id):
    args = validate(action,arguments)
    await check_scope(db,owner,action,args)
    op = args['operation']
    if action == 'github.write':
        prefix = '/repos/'+args['repo']
        if op in ('comment','review'):
            if op == 'review':
                pr = await request('github','GET',prefix+'/pulls/'+str(args['number']))
                if (pr.get('head') or {}).get('sha') != args['commit_id']:
                    raise GatewayDenied('PR head changed; a new review proposal is required')
            path = prefix+('/issues/' if op == 'comment' else '/pulls/')+str(args['number'])+('/comments' if op == 'comment' else '/reviews')
            payload = {k:args[k] for k in (('body',) if op == 'comment' else ('body','commit_id','event'))}
        elif op == 'create_pr':
            path = prefix+'/pulls'; payload = {k:args[k] for k in ('head','base','title','body')}; payload['draft']=True
        elif op == 'branch':
            path = prefix+'/git/refs'; payload = {'ref':'refs/heads/'+args['branch'],'sha':args['sha']}
        else:
            import base64
            path = prefix+'/contents/'+quote(args['path'],safe='/')
            payload = {'message':args['message'],'branch':args['branch'], 'content':base64.b64encode(args['content'].encode()).decode()}
            if 'sha' in args: payload['sha']=args['sha']
        await check_scope(db,owner,action,args)
        value = await request('github','PUT' if op == 'file' else 'POST',path,payload)
        receipt = value.get('id') or value.get('sha') or (value.get('commit') or {}).get('sha') or (value.get('object') or {}).get('sha')
        if not receipt: raise GatewayDenied('Missing GitHub write receipt')
        return {'id':receipt, 'url':value.get('html_url'), 'proposal_id':proposal_id}
    if 'issue_id' in args:
        issue = (await gql('query($id:String!){issue(id:$id){id team{id}}}', {'id':args['issue_id']})).get('issue') or {}
        await authorize(db,owner,'linear',(issue.get('team') or {}).get('id'))
        if not isinstance(issue.get('id'),str) or not re.fullmatch(UUID,issue['id']):
            raise GatewayDenied('Linear issue identity is unavailable')
        args['issue_id'] = issue['id']
    if op == 'comment':
        query='mutation($input:CommentCreateInput!){commentCreate(input:$input){success comment{id url}}}'
        payload={'issueId':args['issue_id'],'body':args['body']}; root='commentCreate'; field='comment'
    elif op == 'create':
        query='mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id url}}}'
        payload={'teamId':args['team_id'],'title':args['title'],'description':args['description']}; root='issueCreate'; field='issue'
    else:
        query='mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id url}}}'
        payload={'title':args['title'],'description':args['description']}; root='issueUpdate'; field='issue'
    if 'state_id' in args:
        state = (await gql('query($id:String!){workflowState(id:$id){team{id}}}', {'id':args['state_id']})).get('workflowState') or {}
        state_team = (state.get('team') or {}).get('id')
        issue_team = (issue.get('team') or {}).get('id')
        if state_team != issue_team:
            raise GatewayDenied('State must belong to the issue team')
        payload['stateId'] = args['state_id']
    await check_scope(db,owner,action,args)
    variables={'input':payload}
    if op == 'update': variables['id']=args['issue_id']
    value = (await gql(query,variables)).get(root) or {}
    if value.get('success') is not True or not (value.get(field) or {}).get('id'):
        raise GatewayDenied('Missing Linear write receipt')
    return {**value[field], 'proposal_id':proposal_id}


async def webhook_owner(db, conversation_id):
    """Bind authenticated webhook ingress to an explicit operator-selected owner.

    Never infer identity from GitHub logins, issue text, or an arbitrary admin.
    Existing attribution is immutable; a mapping change cannot seize history.
    """
    row = await db.conversations.find_one({'conversation_id':conversation_id,'deleted':{'$ne':True}})
    if not row or row.get('source') not in ('github_webhook','linear_webhook'):
        raise GatewayDenied('Not an eligible automation conversation')
    provider = row['source'].split('_')[0]
    metadata = row.get('metadata') or {}
    try:
        owners = json.loads(os.environ.get('LOMA_REMOTE_WEBHOOK_OWNERS','{}')).get(provider,{})
    except (ValueError,AttributeError):
        raise GatewayDenied('Invalid automation owner configuration') from None
    if not isinstance(owners,dict) or not owners:
        raise GatewayDenied('No explicit automation owner is configured')
    if provider == 'github':
        resource = metadata.get('github_repo')
    else:
        issue = metadata.get('linear_issue_id')
        if not isinstance(issue,str) or not re.fullmatch(rf'(?:{UUID}|[A-Z][A-Z0-9]*-[1-9][0-9]*)',issue):
            raise GatewayDenied('Missing trusted issue identifier')
        result = await gql('query($id:String!){issue(id:$id){team{id}}}',{'id':issue})
        resource = ((result.get('issue') or {}).get('team') or {}).get('id')
    owner = owners.get(resource)
    if not isinstance(owner,str) or not owner:
        raise GatewayDenied('No explicit owner for this automation resource')
    await authorize(db,owner,provider,resource)
    existing = metadata.get('user_name')
    if existing and existing != owner:
        raise GatewayDenied('Automation ownership changed; operator repair required')
    result = await db.conversations.update_one({'conversation_id':conversation_id,
        'deleted':{'$ne':True}, 'metadata.user_name':existing},
        {'$set':{'metadata.user_name':owner, 'metadata.remote_automation':{'owner':owner,'provider':provider,'resource':resource}}})
    if result.matched_count != 1:
        raise GatewayDenied('Automation ownership changed')
    return owner, provider, resource


async def authorize_webhook(db, owner, provider, resource):
    await authorize(db,owner,provider,resource)
    try:
        current = json.loads(os.environ.get('LOMA_REMOTE_WEBHOOK_OWNERS','{}')).get(provider,{}).get(resource)
    except (ValueError,AttributeError):
        current = None
    if current != owner:
        raise GatewayDenied('Automation owner grant revoked')
