"""Typed read-only connector adapters for the worker gateway.

Every mapping test patches the connector; the subprocess cases use synthetic
scripts only. No personal account, provider, API key or database is touched.
"""
import sys
from unittest.mock import AsyncMock, patch

import pytest

from autonomy import connector
from isolation.catalog import CATALOG
from isolation.gateway import (FILE_SCHEMAS, GatewayDenied, READ_COMMANDS, READ_SCHEMAS,
                               ToolGateway, personal_read, validate_read)
from isolation.protocol import RunAuthority

ALICE = RunAuthority('run-1', 'alice@example.test', frozenset(READ_SCHEMAS))
OWNER = ALICE.user_email

# Exact argv expectations: flag spellings are pinned to the first-party CLIs.
EXPECTED = [
    ('gmail.search', {'query': 'invoice'}, 'gmail', ['search', '--query', 'invoice', '--limit', '5']),
    ('gmail.search', {'query': 'invoice', 'limit': 20}, 'gmail', ['search', '--query', 'invoice', '--limit', '20']),
    ('gmail.read', {'message_id': 'm1'}, 'gmail', ['read-email', '--message-id', 'm1']),
    ('gmail.inbox', {}, 'gmail', ['list-inbox', '--limit', '10']),
    ('gmail.inbox', {'query': 'is:unread', 'limit': 5}, 'gmail', ['list-inbox', '--query', 'is:unread', '--limit', '5']),
    ('calendar.list', {}, 'calendar', ['list-events', '--limit', '10']),
    ('calendar.list', {'limit': 25}, 'calendar', ['list-events', '--limit', '25']),
    ('calendar.search', {'query': 'standup'}, 'calendar', ['search', '--query', 'standup', '--limit', '10']),
    ('calendar.get', {'event_id': 'e1'}, 'calendar', ['get-event', '--event-id', 'e1']),
    ('drive.list', {}, 'drive', ['list-files', '--limit', '10']),
    ('drive.list', {'query': 'RFP'}, 'drive', ['list-files', '--query', 'RFP', '--limit', '10']),
    ('drive.search', {'query': 'contract', 'limit': 3}, 'drive', ['search', '--query', 'contract', '--limit', '3']),
    ('drive.read', {'file_id': 'f1'}, 'drive', ['read-file', '--file-id', 'f1']),
    ('docs.info', {'document_id': 'd1'}, 'docs', ['get-info', '--document-id', 'd1']),
    ('docs.read', {'document_id': 'd1'}, 'docs', ['read-doc', '--document-id', 'd1']),
    ('sheets.info', {'spreadsheet_id': 's1'}, 'sheets', ['get-info', '--spreadsheet-id', 's1']),
    ('sheets.tabs', {'spreadsheet_id': 's1'}, 'sheets', ['list-sheets', '--spreadsheet-id', 's1']),
    ('sheets.read', {'spreadsheet_id': 's1', 'range': 'Sheet1!A1:B2'}, 'sheets',
     ['read-range', '--spreadsheet-id', 's1', '--range', 'Sheet1!A1:B2']),
    ('slack.read', {'channel': 'C123'}, 'slack', ['read-channel', '--channel', 'C123', '--limit', '50']),
    ('slack.search', {'query': 'deploy', 'limit': 9}, 'slack', ['search', '--query', 'deploy', '--limit', '9']),
    ('notifications.list', {}, 'notify', ['list', '--limit', '20']),
    ('grain.search', {'query': 'quarterly review'}, 'grain', ['search', 'quarterly review']),
    ('grain.transcript', {'recording_id': 'r1'}, 'grain', ['transcript', 'r1']),
    ('grain.recent', {}, 'grain', ['recent', '7']),
    ('grain.recent', {'days': 30}, 'grain', ['recent', '30']),
    ('pylon.issue', {'issue_id': 'i1'}, 'pylon', ['issue', 'i1']),
    ('pylon.messages', {'issue_id': 'i1'}, 'pylon', ['messages', 'i1']),
    ('pylon.teams', {}, 'pylon', ['teams']),
    ('pylon.issues', {}, 'pylon', ['issues', '--days', '7']),
    ('pylon.issues', {'days': 14, 'state': 'new,on_hold', 'team_id': 't1'}, 'pylon',
     ['issues', '--days', '14', '--state', 'new,on_hold', '--team', 't1']),
    ('posthog.projects', {}, 'posthog', ['projects']),
    ('posthog.definitions', {'search': 'campaign'}, 'posthog', ['definitions', '--search', 'campaign', '--limit', '50']),
    ('posthog.events', {'event_name': 'signup', 'from': '2026-09-01', 'to': '2026-09-15'}, 'posthog',
     ['events', 'signup', '--from', '2026-09-01', '--to', '2026-09-15', '--limit', '50']),
    ('linear.velocity', {'month': '2026-08'}, 'linear', ['velocity', '--month', '2026-08']),
    ('linear.bucket_split', {'month': '2026-08'}, 'linear', ['bucket-split', '--month', '2026-08']),
]


def test_catalog_matches_gateway_schemas_exactly():
    by_name = {tool['name']: tool for tool in CATALOG}
    assert set(READ_SCHEMAS) == set(READ_COMMANDS)
    for tool, (required, optional) in READ_SCHEMAS.items():
        schema = by_name[tool]['input_schema']
        assert set(schema['properties']) == required | optional, tool
        assert set(schema['required']) == required, tool
        assert schema['additionalProperties'] is False, tool
    # File transport tools are granted separately, never described to the model.
    assert not set(FILE_SCHEMAS) & set(by_name)


def test_read_templates_only_reference_declared_arguments():
    for tool, (script, subcommand, template) in READ_COMMANDS.items():
        required, optional = READ_SCHEMAS[tool]
        assert script in connector.TOOLS, tool
        names = [name for _, name, _ in template]
        assert len(names) == len(set(names)), tool
        assert required | optional <= set(names), tool
        for _, name, default in template:
            if name in required:
                assert default is None, tool


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,arguments,script,command', EXPECTED)
async def test_adapters_build_exact_fixed_commands(tool, arguments, script, command):
    personal = AsyncMock(return_value={'ok': True})
    with patch('autonomy.connector.personal', personal):
        assert await personal_read(tool, arguments, OWNER) == {'ok': True}
    personal.assert_awaited_once_with(script, command, OWNER)


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,arguments', [
    ('gmail.search', {'query': 'q', 'auth_token': 'forged'}),
    ('gmail.search', {}),
    ('gmail.search', {'query': ''}),
    ('gmail.search', {'query': 'q', 'limit': 0}),
    ('gmail.search', {'query': 'q', 'limit': 51}),
    ('gmail.search', {'query': 'q', 'limit': True}),
    ('gmail.search', {'query': 'q', 'limit': '5'}),
    ('drive.read', {'file_id': 'a\x00b'}),
    ('drive.read', {'file_id': 'a\nb'}),
    ('drive.read', {'file_id': 'x' * 1001}),
    ('drive.read', {'file_id': 7}),
    ('sheets.read', {'spreadsheet_id': 's1'}),
    ('grain.search', {'query': '--text'}),
    ('grain.transcript', {'recording_id': ' -r'}),
    ('grain.recent', {'days': 91}),
    ('pylon.issue', {'issue_id': '--days'}),
    ('pylon.issues', {'days': 0}),
    ('posthog.events', {'event_name': '-signup'}),
    ('posthog.events', {'event_name': 'signup', 'from': '01-09-2026'}),
    ('posthog.events', {'event_name': 'signup', 'to': '2026-9-1'}),
    ('linear.velocity', {'month': '2026-08-01'}),
    ('linear.velocity', {'month': 'august'}),
    ('notifications.list', {'limit': -1}),
    ('slack.read', {'channel': 'C1', 'thread_ts': '123.456'}),
])
async def test_invalid_read_arguments_never_reach_the_connector(tool, arguments):
    with pytest.raises(GatewayDenied):
        validate_read(tool, arguments)
    personal = AsyncMock()
    with patch('autonomy.connector.personal', personal):
        with pytest.raises(GatewayDenied):
            await personal_read(tool, arguments, OWNER)
    personal.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_dispatches_reads_and_audits(tmp_path):
    from isolation.artifacts import ArtifactScope
    scope = ArtifactScope(tmp_path / 'store', ALICE, 'conversation-1')
    try:
        read = AsyncMock(return_value={'issues': []})
        audit = AsyncMock()
        gate = ToolGateway(ALICE, authorize=AsyncMock(return_value=True), audit=audit,
                           artifacts=scope, connector=read)
        assert await gate(ALICE, 'pylon.issues', {'days': 14}) == {'issues': []}
        read.assert_awaited_once_with('pylon.issues', {'days': 14}, OWNER)
        stages = [call.args[1]['stage'] for call in audit.await_args_list]
        assert stages == ['requested', 'completed']
        # Arguments failing typed validation are denied before dispatch.
        with pytest.raises(GatewayDenied):
            await gate(ALICE, 'pylon.issues', {'days': 'fourteen'})
        # A granted-but-unknown name and a known-but-ungranted tool fail closed.
        with pytest.raises(GatewayDenied):
            await gate(ALICE, 'pylon.delete', {})
        with pytest.raises(GatewayDenied):
            await gate(RunAuthority('run-9', OWNER, frozenset({'artifacts.list'})), 'pylon.issues', {})
        assert read.await_count == 1
    finally:
        scope.close()


@pytest.mark.parametrize('tool,shape', [
    ('drive', 'trailing'), ('docs', 'trailing'), ('sheets', 'trailing'),
    ('gmail', 'trailing'), ('calendar', 'trailing'),
    ('slack', 'global'), ('notify', 'global'),
    ('grain', 'service'), ('pylon', 'service'), ('posthog', 'service'), ('linear', 'service'),
])
def test_connector_argv_shapes(tool, shape):
    argv = connector.build_argv(tool, ['sub', '--flag', 'value'], OWNER, 'token-1')
    script = argv[argv.index('-I') + 1]
    assert script.endswith(connector.TOOLS[tool])
    tail = argv[argv.index(script) + 1:]
    if shape == 'service':
        assert tail == ['sub', '--flag', 'value']
        assert 'token-1' not in argv and OWNER not in argv
    elif shape == 'global':
        assert tail == ['--auth-token', 'token-1', '--user-email', OWNER, 'sub', '--flag', 'value']
    else:
        assert tail == ['--auth-token', 'token-1', 'sub', '--flag', 'value', '--user-email', OWNER]


@pytest.mark.asyncio
async def test_no_identity_token_minted_for_service_tools(tmp_path):
    script = tmp_path / 'synthetic.py'
    script.write_text('print(\'{"ok": true}\')')
    argv = [sys.executable, '-I', str(script)]
    with patch.object(connector, 'build_argv', return_value=argv) as build, \
         patch.object(connector, 'environment', return_value={}), \
         patch('tools._auth_token.create_user_auth_token', side_effect=AssertionError('minted')):
        assert await connector.personal('pylon', ['teams'], OWNER) == {'ok': True}
    assert build.call_args.args == ('pylon', ['teams'], OWNER, '')


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', ['', '  ', None, 7])
async def test_connector_requires_backend_owner(owner):
    with pytest.raises(ValueError):
        await connector.personal('pylon', ['teams'], owner)


@pytest.mark.asyncio
async def test_connector_rejects_non_dict_json(tmp_path):
    script = tmp_path / 'synthetic.py'
    script.write_text('print(\'["not", "a", "dict"]\')')
    with patch.object(connector, 'build_argv', return_value=[sys.executable, '-I', str(script)]), \
         patch.object(connector, 'environment', return_value={}), \
         patch('tools._auth_token.create_user_auth_token', return_value='synthetic'):
        with pytest.raises(ValueError):
            await connector.personal('gmail', [], OWNER)


@pytest.mark.asyncio
async def test_real_subprocess_receives_exact_service_argv(tmp_path):
    """Actual child process through the adapter path with a synthetic script."""
    recorder = tmp_path / 'recorder.py'
    recorder.write_text('import json, sys\nprint(json.dumps({"argv": sys.argv[1:]}))\n')
    with patch.object(connector, 'build_argv',
                      side_effect=lambda tool, command, owner, token: [sys.executable, '-I', str(recorder), *command]), \
         patch.object(connector, 'environment', return_value={}):
        with patch('autonomy.connector.personal', wraps=connector.personal) as spy:
            result = await personal_read('pylon.issues', {'days': 14}, OWNER)
    assert result == {'argv': ['issues', '--days', '14']}
    assert spy.await_args.args == ('pylon', ['issues', '--days', '14'], OWNER)
