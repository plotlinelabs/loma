"""Phase 1: settle + diff, structured errors, richer tree, ambiguity, generation refs, scroll end,
app-scoped logs and iOS reset_app. Runner behaviour runs against the fake adb (no emulator)."""
import asyncio
import json

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from device_loader.backend import service as service_mod, store
from device_loader.backend.builds import BlobStore
from device_loader.backend.gateway import failure
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import DeviceService, compact_tree, settle_diff
from device_loader.runner import loma_device_runner as ldr
from device_loader.tests.test_device_runner import adb, runner  # noqa: F401  (pytest fixture)

OWNER = 'owner@example.com'


def screen(*nodes):
    body = ''.join(nodes)
    return (f'<?xml version="1.0"?><hierarchy rotation="0"><node class="android.widget.FrameLayout" '
            f'bounds="[0,0][1080,2400]">{body}</node></hierarchy>').encode()


def button(text, bounds, **attrs):
    extra = ''.join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<node class="android.widget.Button" text="{text}" clickable="true" bounds="{bounds}"{extra}/>'


def show(tmp_path, *screens):
    """The fake adb returns these screens, one per UI dump; the last one repeats."""
    for path in tmp_path.glob('ui-seq-*'):
        path.unlink()
    for i, xml in enumerate(screens):
        (tmp_path / f'ui-seq-{i:02d}.xml').write_bytes(xml)


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(ldr, 'POLL_SECONDS', 0.01)


# ── Richer tree ──────────────────────────────────────────────────────────


def test_android_tree_reports_states_and_scroll_containers():
    xml = screen(button('Pay', '[0,0][10,10]', enabled='false'),
                 '<node class="android.widget.CheckBox" text="Terms" checkable="true" checked="false" '
                 'clickable="true" bounds="[0,20][10,30]"/>',
                 '<node class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,40][10,90]"/>',
                 '<node class="android.widget.EditText" password="true" focused="true" resource-id="a:id/pin" '
                 'bounds="[0,100][10,110]"/>')
    elements, _, total = ldr.parse_uiautomator(xml, with_total=True)
    pay, terms, recycler, pin = elements
    assert pay['disabled'] is True and terms['checked'] is False
    assert recycler == {'type': 'RecyclerView', 'scrollable': True, 'bounds': [0, 40, 10, 90], 'center': [5, 65]}
    assert pin['password'] is True and pin['focused'] is True and total == 4
    lines = compact_tree({'elements': elements}, compact=True)['tree'].splitlines()
    assert lines[0].endswith('[disabled]') and '[unchecked]' in lines[1] and lines[2].endswith('[scrollable]')


def test_android_tree_flags_the_element_cap(monkeypatch):
    monkeypatch.setattr(ldr, 'MAX_UI_ELEMENTS', 2)
    elements, _, total = ldr.parse_uiautomator(screen(*(button(f'B{i}', f'[0,{i}][1,{i + 1}]') for i in range(5))),
                                               with_total=True)
    assert len(elements) == 2 and total == 5
    out = compact_tree({'elements': elements, 'truncated': True, 'total': 5})
    assert 'first 2 of 5' in out['note']


# ── Runner: ambiguity, settle, scroll end, logs ─────────────────────────


@pytest.mark.asyncio
async def test_tap_text_refuses_ambiguous_matches_and_nth_picks_one(adb, tmp_path):
    driver, calls = adb
    show(tmp_path, screen(button('Buy', '[0,100][500,200]'), button('Buy', '[0,300][500,400]')))
    r = runner(driver)
    with pytest.raises(ldr.OpError) as caught:
        await r.call('tap_text', 'emulator-5554', {'match': 'Buy'})
    assert caught.value.code == 'ambiguous' and len(caught.value.details['candidates']) == 2
    assert not [c for c in calls() if 'tap' in c]  # nothing was tapped
    result = await r.call('tap_text', 'emulator-5554', {'match': 'Buy', 'nth': 2})
    assert result['tapped'] == [250, 350] and result['matches'] == 2


@pytest.mark.asyncio
async def test_a_wrapper_and_its_own_child_are_not_ambiguous(adb, tmp_path):
    driver, _ = adb
    show(tmp_path, screen('<node class="android.widget.FrameLayout" content-desc="Buy" clickable="true" '
                          'bounds="[0,100][500,200]">' + button('Buy', '[10,110][490,190]') + '</node>'))
    result = await runner(driver).call('tap_text', 'emulator-5554', {'match': 'Buy'})
    assert result['tapped'] == [250, 150]


@pytest.mark.asyncio
async def test_settle_waits_for_two_identical_screens(adb, tmp_path):
    driver, _ = adb
    loading = screen(button('Pay', '[0,0][100,100]'), '<node class="android.widget.ProgressBar" text="Loading" '
                                                       'bounds="[0,200][100,300]"/>')
    done = screen(button('Done', '[0,0][100,100]'))
    show(tmp_path, loading, done, done)
    result = await runner(driver).call('tap', 'emulator-5554', {'x': 5, 'y': 5, 'settle': True})
    after = result['screen_after']
    assert after['settled'] is True and after['polls'] == 3
    assert [e['text'] for e in after['tree']['elements']] == ['Done']


@pytest.mark.asyncio
async def test_settle_never_fails_the_action(adb, tmp_path):
    driver, _ = adb
    show(tmp_path, *(screen(button(f'Frame {i}', '[0,0][100,100]')) for i in range(50)))  # never stops changing
    result = await runner(driver).call('tap', 'emulator-5554', {'x': 5, 'y': 5, 'settle_ms': 500})
    assert result['tapped'] == [5, 5] and result['screen_after']['settled'] is False


@pytest.mark.asyncio
async def test_scroll_until_visible_stops_at_the_end_of_the_list(adb, tmp_path):
    driver, calls = adb
    show(tmp_path, screen(button('Last item', '[0,0][1080,200]')))
    result = await runner(driver).call('scroll_until_visible', 'emulator-5554', {'match': 'Missing', 'max_swipes': 8})
    assert result == {'found': False, 'swipes': 1, 'reason': 'end_of_list', 'visible': ['Last item']}
    assert len([c for c in calls() if 'swipe' in c]) == 1


@pytest.mark.asyncio
async def test_android_logs_for_one_app_use_its_pid(adb, monkeypatch):
    driver, _ = adb
    seen = []

    async def fake_run(args, **kwargs):
        seen.append(args)
        return 0, (b'4242\n' if 'pidof' in args else b'a line\n'), ''

    monkeypatch.setattr(ldr, 'run', fake_run)
    assert await driver.logs('emulator-5554', 10, False, app_id='com.example.demo') == ['a line']
    assert seen[-1][-1] == '--pid=4242'


# ── Runner: structured failures ──────────────────────────────────────────


class WS:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_str(self, data):
        self.sent.append(json.loads(data))


async def reply(r, op, args):
    ws, r.send_lock = WS(), asyncio.Lock()
    await r.handle_call(ws, {'id': 'c1', 'op': op, 'device': 'emulator-5554', 'args': args, 'timeout': 60})
    return ws.sent[-1]


@pytest.mark.asyncio
async def test_failures_carry_code_and_whether_input_reached_the_device(adb, tmp_path):
    driver, _ = adb
    r = runner(driver)
    invalid = await reply(r, 'tap', {'x': -1, 'y': 5})
    assert invalid['code'] == 'invalid_args' and invalid['dispatched'] == 'no'
    show(tmp_path, screen(button('Save', '[0,0][10,10]')))
    missing = await reply(r, 'tap_text', {'match': 'Nope', 'timeout_s': 0})
    assert missing['code'] == 'not_found' and missing['dispatched'] == 'no'  # only UI reads ran
    show(tmp_path, screen(button('Buy', '[0,0][10,10]'), button('Buy', '[0,20][10,30]')))
    ambiguous = await reply(r, 'tap_text', {'match': 'Buy'})
    assert ambiguous['code'] == 'ambiguous' and len(ambiguous['details']['candidates']) == 2


def test_hub_reads_structured_and_legacy_runner_failures():
    new = DeviceError.from_runner({'error': 'x', 'code': 'ui_not_idle', 'dispatched': 'no'})
    assert new.to_dict()['retriable'] is True and new.dispatched == 'no' and 'animations' in new.hint
    legacy = DeviceError.from_runner({'error': 'old runner'})
    assert legacy.code == 'device_error' and legacy.dispatched == 'unknown' and legacy.retriable is False
    assert DeviceError.from_runner({'error': 'x', 'code': 'made_up'}).code == 'device_error'
    assert 'details' not in DeviceError.from_runner({'error': 'x', 'details': {'big': 'y' * 10000}}).to_dict()
    result = failure('x', **new.to_dict())
    assert 'error' not in result and result['code'] == 'ui_not_idle'  # 'error' would poison the worker run


# ── iOS reset_app ────────────────────────────────────────────────────────


def test_ios_container_is_emptied_but_keeps_its_folders(tmp_path):
    uuid = '0F2A6C1E-4B7D-4C1A-9E3B-2D5F8A7C6B10'
    container = tmp_path / 'CoreSimulator/Devices/SIM-1/data/Containers/Data/Application' / uuid
    (container / 'Documents/sub').mkdir(parents=True)
    (container / 'Documents/sub/a.db').write_text('x')
    (container / 'Library/Preferences').mkdir(parents=True)
    (container / 'Library/Preferences/com.x.plist').write_text('x')
    (container / 'tmp').mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('keep')
    (container / 'tmp/link').symlink_to(outside)
    assert ldr.empty_app_container(container, 'SIM-1') == 3
    assert sorted(p.name for p in container.iterdir()) == ['Documents', 'Library', 'tmp']
    assert not any(container.rglob('*.*')) and outside.read_text() == 'keep'
    for bad in (container.parent, tmp_path / 'other' / uuid, container):
        with pytest.raises(ldr.OpError):
            ldr.empty_app_container(bad, 'SIM-2' if bad == container else 'SIM-1')


@pytest.mark.asyncio
async def test_ios_reset_app_clears_defaults_then_the_container(monkeypatch, tmp_path):
    uuid = '0F2A6C1E-4B7D-4C1A-9E3B-2D5F8A7C6B10'
    container = tmp_path / 'CoreSimulator/Devices/SIM-1/data/Containers/Data/Application' / uuid
    (container / 'Documents').mkdir(parents=True)
    (container / 'Documents/state.json').write_text('{}')
    seen = []

    async def fake_run(args, **kwargs):
        seen.append(args[2] if args[:2] == ['xcrun', 'simctl'] else args[0])
        return 0, (str(container).encode() if 'get_app_container' in args else b''), ''

    monkeypatch.setattr(ldr, 'run', fake_run)
    result = await ldr.IOS().reset_app('SIM-1', 'com.example.demo')
    assert result['removed'] == 1 and seen == ['terminate', 'get_app_container', 'spawn']


# ── Backend: generation refs, settle diff, version gate ─────────────────


class Hub(RunnerHub):
    version = '1.3.0'

    def __init__(self):
        super().__init__()
        self.calls, self.after = [], None

    def get(self, runner_id):
        return type('C', (), {'devices': [{'serial': 'emulator-5554', 'platform': 'android'}],
                              'version': self.version})()

    async def call(self, runner_id, op, serial, args):
        self.calls.append((op, dict(args)))
        if op == 'ui_tree':
            return {'units': 'pixels', 'screen': [1080, 2400], 'elements': [
                {'type': 'TextView', 'text': 'Cart'}, {'type': 'Button', 'text': 'Pay', 'clickable': True,
                                                       'center': [540, 1800]}]}
        return {'tapped': [args.get('x'), args.get('y')], **({'screen_after': self.after} if self.after else {})}


@pytest_asyncio.fixture
async def setup():
    service_mod._REFS.clear()
    db = AsyncMongoMockClient()['loma_phase1_test']
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    runner_doc = await store.redeem_enrollment(db, token, {})
    hub = Hub()
    return DeviceService(db, hub=hub, blobs=BlobStore()), hub, store.device_id(runner_doc['runner_id'], 'emulator-5554')


@pytest.mark.asyncio
async def test_settle_returns_a_diff_and_refs_for_the_new_screen(setup):
    service, hub, device = setup
    tree = await service.call(OWNER, 'conv-1', device, 'ui_tree', {'compact': True})
    assert tree['refs_generation'] >= 1
    hub.after = {'settled': True, 'settle_ms': 900, 'polls': 3, 'tree': {'elements': [
        {'type': 'TextView', 'text': 'Cart'},
        {'type': 'TextView', 'text': 'Paid', 'center': [540, 900]},
        {'type': 'Button', 'text': 'Receipt', 'clickable': True, 'center': [540, 2000]}]}}
    result = await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e2', 'settle': True})
    after = result['screen_after']
    assert after['added'] == ["e3 TextView 'Paid' @540,900", "e4 Button 'Receipt' @540,2000 *"]
    assert after['removed'] == ["e2 Button 'Pay'"] and after['unchanged'] == 1
    assert after['refs_generation'] > tree['refs_generation'] and 'tree' not in after
    hub.after = None
    await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e4'})  # a new ref works without ui_tree
    assert hub.calls[-1] == ('tap', {'x': 540, 'y': 2000})
    with pytest.raises(DeviceError) as caught:
        await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e4'})  # that tap changed the screen
    assert caught.value.code == 'ref_stale' and caught.value.dispatched == 'no'


def test_settle_diff_without_a_previous_screen_lists_it():
    diff, state = settle_diff(None, {'settled': True, 'settle_ms': 1, 'tree': {'elements': [
        {'type': 'Button', 'text': 'Go', 'center': [1, 2]}]}})
    assert diff['screen'] == ["e1 Button 'Go' @1,2"] and state['refs'] == {'e1': [1, 2]}
    diff, state = settle_diff(None, {'settled': False, 'settle_ms': 3000})
    assert state is None and 'ui_tree' in diff['note']


@pytest.mark.asyncio
async def test_refs_no_longer_expire_during_a_slow_model_turn(setup, monkeypatch):
    service, hub, device = setup
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
    clock = service_mod.time.monotonic() + 300  # the old 120 s TTL would have expired this
    monkeypatch.setattr(service_mod.time, 'monotonic', lambda: clock)
    await service.call(OWNER, 'conv-1', device, 'tap', {'ref': 'e2'})
    assert hub.calls[-1] == ('tap', {'x': 540, 'y': 1800})


@pytest.mark.asyncio
async def test_new_arguments_need_runner_1_3(setup):
    service, hub, device = setup
    hub.version = '1.2.0'
    for op, args in (('tap', {'x': 1, 'y': 1, 'settle': True}), ('tap_text', {'match': 'a', 'nth': 2}),
                     ('logs', {'app_id': 'com.example.demo'})):
        with pytest.raises(DeviceError) as caught:
            await service.call(OWNER, 'conv-1', device, op, args)
        assert caught.value.code == 'runner_too_old' and '1.3.0' in str(caught.value)
    await service.call(OWNER, 'conv-1', device, 'tap', {'x': 1, 'y': 1})  # old arguments still work


@pytest.mark.asyncio
async def test_invalid_arguments_are_coded(setup):
    service, _, device = setup
    for args in ({'match': 'a', 'nth': 0}, {'match': 'a', 'settle': False, 'settle_ms': 900}):
        with pytest.raises(DeviceError) as caught:
            await service.call(OWNER, 'conv-1', device, 'tap_text', args)
        assert caught.value.code == 'invalid_args' and caught.value.dispatched == 'no'
    with pytest.raises(DeviceError, match='needs match'):
        await service.call(OWNER, 'conv-1', device, 'clear_text', {'nth': 2})
