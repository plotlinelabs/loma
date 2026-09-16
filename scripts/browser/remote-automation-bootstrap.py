"""Synthetic utility transport for isolated browser integration only."""
import asyncio
import runpy
import ssl
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from dotenv import load_dotenv
load_dotenv(Path.cwd() / '.env', override=True)
import os
assert os.environ['OBSERVABILITY_DB_NAME'].startswith('loma_local_utility_')
assert os.environ['LOMA_ENABLE_SLACK'] == 'false'
assert os.environ['LOMA_ENABLE_SCHEDULER'] == 'false'
assert os.environ['LOMA_REMOTE_WORKERS'] == 'on'
assert os.environ['WEBHOOK_PORT'] == '13000'
from isolation import utility
from isolation.deployment import RemoteDeployment
from isolation.context import UtilityContext
from isolation.protocol import RunAuthority
utility.load_deployment = lambda: RemoteDeployment(
    url='https://synthetic.example.test', token='synthetic-test-control-token-00000000',
    tls=ssl.create_default_context(), artifact_root=Path('/tmp/pr191-utility-browser-artifacts'),
    claude_accounts=(), codex_accounts=(), chat_endpoint='https://synthetic.example.test/v1/chat/completions',
    chat_headers={'authorization': 'Bearer synthetic'}, default_model='test/model', budget_nusd=20_000_000_000)
async def stream(**kwargs):
    assert kwargs['utility'] is True and kwargs['allowed_tools'] == frozenset()
    auth=RunAuthority('browser-fixture',kwargs['owner'],frozenset())
    context=UtilityContext(kwargs['db'],auth,kwargs['conversation_id'],cancelled=kwargs['cancelled'],check_access=kwargs['check_access'])
    await context.load(kwargs['prompt'])
    assert context.history == ()
    yield '{"remote-utility-qa":"Engineering"}' if 'Categorize these skills' in kwargs['prompt'] else 'Remote utility title'
utility.stream_run=stream
from isolation import automation
async def provider_request(provider, method, path, payload=None):
    assert provider == 'github' and method == 'POST'
    assert path == '/repos/qa/remote-worker/issues/191/comments'
    assert payload == {'body':'QA approved comment'}
    return {'id':191001,'html_url':'https://example.test/qa-receipt'}
automation.request=provider_request
runpy.run_path('app.py',run_name='__main__')
