import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import subscription_providers as providers
from broker import controller
from broker.gateway import SubscriptionProxyRegistry


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    for provider, info in providers.PROVIDERS.items():
        root = tmp_path / provider
        monkeypatch.setenv(info['directory_env'], str(root))
        for email in ('owner@example.test', 'other@example.test'):
            directory = root / email
            directory.mkdir(parents=True)
            (directory / info['filename']).write_text(json.dumps({'synthetic_private_value': email}))
    return tmp_path


def test_provider_configs_contain_only_own_run_proxies(accounts, monkeypatch):
    registry = SubscriptionProxyRegistry()
    monkeypatch.setattr(controller, '_sub_registry', registry)
    ctx = SimpleNamespace(capability='test-run', user_email='owner@example.test', sub_proxy_tokens=[])
    configs = providers.worker_providers(ctx)
    assert set(configs) == {'loma-claude', 'loma-codex'}
    assert len(ctx.sub_proxy_tokens) == 2
    for token in ctx.sub_proxy_tokens:
        entry = registry.lookup(token)
        assert entry['capability'] == 'test-run'
        assert Path(entry['credentials_path']).parent.name == 'owner@example.test'
    assert 'synthetic_private_value' not in json.dumps(configs)
    assert 'other@example.test' not in json.dumps(configs)
    assert configs['loma-codex']['npm'] == '@ai-sdk/openai'


def test_missing_personal_account_does_not_use_pool(accounts):
    assert providers.model_catalog('missing@example.test') == []
    assert providers.worker_providers(SimpleNamespace(capability='test-run', user_email='missing@example.test')) == {}


@pytest.mark.parametrize('email', ['../other@example.test', '/other@example.test', None, ''])
def test_invalid_identity_cannot_select_account(accounts, email):
    assert providers.personal_accounts(email) == {}


def test_symlink_cannot_alias_another_persons_account(accounts):
    source = accounts / 'claude/owner@example.test'
    target = accounts / 'claude/alias@example.test'
    target.symlink_to(source)
    assert providers.personal_accounts('alias@example.test') == {}


def test_catalog_has_no_cross_user_cache(accounts):
    first = providers.model_catalog('owner@example.test')
    assert first
    first.clear()
    assert providers.model_catalog('owner@example.test')
    assert not providers.model_catalog('missing@example.test')


def test_unowned_runs_have_no_provider_configuration(accounts):
    assert providers.worker_providers(None) == {}
    assert providers.worker_providers(SimpleNamespace(capability=None)) == {}
