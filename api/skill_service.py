"""Mongo-backed Loma skills and local asset storage.

Skills are logical packages: one required SKILL.md plus optional supporting
files. Text files are stored inline in MongoDB; non-text files are stored on
local disk and referenced from MongoDB.
"""

from __future__ import annotations

import difflib
import hashlib
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from config.app_config import LOMA_SKILL_ASSET_DIR

TEXT_EXTENSIONS = {".md", ".py", ".json", ".xml", ".txt", ".yaml", ".yml", ".csv"}
MAX_INLINE_TEXT_BYTES = int(os.environ.get("LOMA_SKILL_MAX_INLINE_TEXT_BYTES", str(512 * 1024)))
MAX_ASSET_BYTES = int(os.environ.get("LOMA_SKILL_MAX_ASSET_BYTES", str(100 * 1024 * 1024)))
MAX_PACKAGE_BYTES = int(os.environ.get("LOMA_SKILL_MAX_PACKAGE_BYTES", str(500 * 1024 * 1024)))
SKILL_INDEX_LIMIT = int(os.environ.get("LOMA_SKILL_INDEX_LIMIT", "40"))


class SkillError(ValueError):
    """Expected validation or not-found error for skill operations."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def serialize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            result[key] = [serialize_doc(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        else:
            result[key] = value
    return result


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise SkillError("Skill slug is required")
    return slug


def normalize_file_path(path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if not raw:
        raise SkillError("File path is required")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        raise SkillError("File path must be relative and cannot contain '..'")
    normalized = str(pure)
    if normalized in {".", ""}:
        raise SkillError("File path is required")
    return normalized


def is_text_path(path: str) -> bool:
    return Path(path).suffix.lower() in TEXT_EXTENSIONS


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "asset").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return safe or "asset"


def parse_skill_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    try:
        parsed = yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"Invalid SKILL.md frontmatter: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {}


def validate_text_file(path: str, content: str) -> dict[str, Any]:
    normalized = normalize_file_path(path)
    data = content.encode("utf-8")
    if len(data) > MAX_INLINE_TEXT_BYTES:
        raise SkillError(f"{normalized} exceeds inline text limit of {MAX_INLINE_TEXT_BYTES} bytes")
    return {
        "path": normalized,
        "kind": "inline_text",
        "content": content,
        "content_type": mimetypes.guess_type(normalized)[0] or "text/plain",
        "size_bytes": len(data),
        "content_hash": content_hash(data),
    }


def validate_asset_file(path: str, data: bytes, original_filename: str | None = None) -> dict[str, Any]:
    normalized = normalize_file_path(path)
    if len(data) > MAX_ASSET_BYTES:
        raise SkillError(f"{normalized} exceeds asset limit of {MAX_ASSET_BYTES} bytes")
    return {
        "path": normalized,
        "kind": "local_asset",
        "content_type": mimetypes.guess_type(original_filename or normalized)[0] or "application/octet-stream",
        "size_bytes": len(data),
        "content_hash": content_hash(data),
        "original_filename": original_filename or Path(normalized).name,
    }


def validate_skill_package(files: list[dict[str, Any]]) -> None:
    paths = [normalize_file_path(f.get("path", "")) for f in files]
    if paths.count("SKILL.md") != 1:
        raise SkillError("A skill must contain exactly one SKILL.md file")
    if len(set(paths)) != len(paths):
        raise SkillError("Skill contains duplicate file paths")
    total_size = sum(int(f.get("size_bytes") or 0) for f in files)
    if total_size > MAX_PACKAGE_BYTES:
        raise SkillError(f"Skill package exceeds limit of {MAX_PACKAGE_BYTES} bytes")

    skill_md = next((f for f in files if f.get("path") == "SKILL.md"), None)
    if not skill_md or skill_md.get("kind") != "inline_text":
        raise SkillError("SKILL.md must be a text file")
    if not (skill_md.get("content") or "").strip():
        raise SkillError("SKILL.md cannot be empty")
    parse_skill_frontmatter(skill_md.get("content") or "")


def _asset_root() -> Path:
    return Path(LOMA_SKILL_ASSET_DIR).expanduser()


def store_asset(skill_slug: str, path: str, data: bytes, original_filename: str | None = None) -> dict[str, Any]:
    meta = validate_asset_file(path, data, original_filename)
    filename = sanitize_filename(original_filename or Path(path).name)
    target = _asset_root() / "skills" / skill_slug / meta["content_hash"] / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    meta["asset_path"] = str(target)
    return meta


def _skill_metadata_from_files(slug: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    skill_md = next(f for f in files if f["path"] == "SKILL.md")
    frontmatter = parse_skill_frontmatter(skill_md.get("content") or "")
    name = str(frontmatter.get("name") or slug)
    description = str(frontmatter.get("description") or "").strip()
    tags = frontmatter.get("tags") or []
    if isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if not isinstance(tags, list):
        tags = []
    scope = frontmatter.get("scope") or ""
    return {"name": name, "description": description, "tags": [str(t) for t in tags], "scope": scope}


def format_skill_dump_markdown(skill: dict[str, Any]) -> str:
    """Render all inline text files in a skill as one markdown document."""
    slug = str(skill.get("slug") or "skill")
    name = str(skill.get("name") or slug)
    description = str(skill.get("description") or "").strip()
    tags = [str(tag) for tag in (skill.get("tags") or [])]

    lines = [f"# {name}", "", f"Slug: `{slug}`"]
    if description:
        lines.append(f"Description: {description}")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")

    files = skill.get("files") or []
    inline_files = [
        file_doc for file_doc in files
        if file_doc.get("kind") == "inline_text"
    ]
    asset_files = [
        file_doc for file_doc in files
        if file_doc.get("kind") == "local_asset"
    ]

    for file_doc in inline_files:
        path = file_doc.get("path") or "file"
        content = file_doc.get("content") or ""
        lines.extend(["", f"## {path}", "", content.rstrip()])

    if asset_files:
        lines.extend(["", "## Asset files", ""])
        for file_doc in asset_files:
            path = file_doc.get("path") or "asset"
            content_type = file_doc.get("content_type") or "application/octet-stream"
            size = int(file_doc.get("size_bytes") or 0)
            lines.append(f"- `{path}` ({content_type}, {size} bytes)")

    return "\n".join(lines).rstrip() + "\n"


async def ensure_skill_indexes(db) -> None:
    await db.skills.create_index("slug", unique=True)
    await db.skills.create_index("enabled")
    await db.skills.create_index("scope")
    await db.skills.create_index([("updated_at", -1)])
    await db.skill_files.create_index([("skill_slug", 1), ("path", 1)], unique=True)
    await db.skill_files.create_index("content_hash")
    await db.skill_versions.create_index([("skill_slug", 1), ("created_at", -1)])
    await db.skill_versions.create_index("version_id", unique=True)


async def _load_files(db, slug: str, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"skill_slug": slug}
    if not include_disabled:
        query["deleted"] = {"$ne": True}
    docs = await db.skill_files.find(query, {"_id": 0}).sort("path", 1).to_list(1000)
    return [serialize_doc(doc) or {} for doc in docs]


async def get_skill(db, slug: str) -> dict[str, Any]:
    slug = slugify(slug)
    skill = await db.skills.find_one({"slug": slug, "enabled": {"$ne": False}})
    if not skill:
        raise SkillError("Skill not found", status=404)
    files = await _load_files(db, slug)
    skill_doc = serialize_doc(skill) or {}
    skill_doc["scope"] = skill_doc.get("scope") or ("system" if skill_doc.get("created_by") in ("system", "import") else "personal")
    skill_doc["files"] = files
    skill_md = next((f for f in files if f["path"] == "SKILL.md"), None)
    skill_doc["content"] = skill_md.get("content", "") if skill_md else ""
    skill_doc["extra_files"] = {
        f["path"]: f.get("content", "(asset file)")
        for f in files
        if f["path"] != "SKILL.md" and f.get("kind") == "inline_text"
    }
    skill_doc["assets"] = [
        {k: f.get(k) for k in ("path", "content_type", "size_bytes", "content_hash", "original_filename")}
        for f in files
        if f.get("kind") == "local_asset"
    ]
    return skill_doc


async def list_skills(db) -> list[dict[str, Any]]:
    docs = [
        serialize_doc(doc) or {}
        for doc in await db.skills.find({"enabled": {"$ne": False}}, {"_id": 0}).sort("slug", 1).to_list(1000)
    ]
    counts: dict[str, list[dict[str, Any]]] = {}
    async for file_doc in db.skill_files.find(
        {"skill_slug": {"$in": [d["slug"] for d in docs]}, "deleted": {"$ne": True}},
        {"_id": 0, "skill_slug": 1, "path": 1, "kind": 1, "content_type": 1, "size_bytes": 1},
    ):
        counts.setdefault(file_doc["skill_slug"], []).append(serialize_doc(file_doc) or {})
    for doc in docs:
        files = sorted(counts.get(doc["slug"], []), key=lambda f: f["path"])
        doc["files"] = [f["path"] for f in files if f["path"] != "SKILL.md"]
        doc["file_details"] = files
        doc["has_extra_files"] = bool(doc["files"])
        doc["name"] = doc.get("name") or doc["slug"]
        doc["scope"] = doc.get("scope") or ("system" if doc.get("created_by") in ("system", "import") else "personal")
    return docs


async def search_skills(db, query: str) -> list[dict[str, Any]]:
    needle = (query or "").lower().strip()
    skills = await list_skills(db)
    if not needle:
        return skills[:SKILL_INDEX_LIMIT]
    matches = []
    for skill in skills:
        haystack = " ".join([
            skill.get("slug", ""),
            skill.get("name", ""),
            skill.get("description", ""),
            " ".join(skill.get("tags") or []),
        ]).lower()
        if needle in haystack:
            matches.append(skill)
            continue
        skill_md = await db.skill_files.find_one(
            {"skill_slug": skill["slug"], "path": "SKILL.md", "deleted": {"$ne": True}},
            {"content": 1},
        )
        if skill_md and needle in (skill_md.get("content") or "").lower():
            matches.append(skill)
    return matches[:SKILL_INDEX_LIMIT]


async def skill_index_text(db) -> str:
    skills = await list_skills(db)
    if not skills:
        return "No Loma skills are configured yet."
    lines = []
    for skill in skills[:SKILL_INDEX_LIMIT]:
        files = ", ".join(skill.get("files") or [])
        file_part = f" files=[{files}]" if files else ""
        tags = ", ".join(skill.get("tags") or [])
        tag_part = f" tags=[{tags}]" if tags else ""
        lines.append(
            f"- {skill['slug']}: {skill.get('description') or skill.get('name') or skill['slug']}"
            f"{tag_part}{file_part}"
        )
    if len(skills) > SKILL_INDEX_LIMIT:
        lines.append(f"- ... {len(skills) - SKILL_INDEX_LIMIT} more skills; use loma_skills search.")
    return "\n".join(lines)


async def _record_version(db, slug: str, actor: str, source: str, message: str) -> str:
    files = await _load_files(db, slug)
    version_id = uuid.uuid4().hex
    await db.skill_versions.insert_one({
        "version_id": version_id,
        "skill_slug": slug,
        "actor_email": actor,
        "source": source,
        "message": message,
        "files_snapshot": files,
        "created_at": now_utc(),
    })
    await db.skills.update_one({"slug": slug}, {"$set": {"latest_version_id": version_id}})
    return version_id


async def upsert_skill(
    db,
    *,
    slug: str,
    files: list[dict[str, Any]],
    actor: str,
    source: str = "dashboard",
    message: str = "Updated skill",
) -> dict[str, Any]:
    slug = slugify(slug)
    validate_skill_package(files)
    metadata = _skill_metadata_from_files(slug, files)
    scope = metadata.pop("scope", "") or ""
    if not scope:
        scope = "system" if actor in ("system", "import") else "personal"
    timestamp = now_utc()
    existing = await db.skills.find_one({"slug": slug})
    if existing and existing.get("scope"):
        scope = existing["scope"]
    await db.skills.update_one(
        {"slug": slug},
        {
            "$set": {
                **metadata,
                "slug": slug,
                "scope": scope,
                "enabled": True,
                "updated_at": timestamp,
                "updated_by": actor,
            },
            "$setOnInsert": {"created_at": timestamp, "created_by": actor},
        },
        upsert=True,
    )
    for file_doc in files:
        file_doc = {**file_doc, "skill_slug": slug, "updated_at": timestamp, "updated_by": actor, "deleted": False}
        await db.skill_files.update_one(
            {"skill_slug": slug, "path": file_doc["path"]},
            {"$set": file_doc, "$setOnInsert": {"created_at": timestamp, "created_by": actor}},
            upsert=True,
        )
    active_paths = [f["path"] for f in files]
    await db.skill_files.update_many(
        {"skill_slug": slug, "path": {"$nin": active_paths}},
        {"$set": {"deleted": True, "updated_at": timestamp, "updated_by": actor}},
    )
    version_id = await _record_version(db, slug, actor, source, message if existing else "Created skill")
    result = await get_skill(db, slug)
    result["latest_version_id"] = version_id
    return result


async def update_skill_file(
    db,
    *,
    slug: str,
    file_doc: dict[str, Any],
    actor: str,
    source: str = "dashboard",
    message: str | None = None,
) -> dict[str, Any]:
    slug = slugify(slug)
    skill = await db.skills.find_one({"slug": slug, "enabled": {"$ne": False}})
    if not skill:
        raise SkillError("Skill not found", status=404)
    current_files = await _load_files(db, slug)
    merged = [f for f in current_files if f["path"] != file_doc["path"]]
    merged.append(file_doc)
    validate_skill_package(merged)
    metadata = _skill_metadata_from_files(slug, merged)
    timestamp = now_utc()
    await db.skills.update_one(
        {"slug": slug},
        {"$set": {**metadata, "updated_at": timestamp, "updated_by": actor}},
    )
    await db.skill_files.update_one(
        {"skill_slug": slug, "path": file_doc["path"]},
        {"$set": {**file_doc, "skill_slug": slug, "updated_at": timestamp, "updated_by": actor, "deleted": False}},
        upsert=True,
    )
    await _record_version(db, slug, actor, source, message or f"Updated {file_doc['path']}")
    return await get_skill(db, slug)


async def delete_skill_file(db, *, slug: str, path: str, actor: str, source: str = "dashboard") -> dict[str, Any]:
    slug = slugify(slug)
    path = normalize_file_path(path)
    if path == "SKILL.md":
        raise SkillError("SKILL.md cannot be deleted")
    result = await db.skill_files.update_one(
        {"skill_slug": slug, "path": path, "deleted": {"$ne": True}},
        {"$set": {"deleted": True, "updated_at": now_utc(), "updated_by": actor}},
    )
    if result.matched_count == 0:
        raise SkillError("Skill file not found", status=404)
    await _record_version(db, slug, actor, source, f"Deleted {path}")
    return await get_skill(db, slug)


async def update_skill_scope(db, *, slug: str, scope: str, actor: str) -> dict[str, Any]:
    slug = slugify(slug)
    if scope not in ("personal", "workspace"):
        raise SkillError("Scope must be 'personal' or 'workspace'")
    skill = await db.skills.find_one({"slug": slug, "enabled": {"$ne": False}})
    if not skill:
        raise SkillError("Skill not found", status=404)
    if skill.get("scope") == "system" or skill.get("created_by") in ("system", "import"):
        raise SkillError("Cannot change scope of system skills", status=403)
    await db.skills.update_one(
        {"slug": slug},
        {"$set": {"scope": scope, "updated_at": now_utc(), "updated_by": actor}},
    )
    await _record_version(db, slug, actor, "dashboard", f"Changed scope to {scope}")
    return await get_skill(db, slug)


async def delete_skill(db, *, slug: str, actor: str, source: str = "dashboard") -> None:
    slug = slugify(slug)
    result = await db.skills.update_one(
        {"slug": slug, "enabled": {"$ne": False}},
        {"$set": {"enabled": False, "updated_at": now_utc(), "updated_by": actor}},
    )
    if result.matched_count == 0:
        raise SkillError("Skill not found", status=404)
    await _record_version(db, slug, actor, source, "Disabled skill")


async def get_skill_file(db, slug: str, path: str) -> dict[str, Any]:
    slug = slugify(slug)
    path = normalize_file_path(path)
    doc = await db.skill_files.find_one(
        {"skill_slug": slug, "path": path, "deleted": {"$ne": True}},
        {"_id": 0},
    )
    if not doc:
        raise SkillError("Skill file not found", status=404)
    return serialize_doc(doc) or {}


async def history(db, slug: str) -> list[dict[str, Any]]:
    slug = slugify(slug)
    docs = await db.skill_versions.find({"skill_slug": slug}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return [serialize_doc(doc) or {} for doc in docs]


async def version(db, slug: str, version_id: str) -> dict[str, Any]:
    slug = slugify(slug)
    doc = await db.skill_versions.find_one({"skill_slug": slug, "version_id": version_id}, {"_id": 0})
    if not doc:
        raise SkillError("Version not found", status=404)
    return serialize_doc(doc) or {}


def version_skill_md(version_doc: dict[str, Any]) -> str:
    for file_doc in version_doc.get("files_snapshot") or []:
        if file_doc.get("path") == "SKILL.md":
            return file_doc.get("content") or ""
    return ""


async def diff_versions(db, slug: str, from_version: str, to_version: str = "HEAD") -> str:
    from_doc = await version(db, slug, from_version)
    from_text = version_skill_md(from_doc).splitlines(keepends=True)
    if to_version == "HEAD":
        current = await get_skill_file(db, slug, "SKILL.md")
        to_text = (current.get("content") or "").splitlines(keepends=True)
    else:
        to_text = version_skill_md(await version(db, slug, to_version)).splitlines(keepends=True)
    return "".join(difflib.unified_diff(from_text, to_text, fromfile=from_version, tofile=to_version))


async def import_skill_directory(db, source_dir: Path, *, actor: str = "import") -> dict[str, Any]:
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise SkillError(f"Skill directory not found: {source_dir}", status=404)
    slug = slugify(source_dir.name)
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
        rel = normalize_file_path(str(path.relative_to(source_dir)))
        data = path.read_bytes()
        if is_text_path(rel):
            try:
                files.append(validate_text_file(rel, data.decode("utf-8")))
            except UnicodeDecodeError:
                files.append(store_asset(slug, rel, data, path.name))
        else:
            files.append(store_asset(slug, rel, data, path.name))
    return await upsert_skill(db, slug=slug, files=files, actor=actor, source="import", message="Imported skill")


def copy_asset_to_response_path(file_doc: dict[str, Any]) -> Path:
    asset_path = Path(file_doc.get("asset_path") or "")
    root = _asset_root().resolve()
    resolved = asset_path.resolve()
    if not str(resolved).startswith(str(root)):
        raise SkillError("Invalid asset path", status=500)
    if not resolved.is_file():
        raise SkillError("Skill asset not found on disk", status=404)
    return resolved
