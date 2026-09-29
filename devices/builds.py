"""Short-lived build blobs handed to runners, and GitHub Actions artifact resolution.

The worker/agent never handles app binaries or GitHub credentials: it names a
build (repo + artifact name + PR or run id). The backend downloads the artifact
with its own token, stores it as a checksummed blob, and the runner fetches the
blob over HTTPS with its runner secret. Blobs are bound to one runner and expire.
"""
import hashlib
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp

from devices.hub import DeviceError

MAX_BLOB = 500 * 1024 * 1024
BLOB_TTL = 30 * 60
MAX_DOWNLOADS = 5  # per bind (one install), not per blob lifetime
MAX_TOTAL_BYTES = 3 * 1024 * 1024 * 1024
MAX_OWNER_BYTES = 1536 * 1024 * 1024
FILENAME = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
GITHUB_API = 'https://api.github.com'


def allowed_repos():
    raw = os.environ.get('LOMA_DEVICE_BUILD_REPOS', '')
    return {repo.strip().lower() for repo in raw.split(',') if repo.strip()}


class BlobStore:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('LOMA_DEVICE_BLOB_DIR') or Path(tempfile.gettempdir()) / 'loma-device-blobs')
        self.blobs = {}
        self.cache = {}
        self.cleaned = False

    def _prune(self):
        cutoff = time.monotonic()
        for blob_id, blob in list(self.blobs.items()):
            if blob['expires'] < cutoff:
                self.blobs.pop(blob_id, None)
                try:
                    Path(blob['path']).unlink()
                except OSError:
                    pass
        self.cache = {key: value for key, value in self.cache.items() if value in self.blobs}

    def new_path(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.cleaned:
            # The registry is in memory: files left by a previous process are orphans.
            self.cleaned = True
            for leftover in self.root.iterdir():
                if leftover.is_file() and leftover.name not in {Path(b['path']).name for b in self.blobs.values()}:
                    leftover.unlink(missing_ok=True)
        self._prune()
        return self.root / secrets.token_hex(16)

    def reserve(self, owner, size):
        """Reject new builds that would exceed the per-owner or global disk budget."""
        self._prune()
        total = sum(b['size'] for b in self.blobs.values())
        mine = sum(b['size'] for b in self.blobs.values() if b['owner'] == owner)
        if total + size > MAX_TOTAL_BYTES or mine + size > MAX_OWNER_BYTES:
            raise DeviceError('Build storage is full; wait for recent builds to expire (30 min) and retry')

    def _register(self, path, sha256, size, filename, owner, cache_key=None, meta=None):
        self._prune()
        blob_id = 'b_' + secrets.token_urlsafe(18)
        self.blobs[blob_id] = {'path': str(path), 'sha256': sha256, 'size': size, 'filename': filename,
                               'owner': owner, 'runners': {}, 'created': time.monotonic(),
                               'expires': time.monotonic() + BLOB_TTL,
                               'meta': meta or {}}
        if cache_key is not None:
            self.cache[cache_key] = blob_id
        return blob_id

    def add_file(self, path, filename, owner, sha256, size, meta=None):
        """Register an already-written file whose checksum was computed while streaming it."""
        if not size:
            raise DeviceError('Build is empty')
        return self._register(path, sha256, size, filename, owner, meta=meta)

    def get(self, blob_id, owner):
        self._prune()
        blob = self.blobs.get(blob_id)
        if blob is None or blob['owner'] != owner:
            return None
        return blob

    def bind(self, blob_id, runner_id):
        """Allow this runner a few downloads (one install) of the blob."""
        self._prune()
        blob = self.blobs.get(blob_id)
        if blob is None:
            raise DeviceError('Build expired; request it again')
        blob['runners'][runner_id] = MAX_DOWNLOADS
        # Keep it long enough for this install to fetch it, but never past 2 hours old.
        now = time.monotonic()
        blob['expires'] = min(max(blob['expires'], now + 15 * 60), blob['created'] + 4 * BLOB_TTL)

    def open_for_runner(self, blob_id, runner_id):
        self._prune()
        blob = self.blobs.get(blob_id)
        if blob is None or blob['runners'].get(runner_id, 0) <= 0:
            return None
        blob['runners'][runner_id] -= 1
        return blob

    async def from_github(self, owner, repo, artifact_name, pr=None, run_id=None):
        """repo / artifact_name / pr / run_id are validated by DeviceService."""
        repos = allowed_repos()
        if repo.lower() not in repos:
            raise DeviceError('Builds from this repository are not allowed. An operator can add it to '
                              'LOMA_DEVICE_BUILD_REPOS' + (f' (allowed: {", ".join(sorted(repos))})' if repos else ''))
        token = os.environ.get('GITHUB_API_KEY') or os.environ.get('GITHUB_TOKEN')
        if not token:
            raise DeviceError('No GitHub token is configured on the Loma backend')
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28'}
        try:
            return await self._from_github(owner, repo, artifact_name, pr, run_id, headers)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DeviceError(f'Could not fetch the build from GitHub ({type(exc).__name__}); retry') from None

    async def _from_github(self, owner, repo, artifact_name, pr, run_id, headers):
        timeout = aiohttp.ClientTimeout(total=600, sock_read=120)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            artifact, head_sha = await self._find_artifact(session, repo, artifact_name, pr, run_id)
            cache_key = (owner, repo.lower(), artifact['id'])
            cached = self.cache.get(cache_key)
            if cached in self.blobs:
                return cached, self.blobs[cached]
            self.reserve(owner, int(artifact.get('size_in_bytes') or 0))
            path = self.new_path()
            digest, size = hashlib.sha256(), 0
            try:
                async with session.get(artifact['archive_download_url']) as response:
                    if response.status != 200:
                        raise DeviceError(f'GitHub artifact download failed (HTTP {response.status})')
                    with open(path, 'wb') as handle:
                        async for chunk in response.content.iter_chunked(1 << 20):
                            size += len(chunk)
                            if size > MAX_BLOB:
                                raise DeviceError('Artifact is larger than 500 MB')
                            digest.update(chunk)
                            handle.write(chunk)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        filename = re.sub(r'[^A-Za-z0-9._-]', '_', artifact_name)[:120] + '.zip'
        meta = {'repo': repo, 'artifact_id': artifact['id'], 'artifact_name': artifact_name,
                'run_id': (artifact.get('workflow_run') or {}).get('id'), 'head_sha': head_sha,
                'created_at': artifact.get('created_at')}
        blob_id = self._register(path, digest.hexdigest(), size, filename, owner, cache_key, meta)
        return blob_id, self.blobs[blob_id]

    async def _get(self, session, path):
        async with session.get(GITHUB_API + path) as response:
            if response.status == 404:
                raise DeviceError('Not found on GitHub (check repo, PR/run id and token access)')
            if response.status != 200:
                raise DeviceError(f'GitHub API error (HTTP {response.status})')
            return await response.json()

    async def _find_artifact(self, session, repo, name, pr, run_id):
        head_sha = None
        if run_id is not None:
            data = await self._get(session, f'/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')
            candidates = [a for a in data.get('artifacts', []) if a.get('name') == name]
        else:
            if pr is not None:
                head_sha = (await self._get(session, f'/repos/{repo}/pulls/{pr}'))['head']['sha']
            data = await self._get(session, f'/repos/{repo}/actions/artifacts?per_page=100&name={quote(name)}')
            candidates = [a for a in data.get('artifacts', []) if a.get('name') == name
                          and (head_sha is None or (a.get('workflow_run') or {}).get('head_sha') == head_sha)]
        candidates = [a for a in candidates if not a.get('expired')]
        if not candidates:
            where = f'PR #{pr} (head {head_sha[:7]})' if head_sha else (f'run {run_id}' if run_id else repo)
            raise DeviceError(f'No unexpired artifact named "{name}" for {where}. Has the build workflow finished?')
        artifact = max(candidates, key=lambda a: a.get('created_at') or '')
        return artifact, head_sha or (artifact.get('workflow_run') or {}).get('head_sha')


blobs = BlobStore()
