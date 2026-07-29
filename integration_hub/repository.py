"""MongoDB repositories for Integration Hub's account-scoped resources."""
import base64
import json
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from integration_hub.models import ValidationError, calculate_account_health


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
        self.interactions = db.integration_interactions
        self.raw_events = getattr(db, "integration_raw_events", self.interactions)
        self.findings = getattr(db, "integration_findings", self.interactions)
        self.conversations = getattr(db, "integration_external_conversations", self.interactions)
        self.sync_sources = getattr(db, "integration_sync_sources", self.sources)
        self.sync_jobs = getattr(db, "integration_sync_jobs", None)
        self.audit = db.integration_audit_log
        self.timeline = getattr(db, "integration_timeline", self.audit)
        self.contacts = getattr(db, "integration_contacts", self.sources)
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

    async def create_idempotent(self, account, audit_entry, actor, key, response):
        """Create the account, audit row, and idempotency result in one transaction."""
        record = {
            "actor": actor,
            "key": key,
            "response": response,
            "created_at": datetime.now(timezone.utc),
        }

        async def operation(session):
            # The unique (actor, key) index serializes concurrent retries. Inserting
            # this first ensures a losing request cannot create an account.
            await self.idempotency.insert_one(record, session=session)
            await self.collection.insert_one(account, session=session)
            await self.audit.insert_one(audit_entry, session=session)
            return True

        try:
            await self._transaction(operation)
            return response, True
        except DuplicateKeyError:
            existing = await self.idempotency.find_one({"actor": actor, "key": key})
            if not existing:
                raise
            return existing["response"], False

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

    async def list_with_health(self, query, limit=50, cursor=None, health=None):
        """List accounts with the same child-backed health used by account detail.

        Child lookup happens in MongoDB before health filtering and pagination,
        preventing portfolio/detail drift and incorrect urgency ordering.
        """
        query = dict(query)
        if cursor:
            updated_at, account_id = self.decode_cursor(cursor)
            query["$or"] = [
                {"updated_at": {"$lt": updated_at}},
                {"updated_at": updated_at, "account_id": {"$gt": account_id}},
            ]
        pipeline = [{"$match": query}]
        for collection, field in (
            ("integration_tasks", "health_tasks"),
            ("integration_milestones", "health_milestones"),
            ("integration_risks", "health_risks"),
        ):
            pipeline.append({"$lookup": {
                "from": collection, "let": {"account_id": "$account_id"},
                "pipeline": [{"$match": {"$expr": {"$eq": ["$account_id", "$$account_id"]},
                                         "archived_at": None}}],
                "as": field,
            }})
        now = datetime.now(timezone.utc)
        closed = ["completed", "cancelled", "achieved", "resolved", "accepted"]
        pipeline.extend([
            {"$addFields": {"work_items": {"$concatArrays": [
                "$health_tasks", "$health_milestones", "$health_risks"
            ]}}},
            {"$addFields": {"health_open_items": {"$filter": {
                "input": "$work_items", "as": "item",
                "cond": {"$not": [{"$in": ["$$item.status", closed]}]},
            }}}},
            {"$addFields": {
                "health_overdue_count": {"$size": {"$filter": {
                    "input": "$health_open_items", "as": "item",
                    "cond": {"$and": [
                        {"$ne": ["$$item.due_at", None]},
                        {"$lt": ["$$item.due_at", now]},
                    ]},
                }}},
                "health_blocker_count": {"$size": {"$filter": {
                    "input": "$health_open_items", "as": "item",
                    "cond": {"$eq": ["$$item.type", "blocker"]},
                }}},
                "health_severe_count": {"$size": {"$filter": {
                    "input": "$health_open_items", "as": "item",
                    "cond": {"$and": [
                        {"$in": ["$$item.type", ["risk", "blocker"]]},
                        {"$in": ["$$item.severity", ["high", "critical"]]},
                    ]},
                }}},
                "health_escalated_count": {"$size": {"$filter": {
                    "input": "$health_open_items", "as": "item",
                    "cond": {"$and": [
                        {"$eq": ["$$item.escalated", True]},
                        {"$in": ["$$item.type", ["risk", "blocker"]]},
                        {"$in": ["$$item.severity", ["high", "critical"]]},
                    ]},
                }}},
            }},
            {"$addFields": {"calculated_health": {"$switch": {
                "branches": [
                    {"case": {"$gt": ["$health_escalated_count", 0]}, "then": "escalated"},
                    {"case": {"$gt": ["$health_blocker_count", 0]}, "then": "blocked"},
                    {"case": {"$or": [
                        {"$gt": ["$health_severe_count", 0]},
                        {"$gte": ["$health_overdue_count", 2]},
                        {"$and": [
                            {"$ne": ["$target_go_live_at", None]},
                            {"$lt": ["$target_go_live_at", now]},
                        ]},
                    ]}, "then": "at_risk"},
                    {"case": {"$gt": ["$health_overdue_count", 0]},
                     "then": "needs_attention"},
                    {"case": {"$and": [
                        {"$ne": ["$target_go_live_at", None]},
                        {"$lte": ["$target_go_live_at", now + timedelta(days=7)]},
                        {"$lt": ["$completion_percentage", 100]},
                    ]}, "then": "needs_attention"},
                ],
                "default": "on_track",
            }}}},
            {"$addFields": {"effective_health": {"$cond": [
                "$health_override_enabled", "$health", "$calculated_health",
            ]}}},
            {"$project": {"health_tasks": 0, "health_milestones": 0, "health_risks": 0}},
        ])
        if health:
            pipeline.append({"$match": {"effective_health": health}})
        pipeline.extend([
            {"$sort": {"updated_at": -1, "account_id": 1}},
            {"$limit": limit + 1},
        ])
        rows = await self.collection.aggregate(pipeline).to_list(limit + 1)
        enriched = []
        for row in rows:
            item = {**row, **calculate_account_health(row)}
            item.pop("work_items", None)
            item.pop("health_open_items", None)
            for key in tuple(item):
                if key.startswith("health_") and key.endswith("_count"):
                    item.pop(key, None)
            enriched.append(item)
        has_more = len(enriched) > limit
        enriched = enriched[:limit]
        return enriched, self.encode_cursor(enriched[-1]) if has_more and enriched else None

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
        projects, tasks, milestones, risks, sources, sync_sources, interactions, activities, conversations, findings, contacts = await __import__("asyncio").gather(
            self.projects.find(active).sort("created_at", 1).to_list(None),
            self.tasks.find(active).sort("created_at", 1).to_list(None),
            self.milestones.find(active).sort("created_at", 1).to_list(None),
            self.risks.find(active).sort("created_at", 1).to_list(None),
            self.sources.find(active).sort("created_at", 1).to_list(None),
            self.sync_sources.find(active).sort("created_at", 1).to_list(None),
            self.interactions.find({"account_id": account_id}).sort("occurred_at", -1).limit(100).to_list(100),
            self.timeline.find({"account_id": account_id}).sort("created_at", -1).limit(200).to_list(200),
            self.conversations.find({"account_id": account_id}).sort("last_interaction_at", -1).limit(100).to_list(100),
            self.findings.find({"account_id": account_id}).sort("created_at", -1).limit(100).to_list(100),
            self.contacts.find(active).sort("name", 1).to_list(None),
        )
        result = dict(account)
        result["projects"] = projects
        result["work_items"] = tasks + milestones + risks
        result["source_links"] = sources
        result["sync_sources"] = sync_sources
        result["interactions"] = interactions
        result["activities"] = list(reversed(activities))
        result["conversations"] = conversations
        result["findings"] = findings
        result["contacts"] = contacts
        return result

    async def find_active_by_name_key(self, name_key):
        return await self.collection.find_one({"name_key": name_key, "archived_at": None})

    async def create_contact(self, contact, audit_entry, expected_version):
        async def operation(session):
            parent = await self.collection.find_one_and_update(
                {"account_id": contact["account_id"], "archived_at": None,
                 "version": expected_version},
                {"$set": {
                    "updated_at": contact["created_at"],
                    "updated_by": contact["created_by"],
                }, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER, session=session,
            )
            if not parent:
                return None
            await self.contacts.insert_one(contact, session=session)
            await self.audit.insert_one(audit_entry, session=session)
            return parent
        return await self._transaction(operation)

    async def archive_contact(self, account_id, contact_id, actor, audit_entry, expected_version):
        now = datetime.now(timezone.utc)
        async def operation(session):
            existing = await self.contacts.find_one(
                {"account_id": account_id, "contact_id": contact_id, "archived_at": None},
                session=session,
            )
            if not existing:
                return None
            parent = await self.collection.find_one_and_update(
                {"account_id": account_id, "archived_at": None, "version": expected_version},
                {"$set": {"updated_at": now, "updated_by": actor}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER, session=session,
            )
            if not parent:
                return None
            result = await self.contacts.find_one_and_update(
                {"account_id": account_id, "contact_id": contact_id, "archived_at": None},
                {"$set": {"archived_at": now, "archived_by": actor}},
                return_document=ReturnDocument.AFTER, session=session,
            )
            if result:
                await self.audit.insert_one(audit_entry, session=session)
            return result if result else None
        return await self._transaction(operation)

    async def update_contact(self, account_id, contact_id, updates, actor, audit_entry,
                             expected_version):
        async def operation(session):
            parent = await self.collection.find_one_and_update(
                {"account_id": account_id, "archived_at": None, "version": expected_version},
                {"$set": {"updated_at": updates["updated_at"], "updated_by": actor},
                 "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER, session=session,
            )
            if not parent:
                return None
            result = await self.contacts.find_one_and_update(
                {"account_id": account_id, "contact_id": contact_id, "archived_at": None},
                {"$set": updates}, return_document=ReturnDocument.AFTER, session=session,
            )
            if result:
                await self.audit.insert_one(audit_entry, session=session)
            return result
        return await self._transaction(operation)

    async def finish_contact_expiry(self, contact_id, status, error=None, audit_entry=None):
        now = datetime.now(timezone.utc)
        contact = await self.contacts.find_one_and_update(
            {"contact_id": contact_id, "access_status": "revoking"},
            {"$set": {
                "access_status": status,
                "revoked_at": now.isoformat() if status == "expired" else None,
                "provisioning_error": error or "",
                "updated_at": now,
                "updated_by": "integration-hub-access-worker",
            }},
            return_document=ReturnDocument.AFTER,
        )
        if contact:
            await self.collection.update_one(
                {"account_id": contact["account_id"]},
                {"$set": {"updated_at": now, "updated_by": "integration-hub-access-worker"},
                 "$inc": {"version": 1}},
            )
            if audit_entry:
                await self.audit.insert_one(audit_entry)
        return contact

    async def update_contact_access(self, account_id, contact_id, updates, actor, audit_entry,
                                    expected_version):
        """Update server-owned access state and its audit entry atomically."""
        now = datetime.now(timezone.utc)
        async def operation(session):
            parent = await self.collection.find_one_and_update(
                {"account_id": account_id, "archived_at": None, "version": expected_version},
                {"$set": {"updated_at": now, "updated_by": actor}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER, session=session,
            )
            if not parent:
                return None
            contact = await self.contacts.find_one_and_update(
                {"account_id": account_id, "contact_id": contact_id, "archived_at": None},
                {"$set": {**updates, "updated_at": now, "updated_by": actor}},
                return_document=ReturnDocument.AFTER, session=session,
            )
            if not contact:
                raise ValidationError("Contact not found")
            await self.audit.insert_one(audit_entry, session=session)
            return parent
        return await self._transaction(operation)


    async def list_sync_sources(self, account_id):
        return await self.sync_sources.find({"account_id": account_id, "archived_at": None}).sort("created_at", 1).to_list(None)

    async def list_sync_jobs(self, account_id, limit=50):
        return await self.sync_jobs.find({"account_id": account_id}).sort(
            "created_at", -1
        ).limit(limit).to_list(limit)

    async def create_sync_source(self, mapping, audit_entry):
        async def operation(session):
            await self.sync_sources.insert_one(mapping, session=session)
            await self.audit.insert_one(audit_entry, session=session)
            return mapping
        return await self._transaction(operation)

    async def update_sync_result(self, mapping_id, *, status, error, checkpoint, synced_at):
        return await self.sync_sources.find_one_and_update(
            {"mapping_id": mapping_id, "archived_at": None},
            {"$set": {"sync_status": status, "last_error": error, "checkpoint": checkpoint,
                      "last_synced_at": synced_at, "updated_at": synced_at}},
            return_document=ReturnDocument.AFTER,
        )

    async def enqueue_sync_job(self, job):
        """Queue at most one active job for a mapping across all web workers."""
        existing = await self.sync_jobs.find_one({
            "mapping_id": job["mapping_id"], "status": {"$in": ["queued", "running"]},
        })
        if existing:
            return existing, False
        try:
            await self.sync_jobs.insert_one(job)
        except DuplicateKeyError:
            existing = await self.sync_jobs.find_one({
                "mapping_id": job["mapping_id"], "status": {"$in": ["queued", "running"]},
            })
            if existing:
                return existing, False
            raise
        await self.sync_sources.update_one(
            {"mapping_id": job["mapping_id"]},
            {"$set": {"sync_status": "queued", "last_error": None,
                      "updated_at": job["created_at"]}},
        )
        return job, True

    async def schedule_due_syncs(self, now):
        mappings = await self.sync_sources.find({
            "status": "active", "archived_at": None,
            "next_sync_at": {"$lte": now},
        }).limit(50).to_list(50)
        queued = 0
        for mapping in mappings:
            interval = mapping.get("config", {}).get("sync_interval_minutes", 60)
            job = {
                "job_id": f"job_{mapping['mapping_id']}_{int(now.timestamp())}",
                "account_id": mapping["account_id"],
                "mapping_id": mapping["mapping_id"], "source": mapping["source"],
                "status": "queued", "attempt": 0, "max_attempts": 5,
                "created_at": now, "created_by": "integration-hub-scheduler",
                "request_id": f"scheduled:{mapping['mapping_id']}:{int(now.timestamp())}",
                "next_attempt_at": now,
            }
            _, created = await self.enqueue_sync_job(job)
            queued += int(created)
            await self.sync_sources.update_one(
                {"mapping_id": mapping["mapping_id"]},
                {"$set": {"next_sync_at": now + timedelta(minutes=interval)}},
            )
        return queued

    async def create_interaction(self, interaction, audit_entry):
        async def operation(session):
            raw_event = {
                "account_id": interaction["account_id"],
                "source": interaction["source"],
                "tenant_id": interaction["tenant_id"],
                "source_id": interaction["source_id"],
                "occurred_at": interaction["occurred_at"],
                "ingested_at": interaction["ingested_at"],
                "payload": interaction.get("raw", {}),
            }
            if self.raw_events is not self.interactions:
                await self.raw_events.insert_one(raw_event, session=session)
            await self.interactions.insert_one(interaction, session=session)
            conversation_id = interaction.get("conversation_id")
            if conversation_id and self.conversations is not self.interactions:
                await self.conversations.update_one(
                    {"account_id": interaction["account_id"],
                     "source": interaction["source"],
                     "tenant_id": interaction["tenant_id"],
                     "conversation_id": conversation_id},
                    {"$set": {
                        "last_interaction_at": interaction["occurred_at"],
                        "state": interaction["conversation_state"],
                        "summary": interaction["summary"],
                        "requires_response": interaction["requires_response"],
                        "source_url": interaction.get("source_url"),
                        "issue_title": interaction.get("raw", {}).get("issue_title"),
                        "issue_status": interaction.get("raw", {}).get("issue_status"),
                        "assignee": interaction.get("raw", {}).get("assignee"),
                        "updated_at": interaction["ingested_at"],
                    }, "$setOnInsert": {
                        "created_at": interaction["ingested_at"],
                    }},
                    upsert=True, session=session,
                )
            if self.findings is not self.interactions and interaction.get("classification") in {
                "reported_issue", "customer_question"
            }:
                await self.findings.insert_one({
                    "finding_id": f"finding_{interaction['interaction_id']}",
                    "account_id": interaction["account_id"],
                    "interaction_id": interaction["interaction_id"],
                    "classification": interaction["classification"],
                    "summary": interaction["summary"],
                    "evidence": interaction.get("evidence", {}),
                    "requires_response": interaction["requires_response"],
                    "conversation_state": interaction["conversation_state"],
                    "confidence": interaction["confidence"],
                    "review_status": "unreviewed",
                    "created_at": interaction["ingested_at"],
                }, session=session)
            await self.audit.insert_one(audit_entry, session=session)
            return True

        # Never swallow DuplicateKeyError inside the transaction callback. MongoDB
        # aborts the transaction on that write error, so continuing the callback
        # makes with_transaction attempt to commit an already-aborted transaction
        # and surfaces NoSuchTransaction instead of a harmless deduplication.
        try:
            return await self._transaction(operation)
        except DuplicateKeyError:
            return False

    async def list_interactions(self, account_id, limit=50, cursor=None):
        query = {"account_id": account_id}
        if cursor:
            occurred_at, interaction_id = self.decode_cursor(cursor)
            query["$or"] = [
                {"occurred_at": {"$lt": occurred_at}},
                {"occurred_at": occurred_at, "interaction_id": {"$gt": interaction_id}},
            ]
        rows = await self.interactions.find(query).sort(
            [("occurred_at", -1), ("interaction_id", 1)]
        ).limit(limit + 1).to_list(limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        cursor = self.encode_cursor({
            "updated_at": rows[-1]["occurred_at"], "account_id": rows[-1]["interaction_id"],
        }) if more and rows else None
        return rows, cursor

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

    async def purge_archived_account_ingestion(self, account_id, archived_at):
        """Remove copied external PII and stop future pulls for an archived account."""
        await self.sync_sources.update_many(
            {"account_id": account_id, "archived_at": None},
            {"$set": {"status": "paused", "archived_at": archived_at}},
        )
        await self.sync_jobs.update_many(
            {"account_id": account_id, "status": {"$in": ["queued", "running"]}},
            {"$set": {"status": "cancelled", "finished_at": archived_at,
                      "lease_expires_at": None}},
        )
        for collection in (
            self.raw_events, self.interactions, self.findings, self.conversations,
        ):
            await collection.delete_many({"account_id": account_id})

    async def create_timeline_activity(self, account_id, activity, updates,
                                       expected_version, audit_entry):
        async def operation(session):
            parent = await self.collection.find_one_and_update(
                {"account_id": account_id, "version": expected_version,
                 "archived_at": None},
                {"$set": updates, "$inc": {"version": 1}}, session=session,
                return_document=ReturnDocument.AFTER,
            )
            if not parent:
                return None
            await self.timeline.insert_one(activity, session=session)
            audit_entry.update({"before": None, "after": activity,
                                "resource_version": parent["version"]})
            await self.audit.insert_one(audit_entry, session=session)
            return parent
        return await self._transaction(operation)

    def resource_collection(self, kind):
        return getattr(self, RESOURCE_COLLECTIONS[kind])

    async def get_resource(self, kind, resource_id, account_id):
        return await self.resource_collection(kind).find_one({
            "resource_id": resource_id, "account_id": account_id, "archived_at": None,
        })

    async def get_any_work_item(self, resource_id, account_id):
        for collection in (self.tasks, self.milestones, self.risks):
            row = await collection.find_one({
                "resource_id": resource_id, "account_id": account_id,
                "archived_at": None,
            })
            if row:
                return row
        return None

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

    async def list_actions(self, actor, attention_limit=100):
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
        now = datetime.now(timezone.utc)
        closed = ["completed", "cancelled", "achieved", "resolved", "accepted"]
        attention_pipeline = [
            {"$match": {"archived_at": None, "status": "active"}},
            {"$lookup": {
                "from": "integration_tasks", "let": {"account_id": "$account_id"},
                "pipeline": [{"$match": {"$expr": {"$eq": ["$account_id", "$$account_id"]},
                                         "archived_at": None, "status": {"$nin": closed},
                                         "due_at": {"$lt": now}}},
                             {"$limit": 2}],
                "as": "attention_overdue_tasks",
            }},
            {"$lookup": {
                "from": "integration_milestones", "let": {"account_id": "$account_id"},
                "pipeline": [{"$match": {"$expr": {"$eq": ["$account_id", "$$account_id"]},
                                         "archived_at": None, "status": {"$nin": closed},
                                         "due_at": {"$lt": now}}},
                             {"$limit": 2}],
                "as": "attention_overdue_milestones",
            }},
            {"$lookup": {
                "from": "integration_risks", "let": {"account_id": "$account_id"},
                "pipeline": [{"$match": {"$expr": {"$eq": ["$account_id", "$$account_id"]},
                                         "archived_at": None, "status": {"$nin": closed},
                                         "$or": [{"type": "blocker"},
                                                 {"severity": {"$in": ["high", "critical"]}}]}},
                             {"$limit": 1}],
                "as": "attention_risks",
            }},
            {"$addFields": {
                "attention_overdue_count": {"$add": [
                    {"$size": "$attention_overdue_tasks"},
                    {"$size": "$attention_overdue_milestones"},
                ]},
            }},
            {"$match": {"$or": [
                {"health_override_enabled": True,
                 "health": {"$in": ["blocked", "at_risk", "escalated"]}},
                {"current_blocker": {"$nin": [None, ""]}},
                {"attention_overdue_count": {"$gte": 2}},
                {"attention_risks.0": {"$exists": True}},
                {"$expr": {"$and": [
                    {"$ne": ["$target_go_live_at", None]},
                    {"$lt": ["$target_go_live_at", now]},
                    {"$lt": [{"$ifNull": ["$completion_percentage", 0]}, 100]},
                ]}},
            ]}},
            {"$sort": {"updated_at": -1, "account_id": 1}},
            {"$limit": attention_limit},
            {"$unset": ["attention_overdue_tasks", "attention_overdue_milestones",
                        "attention_risks", "attention_overdue_count"]},
            *[
                {"$lookup": {
                    "from": collection, "let": {"account_id": "$account_id"},
                    "pipeline": [{"$match": {"$expr": {"$eq": ["$account_id", "$$account_id"]},
                                             "archived_at": None}}],
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
        ]
        attention = await self.collection.aggregate(attention_pipeline).to_list(attention_limit)
        return actions, attention
