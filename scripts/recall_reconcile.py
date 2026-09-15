"""Opt-in automatic per-owner reconciliation, outside all agent runtimes.

Use --confirm-db and --user-id. A durable owner lock prevents concurrent writers.
After an unclean process death, an operator must verify the old worker has stopped
before deleting that owner's recall_index_locks record. Never expire a live writer's
lock: stale writers can otherwise race generation cleanup. No production auto-start.
"""
import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from api.recall_controls import ensure_control_indexes
from api.recall_index import ensure_indexes, refresh_owner


async def reconcile_owner(db, user_id):
    if not ObjectId.is_valid(user_id):
        raise ValueError('invalid_argument')
    run_id = uuid4().hex
    try:
        await db.recall_index_locks.insert_one({
            '_id': user_id, 'run_id': run_id, 'started_at': datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        return {'status': 'worker_locked'}
    try:
        cursor, indexed = None, 0
        while True:
            result = await refresh_owner(db, user_id, cursor)
            indexed += result.get('indexed', 0)
            cursor = result['next_cursor']
            if cursor is None:
                return {'status': result['status'], 'indexed': indexed}
            await asyncio.sleep(0)
    finally:
        await db.recall_index_locks.delete_one({'_id': user_id, 'run_id': run_id})


async def watch_owner(db, user_id, interval):
    """Failure is visible and aborts this worker; the supervisor may restart it."""
    if not 30 <= interval <= 86400:
        raise ValueError('invalid_interval')
    while True:
        result = await reconcile_owner(db, user_id)
        # No emails, conversation IDs, query strings, source text or credentials.
        print(json.dumps(result), flush=True)
        await asyncio.sleep(interval)


async def main(args):
    load_dotenv(override=True)
    name = os.environ.get('OBSERVABILITY_DB_NAME')
    if not name or args.confirm_db != name:
        raise SystemExit('Database confirmation does not match configured database')
    if os.environ.get('LOMA_RECALL_INDEXER_ENABLED', '').lower() != 'true':
        raise SystemExit('Automatic recall indexing is disabled')
    client = AsyncIOMotorClient(os.environ['OBSERVABILITY_MONGODB_URI'])
    try:
        db = client[name]
        await ensure_indexes(db)
        await ensure_control_indexes(db)
        if args.once:
            print(json.dumps(await reconcile_owner(db, args.user_id)))
        else:
            await watch_owner(db, args.user_id, args.interval)
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--confirm-db', required=True)
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--once', action='store_true')
    asyncio.run(main(parser.parse_args()))
