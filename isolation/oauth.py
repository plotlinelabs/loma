"""Backend-only subscription OAuth refresh; never imported by a worker.

All adapter processes must share the account directory on a filesystem with
flock/atomic-rename semantics. Legacy CLIs must not concurrently own these files.
An ambiguous exchange is fenced on disk until credentials change (reconnect),
not retried with a possibly consumed refresh token. No background tasks survive
cancellation. The selector rechecks the pinned run/account after this returns.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
import stat
import time
import uuid

import aiohttp

from isolation.accounts import _read
from isolation.models import ModelDenied

# Protocols from Codex rust-v0.153.3 and Claude Code's OAuth client.
PROVIDERS = {
    'codex': ('https://auth.openai.com/oauth/token', 'app_EMoamEEZ73f0CkXaXp7hrann'),
    'claude': ('https://platform.claude.com/v1/oauth/token', '9d1c250a-e61b-44d9-88ed-5944d1962f5e'),
}
LIMIT = 65536
SKEW = 300


def _string(value):
    if (not isinstance(value, str) or not value or len(value) > 16384
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError('Invalid credential field')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid credential expiry')
    return value


def _claims(token):
    # Used only for expiry and identity continuity, never as authentication.
    body = _string(token).split('.')[1]
    result = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    if not isinstance(result, dict):
        raise ValueError('Invalid token claims')
    return result


def _expiry(doc, runtime):
    if runtime == 'claude':
        return _number(doc['claudeAiOauth']['expiresAt']) / 1000
    tokens = doc['tokens']
    try:
        claims = _claims(tokens['access_token'])
    except (ValueError, IndexError):
        claims = {}
    if 'exp' in claims:
        return _number(claims['exp'])
    # Opaque access tokens use the persisted exchange expiry or Codex's
    # eight-day refresh cadence. Missing timestamps require an exchange.
    if 'expires_at' in tokens:
        return _number(tokens['expires_at'])
    refreshed = doc.get('last_refresh')
    if not refreshed:
        return 0
    stamp = datetime.fromisoformat(refreshed.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Invalid refresh timestamp')
    return stamp.timestamp() + 8 * 86400


def _digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def _atomic(directory_fd, name, doc):
    data = json.dumps(doc).encode()
    if len(data) > LIMIT:
        raise ValueError('Credential file too large')
    temporary = '.loma-oauth-' + uuid.uuid4().hex
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


@asynccontextmanager
async def _locked(directory):
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        fd = os.open('.loma-oauth.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Invalid lock file')
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(.05)
        yield directory_fd
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory_fd)


async def _exchange(runtime, body):
    # No environment URL overrides, ambient proxy credentials, redirects,
    # automatic retries or provider response bodies in exception messages.
    async with aiohttp.ClientSession(trust_env=False,
            timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(PROVIDERS[runtime][0], json=body, allow_redirects=False) as response:
            if response.status != 200:
                raise ModelDenied('Subscription refresh failed; reconnect account')
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > LIMIT:
                    raise ValueError('Refresh response too large')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('Invalid refresh response')
            return result


class OAuthRefresh:
    def __init__(self, *, check_access):
        self.check_access = check_access

    async def __call__(self, authority, account):
        try:
            async with asyncio.timeout(25), _locked(account.directory) as directory_fd:
                return await self._resolve(authority, account, directory_fd)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ModelDenied('Subscription refresh failed; reconnect account') from None

    async def _resolve(self, authority, account, directory_fd):
        async def allowed():
            if await self.check_access(authority, account) is not True:
                raise ModelDenied('Subscription account access changed')
        await allowed()
        identity = account.identity()
        runtime = account.runtime
        name = '.credentials.json' if runtime == 'claude' else 'auth.json'
        doc = _read(account.directory, name)
        original = _digest(doc)
        # Crash/cancel/timeout after dispatch may have rotated the remote token.
        # Fence even a still-unexpired access token from this uncertain bundle.
        try:
            pending = _read(account.directory, '.loma-oauth.pending')
        except FileNotFoundError:
            pending = None
        if pending is not None and pending.get('credential_digest') == original:
            raise ModelDenied('Uncertain refresh requires reconnect')
        tokens = doc['claudeAiOauth' if runtime == 'claude' else 'tokens']
        access_key = 'accessToken' if runtime == 'claude' else 'access_token'
        refresh_key = 'refreshToken' if runtime == 'claude' else 'refresh_token'
        _string(tokens[access_key])
        if _expiry(doc, runtime) <= time.time() + SKEW:
            body = {'grant_type': 'refresh_token', 'refresh_token': _string(tokens[refresh_key]),
                    'client_id': PROVIDERS[runtime][1]}
            if runtime == 'claude':
                if tokens.get('clientId', body['client_id']) != body['client_id']:
                    raise ValueError('Unsupported OAuth client')
                scopes = tokens['scopes']
                if not isinstance(scopes, list) or not scopes:
                    raise ValueError('Missing OAuth scopes')
                body['scope'] = ' '.join(_string(scope) for scope in scopes)
            await allowed()
            _atomic(directory_fd, '.loma-oauth.pending', {'credential_digest': original})
            result = await _exchange(runtime, body)
            tokens[access_key] = _string(result['access_token'])
            if 'refresh_token' in result:
                tokens[refresh_key] = _string(result['refresh_token'])
            if result.get('token_type', 'Bearer').lower() != 'bearer':
                raise ValueError('Unsupported token type')
            if runtime == 'claude':
                tokens['expiresAt'] = (time.time() + _number(result['expires_in'])) * 1000
                if 'scope' in result:
                    scopes = result['scope'].split()
                    if not scopes or not set(scopes).issubset(set(tokens['scopes'])):
                        raise ValueError('Changed OAuth scopes')
                    tokens['scopes'] = scopes
                if 'account' in result:
                    meta = _read(account.directory, '.claude.json')['oauthAccount']
                    if (result['account']['uuid'] != meta['accountUuid']
                            or result['account']['email_address'] != meta['emailAddress']):
                        raise ValueError('Changed provider identity')
            else:
                if 'id_token' in result:
                    old = _claims(tokens['id_token'])
                    new = _claims(result['id_token'])
                    if new.get('sub') != old['sub']:
                        raise ValueError('Changed provider identity')
                    auth = new.get('https://api.openai.com/auth', {})
                    if auth.get('chatgpt_account_id', tokens['account_id']) != tokens['account_id']:
                        raise ValueError('Changed provider account')
                    tokens['id_token'] = result['id_token']
                try:
                    access_claims = _claims(tokens['access_token'])
                except (ValueError, IndexError):
                    access_claims = {}
                access_account = access_claims.get('https://api.openai.com/auth', {})
                if access_account.get('chatgpt_account_id', tokens['account_id']) != tokens['account_id']:
                    raise ValueError('Changed access-token account')
                tokens.pop('expires_at', None)
                if 'expires_in' in result:
                    tokens['expires_at'] = time.time() + _number(result['expires_in'])
                doc['last_refresh'] = datetime.now(timezone.utc).isoformat()
            if _expiry(doc, runtime) <= time.time() + SKEW:
                raise ValueError('Refreshed credential already expires')
            await allowed()
            # No await between revalidation and rename. Reject an observed
            # external reconnect/disconnect; legacy writers must be stopped.
            if (account.identity() != identity or _digest(_read(account.directory, name)) != original
                    or os.stat(account.directory, follow_symlinks=False) != os.fstat(directory_fd)):
                raise ValueError('Account changed during refresh')
            _atomic(directory_fd, name, doc)
            os.unlink('.loma-oauth.pending', dir_fd=directory_fd)
            os.fsync(directory_fd)
        await allowed()
        if (account.identity() != identity
                or _digest(_read(account.directory, name)) != _digest(doc)):
            raise ValueError('Account changed during refresh')
        if runtime == 'claude':
            return {'Authorization': 'Bearer ' + tokens[access_key],
                    'anthropic-version': '2023-06-01', 'anthropic-beta': 'oauth-2025-04-20'}
        return {'Authorization': 'Bearer ' + tokens[access_key],
                'ChatGPT-Account-Id': _string(tokens['account_id'])}
