"""Optional Google Docs skill source. Normal skills never enter this module.

Published instructions live atomically on the skill record. File reads overlay this
snapshot rather than exposing a partially written skill_files/skill_versions pair.
A fenced lease serializes imports, polls, edits, disconnects and permission changes.
Google revision guards additionally protect against concurrent human edits.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import os
import uuid

import aiohttp
import yaml
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from api import skill_service as skills
from integrations.google_docs_skill_source import GoogleDocsSource, SourceError, all_tabs, block_key, build_requests, parse_markdown, parse_url, read_tab


def enabled():
    return os.environ.get("LOMA_GOOGLE_DOCS_SKILLS_ENABLED", "false").lower() == "true"


def require_enabled():
    if not enabled():
        raise SourceError("Google Docs skill integration is not enabled.", status=403)


async def adapter(actor):
    # Reuse personal OAuth. No service account or importer credential fallback.
    from tools._google_auth import get_google_access_token
    try:
        return GoogleDocsSource(await get_google_access_token(actor))
    except ValueError as exc:
        raise SourceError("Connect or reconnect your Google account in Integrations.", status=403, code="connection_required") from exc


def public_source(source):
    return {k: source.get(k) for k in (
        "type", "document_id", "tab_id", "tab_title", "title", "connection_owner",
        "auto_sync_enabled", "status", "last_checked", "last_published", "hash", "error", "runtime_refresh_pending",
    )}


def skill_md(metadata, body):
    header = {k: metadata.get(k) for k in ("name", "description", "tags")}
    return "---\n" + yaml.safe_dump(header, sort_keys=False, allow_unicode=True) + "---\n\n" + body


def instruction_body(content):
    if not content.startswith("---\n"):
        raise SourceError("Keep the Loma metadata header. Edit the instructions below it.")
    parts = content.split("\n---\n", 1)
    if len(parts) != 2:
        raise SourceError("Invalid skill metadata header.")
    return parts[1].removeprefix("\n")


async def ensure_indexes(db):
    await db.skills.create_index("slug", unique=True)
    await db.skills.create_index([("source.document_id", 1), ("source.tab_id", 1)], unique=True,
                                 partialFilterExpression={"source.type": "google_doc"})
    await db.skill_sync_history.create_index([("skill_slug", 1), ("created_at", -1)])


@asynccontextmanager
async def lease(db, slug):
    token = uuid.uuid4().hex
    now = skills.now_utc()
    doc = await db.skills.find_one_and_update({"slug": slug, "enabled": {"$ne": False},
        "source.type": "google_doc", "$or": [{"source.lease_until": {"$lt": now}}, {"source.lease_until": {"$exists": False}}]},
        {"$set": {"source.lease_token": token, "source.lease_until": now + timedelta(seconds=120)}}, return_document=ReturnDocument.AFTER)
    if not doc:
        raise SourceError("Another sync is running. Retry shortly.", status=409, code="busy")
    try:
        # Operations cannot keep issuing Google writes after the lease expires.
        async with asyncio.timeout(110):
            yield doc, {"slug": slug, "enabled": {"$ne": False}, "source.lease_token": token, "source.lease_until": {"$gt": skills.now_utc()}}
    finally:
        await db.skills.update_one({"slug": slug, "source.lease_token": token}, {"$unset": {"source.lease_token": "", "source.lease_until": ""}})


async def audit(db, slug, actor, status, **extra):
    await db.skill_sync_history.insert_one({"skill_slug": slug, "actor": actor, "status": status, "created_at": skills.now_utc(), **extra})


async def preview(db, actor, url, tab_id=None):
    require_enabled()
    source = await adapter(actor)
    document_id = parse_url(url)
    can_edit = await source.can_edit(document_id)
    doc = await source.read(document_id)
    tabs = [{"id": t["tabProperties"]["tabId"], "title": t["tabProperties"].get("title", "")} for t in all_tabs(doc)]
    result = {"document_id": document_id, "title": doc.get("title", ""), "tabs": tabs, "can_edit": can_edit, "connection_owner": actor}
    if tab_id or len(tabs) == 1:
        result.update({k: v for k, v in read_tab(doc, tab_id).items() if k not in ("blocks", "revision")})
    return result


async def import_doc(db, actor, *, url, tab_id, slug, name, description, tags=None, scope="personal", preview_hash=None, confirm_workspace=False):
    require_enabled()
    if scope not in ("personal", "workspace") or (scope == "workspace" and not confirm_workspace):
        raise SourceError("Confirm workspace publication or choose personal visibility.")
    slug = skills.slugify(slug)
    info = await preview(db, actor, url, tab_id)
    if not info.get("can_edit"):
        raise SourceError("Two-way linking requires edit access to this Google Doc.", status=403)
    if not preview_hash or preview_hash != info.get("hash"):
        raise SourceError("The source changed since preview. Preview it again before importing.", status=409, code="conflict")
    metadata = {"name": name.strip(), "description": description.strip(), "tags": tags or []}
    if not metadata["name"] or not metadata["description"] or not isinstance(metadata["tags"], list):
        raise SourceError("Name, description and a tag list are required.")
    content = skill_md(metadata, info["content"])
    skills.validate_skill_package([skills.validate_text_file("SKILL.md", content)])
    await ensure_indexes(db)
    now = skills.now_utc()
    version_id = uuid.uuid4().hex
    record = {"slug": slug, **metadata, "scope": scope, "enabled": True, "created_at": now, "updated_at": now,
        "created_by": actor, "updated_by": actor, "latest_version_id": version_id, "source": {
            "type": "google_doc", "document_id": info["document_id"], "tab_id": info["tab_id"],
            "tab_title": info["tab_title"], "title": info["title"], "connection_owner": actor,
            "auto_sync_enabled": True, "status": "up_to_date", "hash": info["hash"],
            "published_content": content, "last_checked": now, "last_published": now,
            "next_check": now + timedelta(seconds=300), "runtime_refresh_pending": True}}
    try:
        await db.skills.insert_one(record)
    except DuplicateKeyError as exc:
        raise SourceError("That slug or document tab is already linked. Choose the existing skill.", status=409) from exc
    await repair_version(db, record, actor)
    await audit(db, slug, actor, "imported")
    await refresh_runtime(db, slug)
    return await skills.get_skill(db, slug)


async def repair_version(db, record, actor):
    # Deterministic id makes reconciliation idempotent after a process crash.
    version_id = record["latest_version_id"]
    files = await skills._load_files(db, record["slug"])
    files = [f for f in files if f["path"] != "SKILL.md"]
    files.insert(0, skills.validate_text_file("SKILL.md", record["source"]["published_content"]))
    await db.skill_versions.update_one({"version_id": version_id}, {"$setOnInsert": {
        "version_id": version_id, "skill_slug": record["slug"], "actor_email": actor,
        "source": "google_doc", "message": "Synced Google Docs instructions", "files_snapshot": files,
        "created_at": record["source"]["last_published"]}}, upsert=True)


async def refresh_runtime(db, slug):
    try:
        from api.routes import _refresh_skill_prompt_cache
        if not await _refresh_skill_prompt_cache():
            return
        await db.skills.update_one({"slug": slug}, {"$set": {"source.runtime_refresh_pending": False}})
    except Exception:
        # The published version is durable; next poll retries cache refresh.
        pass


async def publish(db, record, guard, snapshot, actor):
    source = record["source"]
    now = skills.now_utc()
    changes = {"source.last_checked": now, "source.next_check": now + timedelta(seconds=300),
        "source.status": "up_to_date", "source.error": None, "source.failures": 0,
        "source.title": snapshot["title"], "source.tab_title": snapshot["tab_title"], "source.revision": snapshot["revision"]}
    if source.get("hash") != snapshot["hash"]:
        content = skill_md(record, snapshot["content"])
        skills.validate_skill_package([skills.validate_text_file("SKILL.md", content)])
        changes.update({"source.published_content": content, "source.hash": snapshot["hash"],
            "source.last_published": now, "updated_at": now, "updated_by": actor,
            "latest_version_id": uuid.uuid4().hex, "source.runtime_refresh_pending": True})
    # Fence rechecked at write time, not at read time.
    guard["source.lease_until"] = {"$gt": skills.now_utc()}
    updated = await db.skills.find_one_and_update(guard, {"$set": changes, "$unset": {"source.pending": ""}}, return_document=ReturnDocument.AFTER)
    if not updated:
        raise SourceError("Sync lease expired. The next sync will reconcile the source.", status=409)
    await repair_version(db, updated, actor)
    if updated["source"].get("runtime_refresh_pending"):
        await refresh_runtime(db, record["slug"])


async def sync(db, slug, actor=None):
    require_enabled()
    slug = skills.slugify(slug)
    if actor:
        await skills.check_linked_access(db, slug, actor=actor, write=True)
    async with lease(db, slug) as (record, guard):
        owner = record["source"]["connection_owner"]
        try:
            source = await adapter(owner)
            await source.can_edit(record["source"]["document_id"])
            doc = await source.read(record["source"]["document_id"])
            snapshot = read_tab(doc, record["source"]["tab_id"])
            await publish(db, record, guard, snapshot, owner)
            await audit(db, slug, actor or owner, "synced")
        except (SourceError, TimeoutError, aiohttp.ClientError) as exc:
            code = getattr(exc, "code", "temporary_error")
            failures = record["source"].get("failures", 0) + 1
            status = "suspended" if code in ("access_revoked", "connection_required") else "invalid" if code == "invalid_source" else "stale"
            await db.skills.update_one(guard, {"$set": {"source.status": status, "source.error": str(exc) or "Google request timed out",
                "source.last_checked": skills.now_utc(), "source.failures": failures,
                "source.next_check": skills.now_utc() + timedelta(seconds=min(3600, 60 * 2 ** min(failures, 6)))}})
            await audit(db, slug, actor or owner, status)
            raise
    if actor:
        return await skills.get_skill(db, slug)


async def write_instructions(db, slug, content, actor, base_hash):
    require_enabled()
    await skills.check_linked_access(db, slug, actor=actor, write=True)
    async with lease(db, slug) as (record, guard):
        source_config = record["source"]
        if source_config.get("pending"):
            raise SourceError("Reconcile the pending save with Sync now before editing again.", status=409)
        body = instruction_body(content)
        if skills.parse_skill_frontmatter(content) != skills.parse_skill_frontmatter(source_config["published_content"]):
            raise SourceError("Linked skill metadata is managed in Loma. Change only the instruction body.")
        source = await adapter(actor)
        if not await source.can_edit(source_config["document_id"]):
            raise SourceError("Your own Google account needs edit access to save.", status=403)
        doc = await source.read(source_config["document_id"])
        snapshot = read_tab(doc, source_config["tab_id"])
        if not base_hash or snapshot["hash"] != base_hash:
            raise SourceError("The Google Doc changed. Your draft is preserved. Sync now, compare the latest source, then revise and resubmit.", status=409, code="conflict")
        requests = build_requests(snapshot, body)
        # Durable source backup BEFORE any Google write; only the editor can view it.
        operation_id = uuid.uuid4().hex
        await audit(db, slug, actor, "write_pending", operation_id=operation_id, source_backup={"revisionId": doc.get("revisionId"), "snapshot": snapshot}, draft=content)
        result = await db.skills.update_one(guard, {"$set": {"source.pending": {"operation_id": operation_id, "actor": actor}, "source.status": "publication_pending"}})
        if not result.matched_count:
            raise SourceError("Sync lease expired. Retry after syncing.", status=409)
        try:
            await source.write(source_config["document_id"], snapshot, requests)
            confirmed = read_tab(await source.read(source_config["document_id"]), source_config["tab_id"])
            if [block_key(b) for b in confirmed["blocks"]] != [block_key(b) for b in parse_markdown(body)]:
                raise SourceError("Google read-back differs from the submitted draft. Review the source before retrying.", status=409, code="conflict")
            await publish(db, record, guard, confirmed, actor)
            await audit(db, slug, actor, "saved", operation_id=operation_id)
        except Exception as exc:
            # Never blindly replay a timed-out batch. Poll reads the authoritative source.
            await db.skills.update_one(guard, {"$set": {"source.next_check": skills.now_utc(), "source.error": "Save outcome requires reconciliation. Sync now before editing again."}})
            if isinstance(exc, SourceError) and exc.code == "conflict":
                raise
            raise SourceError("Google save could not be confirmed or local publication is pending. Your draft is preserved; use Sync now before retrying.", status=503, code="publication_pending") from exc
    return await skills.get_skill(db, slug)


async def configure(db, slug, actor, action):
    await skills.check_linked_access(db, slug, actor=actor, write=True, owner_only=True)
    async with lease(db, slug) as (record, guard):
        if action == "disconnect":
            # Persist the last validated snapshot before removing source ownership.
            file_doc = skills.validate_text_file("SKILL.md", record["source"]["published_content"])
            await db.skill_files.update_one({"skill_slug": slug, "path": "SKILL.md"}, {"$set": {**file_doc, "skill_slug": slug, "deleted": False}}, upsert=True)
            # Retain enforced access even after disconnect; do not downgrade to legacy public reads.
            await db.skills.update_one(guard, {"$unset": {"source": ""}, "$set": {"access_controlled": True}})
        elif action in ("pause", "resume"):
            require_enabled()
            await db.skills.update_one(guard, {"$set": {"source.auto_sync_enabled": action == "resume", "source.next_check": skills.now_utc()}})
        else:
            raise SourceError("Unknown source action.")
        await audit(db, slug, actor, action)
    return await skills.get_skill(db, slug)


async def history(db, slug, actor):
    await skills.check_linked_access(db, slug, actor=actor)
    records = await db.skill_sync_history.find({"skill_slug": slug}, {"_id": 0, "source_backup": 0, "draft": 0}).sort("created_at", -1).to_list(100)
    return [skills.serialize_doc(r) for r in records]
