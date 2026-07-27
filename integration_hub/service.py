"""Business operations for the manual onboarding foundation."""

import uuid
from datetime import datetime, timezone

from integration_hub.models import (
    PLAYBOOKS, ValidationError, calculate_account_health,
    normalize_activity, normalize_create, normalize_source_link, normalize_update,
    normalize_project, normalize_work_item, validate_status_transition,
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
        return self.enrich(await self.repository.create(account))

    async def list(self, *, stage=None, health=None, search=None, owner=None,
                   status="active", page=1, page_size=50):
        query = {"archived_at": None}
        if status == "archived":
            query = {"archived_at": {"$ne": None}}
        elif status:
            query["status"] = status
        if stage:
            query["stage"] = stage
        if health:
            query["health"] = health
        if search:
            query["name"] = {"$regex": search, "$options": "i"}
        if owner:
            query["owner_email"] = owner.lower()
        total = await self.repository.count(query)
        accounts = await self.repository.list(
            query, limit=page_size, skip=(page - 1) * page_size
        )
        return [self.enrich(account) for account in accounts], total

    async def get(self, account_id, include_archived=False):
        account = (
            await self.repository.get_any(account_id)
            if include_archived else await self.repository.get(account_id)
        )
        return self.enrich(account) if account else None

    async def list_actions(self, actor):
        now = datetime.now(timezone.utc)
        accounts = await self.repository.list_all({"archived_at": None})
        actions = []
        attention_accounts = []
        for account in accounts:
            enriched = self.enrich(account, now)
            for item in account.get("work_items", []):
                if (
                    item.get("owner_email") == actor.lower()
                    and item.get("type") in ("task", "milestone")
                    and item.get("status") != "completed"
                ):
                    due_at = item.get("due_at")
                    actions.append({
                        **item,
                        "account_id": account["account_id"],
                        "account_name": account["name"],
                        "is_overdue": bool(due_at and due_at < now),
                    })
            if enriched["effective_health"] in ("blocked", "at_risk", "escalated"):
                attention_accounts.append(enriched)
        actions.sort(key=lambda item: (
            not item["is_overdue"],
            item.get("due_at") is None,
            item.get("due_at") or now,
        ))
        attention_accounts.sort(key=lambda account: (
            {"escalated": 0, "blocked": 1, "at_risk": 2}.get(account["effective_health"], 3),
            -account["overdue_count"],
        ))
        return actions, attention_accounts

    @staticmethod
    def enrich(account, now=None):
        return {**account, **calculate_account_health(account, now)}

    async def update(self, account, data, actor):
        updates = normalize_update(data)
        if not updates:
            return self.enrich(account)
        if "status" in updates:
            validate_status_transition(account.get("status", "active"), updates["status"])
        activity = self._activity({
            "type": "update",
            "message": f"Updated client fields: {', '.join(sorted(updates))}",
        }, actor)
        updates.update({
            "updated_at": datetime.now(timezone.utc),
            "updated_by": actor,
            "version": account.get("version", 1) + 1,
        })
        updated = await self.repository.update(
            account["account_id"], updates,
            expected_version=account.get("version", 1), activity=activity,
        )
        if not updated:
            raise RuntimeError("version_conflict")
        return self.enrich(updated)

    async def archive(self, account_id, actor, expected_version):
        account = await self.repository.get_any(account_id)
        if not account:
            return None
        validate_status_transition(account.get("status", "active"), "archived")
        now = datetime.now(timezone.utc)
        if expected_version is None:
            raise ValidationError("version is required")
        updated = await self.repository.update(account_id, {
            "status": "archived", "archived_at": now, "archived_by": actor,
            "updated_at": now, "updated_by": actor, "version": account["version"] + 1,
        }, expected_version=expected_version, activity=self._activity({
            "type": "update", "message": "Archived client",
        }, actor))
        if not updated:
            raise RuntimeError("version_conflict")
        return self.enrich(updated)

    async def restore(self, account_id, actor, expected_version):
        account = await self.repository.get_any(account_id)
        if not account:
            return None
        validate_status_transition(account.get("status", "archived"), "active")
        now = datetime.now(timezone.utc)
        if expected_version is None:
            raise ValidationError("version is required")
        updated = await self.repository.update(account_id, {
            "status": "active", "archived_at": None, "archived_by": None,
            "updated_at": now, "updated_by": actor, "version": account["version"] + 1,
        }, expected_version=expected_version, activity=self._activity({
            "type": "update", "message": "Restored client",
        }, actor))
        if not updated:
            raise RuntimeError("version_conflict")
        return self.enrich(updated)

    async def create_project(self, account_id, data, actor):
        normalized = normalize_project(data)
        now = datetime.now(timezone.utc)
        project_id = f"project_{uuid.uuid4()}"
        project = {
            "project_id": project_id, **normalized,
            "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
        }
        items = []
        playbook = normalized.get("playbook")
        for item_type, title in PLAYBOOKS.get(playbook, {}).get("items", ()):
            items.append({
                "item_id": f"item_{uuid.uuid4()}",
                **normalize_work_item({
                    "type": item_type, "title": title, "project_id": project_id,
                }),
                "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
            })
        account = await self.repository.add_project(
            account_id, project, items,
            self._activity({"type": "update", "message": f"Added project: {project['name']}"}, actor),
        )
        return self.enrich(account) if account else None

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
        account = await self.repository.add_work_item(
            account_id,
            item,
            self._activity(
                {"type": "update", "message": f"Added {item['type']}: {item['title']}"},
                actor,
            ),
            self._parent_updates(now, actor),
        )
        return self.enrich(account) if account else None

    async def update_work_item(self, account_id, item, data, actor):
        merged = {**item, **data}
        updates = normalize_work_item(merged)
        updates.update({
            "updated_at": datetime.now(timezone.utc),
            "updated_by": actor,
        })
        account = await self.repository.update_work_item(
            account_id,
            item["item_id"],
            updates,
            self._activity(
                {"type": "update", "message": f"Updated {item['type']}: {item['title']}"},
                actor,
            ),
            self._parent_updates(updates["updated_at"], actor),
        )
        return self.enrich(account) if account else None

    async def delete_work_item(self, account_id, item, actor):
        now = datetime.now(timezone.utc)
        account = await self.repository.delete_work_item(
            account_id,
            item["item_id"],
            self._activity(
                {"type": "update", "message": f"Deleted {item['type']}: {item['title']}"},
                actor,
            ),
            self._parent_updates(now, actor),
        )
        return self.enrich(account) if account else None

    async def create_activity(self, account_id, data, actor):
        activity = self._activity(normalize_activity(data), actor)
        account = await self.repository.append_activity(
            account_id,
            activity,
            self._parent_updates(activity["created_at"], actor),
        )
        return self.enrich(account) if account else None

    async def create_source_link(self, account_id, data, actor):
        now = datetime.now(timezone.utc)
        link = {
            "link_id": f"link_{uuid.uuid4()}",
            **normalize_source_link(data),
            "created_at": now,
            "created_by": actor,
        }
        account = await self.repository.add_source_link(
            account_id,
            link,
            self._activity(
                {"type": "update", "message": f"Added source link: {link['title']}"},
                actor,
            ),
            self._parent_updates(now, actor),
        )
        return self.enrich(account) if account else None

    async def delete_source_link(self, account_id, link, actor):
        now = datetime.now(timezone.utc)
        account = await self.repository.delete_source_link(
            account_id,
            link["link_id"],
            self._activity(
                {"type": "update", "message": f"Deleted source link: {link['title']}"},
                actor,
            ),
            self._parent_updates(now, actor),
        )
        return self.enrich(account) if account else None

    def _activity(self, data, actor):
        return {
            "activity_id": f"activity_{uuid.uuid4()}",
            **data,
            "created_at": datetime.now(timezone.utc),
            "created_by": actor,
        }

    @staticmethod
    def _parent_updates(now, actor):
        return {"updated_at": now, "updated_by": actor}
