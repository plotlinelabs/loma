"""MongoDB persistence for Integration Hub accounts."""


class AccountRepository:
    def __init__(self, db):
        self.collection = db.integration_accounts

    async def create(self, account):
        await self.collection.insert_one(account)
        return account

    async def list(self, query, limit=100, skip=0):
        return await self.collection.find(query).sort(
            [("updated_at", -1), ("account_id", 1)]
        ).skip(skip).to_list(limit)

    async def list_all(self, query, batch_size=100):
        accounts = []
        skip = 0
        while True:
            batch = await self.list(query, limit=batch_size, skip=skip)
            accounts.extend(batch)
            if len(batch) < batch_size:
                return accounts
            skip += batch_size

    async def count(self, query):
        return await self.collection.count_documents(query)

    async def get(self, account_id):
        return await self.collection.find_one({
            "account_id": account_id,
            "archived_at": None,
        })

    async def get_any(self, account_id):
        return await self.collection.find_one({"account_id": account_id})

    async def update(self, account_id, updates, *, expected_version=None, activity=None):
        query = {"account_id": account_id}
        if expected_version is not None:
            query["version"] = expected_version
        operation = {"$set": updates}
        if activity:
            operation["$push"] = {"activities": activity}
        result = await self.collection.update_one(
            query,
            operation,
        )
        if not result.matched_count:
            return None
        return await self.get_any(account_id)

    async def add_project(self, account_id, project, work_items, activity):
        result = await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {
                "$push": {
                    "projects": project,
                    "work_items": {"$each": work_items},
                    "activities": activity,
                },
                "$inc": {"version": 1},
                "$set": {"updated_at": project["created_at"], "updated_by": project["created_by"]},
            },
        )
        return await self.get(account_id) if result.matched_count else None

    async def append_activity(self, account_id, activity, parent_updates):
        result = await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {
                "$push": {"activities": activity},
                "$set": parent_updates,
                "$inc": {"version": 1},
            },
        )
        return await self.get(account_id) if result.matched_count else None

    async def add_source_link(self, account_id, link, activity, parent_updates):
        result = await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {
                "$push": {"source_links": link, "activities": activity},
                "$set": parent_updates,
                "$inc": {"version": 1},
            },
        )
        return await self.get(account_id) if result.matched_count else None

    async def delete_source_link(self, account_id, link_id, activity, parent_updates):
        result = await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {
                "$pull": {"source_links": {"link_id": link_id}},
                "$push": {"activities": activity},
                "$set": parent_updates,
                "$inc": {"version": 1},
            },
        )
        return await self.get(account_id) if result.modified_count else None

    async def add_work_item(self, account_id, item, activity, parent_updates):
        result = await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {
                "$push": {"work_items": item, "activities": activity},
                "$set": parent_updates,
                "$inc": {"version": 1},
            },
        )
        return await self.get(account_id) if result.matched_count else None

    async def update_work_item(
        self, account_id, item_id, updates, activity, parent_updates
    ):
        set_fields = {f"work_items.$.{key}": value for key, value in updates.items()}
        set_fields.update(parent_updates)
        result = await self.collection.update_one(
            {
                "account_id": account_id,
                "archived_at": None,
                "work_items.item_id": item_id,
            },
            {
                "$set": set_fields,
                "$push": {"activities": activity},
                "$inc": {"version": 1},
            },
        )
        return await self.get(account_id) if result.matched_count else None

    async def delete_work_item(self, account_id, item_id, activity, parent_updates):
        result = await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {
                "$pull": {"work_items": {"item_id": item_id}},
                "$push": {"activities": activity},
                "$set": parent_updates,
                "$inc": {"version": 1},
            },
        )
        return await self.get(account_id) if result.modified_count else None
