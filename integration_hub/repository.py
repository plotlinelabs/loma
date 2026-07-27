"""MongoDB repositories for Integration Hub's account-scoped resources."""
import base64
import json
from datetime import datetime, timezone

from pymongo import ReturnDocument


RESOURCE_COLLECTIONS = {
    "project": "projects", "task": "tasks", "milestone": "milestones",
    "risk": "risks", "blocker": "risks", "source": "sources",
}


class AccountRepository:
    def __init__(self, db):
        self.db = db
        self.collection = db.integration_accounts
        self.projects = db.integration_projects
        self.tasks = db.integration_tasks
        self.milestones = db.integration_milestones
        self.risks = db.integration_risks
        self.sources = db.integration_source_mappings
        self.audit = db.integration_audit_log
        self.idempotency = db.integration_idempotency

    @staticmethod
    def encode_cursor(document):
        payload = [document["updated_at"].isoformat(), document["account_id"]]
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    @staticmethod
    def decode_cursor(cursor):
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            stamp, account_id = json.loads(raw)
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            return parsed, account_id
        except Exception as exc:
            raise ValueError("cursor is invalid") from exc

    async def _transaction(self, callback):
        async with await self.db.client.start_session() as session:
            return await session.with_transaction(callback)

    async def create(self, account, audit_entry):
        async def operation(session):
            await self.collection.insert_one(account, session=session)
            await self.audit.insert_one(audit_entry, session=session)
            return account
        return await self._transaction(operation)

    async def list(self, query, limit=50, cursor=None):
        query = dict(query)
        if cursor:
            updated_at, account_id = self.decode_cursor(cursor)
            query["$or"] = [
                {"updated_at": {"$lt": updated_at}},
                {"updated_at": updated_at, "account_id": {"$gt": account_id}},
            ]
        docs = await self.collection.find(query).sort(
            [("updated_at", -1), ("account_id", 1)]
        ).limit(limit + 1).to_list(limit + 1)
        has_more = len(docs) > limit
        docs = docs[:limit]
        return docs, self.encode_cursor(docs[-1]) if has_more and docs else None

    async def get(self, account_id, include_archived=False):
        query = {"account_id": account_id}
        if not include_archived:
            query["archived_at"] = None
        return await self.collection.find_one(query)

    async def hydrate(self, account):
        if not account:
            return None
        account_id = account["account_id"]
        active = {"account_id": account_id, "archived_at": None}
        projects, tasks, milestones, risks, sources, activities = await __import__("asyncio").gather(
            self.projects.find(active).sort("created_at", 1).to_list(None),
            self.tasks.find(active).sort("created_at", 1).to_list(None),
            self.milestones.find(active).sort("created_at", 1).to_list(None),
            self.risks.find(active).sort("created_at", 1).to_list(None),
            self.sources.find(active).sort("created_at", 1).to_list(None),
            self.audit.find({"account_id": account_id}).sort("created_at", -1).limit(200).to_list(200),
        )
        result = dict(account)
        result["projects"] = projects
        result["work_items"] = tasks + milestones + risks
        result["source_links"] = sources
        result["activities"] = [{
            "activity_id": row["audit_id"], "type": row.get("activity_type", "update"),
            "message": row["action"], "created_at": row["created_at"],
            "created_by": row["actor"],
        } for row in reversed(activities)]
        return result

    async def mutate_account(self, account_id, updates, expected_version, audit_entry):
        async def operation(session):
            before = await self.collection.find_one({"account_id": account_id}, session=session)
            if not before or before.get("version") != expected_version:
                return None
            after = await self.collection.find_one_and_update(
                {"account_id": account_id, "version": expected_version},
                {"$set": updates, "$inc": {"version": 1}}, session=session,
                return_document=ReturnDocument.AFTER,
            )
            if not after:
                return None
            audit_entry.update({"before": before, "after": after, "resource_version": after["version"]})
            await self.audit.insert_one(audit_entry, session=session)
            return after
        return await self._transaction(operation)

    def resource_collection(self, kind):
        return getattr(self, RESOURCE_COLLECTIONS[kind])

    async def get_resource(self, kind, resource_id, account_id):
        return await self.resource_collection(kind).find_one({
            "resource_id": resource_id, "account_id": account_id, "archived_at": None,
        })

    async def create_resource(self, kind, resource, account_version, audit_entry, extra_resources=()):
        collection = self.resource_collection(kind)
        async def operation(session):
            parent = await self.collection.find_one_and_update(
                {"account_id": resource["account_id"], "version": account_version, "archived_at": None},
                {"$set": {"updated_at": resource["created_at"], "updated_by": resource["created_by"]}, "$inc": {"version": 1}},
                session=session, return_document=ReturnDocument.AFTER,
            )
            if not parent:
                return None
            await collection.insert_one(resource, session=session)
            for extra_kind, extra in extra_resources:
                await self.resource_collection(extra_kind).insert_one(extra, session=session)
            audit_entry.update({"before": None, "after": resource, "resource_version": resource["version"]})
            await self.audit.insert_one(audit_entry, session=session)
            return parent
        return await self._transaction(operation)

    async def mutate_resource(self, kind, resource_id, account_id, updates, expected_version, account_version, audit_entry):
        collection = self.resource_collection(kind)
        async def operation(session):
            before = await collection.find_one({"resource_id": resource_id, "account_id": account_id}, session=session)
            if not before or before.get("version") != expected_version:
                return None
            now = updates["updated_at"]
            parent = await self.collection.find_one_and_update(
                {"account_id": account_id, "version": account_version, "archived_at": None},
                {"$set": {"updated_at": now, "updated_by": updates["updated_by"]}, "$inc": {"version": 1}},
                session=session, return_document=ReturnDocument.AFTER,
            )
            if not parent:
                return None
            after = await collection.find_one_and_update(
                {"resource_id": resource_id, "account_id": account_id, "version": expected_version},
                {"$set": updates, "$inc": {"version": 1}}, session=session,
                return_document=ReturnDocument.AFTER,
            )
            if not after:
                return None
            audit_entry.update({"before": before, "after": after, "resource_version": after["version"]})
            await self.audit.insert_one(audit_entry, session=session)
            return parent
        return await self._transaction(operation)

    async def list_audit(self, account_id, limit=50, cursor=None):
        query = {"account_id": account_id}
        if cursor:
            created_at, audit_id = self.decode_cursor(cursor)
            query["$or"] = [{"created_at": {"$lt": created_at}}, {"created_at": created_at, "audit_id": {"$gt": audit_id}}]
        rows = await self.audit.find(query).sort([("created_at", -1), ("audit_id", 1)]).limit(limit + 1).to_list(limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if more and rows:
            next_cursor = self.encode_cursor({"updated_at": rows[-1]["created_at"], "account_id": rows[-1]["audit_id"]})
        return rows, next_cursor

    async def list_actions(self, actor):
        pipeline = [
            {"$match": {"owner_email": actor.lower(), "archived_at": None,
                        "status": {"$nin": ["completed", "cancelled", "achieved", "resolved", "accepted"]}}},
            {"$lookup": {"from": "integration_accounts", "localField": "account_id", "foreignField": "account_id", "as": "account"}},
            {"$unwind": "$account"},
            {"$match": {"account.archived_at": None}},
            {"$sort": {"due_at": 1, "resource_id": 1}}, {"$limit": 500},
        ]
        actions = []
        for collection in (self.tasks, self.milestones):
            actions.extend(await collection.aggregate(pipeline).to_list(500))
        attention_pipeline = [
            {"$match": {"archived_at": None, "status": "active"}},
            *[
                {"$lookup": {
                    "from": collection,
                    "let": {"account_id": "$account_id"},
                    "pipeline": [
                        {"$match": {"$expr": {"$eq": ["$account_id", "$$account_id"]},
                                    "archived_at": None}},
                    ],
                    "as": field,
                }}
                for collection, field in (
                    ("integration_tasks", "health_tasks"),
                    ("integration_milestones", "health_milestones"),
                    ("integration_risks", "health_risks"),
                )
            ],
            {"$addFields": {"work_items": {
                "$concatArrays": ["$health_tasks", "$health_milestones", "$health_risks"]
            }}},
            {"$project": {"health_tasks": 0, "health_milestones": 0, "health_risks": 0}},
            {"$sort": {"updated_at": -1, "account_id": 1}},
        ]
        attention = await self.collection.aggregate(attention_pipeline).to_list(None)
        return actions, attention

    async def find_idempotent(self, actor, key):
        return await self.idempotency.find_one({"actor": actor, "key": key})

    async def save_idempotent(self, actor, key, response):
        await self.idempotency.update_one({"actor": actor, "key": key}, {"$setOnInsert": {
            "actor": actor, "key": key, "response": response, "created_at": datetime.now(timezone.utc),
        }}, upsert=True)
