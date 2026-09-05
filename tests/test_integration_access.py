"""Owner and sharing checks, with synthetic records and no provider calls."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from integrations.access import allows_user, require_provider
from broker.operations import ToolInvoke, McpRequest, _strip_identity_flags
from broker.service import Denied
from api import integration_routes, routes, skill_service
from tests.test_security_boundaries import Request

EMAIL = 'owner@example.test'


@pytest.mark.parametrize('doc,allowed', [
    ({'status': 'active'}, True),
    ({'status': 'inactive'}, False),
    ({'status': 'active', 'shared_with': {'mode': 'unknown'}}, False),
    ({'status': 'active', 'shared_with': {'mode': 'specific', 'users': [EMAIL]}}, True),
    ({'status': 'active', 'shared_with': {'mode': 'specific', 'users': ['other']}}, False),
    ({'status': 'active', 'is_custom': True, 'connected_by': EMAIL}, True),
    ({'status': 'active', 'is_custom': True, 'connected_by': 'other'}, False),
    ({'status': 'active', 'is_custom': True}, False),
    ({'status': 'active', 'scope': 'personal', 'connected_by': 'other', 'shared_with': {'mode': 'everyone'}}, False),
])
def test_connection_ownership(doc, allowed):
    assert allows_user(doc, EMAIL) is allowed
    assert not allows_user(doc, '')


def test_team_scope():
    doc = {'status': 'active', 'shared_with': {'mode': 'specific', 'teams': ['finance']}}
    assert allows_user(doc, EMAIL, ['finance'])
    assert not allows_user(doc, EMAIL, ['engineering'])


@pytest.mark.asyncio
async def test_permissions_rechecked_on_every_tool_call():
    lookup = AsyncMock(side_effect=[{'status': 'active'}, {'status': 'inactive'}])
    db = SimpleNamespace(integrations=SimpleNamespace(find_one=lookup))
    await require_provider(db, 'posthog', EMAIL)
    with pytest.raises(Denied):
        await ToolInvoke().execute(db, EMAIL, 'posthog', {'argv': []})
    assert lookup.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('record', [None, RuntimeError('unavailable')])
async def test_lookup_failures_deny(record):
    lookup = AsyncMock(return_value=record) if record is None else AsyncMock(side_effect=record)
    db = SimpleNamespace(integrations=SimpleNamespace(find_one=lookup))
    with pytest.raises(Denied):
        await require_provider(db, 'posthog', EMAIL)


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['chatter', 'analyst', 'operator', 'maintainer'])
@pytest.mark.parametrize('handler', [integration_routes._connect_integration, integration_routes._disconnect_integration])
async def test_org_credentials_admin_only(role, handler):
    with pytest.raises(web.HTTPForbidden):
        await handler(Request(role=role))


@pytest.mark.asyncio
async def test_personal_skill_move_checks_owner_before_update():
    db = SimpleNamespace(skills=SimpleNamespace(
        find_one=AsyncMock(return_value={'scope': 'personal', 'created_by': 'other'}),
        update_one=AsyncMock()))
    with pytest.raises(skill_service.SkillError) as caught:
        await skill_service.update_skill_folder(db, slug='sample', actor=EMAIL, folder='Folder')
    assert caught.value.status == 403
    db.skills.update_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['chatter', 'analyst', 'admin'])
async def test_picker_only_returns_owned_or_shared_skills(role):
    skills = [{'slug': 'mine', 'scope': 'personal', 'created_by': EMAIL},
              {'slug': 'other', 'scope': 'personal', 'created_by': 'other'},
              {'slug': 'global', 'scope': 'workspace'}, {'slug': 'builtin', 'scope': 'system'}]
    with patch.object(routes, 'get_db', return_value=object()), patch.object(
        skill_service, 'list_skills', AsyncMock(return_value=skills)):
        result = await routes.handle_list_skills(Request(user=EMAIL, role=role))
    assert [s['slug'] for s in json.loads(result.body)['skills']] == ['mine', 'global', 'builtin']


def test_equals_identity_flags_removed():
    assert _strip_identity_flags(['--user-email=other', '--auth-token=invalid', 'list']) == ['list']


@pytest.mark.asyncio
async def test_mcp_personal_provider_never_uses_org_fallback():
    with patch('api.oauth_helpers.get_valid_provider_token', AsyncMock(return_value=None)):
        with pytest.raises(Denied):
            await McpRequest().execute(object(), EMAIL, 'grain')


@pytest.mark.asyncio
async def test_grain_auth_checked_before_db():
    from tools.grain import run_personal
    with patch('tools._auth_token.verify_user_auth_token', return_value=False):
        with pytest.raises(ValueError, match='authentication'):
            await run_personal('search', ['test'], EMAIL, 'invalid')


def test_grain_ignores_organization_environment(monkeypatch):
    from tools.grain import _get_api_token
    monkeypatch.setenv('GRAIN_API_TOKEN', 'synthetic-test-value')
    with pytest.raises(ValueError, match='personal Grain'):
        _get_api_token()


@pytest.mark.asyncio
@pytest.mark.parametrize('identifier', ['../other', 'id?query=1', 'https://example.test'])
async def test_grain_pins_transcript_resource(identifier):
    from tools.grain import get_transcript
    with pytest.raises(ValueError, match='recording ID'):
        await get_transcript(identifier)


@pytest.mark.asyncio
async def test_owner_custom_config_is_not_shared():
    from agent.client import build_user_mcp_overrides
    from tests.test_runtime_isolation import FakeCursor
    lookup = MagicMock(return_value=FakeCursor([{
        'provider': 'private-example', 'is_custom': True, 'status': 'active',
        'connected_by': EMAIL, 'auth_mode': 'static', 'mcp_url': 'https://example.test/mcp',
        'api_key_encrypted': 'encrypted-placeholder',
    }]))
    db = SimpleNamespace(integrations=SimpleNamespace(find=lookup))
    with patch('observability.db.get_db', return_value=db), patch(
        'api.oauth_helpers.get_valid_provider_token', AsyncMock(return_value=None)), patch(
        'api.oauth_helpers.decrypt_token', return_value='synthetic-test-value'):
        result = await build_user_mcp_overrides(EMAIL)
    assert lookup.call_args.args[0]['connected_by'] == EMAIL
    assert result['private-example']['url'] == 'https://example.test/mcp'


@pytest.mark.asyncio
async def test_provider_refresh_does_not_resurrect_revoked_connection():
    from datetime import datetime, timezone, timedelta
    from api.oauth_helpers import get_valid_provider_token
    doc = {'access_token': 'old-encrypted', 'refresh_token': 'old-refresh',
           'token_expiry': datetime.now(timezone.utc) - timedelta(hours=1)}
    update = AsyncMock(return_value=SimpleNamespace(matched_count=0))
    db = SimpleNamespace(oauth_tokens=SimpleNamespace(find_one=AsyncMock(return_value=doc), update_one=update))
    with patch('api.oauth_helpers.decrypt_token', return_value='synthetic-refresh'), patch(
        'api.oauth_helpers.encrypt_token', return_value='new-encrypted'), patch(
        'api.oauth_helpers._refresh_oauth_token', AsyncMock(return_value={'access_token': 'synthetic-new'})):
        assert await get_valid_provider_token(EMAIL, 'grain', db=db) is None
    assert update.call_args.args[0]['access_token'] == 'old-encrypted'


@pytest.mark.asyncio
async def test_anonymous_mcp_configs_disabled(monkeypatch):
    from broker import controller
    from broker.gateway import McpProxyRegistry
    monkeypatch.setattr(controller, '_registry', McpProxyRegistry())
    marker = controller.current_run.set(None)
    try:
        configs, tokens, disabled = controller.proxy_mcp_servers_for_worker({'example': {'type': 'http', 'url': 'https://example.test'}})
        assert configs == {} and tokens == [] and disabled == ['example']
    finally:
        controller.current_run.reset(marker)


@pytest.mark.asyncio
async def test_claude_run_does_not_reuse_unbound_warm_client(monkeypatch):
    from agent.pool import ClientPool
    from broker import controller
    pool = ClientPool.__new__(ClientPool)
    pool._closed = False
    pool._accounts = [{'email': EMAIL}]
    pool._in_use = 0
    pool._next_account = lambda: pool._accounts[0]
    pool._create_client = AsyncMock(return_value='fresh-bound-client')
    marker = controller.current_run.set(SimpleNamespace(capability='synthetic'))
    try:
        assert await pool.acquire() == 'fresh-bound-client'
        pool._create_client.assert_awaited_once()
    finally:
        controller.current_run.reset(marker)
