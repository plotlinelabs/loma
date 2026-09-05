"""Internal identity assertions cannot be substituted for browser authentication."""
import hashlib
import hmac
import time
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from api import auth_middleware as middleware
from api.proxy_identity import sign_identity, verify_identity

EMAIL = 'owner@example.test'
KEY = 'synthetic-proxy-test-key-32-characters'


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv('BACKEND_PROXY_SECRET', KEY)
    monkeypatch.setattr(middleware, '_IS_PREVIEW', False)
    monkeypatch.setattr(middleware, '_IS_DEV', False)


def test_purpose_bound_signature_matches_cross_language_payload():
    timestamp = str(int(time.time()))
    expected = hmac.new(KEY.encode(), f'loma-proxy-identity-v1\n{timestamp}\n{EMAIL}'.encode(), hashlib.sha256).hexdigest()
    assert sign_identity(EMAIL, timestamp) == expected
    assert verify_identity(EMAIL, timestamp, expected)
    assert not verify_identity('another@example.test', timestamp, expected)


@pytest.mark.parametrize('offset', [-61, 60, 100000])
def test_expired_and_future_assertions_rejected(offset):
    stamp = str(int(time.time()) + offset)
    assert not verify_identity(EMAIL, stamp, sign_identity(EMAIL, stamp))


@pytest.mark.parametrize('timestamp,signature', [('', ''), ('1', 'x'*64), ('unicode-١٢٣', 'a'*64)])
def test_malformed_assertions_rejected(timestamp, signature):
    assert not verify_identity(EMAIL, timestamp, signature)


@pytest.mark.asyncio
async def test_unsigned_backend_identity_rejected_before_database(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(middleware, 'get_db', lookup)
    request = make_mocked_request('GET', '/api/env', headers={'X-User-Email': EMAIL})
    handler = AsyncMock(return_value=web.Response())
    result = await middleware.auth_middleware(request, handler)
    assert result.status == 401
    handler.assert_not_called()
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_missing_signing_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv('BACKEND_PROXY_SECRET')
    monkeypatch.delenv('OAUTH_ENCRYPTION_KEY', raising=False)
    request = make_mocked_request('GET', '/api/env', headers={'X-User-Email': EMAIL})
    result = await middleware.auth_middleware(request, AsyncMock())
    assert result.status == 503


def test_signing_secret_cannot_be_forwarded_to_worker(tmp_path, monkeypatch):
    from broker.worker import build_worker_env, WorkerIsolationError
    with pytest.raises(WorkerIsolationError):
        build_worker_env(tmp_path, {'BACKEND_PROXY_SECRET': KEY})
