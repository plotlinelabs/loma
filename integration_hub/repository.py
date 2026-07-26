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
