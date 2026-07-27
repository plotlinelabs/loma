"""Business operations for the manual onboarding foundation."""

import uuid
from datetime import datetime, timezone

from integration_hub.models import (
    normalize_activity, normalize_create, normalize_source_link, normalize_update,
    normalize_work_item,
)


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
        updated = await self.repository.update(account["account_id"], updates)
        fields = ", ".join(sorted(key for key in updates if key not in {"updated_at", "updated_by", "version"}))
        return await self._record(updated, "update", f"Updated client fields: {fields}", actor)

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
        account = await self.repository.add_work_item(account_id, item)
        return await self._record(account, "update", f"Added {item['type']}: {item['title']}", actor)

    async def update_work_item(self, account_id, item, data, actor):
        merged = {**item, **data}
        updates = normalize_work_item(merged)
        updates.update({
            "updated_at": datetime.now(timezone.utc),
            "updated_by": actor,
        })
        account = await self.repository.update_work_item(
            account_id, item["item_id"], updates
        )
        return await self._record(account, "update", f"Updated {item['type']}: {item['title']}", actor)

    async def delete_work_item(self, account_id, item, actor):
        account = await self.repository.delete_work_item(account_id, item["item_id"])
        return await self._record(account, "update", f"Deleted {item['type']}: {item['title']}", actor)

    async def create_activity(self, account_id, data, actor):
        activity = self._activity(normalize_activity(data), actor)
        return await self.repository.append_activity(account_id, activity)

    async def create_source_link(self, account_id, data, actor):
        now = datetime.now(timezone.utc)
        link = {
            "link_id": f"link_{uuid.uuid4()}",
            **normalize_source_link(data),
            "created_at": now,
            "created_by": actor,
        }
        account = await self.repository.add_source_link(account_id, link)
        return await self._record(account, "update", f"Added source link: {link['title']}", actor)

    async def delete_source_link(self, account_id, link, actor):
        account = await self.repository.delete_source_link(account_id, link["link_id"])
        return await self._record(account, "update", f"Deleted source link: {link['title']}", actor)

    def _activity(self, data, actor):
        return {
            "activity_id": f"activity_{uuid.uuid4()}",
            **data,
            "created_at": datetime.now(timezone.utc),
            "created_by": actor,
        }

    async def _record(self, account, activity_type, message, actor):
        if not account:
            return None
        return await self.repository.append_activity(
            account["account_id"],
            self._activity({"type": activity_type, "message": message}, actor),
        )
