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

import aiohttp

from devices.hub import DeviceError

MAX_BLOB = 500 * 1024 * 1024
BLOB_TTL = 30 * 60
MAX_DOWNLOADS = 5
REPO = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z')
ARTIFACT_NAME = re.compile(r'[A-Za-z0-9._ -]{1,200}\Z')
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
        return self.root / secrets.token_hex(16)

    def _register(self, path, sha256, size, filename, owner, cache_key=None, meta=None):
        self._prune()
        blob_id = 'b_' + secrets.token_urlsafe(18)
        self.blobs[blob_id] = {'path': str(path), 'sha256': sha256, 'size': size, 'filename': filename,
                               'owner': owner, 'runner_id': None, 'downloads': 0,
                               'expires': time.monotonic() + BLOB_TTL, 'meta': meta or {}}
        if cache_key is not None:
            self.cache[cache_key] = blob_id
        return blob_id

    def add_file(self, path, filename, owner, meta=None):
        """Register an already-written file (e.g. a CLI upload). Computes the checksum."""
        if not FILENAME.fullmatch(filename or ''):
            raise DeviceError('Invalid build filename')
        digest, size = hashlib.sha256(), 0
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                size += len(chunk)
                digest.update(chunk)
        if size == 0 or size > MAX_BLOB:
            raise DeviceError('Build must be between 1 byte and 500 MB')
        return self._register(path, digest.hexdigest(), size, filename, owner, meta=meta)

    def get(self, blob_id, owner=None):
        self._prune()
        blob = self.blobs.get(blob_id)
        if blob is None or (owner is not None and blob['owner'] != owner):
            return None
        return blob

    def bind(self, blob_id, runner_id):
        """Allow exactly one runner to download the blob."""
        blob = self.blobs.get(blob_id)
        if blob is None:
            raise DeviceError('Build expired; request it again')
        blob['runner_id'] = runner_id
        blob['expires'] = max(blob['expires'], time.monotonic() + 15 * 60)
        return blob

    def open_for_runner(self, blob_id, runner_id):
        self._prune()
        blob = self.blobs.get(blob_id)
        if blob is None or blob['runner_id'] != runner_id or blob['downloads'] >= MAX_DOWNLOADS:
            return None
        blob['downloads'] += 1
        return blob

    async def from_github(self, owner, repo, artifact_name, pr=None, run_id=None):
        if not REPO.fullmatch(repo or '') or not ARTIFACT_NAME.fullmatch(artifact_name or ''):
            raise DeviceError('Invalid repo or artifact_name')
        repos = allowed_repos()
        if repo.lower() not in repos:
            raise DeviceError('Builds from this repository are not allowed. An operator can add it to '
                              'LOMA_DEVICE_BUILD_REPOS' + (f' (allowed: {", ".join(sorted(repos))})' if repos else ''))
        token = os.environ.get('GITHUB_API_KEY') or os.environ.get('GITHUB_TOKEN')
        if not token:
            raise DeviceError('No GitHub token is configured on the Loma backend')
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28'}
        timeout = aiohttp.ClientTimeout(total=600, sock_read=120)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            artifact, head_sha = await self._find_artifact(session, repo, artifact_name, pr, run_id)
            cache_key = (owner, repo.lower(), artifact['id'])
            cached = self.cache.get(cache_key)
            if cached in self.blobs:
                return cached, self.blobs[cached]
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
            data = await self._get(session, f'/repos/{repo}/actions/runs/{int(run_id)}/artifacts?per_page=100')
            candidates = [a for a in data.get('artifacts', []) if a.get('name') == name]
        else:
            if pr is not None:
                head_sha = (await self._get(session, f'/repos/{repo}/pulls/{int(pr)}'))['head']['sha']
            from urllib.parse import quote
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
