"""Backend artifact broker. Workers exchange bounded bytes, never backend paths.

A scope is constructed from authenticated conversation state. Opaque IDs are
not access grants: reads must also be in this run's server-selected input set.
Published files survive a disposable worker and can be attached to later runs.
The private store is backend-owned and must never be mounted into a worker.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

CHUNK = 256 * 1024
MAX_FILE = 20 * 1024 * 1024
MAX_RUN = 64 * 1024 * 1024
MAX_FILES = 20
ID = re.compile(r'[a-f0-9]{32}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')


def name(value):
    if (not isinstance(value, str) or not value or len(value.encode()) > 200
            or value in ('.', '..') or any(c in value for c in '/\\')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('A plain file name is required')
    return value


def integer(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Invalid file offset or size')
    return value


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Unknown artifact')
    return value


def decode_chunk(value):
    if not isinstance(value, str) or len(value) > 4 * ((CHUNK + 2) // 3):
        raise ValueError('File chunk exceeds the limit')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid file bytes') from exc
    if len(raw) > CHUNK:
        raise ValueError('File chunk exceeds the limit')
    return raw


class ArtifactScope:
    def __init__(self, root, authority, conversation_id, *, inputs=()):
        if not authority.user_email or not authority.run_id or not conversation_id:
            raise ValueError('Authenticated conversation and run are required')
        # Neither directory components nor input IDs originate in worker frames.
        input_ids = frozenset(identifier(i) for i in inputs)
        key = hashlib.sha256(json.dumps([authority.user_email, conversation_id]).encode()).hexdigest()
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                os.mkdir(key, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            self.fd = os.open(key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        finally:
            os.close(root_fd)
        self.authority = authority
        self.inputs = input_ids
        self.pending = {}
        self.published = {}
        self.bytes_reserved = 0
        self.created = 0
        self.closed = False

    def _open(self, filename, flags):
        if self.closed:
            raise ValueError('Artifact scope is closed')
        return os.open(filename, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd)

    def metadata(self, artifact_id):
        artifact_id = identifier(artifact_id)
        if artifact_id not in self.inputs and artifact_id not in self.published:
            raise ValueError('Unknown artifact')
        try:
            fd = self._open(artifact_id + '.json', os.O_RDONLY)
            with os.fdopen(fd, 'rb') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError('Unknown artifact')
                meta = json.loads(handle.read(4097))
            if (set(meta) != {'artifact_id', 'name', 'size', 'sha256'}
                    or meta['artifact_id'] != artifact_id):
                raise ValueError('Unknown artifact')
            name(meta['name'])
            integer(meta['size'], MAX_FILE)
            if not isinstance(meta['sha256'], str) or not SHA.fullmatch(meta['sha256']):
                raise ValueError('Unknown artifact')
            return meta
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError('Unknown artifact') from exc

    def manifest(self):
        return [self.metadata(i) for i in sorted(self.inputs)]

    def begin(self, filename, size):
        filename, size = name(filename), integer(size, MAX_FILE)
        if self.closed or self.created >= MAX_FILES or self.bytes_reserved + size > MAX_RUN:
            raise ValueError('Run artifact quota exceeded')
        artifact_id = uuid.uuid4().hex
        fd = self._open(artifact_id + '.part', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        self.pending[artifact_id] = {'fd': fd, 'size': size, 'offset': 0,
                                    'name': filename, 'hash': hashlib.sha256()}
        self.created += 1
        self.bytes_reserved += size
        return {'artifact_id': artifact_id, 'offset': 0, 'chunk_size': CHUNK}

    def write(self, artifact_id, offset, data):
        identifier(artifact_id)
        offset, data = integer(offset, MAX_FILE), decode_chunk(data)
        item = self.pending.get(artifact_id)
        if not item or offset != item['offset'] or offset + len(data) > item['size']:
            raise ValueError('Unexpected upload offset')
        if not data:
            raise ValueError('Empty upload chunk')
        view = memoryview(data)
        while view:
            count = os.write(item['fd'], view)
            if count <= 0:
                raise OSError('Artifact write failed')
            view = view[count:]
        item['hash'].update(data)
        item['offset'] += len(data)
        return {'offset': item['offset']}

    def commit(self, artifact_id, sha256):
        identifier(artifact_id)
        item = self.pending.get(artifact_id)
        if (not item or item['offset'] != item['size']
                or os.fstat(item['fd']).st_size != item['size'] or not isinstance(sha256, str)
                or not SHA.fullmatch(sha256) or item['hash'].hexdigest() != sha256):
            raise ValueError('Artifact is incomplete or checksum does not match')
        meta = {'artifact_id': artifact_id, 'name': item['name'], 'size': item['size'], 'sha256': sha256}
        os.fsync(item['fd'])
        # Publish metadata last: orphaned bytes after a crash are not readable.
        # No await occurs between authorization at dispatch and this commit.
        os.rename(artifact_id + '.part', artifact_id + '.blob', src_dir_fd=self.fd, dst_dir_fd=self.fd)
        meta_fd = self._open(artifact_id + '.json.part', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(meta_fd, 'wb') as handle:
            handle.write(json.dumps(meta).encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(artifact_id + '.json.part', artifact_id + '.json', src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)
        os.close(item['fd'])
        del self.pending[artifact_id]
        self.published[artifact_id] = meta
        return meta

    def read(self, artifact_id, offset):
        meta = self.metadata(artifact_id)
        offset = integer(offset, meta['size'])
        fd = self._open(artifact_id + '.blob', os.O_RDONLY)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != meta['size']:
                raise ValueError('Artifact is unavailable')
            handle.seek(offset)
            data = handle.read(CHUNK)
        return {'data': base64.b64encode(data).decode(), 'offset': offset,
                'next_offset': offset + len(data), 'eof': offset + len(data) == meta['size']}

    def ingest(self, filename, data):
        """Trusted upload ingestion, bytes only. Never follows an upload's path."""
        if not isinstance(data, bytes):
            raise ValueError('Attachment bytes required')
        artifact_id = self.begin(filename, len(data))['artifact_id']
        for offset in range(0, len(data), CHUNK):
            self.write(artifact_id, offset, base64.b64encode(data[offset:offset + CHUNK]).decode())
        return self.commit(artifact_id, hashlib.sha256(data).hexdigest())

    def close(self):
        if self.closed:
            return
        for artifact_id, item in self.pending.items():
            os.close(item['fd'])
            for suffix in ('.part', '.json.part', '.blob'):
                try:
                    os.unlink(artifact_id + suffix, dir_fd=self.fd)
                except FileNotFoundError:
                    pass
        self.pending.clear()
        os.close(self.fd)
        self.closed = True
