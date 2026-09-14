"""Rebuildable sanitized projection. Only the offline worker writes this index.

Concurrent refreshes are last-writer-wins, not source-write ordered. Search must
recheck the source revision and policy before releasing any indexed result.
"""
from datetime import datetime, timezone
from uuid import uuid4

from bson import BSON, ObjectId

from api.recall_content import SANITIZER_VERSION, revision, sanitize, visible_messages
from api.recall_routes import _PROJECTION


async def ensure_indexes(db):
    await db.recall_index.create_index([('owner_user_id', 1), ('conversation_id', 1)], unique=True)
    await db.recall_index.create_index([('owner_user_id', 1), ('project_id', 1), ('agent_id', 1)])


async def refresh_owner(db, user_id, after=None, batch_size=100):
    """One bounded backfill/reconciliation batch; caller persists returned cursor.

    A new pass starts at after=None. Deleted/excluded source rows are purged after
    a complete pass. Failures leave coverage partial; retries are idempotent.
    This function never changes source conversations or users.
    """
    if not 1 <= batch_size <= 100 or not ObjectId.is_valid(user_id):
        raise ValueError('invalid_argument')
    user = await db.users.find_one({'_id': ObjectId(user_id), 'deleted': {'$ne': True},
        'status': {'$in': [None, 'active']}, 'recall_excluded': {'$ne': True}})
    if not user or not isinstance(user.get('email'), str) or not user['email']:
        await db.recall_index.delete_many({'owner_user_id': user_id})
        await db.recall_coverage.delete_one({'_id': user_id})
        return {'next_cursor': None, 'status': 'excluded'}
    if after is None:
        await db.recall_coverage.replace_one({'_id': user_id}, {
            '_id': user_id, 'status': 'partial', 'started_at': datetime.now(timezone.utc),
            'generation': uuid4().hex,
        }, upsert=True)
    coverage = await db.recall_coverage.find_one({'_id': user_id})
    if not coverage or not coverage.get('generation'):
        raise ValueError('restart_backfill')
    generation = coverage['generation']
    query = {'metadata.user_name': user['email']}
    if after is not None:
        query['_id'] = {'$gt': ObjectId(after)}
    docs = await db.conversations.find(query, {'_id': 1, 'conversation_id': 1}).sort('_id', 1).limit(batch_size).to_list(batch_size)
    cursor = str(docs[-1]["_id"]) if len(docs) == batch_size else None
    count = 0
    for entry in docs:
        cid = entry.get('conversation_id')
        if not isinstance(cid, str) or not 1 <= len(cid) <= 128:
            continue
        key = {'owner_user_id': user_id, 'conversation_id': cid}
        doc = await db.conversations.find_one({'_id': entry['_id'],
            'metadata.user_name': user['email'],
            '$expr': {'$lte': [{'$bsonSize': '$$ROOT'}, 2 * 1024 * 1024]}}, _PROJECTION)
        if doc is None:
            await db.recall_index.delete_one(key)
            continue
        eligible = (doc.get('source') in ('dashboard', 'task') and not doc.get('deleted')
            and not doc.get('recall_excluded') and not doc.get('metadata', {}).get('recall_excluded')
            and not (doc.get('task_status') == 'todo' and doc.get('status') is None)
            and isinstance(doc.get('messages', []), list) and len(BSON.encode(doc)) <= 2 * 1024 * 1024)
        if not eligible:
            await db.recall_index.delete_one(key)
            continue
        messages, excluded = visible_messages(doc)
        title, _ = sanitize(str(doc.get('title') or ''))
        await db.recall_index.replace_one(key, {**key,
            'project_id': doc.get('project_id'), 'agent_id': doc.get('metadata', {}).get('agent_id'),
            'sanitized_text': title[:1000] + '\n' + '\n'.join(m['content'] for m in messages),
            'source_revision': revision(doc), 'sanitizer_version': SANITIZER_VERSION,
            'generation': generation, 'excluded_messages': excluded, 'refreshed_at': datetime.now(timezone.utc),
        }, upsert=True)
        count += 1
    if cursor is None:
        coverage = await db.recall_coverage.find_one({'_id': user_id})
        if coverage and coverage.get('started_at'):
            await db.recall_index.delete_many({'owner_user_id': user_id,
                'generation': {'$ne': generation}})
        await db.recall_coverage.update_one({'_id': user_id}, {'$set': {
            'status': 'stored_messages_only', 'indexed_through': datetime.now(timezone.utc)}}, upsert=True)
    return {'next_cursor': cursor, 'indexed': count, 'status': 'partial' if cursor else 'stored_messages_only'}
