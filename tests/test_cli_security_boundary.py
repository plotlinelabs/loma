"""Synthetic authorization and command-contract tests; no provider calls."""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest

from broker.operations import INTEGRATION_TOOLS, ToolInvoke
from broker.service import Denied
from broker.tool_policy import POLICY, prepare_argv
from tools import _integration_access as access
from tools import loma_skills

EMAIL = 'owner@example.test'


@pytest.mark.parametrize('argv', [[], ['--user-email', EMAIL],
    ['--user-email', EMAIL, '--auth-token'],
    ['--user-email', EMAIL, '--auth-token', 'sample', '--user-email=other'],
    ['--user-email=', '--auth-token=sample']])
def test_identity_must_be_complete_and_unambiguous(argv):
    with pytest.raises(access.IntegrationAccessDenied):
        access.split_identity(argv)


def test_identity_flags_can_be_anywhere():
    assert access.split_identity(['query', '--auth-token=sample', '--user-email', EMAIL, '--sql', 'select 1']) == (
        EMAIL, 'sample', ['query', '--sql', 'select 1'])


@pytest.fixture
def cli_db(monkeypatch):
    monkeypatch.setenv('OBSERVABILITY_MONGODB_URI', 'mongodb://example.test')
    monkeypatch.setattr(access, 'verify_user_auth_token', lambda token, email: token == 'sample')
    db = SimpleNamespace(users=SimpleNamespace(find_one=Mock(return_value={'status': 'active'})),
        integrations=SimpleNamespace(find_one=Mock(return_value={'status': 'active'})),
        teams=SimpleNamespace(find=Mock(return_value=[])))
    client = Mock()
    client.__getitem__ = Mock(return_value=db)
    constructor = Mock(return_value=client)
    monkeypatch.setattr('pymongo.MongoClient', constructor)
    return db, client, constructor


@pytest.mark.parametrize('status', [None, 'pending', 'rejected', 'disabled'])
def test_inactive_user_cannot_use_environment_key(cli_db, monkeypatch, status):
    db, client, _ = cli_db
    monkeypatch.setenv('POSTHOG_API_KEY', 'synthetic-placeholder')
    db.users.find_one.return_value = {'status': status}
    with pytest.raises(access.IntegrationAccessDenied):
        access.require_cli_access('posthog', EMAIL, 'sample')
    db.integrations.find_one.assert_not_called()
    client.close.assert_called_once()


def test_invalid_identity_does_not_connect_to_database(cli_db):
    _, _, constructor = cli_db
    with pytest.raises(access.IntegrationAccessDenied):
        access.require_cli_access('posthog', EMAIL, 'invalid')
    constructor.assert_not_called()


@pytest.mark.parametrize('doc', [None, {'status': 'inactive'},
    {'status': 'active', 'scope': 'personal', 'connected_by': 'other'},
    {'status': 'active', 'shared_with': {'mode': 'specific', 'users': ['other']}}])
def test_connection_restrictions(cli_db, doc):
    db, client, _ = cli_db
    db.integrations.find_one.return_value = doc
    with pytest.raises(access.IntegrationAccessDenied):
        access.require_cli_access('posthog', EMAIL, 'sample')
    client.close.assert_called_once()


def test_team_access_and_revocation_rechecked(cli_db):
    db, _, _ = cli_db
    db.integrations.find_one.return_value = {'status': 'active', 'shared_with': {'mode': 'specific', 'teams': ['t1']}}
    db.teams.find.return_value = [{'team_id': 't1'}]
    access.require_cli_access('posthog', EMAIL, 'sample')
    db.teams.find.return_value = []
    with pytest.raises(access.IntegrationAccessDenied):
        access.require_cli_access('posthog', EMAIL, 'sample')


def test_database_failure_denies_without_error_details(cli_db):
    db, client, _ = cli_db
    db.integrations.find_one.side_effect = RuntimeError('synthetic database failure')
    with pytest.raises(access.IntegrationAccessDenied, match='denied or unavailable'):
        access.require_cli_access('posthog', EMAIL, 'sample')
    client.close.assert_called_once()


@pytest.mark.parametrize('tool', sorted(INTEGRATION_TOOLS))
def test_every_direct_integration_entrypoint_authorizes_before_dispatch(tool):
    source = Path(__file__).resolve().parents[1] / 'tools' / f'{tool}.py'
    tree = ast.parse(source.read_text())
    block = next(n for n in tree.body if isinstance(n, ast.If) and '__name__' in ast.unparse(n.test))
    assert isinstance(block.body[0], ast.ImportFrom)
    assert block.body[0].module == '_integration_access'
    assert ast.unparse(block.body[1]).startswith('authorize_cli(')


@pytest.mark.parametrize('tool,argv', [
    ('loma_skills', ['import-dir', '--source', '/example']),
    ('google_drive', ['upload-file', '--file-path', '/example/private']),
    ('gmail', ['send-email', '--attachments=/example/private']),
    ('gmail', ['create-draft', '--html-body-file', '/example/private']),
    ('google_apps_script', ['update-content', '--code-file', '/example/private']),
    ('slack_user', ['upload-file', '--file', '/example/private']),
    ('notify', ['send', '--title', 'a', '--title', 'b']),
    ('notify', ['send', '--tit', 'a']),
    ('notify', ['send', '--title', '--unexpected']),
    ('notify', ['send', '--title', 'a\0b']),
    ('notify', ['send', 'extra']),
    ('diagrams', ['render']), ('pptx_creator', ['create']),
])
def test_unsafe_or_unreviewed_command_is_denied(tool, argv):
    with pytest.raises(Denied):
        prepare_argv(tool, argv, {})


def test_files_are_rewritten_only_in_file_parameters():
    mapped = prepare_argv('gmail', ['send-email', '--body', '/workspace/a',
        '--attachments=/workspace/a,/workspace/b'],
        {'/workspace/a': '/trusted/input-0', '/workspace/b': '/trusted/input-1'})
    assert mapped == ['send-email', '--body', '/workspace/a', '--attachments', '/trusted/input-0,/trusted/input-1']


def test_unused_upload_denied():
    with pytest.raises(Denied):
        prepare_argv('notify', ['list'], {'unused': 'unused'})


@pytest.mark.asyncio
async def test_denial_happens_before_files_or_subprocess(monkeypatch):
    spawn = AsyncMock()
    mkdir = Mock()
    monkeypatch.setattr('asyncio.create_subprocess_exec', spawn)
    monkeypatch.setattr('tempfile.mkdtemp', mkdir)
    with pytest.raises(Denied):
        await ToolInvoke().execute(None, EMAIL, 'loma_skills', {
            'argv': ['import-dir', '--source', '/example'], 'files': {'x': 'x'}})
    mkdir.assert_not_called()
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_skill_cli_auth_and_owner_filter(monkeypatch, capsys):
    db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value={'status': 'active'})))
    client = Mock()
    monkeypatch.setattr(loma_skills, '_connect_db', lambda: (client, db))
    monkeypatch.setattr(loma_skills, 'verify_user_auth_token', lambda token, email: token == 'sample')
    monkeypatch.setattr(loma_skills.skill_service, 'list_skills', AsyncMock(return_value=[
        {'slug': 'shared', 'scope': 'workspace'},
        {'slug': 'mine', 'scope': 'personal', 'created_by': EMAIL},
        {'slug': 'other', 'scope': 'personal', 'created_by': 'other'},
    ]))
    args = SimpleNamespace(command='list', user_email=EMAIL, auth_token='sample')
    assert await loma_skills._run(args) == 0
    assert [s['slug'] for s in json.loads(capsys.readouterr().out)['skills']] == ['shared', 'mine']
    args.auth_token = 'invalid'
    assert await loma_skills._run(args) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['get', 'dump', 'file', 'asset', 'update-file', 'delete-file'])
async def test_skill_owner_check_precedes_read_or_write(monkeypatch, capsys, command):
    db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value={'status': 'active'})),
        skills=SimpleNamespace(find_one=AsyncMock(return_value={'scope': 'personal', 'created_by': 'other'})))
    monkeypatch.setattr(loma_skills, '_connect_db', lambda: (Mock(), db))
    monkeypatch.setattr(loma_skills, 'verify_user_auth_token', lambda token, email: True)
    args = SimpleNamespace(command=command, user_email=EMAIL, auth_token='sample', slug='sample')
    assert await loma_skills._run(args) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 403


def test_requested_namespace_boundary_cannot_silently_downgrade(monkeypatch):
    from broker import worker
    monkeypatch.setenv('LOMA_WORKER_BWRAP', '1')
    monkeypatch.setattr(worker.shutil, 'which', lambda name: None)
    with pytest.raises(worker.WorkerIsolationError):
        worker.bwrap_available()


def test_sdk_launcher_obeys_namespace_policy(monkeypatch, tmp_path):
    from broker import worker
    monkeypatch.setattr(worker, '_worker_uid_gid', lambda: (None, None))
    monkeypatch.setenv('LOMA_WORKER_BWRAP', '1')
    monkeypatch.setattr(worker.shutil, 'which', lambda name: '/usr/bin/' + name)
    (tmp_path / 'tmp').mkdir(mode=0o700)
    tmp_path.chmod(0o700)
    launcher = worker.write_cli_launcher(tmp_path, '/usr/bin/example-cli', {'HOME': str(tmp_path)})
    text = launcher.read_text()
    assert "'bwrap'" in text and "'--unshare-pid'" in text
    assert "'--bind'" in text
    assert "'/opt'" not in text


def test_sdk_launcher_refuses_missing_privilege_drop(monkeypatch, tmp_path):
    from broker import worker
    monkeypatch.setattr(worker, '_worker_uid_gid', lambda: (12345, 12345))
    monkeypatch.setattr(worker.shutil, 'which', lambda name: None)
    with pytest.raises(worker.WorkerIsolationError):
        worker.write_cli_launcher(tmp_path, '/usr/bin/example-cli', {})


def test_configured_identity_does_not_fall_back_to_backend_user(monkeypatch):
    from broker import worker
    monkeypatch.setenv('LOMA_WORKER_UID', '12345')
    monkeypatch.setenv('LOMA_WORKER_GID', '12345')
    monkeypatch.setattr(worker.os, 'geteuid', lambda: 23456)
    with pytest.raises(worker.WorkerIsolationError):
        worker._worker_uid_gid()


@pytest.mark.parametrize('argv', [
    ['--auth-token', 'sample', 'list', '--user-email', EMAIL],
    ['--user-email', EMAIL, '--auth-token', 'sample', 'get', '--slug', 'sample'],
    ['get', '--slug', 'sample', '--user-email', EMAIL, '--auth-token', 'sample'],
    ['--auth-token', 'sample', 'update-file', '--slug', 'sample', '--path', 'SKILL.md',
     '--content-file', '/workspace/example', '--user-email', EMAIL],
])
def test_skill_real_parser_preserves_global_and_subcommand_identity(monkeypatch, argv):
    seen = []
    async def capture(args):
        seen.append(args)
        return 0
    monkeypatch.setattr(sys, 'argv', ['loma_skills.py', *argv])
    monkeypatch.setattr(loma_skills, '_run', capture)
    assert loma_skills.main() == 0
    assert seen[0].auth_token == 'sample' and seen[0].user_email == EMAIL


def test_ashby_identity_retained_after_command(monkeypatch):
    monkeypatch.setattr(access, 'require_cli_access', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['ashby.py', '--auth-token=sample', 'list-jobs', '--user-email', EMAIL])
    access.authorize_cli('ashby', preserve_identity=True)
    assert sys.argv == ['ashby.py', 'list-jobs', '--user-email', EMAIL, '--auth-token', 'sample']


def test_generated_shim_uploads_only_declared_file_arguments(monkeypatch, tmp_path, capsys):
    import runpy
    from broker.worker import populate_tool_shims
    populate_tool_shims(tmp_path, ['gmail'])
    attachment = tmp_path / 'attachment.txt'
    attachment.write_text('synthetic content')
    sibling = tmp_path.parent / (tmp_path.name + '-sibling')
    sibling.mkdir()
    outside = sibling / 'outside.txt'
    outside.write_text('not part of this workspace')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['gmail.py', '--auth-token=sample', 'send-email',
        '--body', str(attachment), '--attachments=' + str(attachment) + ',' + str(outside)])
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"stdout":"","exit_code":0}'
    def urlopen(request, **kwargs):
        captured.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    shim = runpy.run_path(str(tmp_path / 'tools/gmail.py'))
    assert shim['main']() == 0
    assert captured[0]['params']['files'] == {str(attachment): 'synthetic content'}


@pytest.mark.asyncio
async def test_tool_output_limit_is_enforced_while_reading(monkeypatch):
    from broker import operations
    import asyncio
    monkeypatch.setattr(operations, '_MAX_OUTPUT_BYTES', 32)
    stdout = asyncio.StreamReader()
    stdout.feed_data(b'x' * 33)
    stdout.feed_eof()
    stderr = asyncio.StreamReader()
    stderr.feed_eof()
    process = SimpleNamespace(stdout=stdout, stderr=stderr, wait=AsyncMock(return_value=0))
    with pytest.raises(operations.ToolOutputLimit):
        await operations._collect_tool_output(process)


@pytest.mark.asyncio
async def test_tool_cancellation_terminates_process_and_removes_uploads(monkeypatch):
    from broker import operations
    import asyncio
    process = SimpleNamespace(pid=12345, wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    kill = Mock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(operations, '_collect_tool_output', AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(operations.os, 'killpg', kill)
    monkeypatch.setattr('tools._auth_token.create_user_auth_token', lambda email: 'sample')
    with pytest.raises(asyncio.CancelledError):
        await operations.ToolInvoke().execute(None, EMAIL, 'google_drive', {
            'argv': ['upload-file', '--file-path', '/workspace/a.txt'],
            'files': {'/workspace/a.txt': 'test'},
        })
    kill.assert_called_once()
    process.wait.assert_awaited_once()
    args = spawn.call_args.args
    uploaded_path = args[args.index('--file-path') + 1]
    assert uploaded_path.endswith('.txt') and not Path(uploaded_path).exists()
    assert spawn.call_args.kwargs['start_new_session'] is True
