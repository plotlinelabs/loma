"""Bounded, owner/conversation-scoped image cache for dashboard follow-ups.

Raw bytes live in separate TTL documents, never in the growing conversation
record. Each turn rematerializes images through the existing runtime attachment
path, so cleanup of a worker or temporary file does not break the next turn.
"""
import base64
import binascii
import hashlib
import json
from datetime import datetime, timedelta, timezone

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 40 * 1024 * 1024
MAX_FILES = 10
RETENTION = timedelta(days=7)
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def validate_attachments(files):
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError("Attach at most 10 files")
    total = 0
    for f in files:
        if (not isinstance(f, dict) or f.get("type") not in {"image", "text", "binary"}
                or not isinstance(f.get("name"), str) or not f["name"]
                or len(f["name"]) > 255 or not isinstance(f.get("data"), str)):
            raise ValueError("Invalid attachment")
        if f["type"] == "text":
            size = len(f["data"].encode())
        else:
            if len(f["data"]) > 4 * ((MAX_FILE_BYTES + 2) // 3):
                raise ValueError("Each attachment must be at most 10 MB")
            try:
                raw = base64.b64decode(f["data"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Invalid attachment encoding") from exc
            size = len(raw)
            if not size:
                raise ValueError("Empty attachment")
        if f["type"] == "image" and f.get("mimetype") not in IMAGE_TYPES:
            raise ValueError("Unsupported image type")
        total += size
        if size > MAX_FILE_BYTES or total > MAX_REQUEST_BYTES:
            raise ValueError("Attachments exceed the upload size limit")


def image_key(file):
    return hashlib.sha256(json.dumps([file["name"], file["mimetype"], file["data"]]).encode()).hexdigest()


async def cache_chat_images(db, user_email, conversation_id, files):
    """Only call after authentication and conversation access checks.

    Same-image writes are idempotent; simultaneous turns may see different
    snapshots of recent uploads, but never another owner/conversation's data.
    Current-turn uploads always take priority over cached context.
    """
    if db is None or not user_email or not conversation_id:
        return files
    now = datetime.now(timezone.utc)
    scope = {"user_email": user_email, "conversation_id": conversation_id}
    current_keys = set()
    for f in files:
        if f["type"] != "image":
            continue
        key = image_key(f)
        current_keys.add(key)
        doc_id = hashlib.sha256(json.dumps([user_email, conversation_id, key]).encode()).hexdigest()
        await db.chat_images.update_one({"_id": doc_id}, {"$set": {
            **scope, "image_key": key, "name": f["name"], "mimetype": f["mimetype"],
            "data": base64.b64decode(f["data"]), "created_at": now,
            "expires_at": now + RETENTION,
        }}, upsert=True)
    docs = await db.chat_images.find({
        **scope, "expires_at": {"$gt": now},
    }).sort("created_at", -1).limit(MAX_FILES).to_list(MAX_FILES)
    result = list(files)
    size = sum(len(f["data"]) for f in result)
    for doc in docs:
        if doc["image_key"] in current_keys or len(result) >= MAX_FILES:
            continue
        data = base64.b64encode(doc["data"]).decode()
        if size + len(data) > MAX_REQUEST_BYTES:
            continue
        result.append({"name": doc["name"], "mimetype": doc["mimetype"], "type": "image", "data": data})
        size += len(data)
    return result
