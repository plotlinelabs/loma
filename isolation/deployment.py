"""Operator deployment configuration for the remote-worker entrypoint cutover.

Everything here is trusted operator ingress read from backend environment
variables: never worker frames, conversation content or per-request input.
`remote_workers_enabled()` is the single cutover switch. Enabling it routes
agent runs to the remote transport and disables local model CLIs; it never
adds a local fallback. Enable only after draining local runs and deploying a
dedicated supervisor host (see docs/security-containment/remote-workers.md).
Prices are pinned integers used for budget reservation and durable accounting;
unknown models charge conservative rates rather than running unpriced.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path

from isolation.accounts import SubscriptionAccount
from isolation.accounting import BudgetSpec
from isolation.client import transport_context, validate_origin
from isolation.models import ModelGrant

FLAG = 'LOMA_REMOTE_WORKERS'
PROTOCOLS = {'claude': 'messages', 'codex': 'responses', 'opencode': 'chat'}
ENDPOINTS = {
    'claude': 'https://api.anthropic.com/v1/messages',
    'codex': 'https://chatgpt.com/backend-api/codex/responses',
}
# (input, output, cache_read, cache_write) in integer nanodollars per token,
# matched by model-name prefix after lowercasing. Order matters: first match wins.
PRICES = (
    ('claude-opus', (15000, 75000, 1500, 18750)),
    ('claude-sonnet', (3000, 15000, 300, 3750)),
    ('claude-haiku', (1000, 5000, 100, 1250)),
    ('gpt-5', (1250, 10000, 125, 0)),
)
# Deliberately conservative: over-reserving is safe, running unpriced is not.
DEFAULT_PRICE = (20000, 100000, 20000, 25000)
GRANT_OUTPUT_TOKENS = 16384
GRANT_MAX_CALLS = 64
BUDGET_INPUT_CEILING = 300_000
BUDGET_OUTPUT_CEILING = 32768
BUDGET_MAX_CALLS = 128
DEFAULT_BUDGET_NUSD = 20_000_000_000  # $20 worst-case cap; actual usage settles lower.


class DeploymentError(ValueError):
    """Remote worker mode is enabled but not safely configured. Fail closed."""


def remote_workers_enabled():
    return os.environ.get(FLAG, '').strip().lower() == 'on'


def price_for(model):
    if not isinstance(model, str) or not model.strip():
        raise DeploymentError('A model name is required for pricing')
    name = model.strip().lower()
    for prefix, price in PRICES:
        if name.startswith(prefix):
            return price
    return DEFAULT_PRICE


@dataclass(frozen=True)
class RemoteDeployment:
    url: str
    token: str = field(repr=False)
    tls: object = field(repr=False)
    artifact_root: Path
    claude_accounts: tuple
    codex_accounts: tuple
    chat_endpoint: str | None
    chat_headers: dict = field(repr=False)
    default_model: str | None
    budget_nusd: int

    def accounts_for(self, runtime):
        return {'claude': self.claude_accounts, 'codex': self.codex_accounts}.get(runtime, ())


def _require(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise DeploymentError(f'{FLAG}=on requires {name}')
    return value


def _parse_accounts(runtime, raw):
    accounts = []
    for entry in filter(None, (part.strip() for part in raw.split(','))):
        email, separator, directory = entry.partition('=')
        if not separator or not email.strip() or not directory.strip():
            raise DeploymentError(f'Invalid {runtime} account entry; use email=/absolute/dir')
        try:
            capacity = int(os.environ.get('LOMA_REMOTE_ACCOUNT_CAPACITY', '1'))
            accounts.append(SubscriptionAccount(runtime, email.strip(), Path(directory.strip()), capacity))
        except ValueError as error:
            raise DeploymentError(f'Invalid {runtime} account entry: {error}') from None
    return tuple(accounts)


def load_deployment():
    """Build the trusted deployment contract, or raise DeploymentError.

    Never returns a partial configuration: a missing transport, TLS material or
    malformed account entry disables remote-mode work instead of degrading it.
    """
    url = _require('LOMA_WORKER_URL')
    try:
        validate_origin(url)
    except ValueError as error:
        raise DeploymentError(f'LOMA_WORKER_URL: {error}') from None
    token = _require('LOMA_WORKER_CONTROL_TOKEN')
    if len(token) < 32:
        raise DeploymentError('LOMA_WORKER_CONTROL_TOKEN must be at least 32 characters')
    ca, cert, key = (_require(name) for name in
                     ('LOMA_WORKER_TLS_CA', 'LOMA_WORKER_TLS_CERT', 'LOMA_WORKER_TLS_KEY'))
    try:
        tls = transport_context(url, ca, cert, key)
    except (OSError, ValueError) as error:
        raise DeploymentError(f'Worker TLS material is unusable: {error}') from None
    artifact_root = Path(_require('LOMA_WORKER_ARTIFACT_DIR'))
    if not artifact_root.is_absolute():
        raise DeploymentError('LOMA_WORKER_ARTIFACT_DIR must be an absolute path')
    chat_endpoint = os.environ.get('LOMA_REMOTE_CHAT_ENDPOINT', '').strip() or None
    chat_headers = {}
    if chat_endpoint:
        chat_key = _require('LOMA_REMOTE_CHAT_API_KEY')
        header = os.environ.get('LOMA_REMOTE_CHAT_API_KEY_HEADER', '').strip() or 'authorization'
        chat_headers = {header: chat_key if header.lower() != 'authorization' else 'Bearer ' + chat_key}
    budget_raw = os.environ.get('LOMA_REMOTE_RUN_BUDGET_NUSD', '').strip()
    try:
        budget_nusd = int(budget_raw) if budget_raw else DEFAULT_BUDGET_NUSD
    except ValueError:
        raise DeploymentError('LOMA_REMOTE_RUN_BUDGET_NUSD must be an integer') from None
    if not 1 <= budget_nusd <= 100_000_000_000:
        raise DeploymentError('LOMA_REMOTE_RUN_BUDGET_NUSD is out of range')
    deployment = RemoteDeployment(
        url=url, token=token, tls=tls, artifact_root=artifact_root,
        claude_accounts=_parse_accounts('claude', os.environ.get('LOMA_REMOTE_CLAUDE_ACCOUNTS', '')),
        codex_accounts=_parse_accounts('codex', os.environ.get('LOMA_REMOTE_CODEX_ACCOUNTS', '')),
        chat_endpoint=chat_endpoint, chat_headers=chat_headers,
        default_model=os.environ.get('LOMA_REMOTE_DEFAULT_MODEL', '').strip() or None,
        budget_nusd=budget_nusd)
    if not (deployment.claude_accounts or deployment.codex_accounts or deployment.chat_endpoint):
        raise DeploymentError('Remote mode needs at least one account list or a chat endpoint')
    return deployment


def build_grant(runtime, model, deployment):
    """Server-side model grant. Subscription runtimes get headers per call from
    the account selector; only the operator chat endpoint uses static headers."""
    if runtime in ENDPOINTS:
        endpoint, headers = ENDPOINTS[runtime], {}
    elif runtime == 'opencode':
        if not deployment.chat_endpoint:
            raise DeploymentError('LOMA_REMOTE_CHAT_ENDPOINT is not configured')
        endpoint, headers = deployment.chat_endpoint, deployment.chat_headers
    else:
        raise DeploymentError(f'Unsupported remote runtime: {runtime!r}')
    return ModelGrant(PROTOCOLS[runtime], endpoint, model, headers,
                      max_output_tokens=GRANT_OUTPUT_TOKENS, max_calls=GRANT_MAX_CALLS,
                      native_codex=runtime == 'codex', native_claude=runtime == 'claude')


def build_budget(runtime, model, deployment):
    """Pinned-rate budget. The subscription selector overwrites account_id for
    claude/codex before admission; the chat endpoint bills one fixed account."""
    input_rate, output_rate, cache_read, cache_write = price_for(model)
    spec = BudgetSpec(
        protocol=PROTOCOLS[runtime], model=model,
        account_id='pending-subscription' if runtime in ENDPOINTS else 'deployment-chat-endpoint',
        input_ceiling=BUDGET_INPUT_CEILING, output_ceiling=BUDGET_OUTPUT_CEILING,
        input_rate=input_rate, output_rate=output_rate,
        cache_read_rate=cache_read, cache_write_rate=cache_write,
        budget_nusd=deployment.budget_nusd, max_calls=BUDGET_MAX_CALLS)
    if spec.reservation > spec.budget_nusd:
        raise DeploymentError(
            'LOMA_REMOTE_RUN_BUDGET_NUSD is below the worst-case reservation '
            f'for {model} ({spec.reservation} nUSD); no call could ever be admitted')
    return spec
