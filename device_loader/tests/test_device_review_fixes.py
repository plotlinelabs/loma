"""Regression tests for the PR #231 review: stable refs, nested matches, iOS container metadata,
take-over hand-back and live-view / activity privacy."""

from datetime import timedelta

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from device_loader.api import device_routes
from device_loader.backend import service as service_mod, store
from device_loader.backend.builds import BlobStore
from device_loader.backend.hub import DeviceError, RunnerHub
from device_loader.backend.service import DeviceService, compact_tree
from device_loader.runner import loma_device_runner as ldr

OWNER = 'owner@example.com'
GUEST = 'guest@example.com'
PNG = 'iVBORw0KGgo='  # base64 of the 8-byte PNG signature


class Hub(RunnerHub):
    version = None

    def __init__(self):
        super().__init__()
        self.calls, self.after = [], None
        self.screen = [{'type': 'TextView', 'text': 'Cart'},
                       {'type': 'Button', 'text': 'Pay', 'clickable': True, 'center': [540, 1800]}]

    def get(self, runner_id):
        return type('C', (), {'devices': [{'serial': 'emulator-5554', 'platform': 'android'}],
                              'version': self.version})()

    async def call(self, runner_id, op, serial, args):
        self.calls.append((op, dict(args)))
        if op == 'ui_tree':
            return {'units': 'pixels', 'screen': [1080, 2400], 'elements': [dict(e) for e in self.screen]}
        if op == 'screenshot':
            return {'png_base64': PNG, 'width': 1080, 'height': 2400}
        return {'ok': True, **({'screen_after': self.after} if self.after else {})}


@pytest_asyncio.fixture
async def setup():
    service_mod._REFS.clear()
    service_mod._LAST_FRAME.clear()
    db = AsyncMongoMockClient()['loma_review_fixes']
    token, _ = await store.create_enrollment(db, OWNER, 'Mac')
    runner = await store.redeem_enrollment(db, token, {})
    await db.device_runners.update_one({'runner_id': runner['runner_id']}, {'$set': {
        'devices': [{'serial': 'emulator-5554', 'platform': 'android'}], 'shared_with': [GUEST]}})
    hub = Hub()
    device = store.device_id(runner['runner_id'], 'emulator-5554')
    return DeviceService(db, hub=hub, blobs=BlobStore()), hub, device, db


# ── 1. Refs never point at a different element ───────────────────────────────

@pytest.mark.asyncio
async def test_settle_refs_keep_their_meaning_after_ui_tree(setup):
    service, hub, device, _ = setup
    await service.call(OWNER, 'c', device, 'ui_tree', {'compact': True})  # e1 Cart, e2 Pay
    hub.after = {'settled': True, 'settle_ms': 600, 'tree': {'elements': [
        {'type': 'TextView', 'text': 'Cart'}, {'type': 'Button', 'text': 'Receipt', 'clickable': True,
                                               'center': [540, 2000]}]}}
    result = await service.call(OWNER, 'c', device, 'tap', {'ref': 'e2', 'settle': True})
    assert result['screen_after']['added'] == ["e3 Button 'Receipt' @540,2000 *"]
    hub.after = None
    hub.screen = [{'type': 'Button', 'text': 'X', 'clickable': True, 'center': [9, 9]},
                  {'type': 'TextView', 'text': 'Cart'},
                  {'type': 'Button', 'text': 'Receipt', 'clickable': True, 'center': [540, 2000]}]
    tree = await service.call(OWNER, 'c', device, 'ui_tree', {'compact': True})
    assert tree['tree'].splitlines() == ["e4 Button 'X' @9,9 *", "e1 TextView 'Cart'",
                                         "e3 Button 'Receipt' @540,2000 *"]
    await service.call(OWNER, 'c', device, 'tap', {'ref': 'e3'})
    assert hub.calls[-1] == ('tap', {'x': 540, 'y': 2000})  # still Receipt, not X


@pytest.mark.asyncio
async def test_filtered_ui_tree_keeps_whole_screen_refs(setup):
    service, hub, device, _ = setup
    await service.call(OWNER, 'c', device, 'ui_tree', {})
    tree = await service.call(OWNER, 'c', device, 'ui_tree', {'compact': True, 'filter': 'Pay'})
    assert tree['tree'] == "e2 Button 'Pay' @540,1800 *"
    await service.call(OWNER, 'c', device, 'ui_tree', {'compact': True, 'clickable_only': True})
    await service.call(OWNER, 'c', device, 'tap', {'ref': 'e2'})
    assert hub.calls[-1] == ('tap', {'x': 540, 'y': 1800})


@pytest.mark.asyncio
async def test_stale_refs_fail_instead_of_hitting_new_elements(setup):
    service, hub, device, _ = setup
    await service.call(OWNER, 'c', device, 'ui_tree', {})
    await service.call(OWNER, 'c', device, 'tap', {'ref': 'e2'})
    hub.screen = [{'type': 'Button', 'text': 'Delete', 'clickable': True, 'center': [1, 1]},
                  {'type': 'Button', 'text': 'Other', 'clickable': True, 'center': [2, 2]}]
    tree = await service.call(OWNER, 'c', device, 'ui_tree', {'compact': True})
    assert tree['tree'].startswith('e3 ')
    with pytest.raises(DeviceError) as caught:
        await service.call(OWNER, 'c', device, 'tap', {'ref': 'e2'})  # the old Pay ref
    assert caught.value.code == 'ref_stale'


@pytest.mark.asyncio
@pytest.mark.parametrize('op,args', [('wait_for', {'match': 'Pay'}), ('configure', {'dark_mode': True}),
                                     ('animations', {'enabled': False}), ('netcap', {'action': 'start'})])
async def test_screen_changing_ops_reset_refs(setup, op, args):
    service, hub, device, _ = setup
    await service.call(OWNER, 'c', device, 'ui_tree', {})
    try:
        await service.call(OWNER, 'c', device, op, args)
    except DeviceError:
        pass  # a failed attempt still resets them
    with pytest.raises(DeviceError) as caught:
        await service.call(OWNER, 'c', device, 'tap', {'ref': 'e2'})  # Pay: tappable before the op
    assert caught.value.code == 'ref_stale'


def test_unsettled_screen_says_so():
    diff, state = service_mod.settle_diff(None, {'settled': False, 'settle_ms': 3000, 'tree': {'elements': [
        {'type': 'Button', 'text': 'Go', 'center': [1, 2]}]}})
    assert state is not None and 'still changing' in diff['note']


def test_compact_tree_is_stable_against_previous_refs():
    previous = {'keys': {'e7': ('Button', 'Pay', '', '', False, None, False)}, 'next': 8}
    out = compact_tree({'elements': [{'type': 'TextView', 'text': 'New'},
                                     {'type': 'Button', 'text': 'Pay', 'center': [1, 1]}]}, True, previous=previous)
    assert out['tree'].splitlines() == ["e8 TextView 'New'", "e7 Button 'Pay' @1,1"] and out['_next'] == 9


# ── 2. Nested matches use the innermost element ──────────────────────────────

def test_nested_matches_pick_the_innermost():
    card = {'type': 'Card', 'label': 'Pay', 'clickable': True, 'bounds': [0, 1000, 1080, 2000], 'center': [540, 1500]}
    button = {'type': 'Button', 'text': 'Pay', 'clickable': True, 'bounds': [400, 1700, 680, 1900],
              'center': [540, 1800]}
    ranked = ldr.rank_elements([card, button], 'Pay')
    assert ldr.pick_element(ranked, None, 'Pay') is button
    app = {'type': 'Application', 'label': 'Loma', 'bounds': [0, 0, 390, 844], 'center': [195, 422]}
    title = {'type': 'StaticText', 'label': 'Loma', 'bounds': [150, 60, 240, 90], 'center': [195, 75]}
    assert ldr.pick_element(ldr.rank_elements([app, title], 'Loma'), None, 'Loma') is title


def test_separate_matches_are_still_ambiguous_and_nth_too_large_is_invalid():
    a = {'type': 'Button', 'text': 'Pay', 'clickable': True, 'bounds': [0, 0, 10, 10], 'center': [5, 5]}
    b = {'type': 'Button', 'text': 'Pay', 'clickable': True, 'bounds': [0, 20, 10, 30], 'center': [5, 25]}
    ranked = ldr.rank_elements([a, b], 'Pay')
    with pytest.raises(ldr.OpError) as caught:
        ldr.pick_element(ranked, None, 'Pay')
    assert caught.value.code == 'ambiguous'
    with pytest.raises(ldr.OpError) as caught:
        ldr.pick_element(ranked, 5, 'Pay')
    assert caught.value.code == 'invalid_args'


# ── 3. iOS reset keeps the container metadata ────────────────────────────────

def test_empty_app_container_keeps_metadata_plist(tmp_path):
    serial = 'A1B2C3D4-0000-1111-2222-333344445555'
    container = (tmp_path / 'CoreSimulator/Devices' / serial / 'data/Containers/Data/Application'
                 / '11111111-2222-3333-4444-555555555555')
    (container / 'Documents').mkdir(parents=True)
    (container / 'Documents' / 'db.sqlite').write_text('x')
    (container / '.com.apple.mobile_container_manager.metadata.plist').write_text('meta')
    (container / '.other-dotfile').write_text('app data')
    ldr.empty_app_container(container, serial)
    assert (container / '.com.apple.mobile_container_manager.metadata.plist').read_text() == 'meta'
    assert not (container / '.other-dotfile').exists() and list((container / 'Documents').iterdir()) == []


# ── 4. Take-over: clear code, short hold, live view keeps it alive, any hand-back frees the device ──

@pytest.mark.asyncio
async def test_takeover_error_is_device_held_and_hold_is_short(setup):
    service, hub, device, db = setup
    await service.start_takeover(OWNER, device)
    with pytest.raises(DeviceError) as caught:
        await service.call(OWNER, 'conv-1', device, 'ui_tree', {})
    err = caught.value
    assert err.code == 'device_held' and err.retriable and 'held_until' in err.details
    assert 'another device' not in err.hint and '3 minutes' in str(err)
    assert service_mod.HOLD_TTL <= timedelta(minutes=3)


@pytest.mark.asyncio
async def test_live_view_frames_report_and_extend_the_hold(setup):
    service, hub, device, db = setup
    _, held = await service.screen(OWNER, device)
    assert held is None
    await service.start_takeover(OWNER, device)
    before = store.aware((await db.device_leases.find_one({'_id': device}))['hold']['until'])
    service_mod._LAST_FRAME.clear()
    _, held = await service.screen(OWNER, device)
    after = store.aware((await db.device_leases.find_one({'_id': device}))['hold']['until'])
    assert held == 'you' and after >= before


@pytest.mark.asyncio
async def test_owner_hand_back_frees_a_guest_takeover_lease(setup):
    service, hub, device, db = setup
    await service.start_takeover(GUEST, device)
    await service.end_takeover(OWNER, device)
    assert await db.device_leases.find_one({'_id': device}) is None
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {})  # the agent can use it right away


# ── 5. Privacy: live view and activity ───────────────────────────────────────

@pytest.mark.asyncio
async def test_shared_user_cannot_watch_another_users_session(setup):
    service, hub, device, _ = setup
    await service.call(OWNER, 'conv-1', device, 'ui_tree', {})  # owner's agent session holds the device
    with pytest.raises(DeviceError, match="another person's session") as caught:
        await service.screen(GUEST, device)
    assert caught.value.code == 'device_busy'
    await service.release(OWNER, 'conv-1', device)
    png, _ = await service.screen(GUEST, device)  # free device: fine
    assert png[:4] == b'\x89PNG'


@pytest.mark.asyncio
async def test_runner_owner_can_watch_a_guest_session(setup):
    service, hub, device, _ = setup
    await service.call(GUEST, 'conv-9', device, 'ui_tree', {})
    png, _ = await service.screen(OWNER, device)
    assert png[:4] == b'\x89PNG'


@pytest.mark.asyncio
async def test_activity_shows_shared_users_only_their_own_sessions(setup, monkeypatch):
    service, hub, device, db = setup
    await service.call(OWNER, 'conv:owner-chat', device, 'ui_tree', {})
    await service.release(OWNER, 'conv:owner-chat', device)
    await service.call(GUEST, 'conv:guest-chat', device, 'ui_tree', {})
    monkeypatch.setattr(device_routes, '_db_or_503', lambda: db)

    async def activity(user):
        monkeypatch.setattr(device_routes, 'get_user_email', lambda request: user)
        request = type('R', (), {'query': {'device_id': device}})()
        import json
        return json.loads((await device_routes.handle_activity(request)).body)

    assert {e['actor'] for e in (await activity(GUEST))['events']} == {GUEST}
    assert {e['actor'] for e in (await activity(OWNER))['events']} == {OWNER, GUEST}
