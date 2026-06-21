import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from api.oauth_helpers import store_google_tokens, store_slack_tokens


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.updates = []

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                if projection is None:
                    return doc
                return {k: doc[k] for k in projection if k in doc}
        return None

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update, upsert))
        doc = await self.find_one(query)
        if doc is None:
            if not upsert:
                return SimpleNamespace(modified_count=0)
            doc = dict(query)
            self.docs.append(doc)
        for key, value in update.get("$set", {}).items():
            if key == "tool_assignments":
                doc[key] = value
                continue
            target = doc
            parts = key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        for key, value in update.get("$setOnInsert", {}).items():
            doc.setdefault(key, value)
        return SimpleNamespace(modified_count=1)


def test_store_google_tokens_normalizes_legacy_tool_assignments():
    db = SimpleNamespace(
        users=FakeCollection([{"email": "user@example.com", "tool_assignments": []}]),
        oauth_tokens=FakeCollection(),
    )

    with patch("api.oauth_helpers.encrypt_token", side_effect=lambda token: f"enc:{token}"):
        asyncio.run(
            store_google_tokens(
                db,
                "user@example.com",
                "access-token",
                "refresh-token",
                3600,
                ["email"],
            )
        )

    user = db.users.docs[0]
    assert user["tool_assignments"]["google-personal"]["oauth_status"] == "connected"


def test_store_slack_tokens_normalizes_legacy_tool_assignments():
    db = SimpleNamespace(
        users=FakeCollection([{"email": "user@example.com", "tool_assignments": []}]),
        oauth_tokens=FakeCollection(),
    )

    with patch("api.oauth_helpers.encrypt_token", side_effect=lambda token: f"enc:{token}"):
        asyncio.run(
            store_slack_tokens(
                db,
                "user@example.com",
                "slack-token",
                ["chat:write"],
                slack_user_id="U123",
                slack_team_id="T123",
            )
        )

    user = db.users.docs[0]
    assert user["tool_assignments"]["slack-personal"]["oauth_status"] == "connected"

