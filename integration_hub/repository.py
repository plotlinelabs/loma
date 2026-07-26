"""MongoDB persistence for Integration Hub accounts."""


class AccountRepository:
    def __init__(self, db):
        self.collection = db.integration_accounts

    async def create(self, account):
        await self.collection.insert_one(account)
        return account

    async def list(self, query, limit=100):
        return await self.collection.find(query).sort(
            [("updated_at", -1), ("account_id", 1)]
        ).to_list(limit)

    async def get(self, account_id):
        return await self.collection.find_one({
            "account_id": account_id,
            "archived_at": None,
        })

    async def update(self, account_id, updates):
        await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {"$set": updates},
        )
        return await self.get(account_id)

    async def add_work_item(self, account_id, item):
        await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {"$push": {"work_items": item}},
        )
        return await self.get(account_id)

    async def update_work_item(self, account_id, item_id, updates):
        set_fields = {f"work_items.$.{key}": value for key, value in updates.items()}
        result = await self.collection.update_one(
            {
                "account_id": account_id,
                "archived_at": None,
                "work_items.item_id": item_id,
            },
            {"$set": set_fields},
        )
        return await self.get(account_id) if result.matched_count else None

    async def delete_work_item(self, account_id, item_id):
        result = await self.collection.update_one(
            {"account_id": account_id, "archived_at": None},
            {"$pull": {"work_items": {"item_id": item_id}}},
        )
        return await self.get(account_id) if result.modified_count else None
