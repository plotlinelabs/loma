"""Explicitly attached playbooks. Sharing never shares connection credentials.

Only the author can edit or delete. Reads are checked against the current ACL
and active author, including at dispatch after a human approval wait.
"""
from pymongo import ReturnDocument
from autonomy import core


def readable(owner):
    return {'$or': [{'owner': owner}, {'readers': owner}], 'deleted': {'$ne': True}}


def references(value):
    if not isinstance(value, list) or len(value) > 10 or any(not isinstance(v, str) or not v or len(v) > 100 for v in value):
        raise ValueError('Attach up to 10 playbooks')
    return list(dict.fromkeys(value))


async def resolve(db, owner, ids):
    ids = references(ids)
    result = []
    for source_id in ids:
        source = await db.agent_knowledge.find_one({'source_id': source_id, **readable(owner)}, {'_id': 0})
        if not source:
            raise ValueError('An attached playbook is no longer accessible. Ask its author to restore access or create work without it.')
        await core.authority_account(db, source['owner'])
        result.append({k: source[k] for k in ('source_id', 'title', 'content', 'version')})
    return result


async def listing(db, owner):
    sources = await db.agent_knowledge.find(readable(owner), {'_id': 0}).sort('updated_at', -1).limit(100).to_list(100)
    authors = await db.users.find({'email': {'$in': list({s['owner'] for s in sources})}, 'status': 'active'}, {'email': 1}).to_list(100)
    active = {a['email'] for a in authors}
    return [{k: v for k, v in s.items() if k != 'readers' or s['owner'] == owner} for s in sources if s['owner'] in active]


async def save(db, owner, body, source_id=None):
    await core.authority_account(db, owner)
    readers = body.get('readers', [])
    if not isinstance(readers, list) or len(readers) > 20:
        raise ValueError('Share with up to 20 active workspace accounts')
    readers = sorted(set(core.text(r, 'Reader email', 254).lower() for r in readers) - {owner})
    for reader in readers:
        await core.authority_account(db, reader)
    fields = {'title': core.text(body.get('title'), 'Playbook title', 120),
              'content': core.text(body.get('content'), 'Playbook text', 6000),
              'readers': readers, 'updated_at': core.now()}
    if source_id is None:
        source = {**fields, 'source_id': core.ident(), 'owner': owner, 'version': 1, 'created_at': core.now()}
        await db.agent_knowledge.insert_one(dict(source))
        return source
    version = body.get('version')
    if type(version) is not int or version < 1:
        raise ValueError('Reload the playbook before saving')
    result = await db.agent_knowledge.find_one_and_update(
        {'source_id': source_id, 'owner': owner, 'version': version, 'deleted': {'$ne': True}},
        {'$set': fields, '$inc': {'version': 1}}, return_document=ReturnDocument.AFTER)
    if not result:
        raise ValueError('Playbook changed or is not yours. Reload before saving.')
    return result


async def delete(db, owner, source_id, version):
    await core.authority_account(db, owner)
    if type(version) is not int or version < 1:
        raise ValueError('Reload the playbook before deleting')
    result = await db.agent_knowledge.update_one(
        {'source_id': source_id, 'owner': owner, 'version': version, 'deleted': {'$ne': True}},
        {'$set': {'deleted': True, 'content': '', 'readers': [], 'updated_at': core.now()}, '$inc': {'version': 1}})
    if not result.modified_count:
        raise ValueError('Playbook changed or is not yours. Reload before deleting.')
