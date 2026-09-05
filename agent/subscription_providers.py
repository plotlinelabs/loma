"""Personal OpenCode providers using the existing backend subscription gateways.

Use standard bundled AI SDK adapters instead of auth plugins that need readable
OAuth files. Only the authenticated run owner's native CLI account is eligible;
there is no pool-account or organization fallback for these providers.
"""
import os
from pathlib import Path
import re


PROVIDERS = {
    'claude': {'id': 'loma-claude', 'name': 'Personal Claude via OpenCode',
               'directory_env': 'CLAUDE_USERS_DIR', 'default_directory': '/opt/claude-users',
               'filename': '.credentials.json', 'npm': '@ai-sdk/anthropic', 'suffix': '/v1'},
    'codex': {'id': 'loma-codex', 'name': 'Personal Codex via OpenCode',
              'directory_env': 'CODEX_USERS_DIR', 'default_directory': '/opt/codex-users',
              'filename': 'auth.json', 'npm': '@ai-sdk/openai', 'suffix': ''},
}


def personal_accounts(email):
    if not isinstance(email, str) or not re.fullmatch(r'[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+', email):
        return {}
    result = {}
    for provider, info in PROVIDERS.items():
        directory = Path(os.environ.get(info['directory_env'], info['default_directory'])) / email
        credential = directory / info['filename']
        # No token is loaded to generate the model picker. A symlink cannot
        # alias a different user's connected account.
        try:
            if directory.is_symlink() or credential.is_symlink() or not credential.is_file():
                continue
        except OSError:
            continue
        result[provider] = (directory, credential)
    return result


def model_ids(provider):
    if provider == 'claude':
        values = [os.environ.get('CLAUDE_MODEL', 'claude-opus-4-8')]
    else:
        from agent.codex_runtime import supported_codex_model_ids
        values = supported_codex_model_ids()
    return [value for value in dict.fromkeys(values)
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value)]


def model_catalog(email):
    models = []
    for provider in personal_accounts(email):
        info = PROVIDERS[provider]
        for model in model_ids(provider):
            models.append({'id': f'{info["id"]}/{model}', 'provider_id': info['id'],
                           'model_id': model, 'label': f'{info["name"]} · {model}',
                           'context_limit': None, 'supports_attachments': False,
                           'supports_reasoning': False, 'status': 'active', 'cost': {}})
    return models


def worker_providers(ctx):
    if not ctx or not getattr(ctx, 'capability', None) or not getattr(ctx, 'user_email', None):
        return {}
    from broker.controller import get_subscription_registry
    from broker.credential_files import protect_account_directory
    from broker.gateway import gateway_base_url
    accounts = personal_accounts(ctx.user_email)
    if not accounts:
        return {}
    registry = get_subscription_registry()
    result = {}
    for provider, (directory, credential) in accounts.items():
        info = PROVIDERS[provider]
        protect_account_directory(directory)
        token = registry.register(provider, credential, capability=ctx.capability)
        ctx.sub_proxy_tokens.append(token)
        result[info['id']] = {
            'name': info['name'], 'npm': info['npm'],
            'options': {'baseURL': f'{gateway_base_url()}/sub/{token}{info["suffix"]}',
                        'apiKey': token},
            'models': {model: {'name': model} for model in model_ids(provider)},
        }
    return result
