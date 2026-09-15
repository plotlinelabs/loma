"""Worker-only local files. Copy into worker images without any backend modules.

An RPC callback carries only framed bytes/opaque IDs to the tool gateway. Input
files are materialized into private run storage, and output bytes are committed
before the caller reports a download. Paths never cross the worker boundary.
"""
import base64
import hashlib
import os
from pathlib import Path
import stat

from isolation.artifacts import CHUNK, MAX_FILE, identifier, name, integer, decode_chunk


class Workspace:
    def __init__(self, root, rpc):
        root = Path(root)
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.rpc = rpc
        self.closed = False

    def _parent(self, relative_path):
        if self.closed or not isinstance(relative_path, str):
            raise ValueError('Workspace is unavailable')
        components = relative_path.split('/')
        for component in components:
            name(component)
        fd = os.dup(self.fd)
        try:
            for component in components[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd, components[-1]
        except BaseException:
            os.close(fd)
            raise

    async def stage(self, manifest):
        """Store each input under its opaque ID, so duplicate names never alias."""
        paths = []
        for entry in manifest:
            artifact_id, filename = identifier(entry['artifact_id']), name(entry['name'])
            size = integer(entry['size'], MAX_FILE)
            os.mkdir(artifact_id, mode=0o700, dir_fd=self.fd)
            parent, filename = self._parent(artifact_id + '/' + filename)
            fd = None
            try:
                fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
                with os.fdopen(fd, 'wb') as handle:
                    fd = None
                    offset, digest = 0, hashlib.sha256()
                    while offset < size:
                        result = await self.rpc('artifacts.read', {'artifact_id': artifact_id, 'offset': offset})
                        chunk = decode_chunk(result['data'])
                        if (not chunk or result.get('offset') != offset
                                or result.get('next_offset') != offset + len(chunk)
                                or offset + len(chunk) > size):
                            raise ValueError('Invalid artifact transfer')
                        handle.write(chunk)
                        digest.update(chunk)
                        offset += len(chunk)
                    if digest.hexdigest() != entry['sha256']:
                        raise ValueError('Input checksum mismatch')
                    handle.flush()
                    os.fsync(handle.fileno())
                paths.append(artifact_id + '/' + filename)
            except BaseException:
                if fd is not None:
                    os.close(fd)
                try:
                    os.unlink(filename, dir_fd=parent)
                except FileNotFoundError:
                    pass
                raise
            finally:
                os.close(parent)
        return paths

    async def publish(self, relative_path):
        parent, filename = self._parent(relative_path)
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                raise ValueError('Output must be a bounded regular file')
            begun = await self.rpc('artifacts.begin', {'name': filename, 'size': info.st_size})
            artifact_id = identifier(begun['artifact_id'])
            offset, digest = 0, hashlib.sha256()
            while chunk := handle.read(CHUNK):
                if offset + len(chunk) > info.st_size:
                    raise ValueError('Output changed during transfer')
                result = await self.rpc('artifacts.write', {'artifact_id': artifact_id, 'offset': offset,
                    'data': base64.b64encode(chunk).decode()})
                offset += len(chunk)
                if result.get('offset') != offset:
                    raise ValueError('Invalid upload acknowledgement')
                digest.update(chunk)
            if offset != info.st_size:
                raise ValueError('Output changed during transfer')
            return await self.rpc('artifacts.commit', {'artifact_id': artifact_id, 'sha256': digest.hexdigest()})

    def close(self):
        if not self.closed:
            os.close(self.fd)
            self.closed = True
