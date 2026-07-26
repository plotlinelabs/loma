"""Business operations for the manual onboarding foundation."""

import uuid
from datetime import datetime, timezone

from integration_hub.models import normalize_create, normalize_update, normalize_work_item


class AccountService:
    def __init__(self, repository):
        self.repository = repository

    async def create(self, data, actor):
        now = datetime.now(timezone.utc)
        account = {
            "account_id": f"acc_{uuid.uuid4()}",
            **normalize_create(data),
            "created_at": now,
            "created_by": actor,
            "updated_at": now,
            "updated_by": actor,
            "archived_at": None,
            "archived_by": None,
            "version": 1,
        }
        return await self.repository.create(account)

    async def list(self, *, stage=None, health=None, search=None):
        query = {"archived_at": None}
        if stage:
            query["stage"] = stage
        if health:
            query["health"] = health
        if search:
            query["name"] = {"$regex": search, "$options": "i"}
        return await self.repository.list(query)

    async def update(self, account, data, actor):
        updates = normalize_update(data)
        if not updates:
            return account
        updates.update({
            "updated_at": datetime.now(timezone.utc),
            "updated_by": actor,
            "version": account.get("version", 1) + 1,
        })
        return await self.repository.update(account["account_id"], updates)

    async def create_work_item(self, account_id, data, actor):
        now = datetime.now(timezone.utc)
        item = {
            "item_id": f"item_{uuid.uuid4()}",
            **normalize_work_item(data),
            "created_at": now,
            "created_by": actor,
            "updated_at": now,
            "updated_by": actor,
        }
        return await self.repository.add_work_item(account_id, item)

    async def update_work_item(self, account_id, item, data, actor):
        merged = {**item, **data}
        updates = normalize_work_item(merged)
        updates.update({
            "updated_at": datetime.now(timezone.utc),
            "updated_by": actor,
        })
        return await self.repository.update_work_item(
            account_id, item["item_id"], updates
        )
