"""Static command coverage and public fetch checks; no live providers."""
import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from broker.tool_policy import POLICY, prepare_argv
from broker.service import Denied
from tools._public_download import PublicResolver, validate_url


@pytest.mark.parametrize('tool', ['apollo', 'dataroom', 'stitch'])
def test_every_dispatch_command_has_reviewed_schema(tool):
    tree = ast.parse((Path(__file__).parents[1] / 'tools' / f'{tool}.py').read_text())
    commands = {n.test.comparators[0].value for n in ast.walk(tree)
                if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name) and n.test.left.id == 'command'
                and len(n.test.comparators) == 1 and isinstance(n.test.comparators[0], ast.Constant)}
    assert commands == set(POLICY[tool])


NEW_COMMANDS = [(tool, command) for tool in ['apollo', 'dataroom', 'stitch', 'cdn_upload'] for command in POLICY[tool]]


@pytest.mark.parametrize('tool,command', NEW_COMMANDS)
def test_new_schemas_reject_unknown_flags(tool, command):
    with pytest.raises(Denied):
        prepare_argv(tool, [command, '--unreviewed', 'value'], {})


@pytest.mark.parametrize('tool,command', NEW_COMMANDS)
def test_each_declared_argument_is_accepted(tool, command):
    spec = POLICY[tool][command]
    argv = [command] + ['resource'] * spec.positionals
    files = {}
    for name in spec.values.split():
        argv += ['--' + name, 'value']
    for name in spec.switches.split():
        argv += ['--' + name]
    for name in (spec.files + ' ' + spec.outputs).split():
        path = '/workspace/' + name
        argv += ['--' + name, path]
        files[path] = '/request/' + name
    assert prepare_argv(tool, argv, files)


def test_repeats_allowed_only_for_reviewed_repeatable_fields():
    argv = ['search', '--title', 'A', '--title', 'B']
    assert prepare_argv('apollo', argv, {}) == argv
    with pytest.raises(Denied):
        prepare_argv('apollo', ['enrich', '--email', 'a', '--email', 'b'], {})


def test_cdn_never_accepts_backend_file_paths():
    with pytest.raises(Denied):
        prepare_argv('cdn_upload', ['upload', '--file', '/backend/private'], {})
    assert prepare_argv('cdn_upload', ['upload', '--file', '/workspace/image.png'],
                        {'/workspace/image.png': '/request/input.png'})[-1] == '/request/input.png'


@pytest.mark.parametrize('url', [
    'http://example.com', 'https://localhost:3100', 'https://127.0.0.1/',
    'https://169.254.169.254/', 'https://10.0.0.1/', 'https://[::1]/',
    'https://[::ffff:127.0.0.1]/', 'https://user:pass@example.com/',
    'https://example.com/#fragment', 'https://example.com\\@localhost/',
    'https://2130706433/', 'https://127.1/', 'https://0177.0.0.1/', 'https://１２７.０.０.１/',
    'file:///etc/passwd', 'https://example.com/\n', 'https://[fe80::1%25eth0]/',
])
def test_nonpublic_or_ambiguous_download_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize('addresses', [['127.0.0.1'], ['93.184.216.34', '10.0.0.1'], ['169.254.169.254'], ['::1'], []])
async def test_connector_uses_only_checked_dns_answers(addresses):
    resolver = PublicResolver()
    resolver.resolver = AsyncMock()
    resolver.resolver.resolve.return_value = [{'host': address} for address in addresses]
    with pytest.raises(ValueError):
        await resolver.resolve('example.test', 443)


@pytest.mark.asyncio
async def test_public_dns_answers_returned_without_reresolution():
    resolver = PublicResolver()
    resolver.resolver = AsyncMock()
    answers = [{'host': '93.184.216.34'}]
    resolver.resolver.resolve.return_value = answers
    assert await resolver.resolve('example.test', 443) is answers
    resolver.resolver.resolve.assert_awaited_once()


@pytest.mark.parametrize('argv', [['download', '--url', 'https://example.test'], ['download', '--output', '/workspace/output']])
def test_stitch_requires_explicit_url_and_request_owned_output(argv):
    with pytest.raises(Denied):
        prepare_argv('stitch', argv, {'/workspace/output': '/request/output'} if '--output' in argv else {})


@pytest.mark.asyncio
async def test_download_rejects_redirects_and_large_bodies(monkeypatch):
    from tools import _public_download as downloads
    class Content:
        async def iter_chunked(self, size):
            yield b'x' * 6
            yield b'y' * 6
    class Response:
        status = 302
        content_length = None
        content = Content()
        headers = {}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    response = Response()
    class Session:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['auto_decompress'] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def get(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            return response
    monkeypatch.setattr(downloads.aiohttp, 'ClientSession', Session)
    monkeypatch.setattr(downloads.aiohttp, 'TCPConnector', lambda **kwargs: None)
    monkeypatch.setattr(downloads, 'MAX_DOWNLOAD_BYTES', 10)
    with pytest.raises(ValueError, match='200'):
        await downloads.download_public('https://example.test')
    response.status = 200
    with pytest.raises(ValueError, match='size'):
        await downloads.download_public('https://example.test')
    response.content_length = 11
    with pytest.raises(ValueError, match='size'):
        await downloads.download_public('https://example.test')


def test_agreement_has_personal_identity_and_confined_io():
    from broker.operations import AUTH_TOOLS, UTILITY_TOOLS
    assert 'agreement_review' in AUTH_TOOLS
    assert 'agreement_review' not in UTILITY_TOOLS
    for command in ['download', 'annotate']:
        with pytest.raises(Denied):
            prepare_argv('agreement_review', [command], {})
    with pytest.raises(Denied):
        prepare_argv('agreement_review', ['read', '--file-path', '/backend/private.docx'], {})


def test_docx_archive_expansion_is_bounded(tmp_path):
    from zipfile import ZipFile, ZIP_DEFLATED
    from tools.agreement_review import _validate_docx
    path = tmp_path / 'oversized.docx'
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', b'x' * 20_000_001)
    with pytest.raises(ValueError, match='size limit'):
        _validate_docx(path)
