"""Integration Hub application service with account-scoped authorization."""
import uuid
from datetime import datetime, timezone

from integration_hub.models import (
    PLAYBOOKS, ValidationError, as_utc, calculate_account_health, normalize_activity,
    normalize_create, normalize_project, normalize_source_link, normalize_update,
    normalize_work_item, validate_status_transition,
)

EDIT_ROLES = {"owner", "editor"}
READ_ROLES = EDIT_ROLES | {"viewer"}


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

    async def create(self, data, actor, request_id):
        now = datetime.now(timezone.utc)
        account_id = self._id("acc")
        account = {"account_id": account_id, **normalize_create(data), "created_at": now,
                   "created_by": actor, "updated_at": now, "updated_by": actor,
                   "archived_at": None, "archived_by": None, "version": 1}
        grant = {"grant_id": self._id("grant"), "account_id": account_id,
                 "principal_email": actor.lower(), "role": "owner", "version": 1,
                 "created_at": now, "created_by": actor, "archived_at": None}
        audit = self._audit(account_id, actor, "account", account_id, "account.created", request_id)
        audit.update({"before": None, "after": account, "resource_version": 1})
        return self.enrich(await self.repository.create(account, grant, audit))

    async def authorize(self, account_id, actor, permission="read", system_role="chatter"):
        if system_role == "admin":
            return "owner"
        grant = await self.repository.get_access(account_id, actor)
        role = grant.get("role") if grant else None
        allowed = EDIT_ROLES if permission == "edit" else READ_ROLES
        return role if role in allowed else None

    async def list(self, actor, system_role, *, stage=None, health=None, search=None,
                   owner=None, status="active", limit=50, cursor=None):
        query = {"archived_at": None}
        if system_role != "admin":
            ids = await self.repository.accessible_account_ids(actor)
            query["account_id"] = {"$in": ids}
        if status == "archived": query = {**query, "archived_at": {"$ne": None}}
        elif status: query["status"] = status
        if stage: query["stage"] = stage
        if health: query["health"] = health
        if search: query["name"] = {"$regex": search, "$options": "i"}
        if owner: query["owner_email"] = owner.lower()
        accounts, next_cursor = await self.repository.list(query, limit, cursor)
        return [self.enrich(account) for account in accounts], next_cursor

    async def get(self, account_id, include_archived=False):
        account = await self.repository.get(account_id, include_archived)
        return self.enrich(await self.repository.hydrate(account)) if account else None

    async def list_actions(self, actor):
        now = datetime.now(timezone.utc)
        actions, attention = await self.repository.list_actions(actor)
        result = []
        for row in actions:
            account = row.pop("account")
            due = as_utc(row.get("due_at"))
            result.append({**row, "item_id": row["resource_id"], "account_name": account["name"],
                           "is_overdue": bool(due and due < now)})
        return result, [self.enrich(row) for row in attention]

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

    def _work_item(self, account_id, data, actor, now=None):
        now = now or datetime.now(timezone.utc); normalized = normalize_work_item(data)
        item_id = self._id("item")
        return {"resource_id": item_id, "item_id": item_id, "account_id": account_id, **normalized,
                "version": 1, "created_at": now, "created_by": actor, "updated_at": now,
                "updated_by": actor, "archived_at": None, "archived_by": None}

    async def validate_references(self, account_id, item, current_id=None):
        if item.get("project_id") and not await self.repository.get_resource("project", item["project_id"], account_id):
            raise ValidationError("project_id does not reference an active project")
        for dependency in item.get("depends_on", []):
            if dependency == current_id: raise ValidationError("work item cannot depend on itself")
            found = None
            for kind in ("task", "milestone", "risk"):
                found = found or await self.repository.get_resource(kind, dependency, account_id)
            if not found: raise ValidationError("depends_on contains a missing work item")
            if current_id and current_id in found.get("depends_on", []):
                raise ValidationError("dependency cycle detected")

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
        audit = self._audit(account["account_id"], actor, "activity", self._id("activity"), activity["message"], request_id)
        audit.update({"activity_type": activity["type"]})
        updated = await self.repository.mutate_account(account["account_id"], {"updated_at": now, "updated_by": actor}, account["version"], audit)
        if not updated: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])

    async def create_grant(self, account, data, actor, request_id):
        email = (data.get("principal_email") or "").strip().lower()
        role = data.get("role")
        if "@" not in email: raise ValidationError("principal_email must be valid")
        if role not in ("owner", "editor", "viewer"): raise ValidationError("grant role is invalid")
        if await self.repository.get_access(account["account_id"], email): raise ValidationError("active grant already exists")
        now = datetime.now(timezone.utc); grant_id = self._id("grant")
        grant = {"grant_id": grant_id, "account_id": account["account_id"], "principal_email": email,
                 "role": role, "version": 1, "created_at": now, "created_by": actor, "archived_at": None}
        audit = self._audit(account["account_id"], actor, "access_grant", grant_id, "access_grant.created", request_id)
        parent = await self.repository.create_grant(grant, account["version"], audit)
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"]), grant

    async def create_source_link(self, account, data, actor, request_id):
        now = datetime.now(timezone.utc); link_id = self._id("link")
        link = {"resource_id": link_id, "link_id": link_id, "account_id": account["account_id"], **normalize_source_link(data),
                "version": 1, "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
                "archived_at": None, "archived_by": None}
        audit = self._audit(account["account_id"], actor, "source", link_id, "source.created", request_id)
        parent = await self.repository.create_resource("source", link, account["version"], audit)
        if not parent: raise RuntimeError("version_conflict")
        return await self.get(account["account_id"])
