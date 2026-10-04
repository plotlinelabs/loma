"""Running setup again on an enrolled machine keeps the same runner instead of adding a duplicate."""
import pytest
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient

from device_loader.backend import store
from device_loader.runner import loma_device_runner as ldr
from device_loader.tests.test_device_runner import home, serve_enroll  # noqa: F401  (home is a fixture)

OWNER = 'owner@example.com'


@pytest.fixture
def db():
    return AsyncMongoMockClient()['loma_devices_reenroll_test']


async def redeem(db, owner=OWNER, name='Vamsi Mac', hostname='MacBook-Pro-4.local', is_online=None, **details):
    token, _ = await store.create_enrollment(db, owner, name)
    kwargs = {'is_online': is_online} if is_online else {}
    return await store.redeem_enrollment(db, token, {'name': name, 'hostname': hostname, **details}, **kwargs)


@pytest.mark.asyncio
async def test_reenroll_with_saved_credentials_keeps_the_same_runner(db):
    first = await redeem(db)
    await db.device_runners.update_one({'runner_id': first['runner_id']}, {'$set': {'shared_with': ['qa@example.com']}})
    again = await redeem(db, previous={'runner_id': first['runner_id'], 'secret': first['secret']}, version='1.3.0')
    assert again['runner_id'] == first['runner_id'] and again['reused'] is True and 'replaced' not in again
    assert again['secret'] != first['secret']
    assert await store.authenticate_runner(db, first['runner_id'], first['secret']) is None  # old secret rotated out
    doc = await store.authenticate_runner(db, first['runner_id'], again['secret'])
    assert doc['shared_with'] == ['qa@example.com'] and doc['version'] == '1.3.0'
    assert await db.device_runners.count_documents({}) == 1


@pytest.mark.asyncio
async def test_reenroll_in_place_works_at_the_runner_limit(db):
    runners = [await redeem(db, hostname=f'host{i}') for i in range(store.MAX_RUNNERS_PER_USER)]
    assert 'error' in await redeem(db, hostname='new-host')
    again = await redeem(db, hostname='host0', previous={'runner_id': runners[0]['runner_id'],
                                                         'secret': runners[0]['secret']})
    assert again['runner_id'] == runners[0]['runner_id'] and again['reused'] is True


@pytest.mark.asyncio
async def test_bad_or_foreign_saved_credentials_never_take_over_a_runner(db):
    mine = await redeem(db)
    wrong = await redeem(db, is_online=lambda runner_id: True,
                         previous={'runner_id': mine['runner_id'], 'secret': 'ldr_wrong'})
    assert wrong['runner_id'] != mine['runner_id'] and 'reused' not in wrong and 'replaced' not in wrong
    assert await store.authenticate_runner(db, mine['runner_id'], mine['secret']) is not None
    other = await redeem(db, owner='other@example.com',
                         previous={'runner_id': mine['runner_id'], 'secret': mine['secret']})
    assert other['runner_id'] != mine['runner_id'] and 'reused' not in other and 'replaced' not in other
    assert (await store.authenticate_runner(db, mine['runner_id'], mine['secret']))['owner_email'] == OWNER


@pytest.mark.asyncio
async def test_lost_config_replaces_only_offline_runners_of_the_same_machine(db):
    old = await redeem(db)
    live = await redeem(db, is_online=lambda runner_id: True)          # same machine name, but connected
    renamed = await redeem(db, name='Work Mac')                         # same host, different name
    foreign = await redeem(db, owner='other@example.com')               # same host and name, other owner
    await db.device_runners.update_one({'runner_id': old['runner_id']}, {'$set': {'shared_with': ['qa@example.com']}})
    await db.device_leases.insert_one({'_id': old['runner_id'] + '/emulator-5554', 'holder': 'x'})
    new = await redeem(db, is_online=lambda runner_id: runner_id == live['runner_id'])
    assert new['replaced'] == [old['runner_id']] and 'reused' not in new
    assert await store.authenticate_runner(db, old['runner_id'], old['secret']) is None
    for kept in (live, renamed, foreign):
        assert await store.authenticate_runner(db, kept['runner_id'], kept['secret']) is not None
    assert (await db.device_runners.find_one({'runner_id': new['runner_id']}))['shared_with'] == ['qa@example.com']
    assert await db.device_leases.count_documents({}) == 0


@pytest.mark.asyncio
async def test_runner_sends_saved_credentials_only_to_the_same_server(home, capsys):
    seen = []

    async def handler(request):
        body = await request.json()
        seen.append(body.get('previous'))
        return web.json_response({'runner_id': 'r_0123456789abcdef', 'secret': 'ldr_new', 'name': 'Mac',
                                  **({'reused': True} if body.get('previous') else {})})

    async def run(base):
        ldr.save_config({'server': base, 'runner_id': 'r_0123456789abcdef', 'secret': 'ldr_old',
                         'policy': {'allowed_app_ids': ['com.example.demo']}})
        await ldr.enroll(base, 'lde_x', 'Mac')
        ldr.save_config({**ldr.load_config(), 'server': 'https://other.example.com'})
        await ldr.enroll(base, 'lde_y', 'Mac')

    await serve_enroll(handler, run)
    assert seen == [{'runner_id': 'r_0123456789abcdef', 'secret': 'ldr_old'}, None]
    config = ldr.load_config()
    assert config['secret'] == 'ldr_new' and config['policy'] == {'allowed_app_ids': ['com.example.demo']}
    assert 'Re-enrolled as the same runner r_0123456789abcdef' in capsys.readouterr().out
