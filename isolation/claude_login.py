"""Backend-only login broker and credential store. No host CLI or shell.

Sessions are process-local (sticky routing required); credential generations and
OAuth's flock fence cross-process reconnect/disconnect/refresh races. At-rest
storage must be on the operator's encrypted volume, never mounted into tasks.
"""
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import time
import uuid

import aiohttp

from isolation.login_urls import authorization_url
from isolation.accounts import SubscriptionAccount, _read
from isolation.client import transport_context
from isolation.oauth import _atomic, _locked, _number, _string
from isolation.protocol import worker_frame, response_frame

TTL = 600
EMAIL = re.compile(r'[A-Za-z0-9_+.%=-]+@[A-Za-z0-9.-]+\Z')
CODE = re.compile(r'[A-Za-z0-9_.#~-]{1,2048}\Z')


def enabled():
    default = 'on' if Path(os.getenv('LOMA_LOGIN_SOCKET', '/run/loma-login/login.sock')).is_socket() else 'off'
    return os.getenv('LOMA_CLAUDE_SHARED_LOGIN', default) == 'on'


def directory(email):
    if not isinstance(email, str) or len(email) > 254 or not EMAIL.fullmatch(email):
        raise ValueError('Invalid account identity')
    root = Path(os.getenv('CLAUDE_USERS_DIR', '/opt/claude-users'))
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('Invalid account root')
    path = root / email
    if path.is_symlink():
        raise ValueError('Invalid account directory')
    return path


def transport():
    if not enabled():
        raise ValueError('Isolated Claude login is not configured')
    mode = os.getenv('LOMA_LOGIN_MODE', 'bundled')
    if mode == 'bundled':
        path = Path(os.getenv('LOMA_LOGIN_SOCKET', '/run/loma-login/login.sock'))
        if not path.is_absolute() or path.is_symlink() or not path.is_socket():
            raise ValueError('Bundled Claude login is unavailable. Rebuild and start the full Docker Compose stack.')
        return 'http://loma-login', None, str(path)
    if mode != 'remote':
        raise ValueError('LOMA_LOGIN_MODE must be bundled or remote')
    url = os.environ['LOMA_LOGIN_URL']
    token = os.environ['LOMA_LOGIN_TOKEN']
    if len(token) < 32:
        raise ValueError('Invalid login transport')
    tls = transport_context(url, *(os.environ['LOMA_LOGIN_TLS_' + key] for key in ('CA', 'CERT', 'KEY')))
    return url, token, tls


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {'.claude.json', '.credentials.json'}:
        raise ValueError('Invalid credential bundle')
    if len(json.dumps(bundle)) > 65536:
        raise ValueError('Credential bundle too large')
    meta = bundle['.claude.json']['oauthAccount']
    oauth = bundle['.credentials.json']['claudeAiOauth']
    # Persist an allowlist, not CLI-supplied settings, hooks or MCP servers.
    identity = {key: _string(meta[key]) for key in ('accountUuid', 'emailAddress')}
    credentials = {key: _string(oauth[key]) for key in ('accessToken', 'refreshToken')}
    expires = _number(oauth['expiresAt'])
    if expires <= (time.time() + 60) * 1000:
        raise ValueError('Login credential already expired')
    scopes = oauth['scopes']
    if not isinstance(scopes, list) or not scopes or len(scopes) > 32:
        raise ValueError('Invalid scopes')
    credentials.update(expiresAt=expires, scopes=[_string(s) for s in scopes])
    for key in ('subscriptionType', 'rateLimitTier'):
        if oauth.get(key) is not None:
            credentials[key] = _string(oauth[key])
    return {'.claude.json': {'oauthAccount': identity},
            '.credentials.json': {'claudeAiOauth': credentials}}


async def begin(email, generation):
    path = directory(email)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    async with _locked(path) as fd:
        _atomic(fd, '.loma-login.json', {'generation': generation})


async def publish(email, generation, bundle, authorize=None):
    bundle = validate_bundle(bundle)
    path = directory(email)
    async with _locked(path) as fd:
        if authorize is not None and not await authorize():
            raise ValueError('Login access revoked')
        if _read(path, '.loma-login.json').get('generation') != generation:
            raise ValueError('Login was cancelled or replaced')
        # OAuth readers take the same lock. Credential rename precedes metadata;
        # a crashed partial publish has no ready marker, so is never discoverable.
        for name in ('.loma-shared-login.json', '.claude.json'):
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass
        for name in ('.credentials.json', '.claude.json'):
            _atomic(fd, name, bundle[name])
        _atomic(fd, '.loma-shared-login.json', {'owner': email, 'generation': generation})
        os.fsync(fd)


async def disconnect(email):
    path = directory(email)
    if not path.exists():
        return
    async with _locked(path) as fd:
        _atomic(fd, '.loma-login.json', {'generation': uuid.uuid4().hex})
        for name in ('.loma-shared-login.json', '.claude.json', '.credentials.json', '.loma-oauth.pending'):
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.fsync(fd)


def shared_accounts():
    if not enabled():
        return ()
    root = directory('probe@example.invalid').parent
    if not root.exists():
        return ()
    result = []
    for path in sorted(root.iterdir()):
        try:
            if directory(path.name) != path or _read(path, '.loma-shared-login.json')['owner'] != path.name:
                continue
            a = SubscriptionAccount('claude', path.name, path,
                                    int(os.getenv('LOMA_REMOTE_ACCOUNT_CAPACITY', '1')))
            a.identity()
            result.append(a)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            continue
    return tuple(result)


@dataclass
class Login:
    owner: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    expires: float = field(default_factory=lambda: time.time() + TTL)
    state: str = 'starting'
    url: str | None = None
    error: str | None = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1), repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    submitted: bool = False

    def public(self):
        return {'id': self.id, 'state': self.state, 'url': self.url,
                'error': self.error, 'expires_at': self.expires}


async def run_login(login, connection, authorize):
    url, token, tls = connection
    connector = aiohttp.UnixConnector(path=tls) if token is None else None
    headers = {'Authorization': 'Bearer ' + token} if token is not None else {}
    pending = None
    bundle = None
    text = ''
    async def code_response(ws, request_id):
        code = await login.queue.get()
        if not await authorize():
            raise ValueError('Login access revoked')
        await ws.send_str(response_frame({'type': 'tool_response', 'id': request_id, 'result': {'code': code}}))
    try:
        async with asyncio.timeout(TTL), aiohttp.ClientSession(trust_env=False, connector=connector) as session:
            if not await authorize():
                raise ValueError('Login access revoked')
            async with session.ws_connect(url.rstrip('/') + '/v1/run', ssl=tls if token is not None else True,
                    headers=headers, max_msg_size=131072, heartbeat=20) as ws:
                await ws.send_json({'type': 'start', 'input': {'runtime': 'claude-login'}})
                seen = set()
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT or not await authorize():
                        raise ValueError('Login connection closed')
                    if pending is not None and pending.done():
                        pending.result()
                    frame = worker_frame(message.data)
                    if frame['type'] == 'text':
                        if frame['text'] == 'LOMA_LOGIN_FINISHED':
                            if pending is None or pending.done():
                                raise ValueError('Unexpected login completion')
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                            pending = None
                            await ws.send_json({'type': 'tool_response', 'id': 'code-0', 'result': {'cancelled': True}})
                            continue
                        text += frame['text']
                        if len(text) > 65536:
                            raise ValueError('Login output exceeded budget')
                        # Wait for whitespace termination so a split URL is not
                        # exposed before its state/PKCE parameters are complete.
                        complete = text[:max(text.rfind('\n'), text.rfind('\r'), text.rfind(' ')) + 1]
                        link = authorization_url(complete)
                        if link:
                            login.url, login.state = link, 'waiting'
                    elif frame['type'] == 'tool_request':
                        if frame['id'] in seen:
                            raise ValueError('Repeated login request')
                        seen.add(frame['id'])
                        if frame['tool'] == 'login_code' and frame['id'] == 'code-0' and not frame['arguments']:
                            pending = asyncio.create_task(code_response(ws, frame['id']))
                        elif frame['tool'] == 'save_credentials' and frame['id'] == 'save':
                            bundle = validate_bundle(frame['arguments'])
                            await ws.send_json({'type': 'tool_response', 'id': 'save', 'result': {'ok': True}})
                        else:
                            raise ValueError('Unexpected login operation')
                    elif frame['type'] == 'done':
                        if bundle is None or not await authorize():
                            raise ValueError('Login did not complete')
                        await publish(login.owner, login.id, bundle, authorize)
                        from agent.pool import get_pool
                        try:
                            get_pool().refresh_accounts()
                        except RuntimeError:
                            pass  # remote mode discovers accounts on each admission
                        login.state, login.url = 'connected', None
                        return
                raise ValueError('Login ended before completion')
    except asyncio.CancelledError:
        login.state, login.url = 'cancelled', None
        raise
    except Exception:
        login.state, login.url = 'failed', None
        login.error = 'Login failed or expired. Please start again.'
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
