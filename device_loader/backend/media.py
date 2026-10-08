"""Short-lived media uploaded by runners out of band (runner >= 1.5.0).

A scenario video or screenshot used to travel base64-encoded inside the result frame on the
runner's control WebSocket. A 16 MB video is a ~21 MB frame: on a slow uplink it holds the socket
long enough for the server's ping to go unanswered, the connection is dropped, and the NEXT call
fails with "Runner reconnected". The runner now POSTs large media to /device-runner/media and puts
the returned media id in the result; the service swaps the id for the bytes when the result lands.

Media is kept in memory, bound to the runner that uploaded it, taken once, and expires quickly.
"""
import hashlib
import secrets
import time

MEDIA_TTL = 10 * 60
MAX_MEDIA = 24 * 1024 * 1024  # one item (the runner caps a video at 16 MB; screenshots are smaller)
MAX_RUNNER_BYTES = 96 * 1024 * 1024
MAX_TOTAL_BYTES = 384 * 1024 * 1024


class MediaError(Exception):
    pass


class MediaStore:
    def __init__(self):
        self.items = {}  # media_id -> (runner_id, bytes, expires_at)

    def _prune(self, now=None):
        now = now or time.monotonic()
        for media_id in [k for k, (_, _, expires) in self.items.items() if expires <= now]:
            del self.items[media_id]

    def _bytes(self, runner_id=None):
        return sum(len(data) for owner, data, _ in self.items.values() if runner_id is None or owner == runner_id)

    def put(self, runner_id, data, sha256=None):
        self._prune()
        if not data or len(data) > MAX_MEDIA:
            raise MediaError(f'media must be 1 byte to {MAX_MEDIA // (1024 * 1024)} MB')
        if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
            raise MediaError('media checksum mismatch')
        if self._bytes(runner_id) + len(data) > MAX_RUNNER_BYTES or self._bytes() + len(data) > MAX_TOTAL_BYTES:
            raise MediaError('too much media waiting to be collected; retry shortly')
        media_id = secrets.token_urlsafe(24)
        self.items[media_id] = (runner_id, data, time.monotonic() + MEDIA_TTL)
        return media_id

    def take(self, runner_id, media_id):
        """The bytes, once, and only for the runner that uploaded them; None when unknown or expired."""
        self._prune()
        item = self.items.get(media_id) if isinstance(media_id, str) else None
        if item is None or item[0] != runner_id:
            return None
        del self.items[media_id]
        return item[1]


media = MediaStore()


def resolve(data, runner_id, store=None):
    """Swap every *_media id in a runner result (top level, screenshots[], frames[]) for *_base64 bytes.

    The value under *_base64 is then raw bytes, not text: service._decode accepts both. A missing id
    (expired, wrong runner) becomes media_error instead of failing the whole result.
    """
    store = store or media
    if not isinstance(data, dict):
        return data

    def swap(holder):
        for key in [k for k in holder if k.endswith('_media')]:
            stem = key[:-len('_media')]
            raw = store.take(runner_id, holder.pop(key))
            if raw is None:
                data['media_error'] = f'{stem}: the uploaded media expired or was not found'
            else:
                holder[stem + '_base64'] = raw
    swap(data)
    for field in ('screenshots', 'frames'):
        for item in data.get(field) or []:
            if isinstance(item, dict):
                swap(item)
    return data
