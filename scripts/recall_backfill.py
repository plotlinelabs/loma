"""Offline single-worker reconciliation. Never run inside an agent tool adapter.

Example: python -m scripts.recall_backfill --user-id ID --confirm-db loma_local_ID
Runs one bounded batch; pass the returned --after cursor to resume. Restart a
full pass without --after to reconcile edits, deletes and ownership changes.
"""
import argparse
import asyncio
import json
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from api.recall_index import ensure_indexes, refresh_owner


async def main(args):
    load_dotenv(override=True)
    name = os.environ.get('OBSERVABILITY_DB_NAME')
    if not name or args.confirm_db != name:
        raise SystemExit('Database confirmation does not match configured database')
    client = AsyncIOMotorClient(os.environ['OBSERVABILITY_MONGODB_URI'])
    try:
        db = client[name]
        await ensure_indexes(db)
        print(json.dumps(await refresh_owner(db, args.user_id, args.after)))
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--confirm-db', required=True)
    parser.add_argument('--after')
    asyncio.run(main(parser.parse_args()))
