"""Short-lived build blobs handed to runners, and GitHub Actions artifact resolution.

The worker/agent never handles app binaries or GitHub credentials: it names a
build (repo + artifact name + PR or run id). The backend downloads the artifact
with its own token, stores it as a checksummed blob, and the runner fetches the
blob over HTTPS with its runner secret. Blobs are bound to one runner and expire.
"""
import asyncio
import contextlib
import hashlib
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import aiohttp

from devices.hub import DeviceError
from tools._integration_key import get_integration_key

MAX_BLOB = 500 * 1024 * 1024
BLOB_TTL = 30 * 60
MAX_DOWNLOADS = 5  # per bind (one install), not per blob lifetime
MAX_TOTAL_BYTES = 3 * 1024 * 1024 * 1024
MAX_OWNER_BYTES = 1536 * 1024 * 1024
GITHUB_FETCH_TIMEOUT = 600
FILENAME = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
GITHUB_API = 'https://api.github.com'
POLL_SECONDS = 20
NO_RUN_GRACE = 90  # a push / label event can take a little while to create its workflow run
ACTIVE_RUN = {'queued', 'in_progress', 'waiting', 'requested', 'pending'}
API_TIMEOUT = aiohttp.ClientTimeout(total=30)


class ArtifactMissing(DeviceError):
    """No artifact yet; carries what the wait loop needs to decide whether to keep polling."""

    def __init__(self, message, head_sha=None, head_ref=None, same_repo=True):
        super().__init__(message)
        self.head_sha, self.head_ref, self.same_repo = head_sha, head_ref, same_repo


SETTINGS_ID = 'builds'  # db.device_settings document, edited by admins on the dashboard Devices tab
SETTING_TTL = 60
_settings = {}  # 'builds' -> (read at, {'repos': [...], 'workflows': [...]})
REPO_NAME = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z')
WORKFLOW_NAME = re.compile(r'[A-Za-z0-9_.-]{1,100}\.ya?ml\Z')


def split_list(raw):
    return [item.strip() for item in re.split(r'[,\s]+', raw or '') if item.strip()]


def env_repos():
    return {repo.lower() for repo in split_list(os.environ.get('LOMA_DEVICE_BUILD_REPOS', ''))}


def env_workflows():
    return set(split_list(os.environ.get('LOMA_DEVICE_BUILD_WORKFLOWS', '')))


def read_saved_settings():
    """The admin-saved build sources (db.device_settings). Blocking; {} when Mongo is not configured."""
    uri = os.environ.get('OBSERVABILITY_MONGODB_URI', '').strip()
    if not uri:
        return {}
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        return client.loma_observability.device_settings.find_one({'_id': SETTINGS_ID}) or {}
    finally:
        client.close()


def _saved():
    """Saved build sources, re-read at most every SETTING_TTL s. Fails closed to env-only. Blocking."""
    now = time.monotonic()
    at, value = _settings.get(SETTINGS_ID, (None, {}))
    if at is None or now - at > SETTING_TTL:
        try:
            value = read_saved_settings()
        except Exception:
            value = {}
        _settings[SETTINGS_ID] = (now, value)
    return value


def invalidate_settings():
    _settings.clear()


def allowed_repos():
    """Repos CI builds may be installed from: LOMA_DEVICE_BUILD_REPOS plus the repos an admin saved on
    the Devices tab (no redeploy needed). Blocking (cached Mongo read): call it off the event loop."""
    return env_repos() | {str(r).lower() for r in _saved().get('repos') or []}


def allowed_workflows():
    """Workflow files dispatch_workflow may start (e.g. build-android.yml): LOMA_DEVICE_BUILD_WORKFLOWS
    plus the ones an admin saved on the Devices tab. Empty means no dispatching. Blocking."""
    return env_workflows() | {str(w) for w in _saved().get('workflows') or []}


class BlobStore:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('LOMA_DEVICE_BLOB_DIR') or Path(tempfile.gettempdir()) / 'loma-device-blobs')
        self.blobs = {}
        self.cache = {}
        self.inflight = {}  # builds still streaming in: counted against the disk budget too
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
        held = [(b['owner'], b['size']) for b in self.blobs.values()] + list(self.inflight.values())
        total = sum(held_size for _, held_size in held)
        mine = sum(held_size for held_owner, held_size in held if held_owner == owner)
        if total + size > MAX_TOTAL_BYTES or mine + size > MAX_OWNER_BYTES:
            raise DeviceError('Build storage is full; wait for recent builds to expire (30 min) and retry')

    @contextlib.contextmanager
    def reservation(self, owner, size):
        """Hold `size` bytes of budget while a build streams in; the caller must not write more."""
        self.reserve(owner, size)
        key = object()
        self.inflight[key] = (owner, size)
        try:
            yield
        finally:
            del self.inflight[key]

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

    async def from_github(self, owner, repo, artifact_name, pr=None, run_id=None, wait_s=0, dispatch_workflow=None):
        """repo / artifact_name / pr / run_id / wait_s / dispatch_workflow are validated by DeviceService.

        wait_s > 0 polls the workflow run here in the backend (no model turns) until the artifact
        exists; dispatch_workflow starts that workflow on the PR branch if no run exists for its head.
        """
        repos = await asyncio.to_thread(allowed_repos)
        if repo.lower() not in repos:
            raise DeviceError('Builds from this repository are not allowed. An admin can add it under '
                              'Integrations > Devices > Build sources (or LOMA_DEVICE_BUILD_REPOS)' + (f' (allowed: {", ".join(sorted(repos))})' if repos else ''))
        if dispatch_workflow is not None:
            workflows = await asyncio.to_thread(allowed_workflows)
            if dispatch_workflow not in workflows:
                raise DeviceError(f'Dispatching {dispatch_workflow} is not allowed. An admin can allow it under '
                                  'Integrations > Devices > Build sources (or LOMA_DEVICE_BUILD_WORKFLOWS, '
                                  'comma-separated file names)' + (f' (allowed: {", ".join(sorted(workflows))})'
                                                                   if workflows else '')
                                  + '. Or omit dispatch_workflow and start the build another way.')
        # Same lookup as isolation/automation.request: env var, else the dashboard-managed integration.
        token = os.environ.get('GITHUB_API_KEY') or await asyncio.to_thread(get_integration_key, 'github')
        if not token:
            raise DeviceError('No GitHub token is configured on the Loma backend')
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28'}
        try:
            return await self._from_github(owner, repo, artifact_name, pr, run_id, headers, wait_s, dispatch_workflow)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DeviceError(f'Could not fetch the build from GitHub ({type(exc).__name__}); retry') from None

    async def _from_github(self, owner, repo, artifact_name, pr, run_id, headers, wait_s=0, dispatch_workflow=None):
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            artifact, head_sha = await self._wait_for_artifact(session, repo, artifact_name, pr, run_id,
                                                               wait_s, dispatch_workflow)
            cache_key = (owner, repo.lower(), artifact['id'])
            cached = self.cache.get(cache_key)
            if cached in self.blobs:
                return cached, self.blobs[cached]
            declared = min(int(artifact.get('size_in_bytes') or MAX_BLOB), MAX_BLOB)
            path = self.new_path()
            try:
                with self.reservation(owner, declared):
                    digest, size = await asyncio.wait_for(
                        self._download(session, artifact['archive_download_url'], path, declared), GITHUB_FETCH_TIMEOUT)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        filename = re.sub(r'[^A-Za-z0-9._-]', '_', artifact_name)[:120] + '.zip'
        meta = {'repo': repo, 'artifact_id': artifact['id'], 'artifact_name': artifact_name,
                'run_id': (artifact.get('workflow_run') or {}).get('id'), 'head_sha': head_sha,
                'created_at': artifact.get('created_at')}
        blob_id = self._register(path, digest.hexdigest(), size, filename, owner, cache_key, meta)
        return blob_id, self.blobs[blob_id]

    async def _download(self, session, url, path, declared):
        """GitHub answers the archive URL with a redirect to short-lived, pre-signed blob storage.
        Follow it by hand: the GitHub token must never be sent to another host (the signed URL
        carries its own auth, and blob storage rejects requests with an extra Authorization)."""
        async with session.get(url, allow_redirects=False) as response:
            if response.status == 200:
                return await self._stream(response, path, declared)
            location = response.headers.get('Location')
            if response.status not in (301, 302, 303, 307, 308) or not location:
                raise DeviceError(f'GitHub artifact download failed (HTTP {response.status})')
        target = urljoin(url, location)
        if urlparse(url).scheme == 'https' and urlparse(target).scheme != 'https':
            raise DeviceError('GitHub redirected the artifact download to a non-HTTPS URL')
        if urlparse(target).netloc == urlparse(url).netloc:
            raise DeviceError('Unexpected same-host redirect for the artifact download')
        storage_timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
        async with aiohttp.ClientSession(timeout=storage_timeout) as storage:  # no GitHub headers here
            async with storage.get(target) as response:
                if response.status != 200:
                    raise DeviceError(f'Artifact storage download failed (HTTP {response.status})')
                return await self._stream(response, path, declared)

    @staticmethod
    async def _stream(response, path, declared):
        digest, size = hashlib.sha256(), 0
        with open(path, 'wb') as handle:
            async for chunk in response.content.iter_chunked(1 << 20):
                size += len(chunk)
                if size > declared:
                    raise DeviceError('Artifact is larger than GitHub reported (or than 500 MB)')
                digest.update(chunk)
                handle.write(chunk)
        return digest, size

    async def _get(self, session, path):
        async with session.get(GITHUB_API + path, timeout=API_TIMEOUT) as response:
            if response.status == 404:
                raise DeviceError('Not found on GitHub (check repo, PR/run id and token access)')
            if response.status != 200:
                raise DeviceError(f'GitHub API error (HTTP {response.status})')
            return await response.json()

    async def _wait_for_artifact(self, session, repo, name, pr, run_id, wait_s=0, workflow=None):
        """Poll (server-side, no model turns) until the artifact exists, the run fails, or wait_s runs out."""
        deadline = time.monotonic() + wait_s
        first_poll, dispatched = time.monotonic(), False
        while True:
            try:
                return await self._find_artifact(session, repo, name, pr, run_id)
            except ArtifactMissing as missing:
                if time.monotonic() >= deadline:
                    raise DeviceError(str(missing) + (f' Gave up after waiting {wait_s}s.' if wait_s else
                                                      ' Pass wait_s to wait for the workflow.')) from None
                state = await self._run_state(session, repo, missing.head_sha, run_id, workflow)
                if state.startswith('failed:'):
                    raise DeviceError(f'The build workflow finished without the artifact: {state[7:]}') from None
                if state == 'none':
                    if workflow and not dispatched:
                        await self._dispatch(session, repo, workflow, missing)
                        dispatched = True
                    elif not workflow and time.monotonic() - first_poll > NO_RUN_GRACE:
                        raise DeviceError(
                            f'No workflow run is building {missing.head_sha and missing.head_sha[:7]}. '
                            'Start it (e.g. add the label the workflow needs), or pass dispatch_workflow.') from None
            await asyncio.sleep(min(POLL_SECONDS, max(1, deadline - time.monotonic())))

    async def _run_state(self, session, repo, head_sha, run_id, workflow):
        """'running', 'none', or 'failed:<why>' for the run(s) that should produce the artifact."""
        if run_id is not None:
            runs = [await self._get(session, f'/repos/{repo}/actions/runs/{run_id}')]
        elif head_sha:
            data = await self._get(session, f'/repos/{repo}/actions/runs?head_sha={head_sha}&per_page=50')
            runs = data.get('workflow_runs') or []
        else:
            return 'none'
        if workflow:
            runs = [r for r in runs if str(r.get('path') or '').split('@')[0].endswith('/' + workflow)]
        if any(r.get('status') in ACTIVE_RUN for r in runs):
            return 'running'
        if runs and (workflow or run_id is not None):
            latest = max(runs, key=lambda r: r.get('created_at') or '')
            return f"failed:run {latest.get('id')} concluded {latest.get('conclusion')} ({latest.get('html_url')})"
        return 'none'

    async def _dispatch(self, session, repo, workflow, missing):
        if not missing.head_ref or not missing.same_repo:
            raise DeviceError('Cannot dispatch the workflow: the PR branch is not in this repository')
        url = f'{GITHUB_API}/repos/{repo}/actions/workflows/{quote(workflow)}/dispatches'
        async with session.post(url, json={'ref': missing.head_ref}, timeout=API_TIMEOUT) as response:
            if response.status != 204:
                detail = (await response.text())[:200]
                raise DeviceError(f'Could not dispatch {workflow} (HTTP {response.status}): {detail}')

    async def _find_artifact(self, session, repo, name, pr, run_id):
        head_sha, head_ref, same_repo = None, None, True
        if run_id is not None:
            data = await self._get(session, f'/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')
            candidates = [a for a in data.get('artifacts', []) if a.get('name') == name]
        else:
            if pr is not None:
                head = (await self._get(session, f'/repos/{repo}/pulls/{pr}'))['head']
                head_sha, head_ref = head['sha'], head.get('ref')
                same_repo = str(((head.get('repo') or {}).get('full_name')) or '').lower() == repo.lower()
            data = await self._get(session, f'/repos/{repo}/actions/artifacts?per_page=100&name={quote(name)}')
            candidates = [a for a in data.get('artifacts', []) if a.get('name') == name
                          and (head_sha is None or (a.get('workflow_run') or {}).get('head_sha') == head_sha)]
        candidates = [a for a in candidates if not a.get('expired')]
        if not candidates:
            where = f'PR #{pr} (head {head_sha[:7]})' if head_sha else (f'run {run_id}' if run_id else repo)
            raise ArtifactMissing(f'No unexpired artifact named "{name}" for {where}. Has the build workflow finished?',
                                  head_sha, head_ref, same_repo)
        artifact = max(candidates, key=lambda a: a.get('created_at') or '')
        return artifact, head_sha or (artifact.get('workflow_run') or {}).get('head_sha')


blobs = BlobStore()
