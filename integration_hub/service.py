"""Integration Hub application service with account-scoped authorization."""
import uuid
import re
from pymongo.errors import DuplicateKeyError
from datetime import datetime, timezone

from integration_hub.models import (
    PLAYBOOKS, ValidationError, as_utc, calculate_account_health, normalize_activity,
    normalize_create, normalize_project, normalize_source_link, normalize_update,
    normalize_work_item, validate_status_transition, normalize_interaction, normalize_source_mapping,
    normalize_contact,
)

class AccountService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _id(prefix):
        return f"{prefix}_{uuid.uuid4()}"

    def _audit(self, account_id, actor, resource_type, resource_id, action, request_id):
        return {"audit_id": self._id("audit"), "account_id": account_id, "actor": actor,
                "resource_type": resource_type, "resource_id": resource_id, "action": action,
                "request_id": request_id, "created_at": datetime.now(timezone.utc)}

    def build_account(self, data, actor, request_id):
        now = datetime.now(timezone.utc)
        account_id = self._id("acc")
        account = {"account_id": account_id, **normalize_create(data), "created_at": now,
                   "created_by": actor, "updated_at": now, "updated_by": actor,
                   "archived_at": None, "archived_by": None, "version": 1}
        audit = self._audit(account_id, actor, "account", account_id, "account.created", request_id)
        audit.update({"before": None, "after": account, "resource_version": 1})
        return account, audit

    async def create(self, data, actor, request_id):
        account, audit = self.build_account(data, actor, request_id)
        return self.enrich(await self.repository.create(account, audit))

    async def create_idempotent(self, data, actor, request_id, key):
        account, audit = self.build_account(data, actor, request_id)
        if await self.repository.find_active_by_name_key(account["name_key"]):
            raise ValidationError("An active client with this name already exists")
        enriched = self.enrich(account)
        payload = {"account": enriched}
        try:
            response, created = await self.repository.create_idempotent(
                account, audit, actor, key, payload,
            )
        except DuplicateKeyError as exc:
            if await self.repository.find_active_by_name_key(account["name_key"]):
                raise ValidationError("An active client with this name already exists") from exc
            raise
        return response, created

    async def create_contact(self, account, data, actor, request_id):
        now = datetime.now(timezone.utc)
        contact_id = self._id("contact")
        contact = {
            "contact_id": contact_id, "account_id": account["account_id"],
            **normalize_contact(data), "created_at": now, "created_by": actor,
            "archived_at": None,
        }
        audit = self._audit(
            account["account_id"], actor, "contact", contact_id,
            "contact.created", request_id,
        )
        await self.repository.create_contact(contact, audit)
        return await self.get(account["account_id"])

    async def archive_contact(self, account, contact_id, actor, request_id):
        audit = self._audit(
            account["account_id"], actor, "contact", contact_id,
            "contact.archived", request_id,
        )
        contact = await self.repository.archive_contact(
            account["account_id"], contact_id, actor, audit,
        )
        if not contact:
            raise ValidationError("Contact not found")
        return await self.get(account["account_id"])

    async def list(self, *legacy_context, stage=None, health=None, search=None,
                   owner=None, status="active", limit=50, cursor=None):
        query = {"archived_at": None}
        if status == "archived": query = {**query, "archived_at": {"$ne": None}}
        elif status: query["status"] = status
        if stage: query["stage"] = stage
        if search:
            search = search.strip()
            if len(search) > 100:
                raise ValidationError("search must be 100 characters or less")
            query["name"] = {"$regex": re.escape(search), "$options": "i"}
        if owner: query["owner_email"] = owner.lower()
        if hasattr(self.repository, "list_with_health"):
            return await self.repository.list_with_health(query, limit, cursor, health)
        accounts, next_cursor = await self.repository.list(query, limit, cursor)
        enriched = [self.enrich(account) for account in accounts]
        return ([row for row in enriched if row["effective_health"] == health]
                if health else enriched), next_cursor

    async def get(self, account_id, include_archived=False):
        account = await self.repository.get(account_id, include_archived)
        return self.enrich(await self.repository.hydrate(account)) if account else None

    async def list_actions(self, actor, attention_limit=100):
        now = datetime.now(timezone.utc)
        actions, attention = await self.repository.list_actions(actor, attention_limit)
        result = []
        for row in actions:
            account = row.pop("account")
            due = as_utc(row.get("due_at"))
            result.append({**row, "item_id": row["resource_id"], "account_name": account["name"],
                           "is_overdue": bool(due and due < now)})
        enriched = [self.enrich(row) for row in attention]
        return result, [
            row for row in enriched
            if row["effective_health"] in {"blocked", "at_risk", "escalated"}
            or row.get("current_blocker")
        ]

    @staticmethod
    def enrich(account):
        return {**account, **calculate_account_health(account)}

    async def update(self, account, data, actor, expected_version, request_id):
        updates = normalize_update(data)
        if "status" in updates: validate_status_transition(account.get("status", "active"), updates["status"])
        now = datetime.now(timezone.utc)
        updates.update({"updated_at": now, "updated_by": actor})
        audit = self._audit(account["account_id"], actor, "account", account["account_id"], "account.updated", request_id)
        updated = await self.repository.mutate_account(account["account_id"], updates, expected_version, audit)
        if not updated: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"], include_archived=True)

    async def archive(self, account, actor, expected_version, request_id, reason):
        if not reason: raise ValidationError("reason is required")
        validate_status_transition(account.get("status", "active"), "archived")
        now = datetime.now(timezone.utc)
        audit = self._audit(account["account_id"], actor, "account", account["account_id"], f"account.archived: {reason}", request_id)
        updated = await self.repository.mutate_account(account["account_id"], {
            "status": "archived", "archived_at": now, "archived_by": actor,
            "updated_at": now, "updated_by": actor,
        }, expected_version, audit)
        if not updated: raise RuntimeError("version_conflict")
        return self.enrich(updated)

    async def restore(self, account, actor, expected_version, request_id):
        validate_status_transition(account.get("status", "archived"), "active")
        now = datetime.now(timezone.utc)
        audit = self._audit(account["account_id"], actor, "account", account["account_id"], "account.restored", request_id)
        updated = await self.repository.mutate_account(account["account_id"], {
            "status": "active", "archived_at": None, "archived_by": None,
            "updated_at": now, "updated_by": actor,
        }, expected_version, audit)
        if not updated: raise RuntimeError("version_conflict")
        return self.enrich(updated)

    async def create_project(self, account, data, actor, request_id):
        now = datetime.now(timezone.utc); project_id = self._id("project")
        project = {"resource_id": project_id, "project_id": project_id, "account_id": account["account_id"],
                   **normalize_project(data),
                   "version": 1, "created_at": now, "created_by": actor, "updated_at": now,
                   "updated_by": actor, "archived_at": None, "archived_by": None}
        extras = []
        for item_type, title in PLAYBOOKS.get(project.get("playbook"), {}).get("items", ()):
            extras.append((item_type, self._work_item(account["account_id"], {"type": item_type, "title": title, "project_id": project_id}, actor, now)))
        audit = self._audit(account["account_id"], actor, "project", project_id, "project.created", request_id)
        parent = await self.repository.create_resource("project", project, account["version"], audit, extras)
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    async def archive_resource(self, account, kind, resource, actor, expected_version, request_id, reason):
        if not reason: raise ValidationError("reason is required")
        now = datetime.now(timezone.utc)
        audit = self._audit(
            account["account_id"], actor, kind, resource["resource_id"],
            f"{kind}.archived: {reason}", request_id,
        )
        parent = await self.repository.mutate_resource(
            kind, resource["resource_id"], account["account_id"],
            {"archived_at": now, "archived_by": actor, "updated_at": now, "updated_by": actor},
            expected_version, account["version"], audit,
        )
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    def _work_item(self, account_id, data, actor, now=None):
        now = now or datetime.now(timezone.utc); normalized = normalize_work_item(data)
        item_id = self._id("item")
        return {"resource_id": item_id, "item_id": item_id, "account_id": account_id, **normalized,
                "version": 1, "created_at": now, "created_by": actor, "updated_at": now,
                "updated_by": actor, "archived_at": None, "archived_by": None}

    async def validate_references(self, account_id, item, current_id=None):
        if item.get("project_id") and not await self.repository.get_resource("project", item["project_id"], account_id):
            raise ValidationError("project_id does not reference an active project")
        requested = item.get("depends_on", [])
        for dependency in requested:
            if dependency == current_id: raise ValidationError("work item cannot depend on itself")
            found = None
            for kind in ("task", "milestone", "risk"):
                found = found or await self.repository.get_resource(kind, dependency, account_id)
            if not found: raise ValidationError("depends_on contains a missing work item")
        if current_id:
            # A cycle exists when any proposed dependency can already reach the
            # item being edited. Traverse the full graph, not only direct edges.
            pending, visited = list(requested), set()
            while pending:
                dependency = pending.pop()
                if dependency == current_id:
                    raise ValidationError("dependency cycle detected")
                if dependency in visited:
                    continue
                visited.add(dependency)
                found = await self.repository.get_any_work_item(dependency, account_id)
                if found:
                    pending.extend(found.get("depends_on", []))

    async def create_work_item(self, account, data, actor, request_id):
        item = self._work_item(account["account_id"], data, actor)
        await self.validate_references(account["account_id"], item)
        audit = self._audit(account["account_id"], actor, item["type"], item["item_id"], f"{item['type']}.created", request_id)
        parent = await self.repository.create_resource(item["type"], item, account["version"], audit)
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    async def update_work_item(self, account, kind, item, data, actor, expected_version, request_id):
        merged = {**item, **data, "type": kind}; updates = normalize_work_item(merged)
        await self.validate_references(account["account_id"], updates, item["resource_id"])
        self._validate_item_transition(kind, item["status"], updates["status"])
        updates.update({"updated_at": datetime.now(timezone.utc), "updated_by": actor})
        audit = self._audit(account["account_id"], actor, kind, item["resource_id"], f"{kind}.updated", request_id)
        parent = await self.repository.mutate_resource(kind, item["resource_id"], account["account_id"], updates, expected_version, account["version"], audit)
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    async def archive_work_item(self, account, kind, item, actor, expected_version, request_id, reason):
        if not reason: raise ValidationError("reason is required")
        now = datetime.now(timezone.utc)
        audit = self._audit(account["account_id"], actor, kind, item["resource_id"], f"{kind}.archived: {reason}", request_id)
        parent = await self.repository.mutate_resource(kind, item["resource_id"], account["account_id"],
            {"archived_at": now, "archived_by": actor, "updated_at": now, "updated_by": actor},
            expected_version, account["version"], audit)
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    @staticmethod
    def _validate_item_transition(kind, current, target):
        transitions = {
            "task": {"todo": {"in_progress", "cancelled"}, "in_progress": {"blocked", "completed", "cancelled"}, "blocked": {"in_progress", "cancelled"}, "completed": {"in_progress"}, "cancelled": {"todo"}},
            "milestone": {"pending": {"in_progress", "cancelled"}, "in_progress": {"achieved", "missed", "cancelled"}, "missed": {"in_progress"}, "achieved": {"in_progress"}, "cancelled": {"pending"}},
            "risk": {"open": {"mitigating", "accepted", "resolved"}, "mitigating": {"open", "accepted", "resolved"}, "accepted": {"open"}, "resolved": {"open"}},
            "blocker": {"open": {"mitigating", "resolved"}, "mitigating": {"open", "resolved"}, "resolved": {"open"}},
        }
        if target != current and target not in transitions.get(kind, {}).get(current, set()):
            raise ValidationError(f"Cannot transition {kind} from {current} to {target}")

    async def create_activity(self, account, data, actor, request_id):
        activity = normalize_activity(data); now = datetime.now(timezone.utc)
        activity_id = self._id("activity")
        timeline = {
            "activity_id": activity_id, "account_id": account["account_id"],
            "type": activity["type"], "message": activity["message"],
            "created_at": now, "created_by": actor,
        }
        audit = self._audit(
            account["account_id"], actor, "activity", activity_id,
            "timeline.activity_created", request_id,
        )
        updated = await self.repository.create_timeline_activity(
            account["account_id"], timeline,
            {"updated_at": now, "updated_by": actor},
            account["version"], audit,
        )
        if not updated: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    async def create_source_link(self, account, data, actor, request_id):
        now = datetime.now(timezone.utc); link_id = self._id("link")
        link = {"resource_id": link_id, "link_id": link_id, "account_id": account["account_id"], **normalize_source_link(data),
                "version": 1, "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
                "archived_at": None, "archived_by": None}
        audit = self._audit(account["account_id"], actor, "source", link_id, "source.created", request_id)
        parent = await self.repository.create_resource("source", link, account["version"], audit)
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    async def ingest_interaction(self, account, data, actor, request_id):
        now = datetime.now(timezone.utc)
        normalized = normalize_interaction(data)
        if normalized["occurred_at"] is None:
            raise ValidationError("occurred_at is required")
        interaction_id = self._id("int")
        interaction = {
            "interaction_id": interaction_id, "account_id": account["account_id"],
            **normalized, "ingested_at": now, "human_status": "unreviewed",
        }
        audit = self._audit(
            account["account_id"], actor, "interaction", interaction_id,
            "interaction.ingested", request_id,
        )
        audit.update({"before": None, "after": {
            "interaction_id": interaction_id, "source": interaction["source"],
            "conversation_state": interaction["conversation_state"],
        }})
        created = await self.repository.create_interaction(interaction, audit)
        if not created:
            existing = await self.repository.interactions.find_one({
                "account_id": account["account_id"],
                "source": interaction["source"], "tenant_id": interaction["tenant_id"],
                "source_id": interaction["source_id"],
            })
            return existing, False
        return interaction, True

    async def create_sync_source(self, account, data, actor, request_id):
        now = datetime.now(timezone.utc)
        mapping_id = self._id("sync")
        mapping = {
            "mapping_id": mapping_id, "account_id": account["account_id"],
            **normalize_source_mapping(data), "sync_status": "never_synced",
            "last_error": None, "last_synced_at": None, "checkpoint": None,
            "next_sync_at": now,
            "created_at": now, "created_by": actor, "updated_at": now,
            "archived_at": None,
        }
        audit = self._audit(account["account_id"], actor, "sync_source", mapping_id,
                            "sync_source.created", request_id)
        await self.repository.create_sync_source(mapping, audit)
        return mapping

    async def sync_source(self, account, mapping, actor, request_id):
        from integration_hub.read_only_sync import pull
        now = datetime.now(timezone.utc)
        try:
            records = await pull(mapping, actor)
            created = 0
            for record in records:
                _, was_created = await self.ingest_interaction(account, record, actor, request_id)
                created += int(was_created)
            updated = await self.repository.update_sync_result(
                mapping["mapping_id"], status="succeeded", error=None,
                checkpoint={"records_seen": len(records)}, synced_at=now,
            )
            return updated, created, len(records)
        except Exception as exc:
            await self.repository.update_sync_result(
                mapping["mapping_id"], status="failed", error=str(exc)[:500],
                checkpoint=mapping.get("checkpoint"), synced_at=now,
            )
            raise

    async def queue_sync(self, account, mapping, actor, request_id):
        """Persist a pull-only job and acquire a mapping-scoped distributed lock."""
        job = {
            "job_id": self._id("job"), "account_id": account["account_id"],
            "mapping_id": mapping["mapping_id"], "source": mapping["source"],
            "status": "queued", "attempt": 0, "max_attempts": 5,
            "created_at": datetime.now(timezone.utc), "created_by": actor,
            "request_id": request_id, "next_attempt_at": datetime.now(timezone.utc),
        }
        return await self.repository.enqueue_sync_job(job)
