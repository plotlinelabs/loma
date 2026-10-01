"""Device build sources (Devices tab): admin-only repo / workflow allowlists for GitHub CI installs."""
import json

import pytest
from aiohttp import web
from mongomock_motor import AsyncMongoMockClient

from device_loader.api import device_routes
from device_loader.backend import builds


class FakeRequest(dict):
    def __init__(self, role, body=None):
        super().__init__(user_email=f'{role}@example.com', system_role=role)
        self.body = body

    async def json(self):
        return self.body


@pytest.fixture
def db(monkeypatch):
    database = AsyncMongoMockClient()['loma_devices_settings_test']
    monkeypatch.setattr(device_routes, 'get_db', lambda: database)
    monkeypatch.delenv('LOMA_DEVICE_BUILD_REPOS', raising=False)
    monkeypatch.delenv('LOMA_DEVICE_BUILD_WORKFLOWS', raising=False)
    builds.invalidate_settings()
    return database


def body(response):
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_admin_saves_and_everyone_reads(db, monkeypatch):
    monkeypatch.setenv('LOMA_DEVICE_BUILD_REPOS', 'org/env-app')
    response = await device_routes.handle_put_build_settings(
        FakeRequest('admin', {'repos': 'Org/App, org/app org/other', 'workflows': ['build.yml', 'ios.yaml']}))
    assert response.status == 200
    saved = body(response)
    assert saved['repos'] == ['org/app', 'org/other'] and saved['workflows'] == ['build.yml', 'ios.yaml']
    assert saved['updated_by'] == 'admin@example.com' and saved['can_edit'] is True

    read = body(await device_routes.handle_get_build_settings(FakeRequest('chatter')))
    assert read['repos'] == ['org/app', 'org/other'] and read['env_repos'] == ['org/env-app']
    assert read['can_edit'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['maintainer', 'operator', 'chatter'])
async def test_non_admin_cannot_save(db, role):
    with pytest.raises(web.HTTPForbidden):
        await device_routes.handle_put_build_settings(FakeRequest(role, {'repos': ['evil/repo']}))
    assert await db.device_settings.find_one({}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('update', [{'repos': ['not-a-repo']}, {'repos': ['a/b/c']},
                                    {'workflows': ['deploy.sh']}, {'workflows': ['../x.yml']},
                                    {'repos': 'x/y ' * 51}, {}])
async def test_invalid_input_rejected(db, update):
    response = await device_routes.handle_put_build_settings(FakeRequest('admin', update))
    assert response.status == 400
    assert await db.device_settings.find_one({}) is None


def test_allowlists_merge_env_and_saved(monkeypatch):
    monkeypatch.setenv('LOMA_DEVICE_BUILD_REPOS', 'org/a')
    monkeypatch.setenv('LOMA_DEVICE_BUILD_WORKFLOWS', 'build.yml')
    builds.invalidate_settings()
    monkeypatch.setattr(builds, 'read_saved_settings', lambda: {'repos': ['org/b'], 'workflows': ['ios.yml']})
    assert builds.allowed_repos() == {'org/a', 'org/b'}
    assert builds.allowed_workflows() == {'build.yml', 'ios.yml'}
    # Cached for SETTING_TTL; invalidate_settings (called on save) forces a re-read.
    monkeypatch.setattr(builds, 'read_saved_settings', lambda: pytest.fail('re-read too soon'))
    assert 'org/b' in builds.allowed_repos()
    monkeypatch.setattr(builds, 'read_saved_settings', lambda: {'repos': ['org/c']})
    builds.invalidate_settings()
    assert builds.allowed_repos() == {'org/a', 'org/c'}


def test_mongo_error_fails_closed_to_env(monkeypatch):
    monkeypatch.setenv('LOMA_DEVICE_BUILD_REPOS', 'org/a')
    builds.invalidate_settings()

    def boom():
        raise RuntimeError('mongo down')
    monkeypatch.setattr(builds, 'read_saved_settings', boom)
    assert builds.allowed_repos() == {'org/a'}
    assert builds.allowed_workflows() == set()
