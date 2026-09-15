"""Durable owner/conversation download registration for committed worker bytes."""
from datetime import datetime, timedelta, timezone
import mimetypes
import os
from pathlib import Path

from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from isolation.artifacts import ArtifactScope, identifier
from isolation.protocol import RunAuthority


def store_root():
    value = os.environ.get('LOMA_WORKER_ARTIFACT_DIR', '')
    root = Path(value)
    if not value or not root.is_absolute() or root == Path('/'):
        raise ValueError('Persistent worker artifact storage is not configured')
    return root


def download_info(meta):
    file_id = 'worker-' + identifier(meta['artifact_id'])
    return {'file_id': file_id, 'url': '/api/files/' + file_id, 'name': meta['name'],
            'mime_type': mimetypes.guess_type(meta['name'])[0] or 'application/octet-stream', 'size': meta['size']}


class DownloadRegistry:
    def __init__(self, db, scope, *, emit):
        self.db, self.scope, self.emit = db, scope, emit
        self.collection = db.isolated_artifact_downloads.with_options(write_concern=WriteConcern(w='majority'))

    async def __call__(self, receipt):
        meta = self.scope.metadata(receipt['artifact_id'])
        if receipt != meta or receipt['artifact_id'] not in self.scope.published:
            raise ValueError('Only committed output may be registered')
        # TTL is only housekeeping; open_download also checks the deadline
        # because Mongo's asynchronous TTL monitor is not an access boundary.
        await self.collection.create_index('expires_at', expireAfterSeconds=0)
        row = {'_id': meta['artifact_id'], 'owner': self.scope.authority.user_email,
               'conversation_id': self.scope.conversation_id, 'metadata': meta}
        try:
            await self.collection.insert_one({**row, 'expires_at': datetime.now(timezone.utc) + timedelta(days=30)})
        except DuplicateKeyError:
            if not await self.collection.find_one(row, {'_id': 1}):
                raise ValueError('Conflicting artifact registration') from None
        # Emission is idempotent by artifact_id. No path/text-based detection.
        info = download_info(meta)
        await self.emit({'type': 'file_artifact', 'artifact_id': info['file_id'],
            'title': meta['name'], 'language': Path(meta['name']).suffix.lstrip('.'),
            'file_type': Path(meta['name']).suffix.lstrip('.'), 'file_url': info['url'],
            'file_name': info['name'], 'file_size': info['size'], 'content': '', 'version': 1})
        return info


async def open_download(db, owner, file_id):
    """Return a pinned fd or fail closed; opaque IDs alone never grant access."""
    if not file_id.startswith('worker-'):
        raise ValueError('Unknown artifact')
    artifact_id = identifier(file_id[7:])
    row = await db.isolated_artifact_downloads.find_one({'_id': artifact_id, 'owner': owner,
        'expires_at': {'$gt': datetime.now(timezone.utc)}})
    if not row or not await db.users.find_one({'email': owner, 'status': {'$in': [None, 'active']}, 'deleted': {'$ne': True}}):
        raise ValueError('Unknown artifact')
    if not await db.conversations.find_one({'conversation_id': row['conversation_id'],
            'metadata.user_name': owner, 'deleted': {'$ne': True}}, {'_id': 1}):
        raise ValueError('Unknown artifact')
    authority = RunAuthority('download', owner, frozenset())
    scope = ArtifactScope(store_root(), authority, row['conversation_id'], inputs=[artifact_id])
    try:
        fd, meta = scope.open_committed(artifact_id)
        if meta != row['metadata']:
            os.close(fd)
            raise ValueError('Unknown artifact')
        return fd, {'mime_type': download_info(meta)['mime_type'], 'original_name': meta['name']}
    finally:
        scope.close()
