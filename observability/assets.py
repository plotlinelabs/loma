"""Asset store — durable owner-scoped records of file-backed outputs.

An Asset is recorded at registration time. Bytes stay on the existing serve
path; this module stores only the pointer (identity, owner, source conversation,
display metadata). Availability is computed at read time, never stored.

Doc shape (`assets` collection):
    file_id: str            — identity of the file-backed output (unique)
    owner_email: str        — generating user; the only permitted reader
    conversation_id: str|None
    name: str
    mime_type: str
    size_bytes: int
    created_at: datetime    — utc; newest-first list
    source: str             — producing provider (`opencode` | `claude` | `worker`)
"""
import asyncio
import contextvars
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pymongo.errors import DuplicateKeyError

from observability.db import get_db

logger = logging.getLogger(__name__)

_recording: contextvars.ContextVar[tuple[str | None, str]] = contextvars.ContextVar(
    "asset_recording", default=(None, "opencode"),
)


@dataclass(frozen=True)
class AssetDescriptor:
    """Typed descriptor supplied at the registration seam.

    `(file_id, owner_email, conversation_id, source)` — display fields are
    read from the process registry the serve path already trusts.
    """

    file_id: str
    owner_email: str
    conversation_id: str | None
    source: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def current_recording() -> tuple[str | None, str]:
    return _recording.get()


def bind_asset_recording(conversation_id: str | None, source: str = "opencode"):
    return _recording.set((conversation_id, source))


def reset_asset_recording(token) -> None:
    _recording.reset(token)


@contextmanager
def asset_recording(conversation_id: str | None, source: str = "opencode"):
    """Bind source-conversation provenance for the registration tap."""
    token = bind_asset_recording(conversation_id, source)
    try:
        yield
    finally:
        reset_asset_recording(token)


def _served_meta(file_id: str) -> dict:
    from api.routes import _served_files
    entry = _served_files.get(file_id) or {}
    return {
        "name": entry.get("original_name") or "",
        "mime_type": entry.get("mime_type") or "application/octet-stream",
        "size_bytes": int(entry.get("size") or 0),
    }


async def ensure_indexes(db) -> None:
    await db.assets.create_index("file_id", unique=True)
    await db.assets.create_index([("owner_email", 1), ("created_at", -1)])


def record_asset(descriptor: AssetDescriptor) -> None:
    """Fail-soft durable write. Safe to call from synchronous registration."""
    try:
        meta = _served_meta(descriptor.file_id)
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_asset(descriptor, meta))
    except Exception:
        logger.exception("Asset recording failed for file_id=%s", descriptor.file_id)


async def _persist_asset(descriptor: AssetDescriptor, meta: dict) -> None:
    try:
        db = get_db()
        if db is None:
            logger.warning(
                "Asset recording skipped; observability db unavailable (file_id=%s)",
                descriptor.file_id,
            )
            return
        await ensure_indexes(db)
        await db.assets.insert_one({
            "file_id": descriptor.file_id,
            "owner_email": descriptor.owner_email,
            "conversation_id": descriptor.conversation_id,
            "name": meta["name"],
            "mime_type": meta["mime_type"],
            "size_bytes": meta["size_bytes"],
            "created_at": _utcnow(),
            "source": descriptor.source,
        })
    except DuplicateKeyError:
        logger.warning("Asset already recorded for file_id=%s", descriptor.file_id)
    except Exception:
        logger.exception("Asset recording failed for file_id=%s", descriptor.file_id)


async def list_assets(owner_email: str) -> list[dict]:
    db = get_db()
    if db is None:
        return []
    await ensure_indexes(db)
    cursor = db.assets.find(
        {"owner_email": owner_email},
        {"_id": 0},
    ).sort("created_at", -1)
    return await cursor.to_list(None)


async def delete_assets_for_conversation(conversation_id: str) -> int:
    """Remove library rows tied to a deleted source conversation."""
    if not conversation_id:
        return 0
    db = get_db()
    if db is None:
        return 0
    result = await db.assets.delete_many({"conversation_id": conversation_id})
    return int(result.deleted_count)


def has_live_serve_source(file_id: str) -> bool:
    """Whether the serve path still knows this file (bytes may still be missing).

    A Mongo pointer with no process-registry entry is a restart leftover and
    is omitted from the Library list. Worker ids keep a durable record, so
    they stay listed even when expired.
    """
    if not file_id:
        return False
    if file_id.startswith("worker-"):
        return True
    try:
        from api.routes import _served_files
        return file_id in _served_files
    except Exception:
        logger.exception("Live-source probe failed for file_id=%s", file_id)
        return False


async def resolve_asset_availability(file_id: str) -> tuple[str, str | None]:
    """Probe the sources the serve path trusts. Reason is the failing condition."""
    if file_id.startswith("worker-"):
        return await _resolve_worker_availability(file_id)
    return _resolve_local_availability(file_id)


def _resolve_local_availability(file_id: str) -> tuple[str, str | None]:
    try:
        from api.routes import _served_files
        entry = _served_files.get(file_id)
        path = (entry or {}).get("path")
        if entry and path and Path(path).is_file():
            return ("available", None)
        if entry and path:
            return ("unavailable", f"bytes missing at {path}")
        return ("unavailable", f"{file_id} is not in the process registry")
    except Exception as exc:
        logger.exception("Asset availability probe failed for file_id=%s", file_id)
        return ("unavailable", f"{file_id}: {exc}")


async def _resolve_worker_availability(file_id: str) -> tuple[str, str | None]:
    try:
        from isolation.artifacts import identifier
        artifact_id = identifier(file_id[len("worker-"):])
    except ValueError:
        return ("unavailable", f"{file_id} is not a worker record")
    db = get_db()
    if db is None:
        return (
            "unavailable",
            f"worker record {artifact_id} cannot be read; observability db is unavailable",
        )
    try:
        row = await db.isolated_artifact_downloads.find_one({"_id": artifact_id})
    except Exception:
        logger.exception("Asset availability probe failed for file_id=%s", file_id)
        return ("unavailable", f"worker record {artifact_id} cannot be read")
    if not row:
        return ("unavailable", f"worker record {artifact_id} is absent")
    expires = row.get("expires_at")
    if expires is None:
        return ("unavailable", f"worker record {artifact_id} has no expires_at")
    if getattr(expires, "tzinfo", None) is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        return ("unavailable", f"worker record expired at {expires.isoformat()}")
    owner = row.get("owner")
    if not owner:
        return ("unavailable", f"worker record {artifact_id} has no owner")
    try:
        from isolation.downloads import open_download
        fd, _entry = await open_download(db, owner, file_id)
        os.close(fd)
    except (ValueError, OSError) as exc:
        return ("unavailable", str(exc))
    return ("available", None)
