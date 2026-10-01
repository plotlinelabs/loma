"""Pylon connector contract tests. All provider calls are mocked; no live writes."""
import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools import pylon


def run(coro):
    return asyncio.run(coro)


def test_messages_paginates_and_deduplicates():
    with patch.object(pylon, '_api_get', AsyncMock(side_effect=[
        {'data': [{'id': 'a'}], 'pagination': {'has_next_page': True, 'cursor': 'a&b'}},
        {'data': [{'id': 'a'}, {'id': 'b', 'file_urls': ['https://example.com/a']}]}])) as get:
        result = run(pylon.get_messages('ticket'))
    assert result['complete'] is True
    assert [r['id'] for r in result['data']] == ['a', 'b']
    assert get.call_args_list[1].args[0] == '/issues/ticket/messages?limit=100&cursor=a%26b'


@pytest.mark.parametrize('pagination', [
    {'has_next_page': True}, {'has_next_page': True, 'cursor': 'start'}])
def test_invalid_cursor_is_not_complete(pagination):
    fetch = AsyncMock(return_value={'data': [{'id': 'a'}], 'pagination': pagination})
    result = run(pylon._paginate(fetch, cursor='start'))
    assert result['complete'] is False
    assert 'error' in result


def test_page_limit_and_resume():
    fetch = AsyncMock(return_value={'data': [{'id': 'a'}],
        'pagination': {'has_next_page': True, 'cursor': 'next'}})
    result = run(pylon._paginate(fetch, max_pages=1))
    assert result['complete'] is False and result['next_cursor'] == 'next'
    assert 'warning' in result


def test_mid_page_error_retains_data_and_retry_cursor():
    fetch = AsyncMock(side_effect=[{'data': [{'id': 'a'}],
        'pagination': {'has_next_page': True, 'cursor': 'next'}}, {'error': 'rate limit'}])
    result = run(pylon._paginate(fetch))
    assert result['data'] == [{'id': 'a'}]
    assert result['error'] == 'rate limit'
    assert result['next_cursor'] == 'next'
    assert not result['complete']


@pytest.mark.parametrize('payload', [{}, {'data': {}}, {'data': ['bad']}])
def test_malformed_list(payload):
    assert 'error' in run(pylon._paginate(AsyncMock(return_value=payload)))


def test_nonpositive_pages_never_calls_provider():
    fetch = AsyncMock()
    assert 'error' in run(pylon._paginate(fetch, max_pages=0))
    fetch.assert_not_called()


def test_search_contract_and_all_time():
    with patch.object(pylon, '_api_post', AsyncMock(return_value={'data': []})) as post:
        result = run(pylon.list_issues(days=0, state='new, closed', team_id='team',
            account_id='account', requester_id='contact', assignee_id='owner', query='invoice'))
    body = post.call_args.args[1]
    assert body['search_text'] == 'invoice'
    assert body['filter']['operator'] == 'and'
    filters = {f['field']: f for f in body['filter']['subfilters']}
    assert 'created_at' not in filters
    assert filters['state']['values'] == ['new', 'closed']
    assert filters['account_id']['value'] == 'account'
    assert filters['requester_id']['value'] == 'contact'
    assert filters['assignee_id']['value'] == 'owner'
    assert result['period'] == 'all time'


@pytest.mark.parametrize('kwargs', [{'days': -1}, {'limit': 0}, {'limit': 1000}])
def test_invalid_search(kwargs):
    with patch.object(pylon, '_api_post', AsyncMock()) as post:
        assert 'error' in run(pylon.list_issues(**kwargs))
        post.assert_not_called()


def test_search_partial_summary():
    with patch.object(pylon, '_api_post', AsyncMock(side_effect=[
        {'data': [{'id': 'a', 'account': {'id': 'account', 'name': 'Example'}}],
         'pagination': {'has_next_page': True, 'cursor': 'next'}}, {'error': 'failed'}])):
        result = run(pylon.list_issues())
    assert result['issues'][0]['account_id'] == 'account'
    assert not result['complete'] and result['error'] == 'failed'


@pytest.mark.parametrize('func,path', [(pylon.get_users, '/users'),
    (pylon.get_teams, '/teams'), (lambda: pylon.get_threads('ticket'), '/issues/ticket/threads')])
def test_directory_reads(func, path):
    with patch.object(pylon, '_api_get', AsyncMock(return_value={'data': []})) as get:
        assert run(func())['complete']
    get.assert_awaited_once_with(path)


def test_assign_and_unassign_preserves_fields():
    with patch.object(pylon, '_api_patch', AsyncMock(return_value={'data': {}})) as patch_api:
        run(pylon.update_issue('ticket', team_id='ar', assignee_id=''))
    patch_api.assert_awaited_once_with('/issues/ticket', {'team_id': 'ar', 'assignee_id': ''})


def test_empty_update_does_not_write():
    with patch.object(pylon, '_api_patch', AsyncMock()) as patch_api:
        assert 'error' in run(pylon.update_issue('ticket'))
        patch_api.assert_not_called()


@pytest.mark.parametrize('url', ['http://example.com', 'https://localhost:123/a',
    'https://127.0.0.1/a', 'https://169.254.169.254/a', 'https://[::1]/a',
    'https://user:password@example.com/a', 'file:///etc/passwd'])
def test_unsafe_attachment_urls(url):
    with pytest.raises(ValueError):
        pylon._validate_attachment_url(url)


def test_public_dns_only():
    async def check():
        resolver = pylon._PublicResolver()
        try:
            with patch.object(pylon.aiohttp.resolver.DefaultResolver, 'resolve', AsyncMock(
                    return_value=[{'host': '10.0.0.1'}])):
                with pytest.raises(ValueError):
                    await resolver.resolve('example.com', 443, socket.AF_INET)
        finally:
            await resolver.close()
    run(check())


def fake_session(responses):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    contexts = []
    for response in responses:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=response)
        ctx.__aexit__ = AsyncMock(return_value=False)
        contexts.append(ctx)
    session.get.side_effect = contexts
    return session


def response(chunks=(b'PDF',), status=200, location=None, length=None):
    resp = MagicMock(status=status, headers={'Location': location} if location else {},
                     content_length=length)
    async def stream(_):
        for chunk in chunks:
            yield chunk
    resp.content.iter_chunked = stream
    return resp


def download(tmp_path, responses, **kwargs):
    session = fake_session(responses)
    with patch.object(pylon, 'get_messages', AsyncMock(return_value={
        'complete': True, 'data': [{'id': 'msg', 'file_urls': ['https://example.com/file?secret=x']}]})), \
         patch.object(pylon.aiohttp, 'ClientSession', return_value=session):
        result = run(pylon.download_attachment('issue', 'msg', 0, str(tmp_path / 'file.pdf'), **kwargs))
    return result, session


def test_download_success_no_credentials(tmp_path):
    result, session = download(tmp_path, [response()])
    assert result['bytes'] == 3
    assert (tmp_path / 'file.pdf').read_bytes() == b'PDF'
    assert 'headers' not in session.get.call_args.kwargs
    assert session.get.call_args.kwargs['allow_redirects'] is False


@pytest.mark.parametrize('resp', [response(length=100), response(chunks=(b'123', b'456'))])
def test_download_size_limit_cleans_partial_file(tmp_path, resp):
    result, _ = download(tmp_path, [resp], max_bytes=4)
    assert 'error' in result
    assert not (tmp_path / 'file.pdf').exists()
    assert 'secret' not in str(result)


def test_download_existing_file_untouched(tmp_path):
    (tmp_path / 'file.pdf').write_bytes(b'original')
    result, _ = download(tmp_path, [response()])
    assert 'error' in result
    assert (tmp_path / 'file.pdf').read_bytes() == b'original'


def test_redirect_to_private_address_rejected(tmp_path):
    result, session = download(tmp_path, [response(status=302, location='https://127.0.0.1/')])
    assert 'error' in result
    assert session.get.call_count == 1


def test_public_redirect(tmp_path):
    result, session = download(tmp_path, [response(status=302, location='/new'), response()])
    assert result['bytes'] == 3
    assert session.get.call_args.args[0] == 'https://example.com/new'


@pytest.mark.parametrize('history', [{'complete': False}, {'complete': True, 'data': []}])
def test_download_requires_complete_history_and_matching_message(tmp_path, history):
    with patch.object(pylon, 'get_messages', AsyncMock(return_value=history)), \
         patch.object(pylon.aiohttp, 'ClientSession') as session:
        assert 'error' in run(pylon.download_attachment('issue', 'msg', 0, str(tmp_path / 'file')))
        session.assert_not_called()


def test_timeout_returns_structured_error():
    session = fake_session([])
    session.get.side_effect = asyncio.TimeoutError()
    with patch.object(pylon.aiohttp, 'ClientSession', return_value=session), \
         patch.object(pylon, '_headers', return_value={}):
        assert 'error' in run(pylon._api_get('/users'))


def cli(args, capsys, response_data=None):
    """Exercise the actual CLI without making network calls."""
    import runpy
    from pathlib import Path
    calls = []
    def execute(coro):
        calls.append(dict(coro.cr_frame.f_locals))
        coro.close()
        return response_data if response_data is not None else {'data': {}}
    with patch('sys.argv', ['pylon.py', *args]), patch('asyncio.run', side_effect=execute):
        try:
            runpy.run_path(str(Path(pylon.__file__)), run_name='__main__')
            code = 0
        except SystemExit as exc:
            code = exc.code
    return code, calls, capsys.readouterr().out


def test_cli_update_fields(capsys):
    code, calls, _ = cli(['update', 'issue', '--team', 'ar', '--assignee', ''], capsys)
    assert code == 0
    assert calls[0] == {'issue_id': 'issue', 'fields': {'team_id': 'ar', 'assignee_id': ''}}


def test_cli_legacy_status(capsys):
    code, calls, _ = cli(['update', 'issue', '--status', 'closed'], capsys)
    assert code == 0 and calls[0]['fields'] == {'state': 'closed'}


@pytest.mark.parametrize('args', [
    ['update', 'issue', '--team'], ['update', 'issue', '--team', '--assignee', 'owner'],
    ['update', 'issue', '--unknown', 'x']])
def test_cli_invalid_update_never_calls_api(capsys, args):
    code, calls, _ = cli(args, capsys)
    assert code == 1 and not calls


def test_cli_search_flags(capsys):
    code, calls, _ = cli(['issues', '--days', '0', '--account', 'a', '--requester', 'r',
        '--query', 'INV-123', '--cursor', 'next', '--max-pages', '2'], capsys)
    assert code == 0
    assert calls[0]['days'] == 0 and calls[0]['account_id'] == 'a'
    assert calls[0]['requester_id'] == 'r' and calls[0]['query'] == 'INV-123'
    assert calls[0]['cursor'] == 'next' and calls[0]['max_pages'] == 2


@pytest.mark.parametrize('response_data', [{'error': 'failed'}, {'complete': False, 'data': []}])
def test_cli_incomplete_returns_nonzero(capsys, response_data):
    code, _, output = cli(['messages', 'issue'], capsys, response_data)
    assert code == 1
