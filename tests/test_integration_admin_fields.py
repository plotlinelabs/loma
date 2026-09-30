"""Admin-only integration settings: GitHub device_build_repos / device_build_workflows."""
import pytest
from mongomock_motor import AsyncMongoMockClient

from api import integration_routes


class FakeRequest(dict):
    def __init__(self, body, role):
        super().__init__(user_email=f'{role}@example.com', system_role=role)
        self.body = body

    async def json(self):
        return self.body


@pytest.fixture
def db(monkeypatch):
    database = AsyncMongoMockClient()['loma_integrations_test']
    monkeypatch.setattr(integration_routes, 'get_db', lambda: database)
    monkeypatch.setattr(integration_routes, 'encrypt_token', lambda value: 'enc:' + value)

    async def no_reload():
        return None
    monkeypatch.setattr(integration_routes, '_reload_pool', no_reload)
    return database


def connect(extra, role):
    body = {'provider': 'github', 'api_key': 'ghp_x', 'extra_fields': extra}
    return integration_routes._connect_integration(FakeRequest(body, role))


@pytest.mark.asyncio
async def test_only_admins_set_device_build_settings(db):
    response = await connect({'device_build_repos': 'org/app', 'device_build_workflows': 'build.yml'}, 'admin')
    assert response.status == 200
    # A non-admin cannot set or change them...
    response = await connect({'device_build_repos': 'evil/repo'}, 'operator')
    assert response.status == 403
    response = await connect({'device_build_workflows': 'deploy.yml'}, 'maintainer')
    assert response.status == 403
    doc = await db.integrations.find_one({'provider': 'github'})
    assert doc['extra_fields_encrypted']['device_build_repos'] == 'enc:org/app'
    # ...but can still (re)connect GitHub, and the admin's values are kept.
    response = await connect({}, 'operator')
    assert response.status == 200
    doc = await db.integrations.find_one({'provider': 'github'})
    assert doc['connected_by'] == 'operator@example.com'
    assert doc['extra_fields_encrypted'] == {'device_build_repos': 'enc:org/app',
                                             'device_build_workflows': 'enc:build.yml'}
