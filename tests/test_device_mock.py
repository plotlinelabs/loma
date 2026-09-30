"""Device-mock proxy: token scoping, upstream allowlist, merge patch, asset rules, TTL, isolation."""
import asyncio
import json
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, make_mocked_request
from mongomock_motor import AsyncMongoMockClient

from api import device_mock_routes as routes
from device_mock import core, store
from tools import device_mock as cli

OWNER = 'owner@example.com'
OTHER = 'other@example.com'
IMG = 'https://cdn.plotline.so/media/p/banner.png'
INIT = {'status': 'ok', 'data': {'productId': 'p', 'flows': [{'id': 'f1'}],
                                 'widgets': [{'widgetId': 'w1', 'image': IMG, 'widgetLoaderUrl': ''},
                                             {'widgetId': 'w2', 'font': 'https://cdn.plotline.so/f.ttf'}]}}


# ── Pure logic ────────────────────────────────────────────────────────────


@pytest.mark.parametrize('target,patch_,expected', [  # RFC 7396 appendix A
    ({'a': 'b'}, {'a': 'c'}, {'a': 'c'}),
    ({'a': 'b'}, {'b': 'c'}, {'a': 'b', 'b': 'c'}),
    ({'a': 'b'}, {'a': None}, {}),
    ({'a': 'b', 'b': 'c'}, {'a': None}, {'b': 'c'}),
    ({'a': ['b']}, {'a': 'c'}, {'a': 'c'}),
    ({'a': 'c'}, {'a': ['b']}, {'a': ['b']}),
    ({'a': {'b': 'c'}}, {'a': {'b': 'd', 'c': None}}, {'a': {'b': 'd'}}),
    ({'a': [{'b': 'c'}]}, {'a': [1]}, {'a': [1]}),
    ({'e': None}, {'a': 1}, {'e': None, 'a': 1}),
    ([1, 2], {'a': 'b', 'c': None}, {'a': 'b'}),
    ({}, {'a': {'bb': {'ccc': None}}}, {'a': {'bb': {}}}),
])
def test_merge_patch_rfc7396(target, patch_, expected):
    assert core.merge_patch(target, patch_) == expected


def test_presets_cover_skeleton_scenarios():
    presets = core.load_presets()
    assert {'absent', 'shimmer', 'lottie', 'failure', 'timeout'} <= set(presets)
    base = 'https://loma.example/device-mock/t/__loma_asset?u='
    hosts = {'cdn.plotline.so'}
    absent = core.apply_init({'data': {'widgetSkeletonSettings': {'enabled': True}, 'widgets': []}},
                             presets['absent'], hosts, base)
    assert 'widgetSkeletonSettings' not in absent['data']
    shimmer = core.apply_init(INIT, presets['shimmer'], hosts, base)
    assert shimmer['data']['widgetSkeletonSettings']['mode'] == 'shimmer'
    assert shimmer['data']['flows'] == []
    assert shimmer['data']['widgets'][0]['image'].startswith(base)          # image rewritten
    assert shimmer['data']['widgets'][1]['font'] == 'https://cdn.plotline.so/f.ttf'  # font untouched
    lottie = core.apply_init(INIT, presets['lottie'], hosts, base)
    assert lottie['data']['widgets'][0]['widgetLoaderUrl'] == lottie['data']['widgetSkeletonSettings']['lottieUrl']
    # Mirrors ApplyProductWidgetLottie: widgets with a missing/empty/default loader get the product Lottie.
    assert lottie['data']['widgets'][1]['widgetLoaderUrl'] == lottie['data']['widgetSkeletonSettings']['lottieUrl']
    assert presets['failure']['rules'][0]['status'] == 404
    assert presets['timeout']['rules'][0]['delay_ms'] > 10_000
    assert INIT['data']['flows'] == [{'id': 'f1'}]  # input never mutated


def test_rules_glob_and_regex():
    rules = [core._rule({'match': '*/banner.png', 'delay_ms': 5}), core._rule({'match': 're:\\.gif$', 'status': 404}),
             core._rule({'match': '/sdk/init', 'kind': 'api', 'status': 503})]
    assert core.first_rule(rules, 'asset', IMG)['delay_ms'] == 5
    assert core.first_rule(rules, 'asset', 'https://cdn.plotline.so/x.gif')['status'] == 404
    assert core.first_rule(rules, 'asset', 'https://cdn.plotline.so/x.jpg') is None
    assert core.first_rule(rules, 'api', '/sdk/init')['status'] == 503
    for bad in [{'match': 'x', 'delay_ms': 10 ** 9}, {'match': 'x', 'status': 99}, {'match': 're:('},
                {'match': 'x', 'kind': 'other'}, {'match': 'x', 'evil': 1}, {'match': ''}]:
        with pytest.raises(ValueError):
            core._rule(bad)


def test_compose_patch_file_on_top_of_preset():
    presets = core.load_presets()
    s = core.compose_scenario(presets, 'shimmer', init_patch={'data': {'widgetSkeletonSettings': {'baseColor': '#DD2222'}}})
    out = core.apply_init({'data': {}}, s, set(), '')
    assert out['data']['widgetSkeletonSettings']['baseColor'] == '#DD2222'
    assert out['data']['widgetSkeletonSettings']['mode'] == 'shimmer'
    with pytest.raises(ValueError, match='unknown preset'):
        core.compose_scenario(presets, 'nope')
    with pytest.raises(ValueError, match='unknown scenario keys'):
        core.compose_scenario(presets, scenario={'upstream': 'https://evil.example'})


def test_header_filtering():
    from multidict import CIMultiDict
    h = CIMultiDict({'Cookie': 'authjs.session-token=x', 'X-User-Email': 'a@b', 'Connection': 'keep-alive, X-Foo',
                     'X-Foo': '1', 'Host': 'loma', 'X-Forwarded-For': '1.2.3.4', 'CF-Access-Jwt-Assertion': 'j',
                     'Transfer-Encoding': 'chunked', 'ref-id': 'r1', 'Authorization': 'Bearer sdk', 'X-Loma-Auth-Token': 't'})
    assert core.request_headers(h) == {'ref-id': 'r1', 'Authorization': 'Bearer sdk'}
    r = CIMultiDict({'Set-Cookie': 'a=b', 'Content-Type': 'application/json', 'Content-Encoding': 'gzip', 'X-Up': '1'})
    assert core.response_headers(r) == [('Content-Type', 'application/json'), ('X-Up', '1')]


def test_upstream_allowlist_parsing(monkeypatch):
    monkeypatch.delenv('LOMA_DEVICE_MOCK_UPSTREAMS', raising=False)
    assert core.upstream_allowlist() == ['https://api.plotline.so']
    monkeypatch.setenv('LOMA_DEVICE_MOCK_UPSTREAMS', 'api.plotline.so, https://staging.plotline.so/, http://u:p@x, https://a/b')
    assert core.upstream_allowlist() == ['https://api.plotline.so', 'https://staging.plotline.so']


def test_cli_build_body(tmp_path):
    base = ['--user-email', OWNER, '--auth-token', 't', '--scope', 'c1']
    assert cli.build_body(cli.parser().parse_args(base + ['create'])) == {
        'scope': 'conv:c1', 'action': 'create', 'upstream': None, 'ttl_hours': 6, 'label': None}
    patch_file = tmp_path / 'red.json'
    patch_file.write_text(json.dumps({'data': {'x': 1}}))
    args = base + ['set-scenario', '--session-id', 'dm_1', '--patch-file', str(patch_file)]
    body = cli.build_body(cli.parser().parse_args(args))
    assert body == {'scope': 'conv:c1', 'session_id': 'dm_1', 'action': 'set_scenario', 'preset': None,
                    'name': 'red', 'init_patch': {'data': {'x': 1}}}
    with pytest.raises(SystemExit):
        cli.build_body(cli.parser().parse_args(base + ['set-scenario', '--session-id', 'dm_1']))


def test_control_plane_is_loopback_only(monkeypatch):
    monkeypatch.setenv('OAUTH_ENCRYPTION_KEY', 'k')
    transport = MagicMock()
    transport.get_extra_info.side_effect = lambda name, default=None: ('10.0.0.5', 1) if name == 'peername' else default
    request = make_mocked_request('POST', '/internal/device-mock/call', transport=transport)
    with pytest.raises(web.HTTPForbidden):
        routes._internal_identity(request)


@pytest.mark.asyncio
async def test_log_is_a_ring_buffer():
    db = AsyncMongoMockClient()['dm_ring']
    doc, _ = await store.create(db, OWNER, 'conv:a', 'https://api.plotline.so', 'https://loma')
    for i in range(store.LOG_SIZE + 25):
        await store.append_log(db, doc['session_id'], {'i': i})
    log = (await db[store.COLLECTION].find_one({'session_id': doc['session_id']}))['log']
    assert len(log) == store.LOG_SIZE and log[0]['i'] == 25 and log[-1]['i'] == store.LOG_SIZE + 24


# ── End to end: stub upstream <- Loma routes <- client ────────────────────


class Stack:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.seen = []

    async def __aenter__(self):
        async def upstream(request):
            body = await request.read()
            self.seen.append({'path': request.path_qs, 'method': request.method, 'headers': dict(request.headers),
                              'body': body, 'host': request.host})
            if request.path == '/sdk/big':
                return web.json_response({'data': {'blob': 'x' * 3_000_000}})
            if request.path == '/sdk/init':
                return web.json_response(INIT, headers={'Set-Cookie': 'up=1'})
            return web.json_response({'echo': request.path_qs})

        up_app = web.Application()
        up_app.router.add_route('*', '/{tail:.*}', upstream)
        self.upstream = TestServer(up_app)
        await self.upstream.start_server()
        self.upstream_origin = f'http://127.0.0.1:{self.upstream.port}'
        self.monkeypatch.setenv('LOMA_DEVICE_MOCK_UPSTREAMS', self.upstream_origin)
        self.monkeypatch.setenv('LOMA_DEVICE_MOCK_ASSET_HOSTS', 'cdn.plotline.so')
        self.monkeypatch.setenv('OAUTH_ENCRYPTION_KEY', 'test-key')
        self.monkeypatch.setenv('LOMA_DEVICE_MOCK_BASE_URL', 'https://loma.example')
        self.db = AsyncMongoMockClient()['dm_e2e']
        self.patcher = patch('api.device_mock_routes.get_db', return_value=self.db)
        self.patcher.start()
        routes.limiter.hits.clear()
        app = web.Application()
        routes.setup_device_mock_routes(app)
        self.loma = TestServer(app)
        await self.loma.start_server()
        self.http = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        await self.http.close()
        await self.loma.close()
        await self.upstream.close()
        self.patcher.stop()

    async def control(self, body, user=OWNER, token_user=None):
        from tools._auth_token import create_user_auth_token
        headers = {'X-Loma-User': user, 'X-Loma-Auth-Token': create_user_auth_token(token_user or user)}
        async with self.http.post(self.loma.make_url('/internal/device-mock/call'), json=body, headers=headers) as r:
            return r.status, await r.json()

    async def create(self, scope='conv:a', user=OWNER, **extra):
        status, view = await self.control({'action': 'create', 'scope': scope, **extra}, user=user)
        assert status == 200, view
        view['token'] = view['base_url'].rsplit('/', 1)[1]
        view['path'] = '/device-mock/' + view['token']
        return view

    async def call(self, path, method='POST', **kw):
        async with self.http.request(method, self.loma.make_url(path), allow_redirects=False, **kw) as r:
            return r.status, r.headers, await r.read()


@pytest.mark.asyncio
async def test_init_is_patched_and_logged(monkeypatch):
    async with Stack(monkeypatch) as s:
        sess = await s.create()
        assert sess['base_url'].startswith('https://loma.example/device-mock/dmt_')
        status, body = await s.control({'action': 'set_scenario', 'scope': 'conv:a', 'session_id': sess['session_id'],
                                        'preset': 'shimmer'})
        assert status == 200 and body['scenario_version'] == 1
        status, headers, raw = await s.call(sess['path'] + '/sdk/init?x=1', json={'oldResponseBody': {'big': 1}, 'k': 2},
                                            headers={'Cookie': 'authjs.session-token=secret', 'X-User-Email': 'spoof@x',
                                                     'ref-id': 'Loma-e2e'})
        assert status == 200
        data = json.loads(raw)['data']
        assert data['widgetSkeletonSettings']['enabled'] is True and data['flows'] == []
        assert data['widgets'][0]['image'].startswith(f"https://loma.example{sess['path']}/__loma_asset?v=1&u=")
        assert 'Set-Cookie' not in headers and headers['Cache-Control'] == 'no-store'
        assert "sandbox" in headers['Content-Security-Policy']
        sent = s.seen[-1]
        assert sent['path'] == '/sdk/init?x=1' and json.loads(sent['body']) == {'k': 2}   # oldResponseBody stripped
        assert 'Cookie' not in sent['headers'] and 'X-User-Email' not in sent['headers']
        assert sent['headers']['ref-id'] == 'Loma-e2e'
        # Multi-MB bodies arrive whole.
        status, _, raw = await s.call(sess['path'] + '/sdk/big', method='GET')
        assert status == 200 and len(json.loads(raw)['data']['blob']) == 3_000_000
        # Non-init paths pass through untouched.
        status, _, raw = await s.call(sess['path'] + '/sdk/events?a=b', method='GET')
        assert status == 200 and json.loads(raw) == {'echo': '/sdk/events?a=b'}
        status, log = await s.control({'action': 'log', 'scope': 'conv:a', 'session_id': sess['session_id']})
        init_entry, events_entry = log['entries'][-3], log['entries'][-1]
        assert init_entry['path'] == '/sdk/init' and init_entry['applied'] == 'init'
        assert init_entry['scenario'] == 'shimmer' and init_entry['scenario_version'] == 1
        assert init_entry['served']['data.widgetSkeletonSettings']['mode'] == 'shimmer'
        assert events_entry['applied'] is None and events_entry['status'] == 200


@pytest.mark.asyncio
async def test_asset_delay_and_404_rules(monkeypatch):
    async with Stack(monkeypatch) as s:
        sess = await s.create()
        scenario = {'rules': [{'match': '*banner.png', 'delay_ms': 300, 'status': 404},
                              {'match': 're:\\.jpg$', 'delay_ms': 0}]}
        await s.control({'action': 'set_scenario', 'scope': 'conv:a', 'session_id': sess['session_id'], 'scenario': scenario})
        _, _, raw = await s.call(sess['path'] + '/sdk/init', json={})
        asset_url = json.loads(raw)['data']['widgets'][0]['image']
        asset_path = asset_url.replace('https://loma.example', '')
        started = time.monotonic()
        status, _, _ = await s.call(asset_path, method='GET')
        assert status == 404 and time.monotonic() - started >= 0.3
        jpg = sess['path'] + '/__loma_asset?u=https%3A%2F%2Fcdn.plotline.so%2Fa.jpg'
        status, headers, _ = await s.call(jpg, method='GET')
        assert status == 302 and headers['Location'] == 'https://cdn.plotline.so/a.jpg'
        # Only allowlisted asset hosts can be redirected to (no open redirect).
        status, _, _ = await s.call(sess['path'] + '/__loma_asset?u=https%3A%2F%2Fevil.example%2Fa.jpg', method='GET')
        assert status == 400
        _, log = await s.control({'action': 'log', 'scope': 'conv:a', 'session_id': sess['session_id']})
        assert [e['status'] for e in log['entries'] if e['path'] == '/__loma_asset'] == [404, 302]
        # api rules short-circuit without touching the upstream.
        before = len(s.seen)
        await s.control({'action': 'set_scenario', 'scope': 'conv:a', 'session_id': sess['session_id'],
                         'scenario': {'rules': [{'kind': 'api', 'match': '/sdk/init', 'status': 503}]}})
        status, _, _ = await s.call(sess['path'] + '/sdk/init', json={})
        assert status == 503 and len(s.seen) == before


@pytest.mark.asyncio
async def test_token_scoping(monkeypatch):
    async with Stack(monkeypatch) as s:
        sess = await s.create(scope='conv:a')
        sid = sess['session_id']
        # Another user, or the same user in another conversation, cannot see or drive it.
        for user, scope in [(OTHER, 'conv:a'), (OWNER, 'conv:b')]:
            for action in ('log', 'set_scenario', 'delete'):
                body = {'action': action, 'scope': scope, 'session_id': sid, 'preset': 'shimmer'}
                status, body = await s.control(body, user=user)
                assert status == 404, (user, scope, action, body)
            _, listed = await s.control({'action': 'list', 'scope': scope}, user=user)
            assert listed['sessions'] == []
        _, listed = await s.control({'action': 'list', 'scope': 'conv:a'})
        assert [x['session_id'] for x in listed['sessions']] == [sid] and 'base_url' not in listed['sessions'][0]
        # A token minted for someone else is rejected outright.
        status, _ = await s.control({'action': 'list', 'scope': 'conv:a'}, user=OWNER, token_user=OTHER)
        assert status == 401
        # Unknown / malformed data-plane tokens are 404 and never reach the upstream.
        for bad in ['dmt_' + 'A' * 43, 'nope', sess['token'][:-1]]:
            status, _, _ = await s.call(f'/device-mock/{bad}/sdk/init', json={})
            assert status == 404
        assert s.seen == []
        # The clear token is never stored.
        doc = await s.db[store.COLLECTION].find_one({'session_id': sid})
        assert sess['token'] not in json.dumps(doc, default=str)


@pytest.mark.asyncio
async def test_upstream_allowlist(monkeypatch):
    async with Stack(monkeypatch) as s:
        status, body = await s.control({'action': 'create', 'scope': 'conv:a', 'upstream': 'https://evil.example'})
        assert status == 403 and 'not allowed' in body['error']
        sess = await s.create()
        # Path tricks cannot change the upstream host.
        for trick in ['//evil.example/x', '/%2F%2Fevil.example/x', '/..%2F..%2Fx', '/@evil.example/x']:
            status, _, _ = await s.call(sess['path'] + trick, method='GET')
            assert status in (200, 404)
        assert {x['host'] for x in s.seen} == {f'127.0.0.1:{s.upstream.port}'}
        # Shrinking the allowlist cuts off existing sessions too.
        monkeypatch.setenv('LOMA_DEVICE_MOCK_UPSTREAMS', 'api.plotline.so')
        before = len(s.seen)
        status, _, _ = await s.call(sess['path'] + '/sdk/init', json={})
        assert status == 502 and len(s.seen) == before


@pytest.mark.asyncio
async def test_ttl_expiry_and_delete(monkeypatch):
    async with Stack(monkeypatch) as s:
        sess = await s.create(ttl_hours=6)
        status, _, _ = await s.call(sess['path'] + '/sdk/init', json={})
        assert status == 200
        await s.db[store.COLLECTION].update_one({'session_id': sess['session_id']},
                                                {'$set': {'expires_at': store.now() - timedelta(seconds=1)}})
        status, _, _ = await s.call(sess['path'] + '/sdk/init', json={})
        assert status == 404
        status, _ = await s.control({'action': 'log', 'scope': 'conv:a', 'session_id': sess['session_id']})
        assert status == 404
        # TTL is clamped to 24h.
        long = await s.create(ttl_hours=1000)
        doc = await s.db[store.COLLECTION].find_one({'session_id': long['session_id']})
        assert store.aware(doc['expires_at']) - store.aware(doc['created_at']) == store.MAX_TTL
        status, body = await s.control({'action': 'delete', 'scope': 'conv:a', 'session_id': long['session_id']})
        assert status == 200 and body['deleted']
        status, _, _ = await s.call(long['path'] + '/sdk/init', json={})
        assert status == 404


@pytest.mark.asyncio
async def test_concurrent_sessions_are_isolated(monkeypatch):
    async with Stack(monkeypatch) as s:
        a = await s.create(scope='conv:a')
        b = await s.create(scope='conv:b')
        await s.control({'action': 'set_scenario', 'scope': 'conv:a', 'session_id': a['session_id'], 'preset': 'shimmer'})
        await s.control({'action': 'set_scenario', 'scope': 'conv:b', 'session_id': b['session_id'],
                         'preset': 'absent'})
        results = await asyncio.gather(*[s.call(x['path'] + '/sdk/init', json={}) for x in [a, b] * 10])
        for i, (status, _, raw) in enumerate(results):
            data = json.loads(raw)['data']
            if i % 2 == 0:
                assert data['widgetSkeletonSettings']['mode'] == 'shimmer'
            else:
                assert 'widgetSkeletonSettings' not in data
        _, log_a = await s.control({'action': 'log', 'scope': 'conv:a', 'session_id': a['session_id']})
        _, log_b = await s.control({'action': 'log', 'scope': 'conv:b', 'session_id': b['session_id']})
        assert {e['scenario'] for e in log_a['entries']} == {'shimmer'} and len(log_a['entries']) == 10
        assert {e['scenario'] for e in log_b['entries']} == {'absent'} and len(log_b['entries']) == 10


@pytest.mark.asyncio
async def test_body_and_rate_limits(monkeypatch):
    async with Stack(monkeypatch) as s:
        sess = await s.create()
        status, _, _ = await s.call(sess['path'] + '/sdk/init', data=b'x' * (routes.MAX_REQUEST_BODY + 1))
        assert status == 413
        monkeypatch.setattr(routes, 'RATE_PER_MINUTE', 3)
        routes.limiter.hits.clear()
        statuses = [(await s.call(sess['path'] + '/sdk/events', method='GET'))[0] for _ in range(5)]
        assert statuses == [200, 200, 200, 429, 429]
        # Per-user active-session cap.
        monkeypatch.setattr(store, 'MAX_ACTIVE_PER_USER', 2)
        await s.create()
        status, body = await s.control({'action': 'create', 'scope': 'conv:a'})
        assert status == 409 and 'At most 2' in body['error']
