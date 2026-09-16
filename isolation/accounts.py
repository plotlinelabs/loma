"""Backend-only subscription selection, independent of local CLI pools.

Candidates are supplied by trusted ingress; refresh defaults to backend OAuth.
This module never discovers grants from disk, starts a CLI, or gives workers account paths.
Selection order is process-local; durable cooldowns are shared across processes.
The default backend OAuth adapter refreshes tokens without starting a CLI.
Deployment-wide pool capacity is not implemented.
"""
import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from bson.codec_options import CodecOptions
from pymongo.write_concern import WriteConcern

from isolation.models import ModelDenied


def _read(directory, name):
    # Open relative to a no-follow directory descriptor. Reject pipes/devices
    # before reading, bound the read, and never return parse errors with secrets.
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Not a regular file')
            data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError('Account file too large')
            return json.loads(data)
    finally:
        os.close(directory_fd)


@dataclass(frozen=True)
class SubscriptionAccount:
    runtime: str
    email: str
    directory: Path = field(repr=False)

    def __post_init__(self):
        if (self.runtime not in {'claude', 'codex'} or not isinstance(self.email, str)
                or not self.email or len(self.email) > 254
                or not isinstance(self.directory, Path) or not self.directory.is_absolute()):
            raise ValueError('Invalid trusted subscription account')

    @property
    def account_id(self):
        return 'subscription-' + hashlib.sha256(
            (self.runtime + '\0' + self.email).encode()).hexdigest()

    def identity(self):
        """Fingerprint provider identity, not token bytes (rotation is allowed).

        Decoding local ID-token claims is not authentication: provider validation
        still happens upstream. This only detects re-login to another identity.
        API-key files are intentionally not subscription-account candidates.
        """
        try:
            if self.runtime == 'claude':
                meta = _read(self.directory, '.claude.json')['oauthAccount']
                identity = [meta['accountUuid'], meta['emailAddress']]
                oauth = _read(self.directory, '.credentials.json')['claudeAiOauth']
                if not isinstance(oauth.get('accessToken'), str) or not oauth['accessToken']:
                    raise ValueError('Missing OAuth token')
            else:
                tokens = _read(self.directory, 'auth.json')['tokens']
                token = tokens['id_token'].split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
                identity = [tokens['account_id'], claims['sub']]
                if not isinstance(tokens.get('access_token'), str) or not tokens['access_token']:
                    raise ValueError('Missing OAuth token')
            if any(not isinstance(value, str) or not value for value in identity):
                raise ValueError('Missing provider identity')
            return hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            raise ModelDenied('Subscription account is unavailable') from None


class SubscriptionAccounts:
    def __init__(self, db, accounts, *, check_access, refresh=None):
        if refresh is None:
            from isolation.oauth import OAuthRefresh
            refresh = OAuthRefresh(check_access=self._allowed)
        # Neither candidates nor adapters may originate in a worker request.
        accounts = tuple(accounts)
        if (not all(isinstance(a, SubscriptionAccount) for a in accounts)
                or len({a.account_id for a in accounts}) != len(accounts)
                or not callable(check_access) or not callable(refresh)):
            raise ValueError('Invalid subscription account configuration')
        self.accounts, self.check_access, self.refresh = accounts, check_access, refresh
        self.users = db.users
        self.states = db.isolated_subscription_accounts.with_options(
            write_concern=WriteConcern(w='majority'), codec_options=CodecOptions(tz_aware=True))
        self._index = 0
        self._lock = asyncio.Lock()

    async def _allowed(self, authority, account):
        # Fail closed on DB/policy errors, unknown users and admin disablement.
        if await self.check_access(authority, account) is not True:
            return False
        user = await self.users.find_one({'email': account.email, 'deleted': {'$ne': True}},
            {'status': 1, account.runtime + '_pool_enabled': 1})
        if (not user or user.get('status', 'active') != 'active'
                or user.get(account.runtime + '_pool_enabled', True) is not True):
            return False
        state = await self.states.find_one({'_id': account.account_id})
        return not state or state.get('cooldown_until', datetime.min.replace(tzinfo=timezone.utc)) <= datetime.now(timezone.utc)

    async def select(self, authority, runtime):
        async with self._lock:
            for offset in range(len(self.accounts)):
                index = (self._index + offset) % len(self.accounts)
                account = self.accounts[index]
                if account.runtime != runtime or not await self._allowed(authority, account):
                    continue
                try:
                    identity = account.identity()
                except ModelDenied:
                    continue
                self._index = (index + 1) % len(self.accounts)
                return SelectedSubscription(self, authority, account, identity)
        raise ModelDenied('No authorized subscription account is available')

    async def cooldown(self, account_id, seconds):
        if (account_id not in {a.account_id for a in self.accounts}
                or type(seconds) is not int or not 1 <= seconds <= 86400):
            raise ValueError('Invalid subscription cooldown')
        # Concurrent reports can extend, never shorten, a cooldown.
        await self.states.update_one({'_id': account_id}, {'$max': {
            'cooldown_until': datetime.now(timezone.utc) + timedelta(seconds=seconds)}}, upsert=True)


@dataclass(frozen=True)
class SelectedSubscription:
    selector: SubscriptionAccounts = field(repr=False)
    authority: object = field(repr=False)
    account: SubscriptionAccount = field(repr=False)
    identity: str = field(repr=False)

    @property
    def account_id(self):
        return self.account.account_id

    async def authorize(self, authority):
        try:
            return (authority == self.authority
                and await self.selector._allowed(authority, self.account)
                and self.account.identity() == self.identity)
        except Exception:
            return False

    async def resolve_headers(self, authority, account_id):
        # Re-check before and after refresh. Never switch identities/accounts in
        # an existing budget, even if another account is available.
        async def validate():
            if account_id != self.account_id or not await self.authorize(authority):
                raise ModelDenied('Subscription account access changed')
        try:
            await validate()
            headers = await self.selector.refresh(authority, self.account)
            await validate()
            return headers
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ModelDenied('Subscription credential resolution failed') from None
