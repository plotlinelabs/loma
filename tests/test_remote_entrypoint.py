"""Entrypoint cutover tests: routing, fail-closed config, and run assembly.

Uses throwaway opt-in Mongo for DB-backed tests and synthetic stream_run
generators; never opens a network transport or calls a paid provider.
"""
import asyncio
import base64
import ssl
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from isolation import deployment as dep
from isolation import entrypoint as ep
from isolation.accounts import SubscriptionAccount
from isolation.context import Attachment
from isolation.models import ModelDenied
from isolation.protocol import RunAuthority
from tests.test_bounded_work import db, OWNER  # noqa: F401  (fixture)

VALID_ENV = {
    'LOMA_REMOTE_WORKERS': 'on',
    'LOMA_WORKER_URL': 'https://worker.example.test',
    'LOMA_WORKER_CONTROL_TOKEN': 'x' * 40,
    'LOMA_WORKER_TLS_CA': '/tmp/ca.pem', 'LOMA_WORKER_TLS_CERT': '/tmp/cert.pem',
    'LOMA_WORKER_TLS_KEY': '/tmp/key.pem',
    'LOMA_WORKER_ARTIFACT_DIR': '/srv/worker-artifacts',
    'LOMA_REMOTE_CHAT_ENDPOINT': 'https://provider.example.test/v1/chat/completions',
    'LOMA_REMOTE_CHAT_API_KEY': 'synthetic-key',
}


def set_env(monkeypatch, **overrides):
    for name in ('LOMA_REMOTE_WORKERS', 'LOMA_WORKER_URL', 'LOMA_WORKER_CONTROL_TOKEN',
                 'LOMA_WORKER_TLS_CA', 'LOMA_WORKER_TLS_CERT', 'LOMA_WORKER_TLS_KEY',
                 'LOMA_WORKER_ARTIFACT_DIR', 'LOMA_REMOTE_CHAT_ENDPOINT',
                 'LOMA_REMOTE_CHAT_API_KEY', 'LOMA_REMOTE_CHAT_API_KEY_HEADER',
                 'LOMA_REMOTE_CLAUDE_ACCOUNTS', 'LOMA_REMOTE_CODEX_ACCOUNTS',
                 'LOMA_REMOTE_DEFAULT_MODEL', 'LOMA_REMOTE_RUN_BUDGET_NUSD'):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**VALID_ENV, **overrides}.items():
        if value is not None:
            monkeypatch.setenv(name, value)


def synthetic_deployment(tmp_path, **overrides):
    values = dict(url='https://worker.example.test', token='x' * 40,
        tls=ssl.create_default_context(), artifact_root=Path(tmp_path),
        claude_accounts=(), codex_accounts=(),
        chat_endpoint='https://provider.example.test/v1/chat/completions',
        chat_headers={'authorization': 'Bearer synthetic'},
        default_model=None, budget_nusd=dep.DEFAULT_BUDGET_NUSD)
    return dep.RemoteDeployment(**{**values, **overrides})


class Observer:
    def __init__(self, database, conversation_id='remote-convo'):
        self.db, self.conversation_id, self.turn_count = database, conversation_id, 0
        for name in ('record_text', 'record_artifact', 'record_error', 'finish', 'mark_interrupted'):
            setattr(self, name, AsyncMock())


@pytest.mark.parametrize('value,expected', [('on', True), (' ON ', True), ('off', False),
                                            ('', False), ('true', False), (None, False)])
def test_flag_requires_explicit_on(monkeypatch, value, expected):
    monkeypatch.delenv('LOMA_REMOTE_WORKERS', raising=False)
    if value is not None:
        monkeypatch.setenv('LOMA_REMOTE_WORKERS', value)
    assert dep.remote_workers_enabled() is expected


@pytest.mark.parametrize('overrides', [
    {'LOMA_WORKER_URL': None}, {'LOMA_WORKER_URL': 'http://worker.example.test'},
    {'LOMA_WORKER_URL': 'https://worker.example.test/path'},
    {'LOMA_WORKER_CONTROL_TOKEN': 'short'}, {'LOMA_WORKER_TLS_CA': None},
    {'LOMA_WORKER_ARTIFACT_DIR': 'relative/dir'},
    {'LOMA_REMOTE_CHAT_API_KEY': None},
    {'LOMA_REMOTE_CLAUDE_ACCOUNTS': 'not-an-entry'},
    {'LOMA_REMOTE_CLAUDE_ACCOUNTS': 'a@example.test=relative/dir'},
    {'LOMA_REMOTE_RUN_BUDGET_NUSD': 'not-a-number'},
    {'LOMA_REMOTE_RUN_BUDGET_NUSD': '0'},
    {'LOMA_REMOTE_CHAT_ENDPOINT': None},  # no accounts and no chat endpoint
])
def test_incomplete_configuration_fails_closed(monkeypatch, overrides):
    set_env(monkeypatch, **overrides)
    with pytest.raises(dep.DeploymentError):
        dep.load_deployment()


def test_valid_configuration_parses(monkeypatch):
    set_env(monkeypatch, LOMA_REMOTE_CLAUDE_ACCOUNTS='a@example.test=/srv/accounts/a, b@example.test=/srv/accounts/b',
            LOMA_REMOTE_CODEX_ACCOUNTS='c@example.test=/srv/accounts/c',
            LOMA_REMOTE_DEFAULT_MODEL='anthropic/claude-sonnet-4-5')
    monkeypatch.setattr(dep, 'transport_context', lambda url, ca, cert, key: ssl.create_default_context())
    deployment = dep.load_deployment()
    assert [a.email for a in deployment.claude_accounts] == ['a@example.test', 'b@example.test']
    assert deployment.codex_accounts[0].runtime == 'codex'
    assert deployment.chat_headers == {'authorization': 'Bearer synthetic-key'}
    assert deployment.default_model == 'anthropic/claude-sonnet-4-5'
    assert deployment.accounts_for('claude') == deployment.claude_accounts
    assert deployment.accounts_for('opencode') == ()


def test_tls_material_must_load(monkeypatch, tmp_path):
    set_env(monkeypatch, LOMA_WORKER_TLS_CA=str(tmp_path / 'missing-ca.pem'),
            LOMA_WORKER_TLS_CERT=str(tmp_path / 'missing-cert.pem'),
            LOMA_WORKER_TLS_KEY=str(tmp_path / 'missing-key.pem'))
    with pytest.raises(dep.DeploymentError, match='TLS material'):
        dep.load_deployment()


def test_prices_are_pinned_and_conservative_by_default():
    assert dep.price_for('claude-sonnet-4-5') == (3000, 15000, 300, 3750)
    assert dep.price_for('claude-opus-4-8')[1] == 75000
    assert dep.price_for('gpt-5.3-codex')[3] == 0
    unknown = dep.price_for('some-unknown-model')
    assert unknown == dep.DEFAULT_PRICE and unknown[0] >= max(p[0] for _, p in dep.PRICES)
    with pytest.raises(dep.DeploymentError):
        dep.price_for('  ')


def test_grant_and_budget_composition(tmp_path):
    deployment = synthetic_deployment(tmp_path)
    for runtime, model in (('claude', 'claude-opus-4-8'), ('codex', 'gpt-5.3-codex'),
                           ('opencode', 'deepseek-v4-flash')):
        grant = dep.build_grant(runtime, model, deployment)
        budget = dep.build_budget(runtime, model, deployment)
        assert grant.protocol == budget.protocol == dep.PROTOCOLS[runtime]
        assert budget.reservation <= budget.budget_nusd
        assert budget.max_calls >= grant.max_calls and budget.output_ceiling >= grant.max_output_tokens
        if runtime == 'opencode':
            assert grant.endpoint == deployment.chat_endpoint and grant.headers
            assert budget.account_id == 'deployment-chat-endpoint'
        else:
            assert grant.endpoint == dep.ENDPOINTS[runtime] and grant.headers == {}
            assert budget.account_id == 'pending-subscription'
    with pytest.raises(dep.DeploymentError):
        dep.build_grant('opencode', 'x', synthetic_deployment(tmp_path, chat_endpoint=None, chat_headers={}))
    with pytest.raises(dep.DeploymentError):
        dep.build_grant('bash', 'x', deployment)
    with pytest.raises(dep.DeploymentError):
        dep.build_budget('claude', 'claude-opus-4-8', synthetic_deployment(tmp_path, budget_nusd=1000))


def test_runtime_mapping(tmp_path):
    deployment = synthetic_deployment(tmp_path)
    assert ep.runtime_for('codex/gpt-5.3-codex', deployment) == ('codex', 'gpt-5.3-codex')
    assert ep.runtime_for('anthropic/claude-sonnet-4-5', deployment) == ('claude', 'claude-sonnet-4-5')
    assert ep.runtime_for('claude-haiku-4-5', deployment) == ('claude', 'claude-haiku-4-5')
    assert ep.runtime_for('opencode-go/deepseek-v4-flash', deployment) == ('opencode', 'deepseek-v4-flash')
    assert ep.runtime_for(None, synthetic_deployment(tmp_path, default_model='anthropic/claude-sonnet-4-5')) \
        == ('claude', 'claude-sonnet-4-5')
    with pytest.raises(dep.DeploymentError):
        ep.runtime_for(None, deployment)


def test_tool_policy_is_full_or_conservatively_restricted():
    assert ep.allowed_tools_for(None) == ep.FULL_TOOLS
    assert ep.allowed_tools_for({'enabled_skills': ['docx']}) == ep.FULL_TOOLS
    restricted = ep.allowed_tools_for({'enabled_tools': ['mcp__github__get_me']})
    assert restricted == ep.RESTRICTED_TOOLS
    assert not any(name.startswith(('gmail.', 'slack.', 'pylon.')) for name in restricted)
    assert ep.allowed_skills_for({'enabled_skills': ['docx', 'pdf']}) == ('docx', 'pdf')
    assert ep.allowed_skills_for(None) is None


def test_convert_files_validates_payloads():
    converted = ep.convert_files([
        {'type': 'text', 'name': 'notes.txt', 'data': 'hello'},
        {'type': 'image', 'name': 'shot.png', 'data': base64.b64encode(b'png-bytes').decode()},
        {'type': 'binary', 'name': 'data.xlsx', 'data': base64.b64encode(b'xlsx').decode()}])
    assert [a.name for a in converted] == ['notes.txt', 'shot.png', 'data.xlsx']
    assert converted[0].data == b'hello' and converted[1].data == b'png-bytes'
    assert ep.convert_files(None) == ()
    for bad in ([{'type': 'zip', 'name': 'a', 'data': 'x'}], [{'type': 'text', 'name': 'a'}],
                [{'type': 'image', 'name': 'a', 'data': 42}], ['not-a-dict']):
        with pytest.raises(ValueError):
            ep.convert_files(bad)


def test_instructions_have_no_personal_tokens_and_state_the_surface(monkeypatch):
    import tools._auth_token as auth_token
    monkeypatch.setattr(auth_token, 'create_user_auth_token',
        lambda *a, **k: pytest.fail('remote instructions must never mint auth tokens'))
    text = ep._instructions('dashboard', OWNER)
    assert OWNER in text and 'isolated workspace' in text
    assert 'Personal Tools Auth Token' not in text
    assert len(text.encode()) <= 255 * 1024


def test_local_cli_utilities_fail_closed(monkeypatch):
    set_env(monkeypatch)
    from agent.pool import background_cli_env
    with pytest.raises(RuntimeError, match='LOMA_REMOTE_WORKERS'):
        background_cli_env()
    from agent.opencode_runtime import _should_prewarm_model
    assert _should_prewarm_model('deepseek-v4-flash') is False
    from gate.verifier import ClaudeCLIVerifier
    with pytest.raises(RuntimeError, match='LOMA_REMOTE_WORKERS'):
        ClaudeCLIVerifier()._complete('prompt')


@pytest.mark.asyncio
async def test_pool_status_never_500s_in_remote_mode(monkeypatch, tmp_path):
    """The dashboard polls /api/pool-status on every session; with no local
    pool it must report remote mode (and misconfiguration) instead of raising."""
    from api.routes import handle_pool_status
    set_env(monkeypatch, LOMA_WORKER_URL=None)  # flag on, transport missing
    response = await handle_pool_status(None)
    assert response.status == 200
    body = __import__('json').loads(response.text)
    assert body['pool_size'] == 0 and body['accounts'] == []
    assert body['remote_workers']['enabled'] is True
    assert body['remote_workers']['configured'] is False
    assert 'LOMA_WORKER_URL' in body['remote_workers']['error']

    set_env(monkeypatch, LOMA_REMOTE_CLAUDE_ACCOUNTS=f'owner@example.test={tmp_path}/claude')
    monkeypatch.setattr(dep, 'transport_context', lambda url, ca, cert, key: ssl.create_default_context())
    body = __import__('json').loads((await handle_pool_status(None)).text)
    assert body['accounts'] == ['owner@example.test']
    assert body['remote_workers']['configured'] is True
    assert body['remote_workers']['claude_accounts'] == 1
    assert str(tmp_path) not in body['accounts'][0]  # emails only, never directories


@pytest.mark.asyncio
async def test_title_generation_degrades_without_local_cli(monkeypatch):
    set_env(monkeypatch)
    from api.routes import _generate_title_llm
    title = await _generate_title_llm('Summarize the quarterly revenue numbers please')
    assert title and 'quarterly' in title.lower()


@pytest.mark.asyncio
async def test_stream_agent_routes_by_flag(monkeypatch):
    from agent import client

    async def remote(*args, **kwargs):
        yield 'remote-chunk'

    async def local(*args, **kwargs):
        yield 'local-chunk'

    import isolation.entrypoint as entrypoint
    monkeypatch.setattr(entrypoint, 'remote_stream_agent', remote)
    monkeypatch.setattr(client, '_stream_agent', local)
    monkeypatch.setenv('LOMA_REMOTE_WORKERS', 'on')
    assert [e async for e in client.stream_agent('hi')] == ['remote-chunk']
    monkeypatch.setenv('LOMA_REMOTE_WORKERS', 'off')
    assert [e async for e in client.stream_agent('hi')] == ['local-chunk']


@pytest.mark.asyncio
async def test_remote_run_fails_closed_when_unconfigured(monkeypatch, db):
    set_env(monkeypatch, LOMA_WORKER_URL=None)
    observer = Observer(db)
    events = [e async for e in ep.remote_stream_agent('hi', observer=observer,
        user_email=OWNER, selected_model='anthropic/claude-sonnet-4-5')]
    assert len(events) == 1 and ep.UNAVAILABLE.strip() in events[0]
    assert 'LOMA_WORKER_URL' in events[0]
    # The dashboard re-renders the persisted error on reload: it must be the
    # same user-facing message that was streamed, not the bare reason.
    observer.record_error.assert_awaited_once_with(events[0])
    observer.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_run_requires_owner_and_observer(db):
    observer = Observer(db)
    events = [e async for e in ep.remote_stream_agent('hi', observer=observer, user_email='  ')]
    assert 'authenticated owner' in events[0]
    with pytest.raises(RuntimeError):
        _ = [e async for e in ep.remote_stream_agent('hi', observer=None, user_email=OWNER)]


@pytest.mark.asyncio
async def test_remote_run_full_assembly(monkeypatch, db, tmp_path):
    from datetime import datetime, timedelta, timezone
    import tools._auth_token as auth_token
    monkeypatch.setattr(auth_token, 'create_user_auth_token',
        lambda *a, **k: pytest.fail('remote runs must never mint auth tokens'))
    now = datetime.now(timezone.utc)
    await db.isolated_artifact_downloads.insert_many([
        {'_id': 'a' * 64, 'owner': OWNER, 'conversation_id': 'remote-convo',
         'expires_at': now + timedelta(days=1), 'metadata': {'size': 1024}},
        {'_id': 'b' * 64, 'owner': OWNER, 'conversation_id': 'remote-convo',
         'expires_at': now + timedelta(days=1), 'metadata': {'size': 64 * 1024 * 1024}},
        {'_id': 'c' * 64, 'owner': OWNER, 'conversation_id': 'remote-convo',
         'expires_at': now - timedelta(days=1), 'metadata': {'size': 10}},
        {'_id': 'd' * 64, 'owner': 'other@example.test', 'conversation_id': 'remote-convo',
         'expires_at': now + timedelta(days=1), 'metadata': {'size': 10}}])
    deployment = synthetic_deployment(tmp_path)
    monkeypatch.setattr(ep, 'load_deployment', lambda: deployment)
    captured = {}

    async def fake_stream_run(**kwargs):
        captured.update(kwargs)
        from agent.active_streams import get_for_user
        assert await get_for_user('remote-convo', OWNER) is not None
        yield 'Hello '
        yield 'world'
        yield {'type': 'file_artifact', 'file': {'name': 'out.txt'}}

    monkeypatch.setattr(ep, 'stream_run', fake_stream_run)
    observer = Observer(db)
    events = [e async for e in ep.remote_stream_agent(
        'Current message', observer=observer, user_email=OWNER, source='dashboard',
        include_steps=True, selected_model='opencode-go/test-chat-model',
        files=[{'type': 'text', 'name': 'notes.txt', 'data': 'attached'}])]
    assert events[:2] == ['Hello ', 'world'] and events[2]['type'] == 'file_artifact'
    assert captured['runtime'] == 'opencode' and captured['owner'] == OWNER
    assert captured['grant'].endpoint == deployment.chat_endpoint
    assert captured['budget_spec'].account_id == 'deployment-chat-endpoint'
    assert captured['subscription_accounts'] is None
    assert captured['allowed_tools'] == ep.FULL_TOOLS
    assert captured['input_ids'] == ('a' * 64,)
    assert [a.data for a in captured['attachments']] == [b'attached']
    assert 'Personal Tools Auth Token' not in captured['instructions']
    assert captured['url'] == deployment.url and captured['tls'] is deployment.tls
    assert await captured['check_access'](RunAuthority('test', OWNER, frozenset())) is True
    assert await captured['check_access'](RunAuthority('test', 'other@x', frozenset())) is False
    observer.record_text.assert_awaited_once_with(1, 'Hello world')
    observer.finish.assert_awaited_once_with(final_response='Hello world')
    observer.record_artifact.assert_awaited_once()
    from agent.active_streams import get_for_user
    assert await get_for_user('remote-convo', OWNER) is None


@pytest.mark.asyncio
async def test_remote_run_interrupt_marks_interrupted(monkeypatch, db, tmp_path):
    monkeypatch.setattr(ep, 'load_deployment', lambda: synthetic_deployment(tmp_path))

    async def fake_stream_run(**kwargs):
        yield 'partial'
        await kwargs['cancelled'].wait()
        raise asyncio.CancelledError()

    monkeypatch.setattr(ep, 'stream_run', fake_stream_run)
    observer = Observer(db)
    stream = ep.remote_stream_agent('hi', observer=observer, user_email=OWNER,
        selected_model='opencode-go/test-chat-model')
    assert await anext(stream) == 'partial'
    from agent.active_streams import get_for_user
    handle = await get_for_user('remote-convo', OWNER)
    await handle.client.interrupt()
    remaining = [e async for e in stream]
    assert remaining == []
    observer.mark_interrupted.assert_awaited_once()
    observer.record_text.assert_awaited_once_with(1, 'partial')
    observer.record_error.assert_not_awaited()
    assert await get_for_user('remote-convo', OWNER) is None


@pytest.mark.asyncio
async def test_remote_run_surfaces_denial_without_fallback(monkeypatch, db, tmp_path):
    monkeypatch.setattr(ep, 'load_deployment', lambda: synthetic_deployment(tmp_path))

    async def fake_stream_run(**kwargs):
        raise ModelDenied('No authorized subscription account is available')
        yield  # pragma: no cover

    monkeypatch.setattr(ep, 'stream_run', fake_stream_run)
    observer = Observer(db)
    events = [e async for e in ep.remote_stream_agent('hi', observer=observer,
        user_email=OWNER, selected_model='opencode-go/test-chat-model')]
    assert len(events) == 1 and ep.UNAVAILABLE.strip() in events[0]
    observer.record_error.assert_awaited_once()
    observer.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_check_requires_live_active_user(db):
    check = ep._owner_check(db, OWNER)
    active = RunAuthority('test', OWNER, frozenset())
    assert await check(active) is True
    assert await check(RunAuthority('test', 'missing@example.test', frozenset())) is False
    await db.users.update_one({'email': OWNER}, {'$set': {'status': 'suspended'}})
    assert await check(active) is False
    await db.users.update_one({'email': OWNER}, {'$set': {'status': 'active'}})


@pytest.mark.asyncio
async def test_selector_requires_configured_accounts(db, tmp_path):
    deployment = synthetic_deployment(tmp_path)
    with pytest.raises(dep.DeploymentError):
        ep._selector_for(db, deployment, 'claude')
    account = SubscriptionAccount('claude', 'a@example.test', Path('/srv/accounts/a'))
    configured = synthetic_deployment(tmp_path, claude_accounts=(account,))
    selector = ep._selector_for(db, configured, 'claude')
    assert ep._selector_for(db, configured, 'claude') is selector
    assert await selector.check_access(RunAuthority('test', OWNER, frozenset()), account)
    assert not await selector.check_access(RunAuthority('test', 'missing@example.test', frozenset()), account)
    assert selector.accounts == (account,)

@pytest.mark.asyncio
async def test_plain_text_consumers_never_receive_dict_events(monkeypatch, db, tmp_path):
    monkeypatch.setattr(ep, 'load_deployment', lambda: synthetic_deployment(tmp_path))

    async def fake_stream_run(**kwargs):
        yield 'text'
        yield {'type': 'file_artifact', 'file': {'name': 'out.txt'}}

    monkeypatch.setattr(ep, 'stream_run', fake_stream_run)
    observer = Observer(db)
    events = [e async for e in ep.remote_stream_agent('hi', observer=observer,
        user_email=OWNER, source='slack', include_steps=False,
        selected_model='opencode-go/test-chat-model')]
    assert events == ['text']
    observer.record_artifact.assert_awaited_once()


@pytest.mark.asyncio
async def test_mid_stream_injection_is_refused_clearly():
    handle = ep.RemoteRunHandle()
    with pytest.raises(RuntimeError, match='not supported'):
        await handle.query('another message')
    assert not handle.cancelled.is_set()
