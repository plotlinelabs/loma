import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from api.dev_environment_routes import _list_dev_environments, _upsert_dev_environment


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args):
        return self

    async def to_list(self, _limit):
        return self.docs


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def find(self, _query):
        return FakeCursor(list(self.docs.values()))

    async def find_one(self, query):
        return self.docs.get(query.get("environment_id"))

    async def update_one(self, query, update, upsert=False):
        environment_id = query["environment_id"]
        doc = self.docs.get(environment_id)
        if doc is None:
            if not upsert:
                return SimpleNamespace(modified_count=0)
            doc = {"environment_id": environment_id}
            self.docs[environment_id] = doc
        doc.update(update.get("$set", {}))
        for key, value in update.get("$setOnInsert", {}).items():
            doc.setdefault(key, value)
        return SimpleNamespace(modified_count=1)


class FakeRequest(dict):
    def __init__(self, body):
        super().__init__()
        self["system_role"] = "maintainer"
        self["user_email"] = "maintainer@example.com"
        self._body = body

    async def json(self):
        return self._body


def _payload(**overrides):
    base = {
        "environment_id": "app-dev",
        "name": "Application Dev",
        "repo": "owner/repo",
        "default_branch": "main",
        "worktree_base_path": "/var/lib/loma/worktrees/app",
        "service_commands": ["cd apps/dashboard && npm run dev"],
        "health_urls": ["http://127.0.0.1:3000"],
        "env_files": [{"path": "apps/dashboard/.env.local", "content": "SECRET=value"}],
        "browser_auth": {
            "login_url": "http://127.0.0.1:3000/login",
            "username": "loma-dev@example.com",
            "password": "password-123",
            "success_url_contains": "/dashboard",
            "allowed_domains": ["127.0.0.1:3000"],
        },
    }
    base.update(overrides)
    return base


def test_dev_environment_upsert_masks_secrets(monkeypatch):
    collection = FakeCollection()
    db = SimpleNamespace(dev_environments=collection)
    monkeypatch.setattr("api.dev_environment_routes.get_db", lambda: db)

    with patch("api.dev_environment_routes.encrypt_token", side_effect=lambda v: f"enc:{v}"):
        response = asyncio.run(_upsert_dev_environment(FakeRequest(_payload())))

    body = json.loads(response.text)["environment"]
    saved = collection.docs["app-dev"]

    assert saved["env_files"][0]["content_encrypted"] == "enc:SECRET=value"
    assert saved["browser_auth"]["password_encrypted"] == "enc:password-123"
    assert "SECRET=value" not in response.text
    assert "password-123" not in response.text
    assert body["env_files"][0]["configured"] is True
    assert body["browser_auth"]["password_configured"] is True


def test_dev_environment_update_preserves_existing_secrets(monkeypatch):
    collection = FakeCollection()
    db = SimpleNamespace(dev_environments=collection)
    monkeypatch.setattr("api.dev_environment_routes.get_db", lambda: db)

    with patch("api.dev_environment_routes.encrypt_token", side_effect=lambda v: f"enc:{v}"):
        asyncio.run(_upsert_dev_environment(FakeRequest(_payload())))
        response = asyncio.run(_upsert_dev_environment(FakeRequest(_payload(
            name="Renamed",
            env_files=[{"path": "apps/dashboard/.env.local"}],
            browser_auth={
                "login_url": "http://127.0.0.1:3000/login",
                "success_url_contains": "/dashboard",
                "allowed_domains": ["127.0.0.1:3000"],
            },
        ))))

    saved = collection.docs["app-dev"]
    body = json.loads(response.text)["environment"]

    assert saved["name"] == "Renamed"
    assert saved["env_files"][0]["content_encrypted"] == "enc:SECRET=value"
    assert saved["browser_auth"]["username_encrypted"] == "enc:loma-dev@example.com"
    assert saved["browser_auth"]["password_encrypted"] == "enc:password-123"
    assert body["browser_auth"]["username_configured"] is True


def test_dev_environment_list_masks_secrets(monkeypatch):
    collection = FakeCollection()
    db = SimpleNamespace(dev_environments=collection)
    monkeypatch.setattr("api.dev_environment_routes.get_db", lambda: db)

    with patch("api.dev_environment_routes.encrypt_token", side_effect=lambda v: f"enc:{v}"):
        asyncio.run(_upsert_dev_environment(FakeRequest(_payload())))
    response = asyncio.run(_list_dev_environments(FakeRequest({})))

    assert response.status == 200
    assert "SECRET=value" not in response.text
    assert "password-123" not in response.text
    assert json.loads(response.text)["environments"][0]["env_files"][0]["configured"] is True
