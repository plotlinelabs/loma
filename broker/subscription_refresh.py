"""Backend-only refresh for pooled CLI accounts. Never returns tokens to workers.

Protocol constants match the installed Claude Code and Codex CLI clients. A live
compatibility check is still required when upgrading those clients.
"""
import asyncio
import base64
import fcntl
import json
import math
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from broker.service import Denied

# Public OAuth client IDs, not secrets. Endpoints are never worker configurable.
PROVIDERS = {
    'claude': ('https://platform.claude.com/v1/oauth/token', '9d1c250a-e61b-44d9-88ed-5944d1962f5e'),
    'codex': ('https://auth.openai.com/oauth/token', 'app_EMoamEEZ73f0CkXaXp7hrann'),
}


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 131072:
            raise Denied()
        raw = source.read(131073)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise Denied()
    return raw, data


def _expiry(provider, data):
    if provider == 'claude':
        value = data.get('claudeAiOauth', {}).get('expiresAt')
        return value / 1000 if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    token = data.get('tokens', {}).get('access_token', '')
    try:
        payload = token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))).get('exp')
        # This is a scheduling hint, NOT identity verification. Only backend-owned
        # credentials are read and account admission is independently enforced.
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except (ValueError, IndexError, AttributeError):
        return None


def _token(value):
    if not isinstance(value, str) or not value or len(value) > 32768 or any(ord(c) < 32 for c in value):
        raise Denied()
    return value


async def _exchange(provider, refresh, scopes):
    endpoint, client_id = PROVIDERS[provider]
    payload = {'grant_type': 'refresh_token', 'refresh_token': refresh, 'client_id': client_id}
    if provider == 'claude' and scopes:
        payload['scope'] = ' '.join(scopes)
    async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.post(endpoint, json=payload, allow_redirects=False) as response:
            if response.status != 200:
                raise Denied()
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise Denied()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise Denied()
            return result


def _updated(provider, original, response):
    data = dict(original)
    access = _token(response.get('access_token'))
    if provider == 'claude':
        oauth = dict(data['claudeAiOauth'])
        expires = response.get('expires_in')
        if not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires) or not 0 < expires <= 31_536_000:
            raise Denied()
        oauth.update({'accessToken': access, 'expiresAt': int((time.time() + expires) * 1000)})
        if 'refresh_token' in response:
            oauth['refreshToken'] = _token(response['refresh_token'])
        data['claudeAiOauth'] = oauth
    else:
        tokens = dict(data['tokens'])
        tokens['access_token'] = access
        for name in ('refresh_token', 'id_token'):
            if name in response:
                tokens[name] = _token(response[name])
        # Keep the account bound at setup; a response may not change it.
        data['tokens'] = tokens
        data['last_refresh'] = datetime.now(timezone.utc).isoformat()
    return data


def _persist(path, original_raw, updated):
    # A login utility may have replaced the account while the request was in flight.
    if _read(path)[0] != original_raw:
        raise Denied()
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='.refresh-', dir=path.parent)
        with os.fdopen(fd, 'w') as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(updated, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, path)
        tmp = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if tmp is not None:
            os.unlink(tmp)


async def ensure_fresh(provider, credentials_path, *, rejected_access_token=None):
    """Refresh expiring credentials, or recover a provider-rejected token.

    Forced recovery is for an upstream HTTP 401 before streaming only. Comparing
    the rejected access token under the account lock coalesces concurrent callers.
    A persisted cooldown bounds refresh attempts even across backend processes.
    """
    fd = None
    try:
        if provider not in PROVIDERS:
            raise Denied()
        if rejected_access_token is not None:
            _token(rejected_access_token)
        path = Path(credentials_path)
        _, current = _read(path)
        expiry = _expiry(provider, current)
        if rejected_access_token is None and (expiry is None or expiry > time.time() + 60):
            return
        fd = os.open(str(path) + '.refresh.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Denied()
        async with asyncio.timeout(40):
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            raw, current = _read(path)
            expiry = _expiry(provider, current)
            if rejected_access_token is None and (expiry is None or expiry > time.time() + 60):
                return
            oauth = current.get('claudeAiOauth' if provider == 'claude' else 'tokens', {})
            if rejected_access_token is not None:
                access = oauth.get('accessToken' if provider == 'claude' else 'access_token')
                if _token(access) != rejected_access_token:
                    # Another admitted request already refreshed this account.
                    return True
                attempted = current.get('_loma_recovery_at', 0)
                if (not isinstance(attempted, (int, float)) or isinstance(attempted, bool)
                        or not math.isfinite(attempted) or time.time() - attempted < 60):
                    raise Denied()
            refresh_value = oauth.get('refreshToken' if provider == 'claude' else 'refresh_token')
            # Non-refreshable setup tokens remain usable until their actual expiry.
            if not refresh_value and rejected_access_token is None and expiry is not None and expiry > time.time():
                return
            refresh = _token(refresh_value)
            scopes = oauth.get('scopes', []) if provider == 'claude' else []
            if not isinstance(scopes, list) or not all(isinstance(s, str) and len(s) < 200 for s in scopes):
                raise Denied()
            if rejected_access_token is not None:
                attempted = dict(current, _loma_recovery_at=time.time())
                _persist(path, raw, attempted)
                raw, current = _read(path)
            response = await _exchange(provider, refresh, scopes)
            _persist(path, raw, _updated(provider, current, response))
            return True
    except asyncio.CancelledError:
        raise
    except Exception:
        raise Denied() from None
    finally:
        if fd is not None:
            os.close(fd)
