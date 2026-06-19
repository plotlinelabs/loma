import asyncio
import json
import logging
import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from aiohttp import web

from agent.client import stream_agent
from agent.opencode_runtime import get_agent_models, get_opencode_pool_status
from agent.pool import ClientPool
from observability.db import get_db
from observability.observer import ConversationObserver
from api.auth_helpers import get_system_role, get_user_email, require_admin, require_analyst_or_above
from api.dashboard_ingestion import ingest_dashboard_chat

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / ".claude" / "skills"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# ── File serving for dashboard chat attachments ────────────────────────────
# Files are registered by the agent during streaming and served via /api/files/<id>
SERVED_FILES_DIR = Path(os.environ.get("SERVED_FILES_DIR", "/tmp/loma-served-files"))
SERVED_FILES_DIR.mkdir(parents=True, exist_ok=True)

# In-memory registry: file_id -> {path, original_name, mime_type, size}
_served_files: dict[str, dict] = {}


def register_served_file(source_path: str, original_name: str | None = None) -> dict:
    """Copy a file to the served directory and register it for download.

    Returns a dict with file_id, url, name, mime_type, size.
    """
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"File not found: {source_path}")

    file_id = uuid.uuid4().hex[:16]
    name = original_name or src.name
    ext = src.suffix
    dest = SERVED_FILES_DIR / f"{file_id}{ext}"

    shutil.copy2(str(src), str(dest))

    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    size = dest.stat().st_size

    entry = {
        "path": str(dest),
        "original_name": name,
        "mime_type": mime_type,
        "size": size,
    }
    _served_files[file_id] = entry

    return {
        "file_id": file_id,
        "url": f"/api/files/{file_id}",
        "name": name,
        "mime_type": mime_type,
        "size": size,
    }


# MIME types that should be displayed inline (preview) rather than downloaded
_INLINE_MIME_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "text/html", "text/plain", "text/csv",
}


async def handle_serve_file(request: web.Request) -> web.Response:
    """GET /api/files/{file_id} — serve a registered file for download or inline preview."""
    file_id = request.match_info["file_id"]
    entry = _served_files.get(file_id)

    # Fallback: try decoding file_id as a base64-encoded file path
    # (backward compat for any persisted file artifact URLs)
    if not entry:
        import base64
        try:
            # Re-add padding stripped during encoding
            padded = file_id + "=" * (-len(file_id) % 4)
            decoded_path = base64.urlsafe_b64decode(padded).decode()
            if os.path.isfile(decoded_path):
                # Register the file so subsequent requests use the fast path
                file_info = register_served_file(decoded_path)
                entry = _served_files.get(file_info["file_id"])
        except Exception:
            pass

    if not entry:
        return web.json_response({"error": "File not found"}, status=404)

    file_path = Path(entry["path"])
    if not file_path.exists():
        del _served_files[file_id]
        return web.json_response({"error": "File expired"}, status=410)

    mime_type = entry["mime_type"]
    filename = entry["original_name"]

    # Use inline disposition for previewable types (PDF, images)
    # so they can be rendered in iframes; attachment for everything else
    if mime_type in _INLINE_MIME_TYPES:
        disposition = f'inline; filename="{filename}"'
    else:
        disposition = f'attachment; filename="{filename}"'

    return web.FileResponse(
        file_path,
        headers={
            "Content-Disposition": disposition,
            "Content-Type": mime_type,
            "Access-Control-Allow-Origin": "*",
        },
    )

logger = logging.getLogger(__name__)

# Claude Agent SDK models surfaced in the dashboard picker, newest first.
# The first entry is treated as the headline/default Claude model.
SUPPORTED_CLAUDE_MODEL_IDS = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
)

FAVORITE_MODEL_IDS = (
    "opencode-go/deepseek-v4-flash",
    "anthropic/claude-opus-4-8",
    "anthropic/claude-opus-4-7",
    "anthropic/claude-opus-4-6",
    "openai/gpt-5.5",
)

FAVORITE_MODEL_TEMPLATES = {
    "opencode-go/deepseek-v4-flash": {
        "id": "opencode-go/deepseek-v4-flash",
        "provider_id": "opencode-go",
        "model_id": "deepseek-v4-flash",
        "label": "OpenCode Go · DeepSeek V4 Flash",
        "context_limit": None,
        "supports_attachments": False,
        "supports_reasoning": True,
        "status": "active",
        "cost": {},
    },
    "openai/gpt-5.5": {
        "id": "openai/gpt-5.5",
        "provider_id": "openai",
        "model_id": "gpt-5.5",
        "label": "OpenAI · GPT-5.5",
        "context_limit": None,
        "supports_attachments": True,
        "supports_reasoning": True,
        "status": "active",
        "cost": {},
    },
}


def _ensure_favorite_models(models: list[dict]) -> list[dict]:
    """Ensure configured favorite provider/model aliases are available."""
    by_id = {model.get("id"): model for model in models}
    if os.environ.get("OPENCODE_API_KEY") and "opencode-go/deepseek-v4-flash" not in by_id:
        models.append(FAVORITE_MODEL_TEMPLATES["opencode-go/deepseek-v4-flash"])
    if os.environ.get("OPENAI_API_KEY") and "openai/gpt-5.5" not in by_id:
        models.append(FAVORITE_MODEL_TEMPLATES["openai/gpt-5.5"])
    return models


def _recommended_model_rank(model: dict) -> int | None:
    """Return favorite rank for the model picker, or None for regular models."""
    full_id = (model.get("id") or "").lower()
    for index, favorite_id in enumerate(FAVORITE_MODEL_IDS):
        if full_id == favorite_id:
            return index
    return None


def _order_agent_models(models: list[dict]) -> list[dict]:
    """Put favorite dashboard models first, preserving all others after."""
    fallback_rank = len(FAVORITE_MODEL_IDS)

    ordered = []
    for index, model in enumerate(models):
        favorite_rank = _recommended_model_rank(model)
        is_recommended = favorite_rank is not None
        ordered.append((
            favorite_rank if favorite_rank is not None else fallback_rank + index,
            index,
            {**model, "recommended": is_recommended},
        ))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [model for _, __, model in ordered]


# Valid topic categories for LLM classification
_VALID_TOPICS = [
    "debugging", "integration", "billing", "feature-request",
    "campaign", "sdk", "data", "security", "documentation", "other",
]

# Track whether the text search index has been ensured this process
_search_index_ensured = False


def _serialize(doc):
    """Make a MongoDB document JSON-serializable."""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [_serialize(d) for d in doc]
    if isinstance(doc, dict):
        result = {}
        for k, v in doc.items():
            if k == "_id":
                result[k] = str(v)
            elif isinstance(v, datetime):
                result[k] = v.isoformat()
            elif isinstance(v, dict):
                result[k] = _serialize(v)
            elif isinstance(v, list):
                result[k] = _serialize(v)
            else:
                result[k] = v
        return result
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


async def _ensure_search_index(db) -> bool:
    """Ensure the text search index exists on conversations collection.

    Creates a compound text index on prompt, title, and final_response
    fields. This is idempotent — MongoDB's create_index is a no-op if
    the index already exists.

    Returns True if the index is available, False otherwise.
    """
    global _search_index_ensured
    if _search_index_ensured:
        return True
    try:
        await db.conversations.create_index(
            [("prompt", "text"), ("title", "text"), ("final_response", "text")],
            name="search_text_index",
            background=True,
        )
        _search_index_ensured = True
        return True
    except Exception as e:
        logger.warning("Failed to create search text index: %s", e)
        return False


async def _generate_title_llm(prompt: str, response_snippet: str = "") -> str:
    """Generate a short 5-word conversation title using Claude Haiku.

    Uses the claude CLI (same pattern as observability/confidence.py).
    Returns 'Untitled conversation' on any failure.
    """
    context = prompt[:500]
    if response_snippet:
        context += f"\n\nAgent response: {response_snippet[:300]}"

    message = (
        "Generate a concise title (max 5 words) for this conversation. "
        "The title should capture the main intent or topic. "
        "Reply with ONLY the title, no quotes, no explanation.\n\n"
        f"Conversation:\n{context}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", message,
            "--model", "claude-haiku-4-5-20251001",
            "--max-turns", "1",
            "--output-format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            logger.warning("Title generation CLI failed (rc=%d)", proc.returncode)
            return "Untitled conversation"

        output = stdout.decode().strip()
        try:
            envelope = json.loads(output)
            raw = envelope.get("result", output)
        except json.JSONDecodeError:
            raw = output

        # Clean up: remove quotes, trim, limit to ~8 words max
        title = raw.strip().strip('"').strip("'").strip()
        words = title.split()
        if len(words) > 8:
            title = " ".join(words[:8])
        return title or "Untitled conversation"

    except asyncio.TimeoutError:
        logger.warning("Title generation timed out")
        return "Untitled conversation"
    except Exception as e:
        logger.warning("Title generation failed: %s", e)
        return "Untitled conversation"


async def _classify_topic_llm(prompt: str, response_snippet: str = "") -> str:
    """Classify a conversation into a topic category using Claude Haiku.

    Returns one of the _VALID_TOPICS values, or 'other' on failure.
    """
    context = prompt[:500]
    if response_snippet:
        context += f"\n\nAgent response: {response_snippet[:300]}"

    valid_list = ", ".join(_VALID_TOPICS)
    message = (
        f"Classify this conversation into exactly one topic category.\n"
        f"Valid categories: {valid_list}\n\n"
        f"Reply with ONLY the category name, nothing else.\n\n"
        f"Conversation:\n{context}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", message,
            "--model", "claude-haiku-4-5-20251001",
            "--max-turns", "1",
            "--output-format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            return "other"

        output = stdout.decode().strip()
        try:
            envelope = json.loads(output)
            raw = envelope.get("result", output)
        except json.JSONDecodeError:
            raw = output

        topic = raw.strip().lower().strip('"').strip("'").strip()
        return topic if topic in _VALID_TOPICS else "other"

    except Exception as e:
        logger.warning("Topic classification failed: %s", e)
        return "other"


async def _enrich_conversation(db, conversation: dict) -> dict:
    """Enrich a conversation with title and topic if missing.

    Generates them via LLM and persists back to the DB.
    """
    needs_update = {}
    prompt = conversation.get("prompt", "")
    response_snippet = (conversation.get("final_response") or "")[:300]

    if not conversation.get("title") and not conversation.get("title_edited"):
        title = await _generate_title_llm(prompt, response_snippet)
        conversation["title"] = title
        needs_update["title"] = title

    if not conversation.get("topic"):
        topic = await _classify_topic_llm(prompt, response_snippet)
        conversation["topic"] = topic
        needs_update["topic"] = topic

    if needs_update:
        try:
            await db.conversations.update_one(
                {"conversation_id": conversation["conversation_id"]},
                {"$set": needs_update},
            )
        except Exception as e:
            logger.warning("Failed to persist enrichment for %s: %s",
                           conversation.get("conversation_id"), e)

    return conversation


async def handle_list_conversations(request: web.Request) -> web.Response:
    """GET /api/conversations — paginated list with filters."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    # Ensure search index exists (idempotent)
    has_text_index = await _ensure_search_index(db)

    # Parse query params
    source = request.query.get("source")
    category = request.query.get("category")
    status = request.query.get("status")
    search = request.query.get("search", "").strip()
    person = request.query.get("person", "").strip()
    topic = request.query.get("topic", "").strip()
    page = int(request.query.get("page", 1))
    per_page = min(int(request.query.get("per_page", 50)), 100)

    query: dict = {"deleted": {"$ne": True}}
    # Track isolation condition separately to avoid $or key conflicts with search
    isolation_condition: dict | None = None

    # ── Chat isolation ──
    user_email = get_user_email(request)
    system_role = get_system_role(request)

    if system_role == "admin":
        # Admin sees all — allow optional person filter
        if person:
            query["metadata.user_name"] = {"$regex": person, "$options": "i"}
    elif system_role == "maintainer":
        # Maintainer sees own conversations + shared flow/task conversations (like analyst)
        isolation_condition = {
            "$or": [
                {"metadata.user_name": user_email},
                {"source": "task_step"},
                {
                    "source": {"$in": ["flow", "webhook"]},
                    "metadata.visibility": {"$ne": "private"},
                },
            ]
        }
    elif system_role == "analyst":
        # Analyst sees own conversations + shared flow/task conversations.
        # Private flow conversations are only visible to the creator
        # (matched via metadata.user_name in the first branch).
        isolation_condition = {
            "$or": [
                {"metadata.user_name": user_email},
                {"source": "task_step"},
                {
                    "source": {"$in": ["flow", "webhook"]},
                    "metadata.visibility": {"$ne": "private"},
                },
            ]
        }
    else:
        # operator / chatter — own conversations only
        query["metadata.user_name"] = user_email

    if source:
        query["source"] = source
    if category:
        query["confidence.category"] = category
    if status:
        query["status"] = status
    if topic and topic in _VALID_TOPICS:
        query["topic"] = topic

    # Full-text search
    if search:
        if has_text_index:
            query["$text"] = {"$search": search}
        else:
            # Fallback to regex search if text index isn't ready
            query["$or"] = [
                {"prompt": {"$regex": search, "$options": "i"}},
                {"title": {"$regex": search, "$options": "i"}},
                {"final_response": {"$regex": search, "$options": "i"}},
            ]

    # Merge isolation condition with $and to avoid $or key conflicts
    if isolation_condition:
        final_query = {"$and": [query, isolation_condition]} if query else isolation_condition
    else:
        final_query = query

    total = await db.conversations.count_documents(final_query)

    # If using $text search, sort by relevance score; otherwise by time
    if search and has_text_index and "$text" in query:
        conversations = await db.conversations.find(
            final_query, {"score": {"$meta": "textScore"}}
        ).sort(
            [("score", {"$meta": "textScore"})]
        ).skip((page - 1) * per_page).limit(per_page).to_list(per_page)
    else:
        conversations = await db.conversations.find(final_query) \
            .sort("started_at", -1) \
            .skip((page - 1) * per_page) \
            .limit(per_page) \
            .to_list(per_page)

    return web.json_response({
        "conversations": _serialize(conversations),
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": (total + per_page - 1) // per_page,
    })


async def handle_get_conversation(request: web.Request) -> web.Response:
    """GET /api/conversations/{conversation_id} — single conversation + turns."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    cid = request.match_info["conversation_id"]
    conversation = await db.conversations.find_one({"conversation_id": cid, "deleted": {"$ne": True}})
    if not conversation:
        return web.json_response({"error": "Not found"}, status=404)

    # ── Chat isolation ──
    user_email = get_user_email(request)
    system_role = get_system_role(request)
    owner = (conversation.get("metadata") or {}).get("user_name", "")
    conv_source = conversation.get("source", "")

    if system_role == "admin":
        pass  # admin sees everything
    elif system_role in ("maintainer", "analyst"):
        # maintainer/analyst can see own conversations + flow/task-spawned
        if owner != user_email and conv_source not in ("flow", "task_step"):
            return web.json_response({"error": "Not found"}, status=404)
    else:
        # operator / chatter — own conversations only
        if owner != user_email:
            return web.json_response({"error": "Not found"}, status=404)

    turns = await db.turns.find({"conversation_id": cid}) \
        .sort("turn_number", 1) \
        .to_list(None)

    # Include persisted artifacts if available
    artifacts = await db.artifacts.find({"conversation_id": cid}).to_list(None)

    return web.json_response({
        "conversation": _serialize(conversation),
        "turns": _serialize(turns),
        "artifacts": _serialize(artifacts),
    })


async def handle_get_stats(request: web.Request) -> web.Response:
    """GET /api/stats — aggregate stats."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    total = await db.conversations.count_documents({})
    by_source = await db.conversations.aggregate([
        {"$group": {"_id": "$source", "count": {"$sum": 1}}}
    ]).to_list(20)
    by_category = await db.conversations.aggregate([
        {"$group": {"_id": "$confidence.category", "count": {"$sum": 1}}}
    ]).to_list(20)
    by_status = await db.conversations.aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]).to_list(20)

    return web.json_response({
        "total_conversations": total,
        "by_source": {item["_id"]: item["count"] for item in by_source if item["_id"]},
        "by_category": {item["_id"]: item["count"] for item in by_category if item["_id"]},
        "by_status": {item["_id"]: item["count"] for item in by_status if item["_id"]},
    })


async def handle_cost_stats(request: web.Request) -> web.Response:
    """GET /api/cost-stats — daily cost aggregations with savings analytics."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    days = int(request.query.get("days", 30))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {
            "started_at": {"$gte": since},
            "cost": {"$ne": None},
        }},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$started_at"}},
            "total_cost_usd": {"$sum": {"$ifNull": ["$cost.total_cost_usd", 0]}},
            "agent_cost_usd": {"$sum": {"$ifNull": ["$cost.agent_cost_usd", 0]}},
            "confidence_cost_usd": {"$sum": {"$ifNull": ["$cost.confidence_cost_usd", 0]}},
            "input_tokens": {"$sum": {"$ifNull": ["$cost.input_tokens", 0]}},
            "output_tokens": {"$sum": {"$ifNull": ["$cost.output_tokens", 0]}},
            "conversations": {"$sum": 1},
            # Savings aggregations
            "estimated_human_cost_usd": {"$sum": {"$ifNull": ["$savings.estimated_human_cost_usd", 0]}},
            "savings_usd": {"$sum": {"$ifNull": ["$savings.savings_usd", 0]}},
            "estimated_human_duration_minutes": {"$sum": {"$ifNull": ["$savings.estimated_human_duration_minutes", 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]

    daily = await db.conversations.aggregate(pipeline).to_list(days + 1)

    total_cost = sum(d["total_cost_usd"] for d in daily)
    total_conversations = sum(d["conversations"] for d in daily)
    total_input = sum(d["input_tokens"] for d in daily)
    total_output = sum(d["output_tokens"] for d in daily)
    avg_cost = round(total_cost / total_conversations, 6) if total_conversations > 0 else 0

    # Savings totals
    total_human_cost = sum(d["estimated_human_cost_usd"] for d in daily)
    total_savings = sum(d["savings_usd"] for d in daily)
    total_human_minutes = sum(d["estimated_human_duration_minutes"] for d in daily)
    savings_percentage = round((total_savings / total_human_cost) * 100, 1) if total_human_cost > 0 else 0

    for d in daily:
        d["date"] = d.pop("_id")

    return web.json_response({
        "daily": daily,
        "total_cost_usd": round(total_cost, 4),
        "total_conversations": total_conversations,
        "avg_cost_per_conversation": round(avg_cost, 4),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        # Savings summary
        "total_estimated_human_cost_usd": round(total_human_cost, 2),
        "total_savings_usd": round(total_savings, 2),
        "total_estimated_human_minutes": round(total_human_minutes, 1),
        "savings_percentage": savings_percentage,
    })


async def handle_token_usage(request: web.Request) -> web.Response:
    """GET /api/token-usage — token usage breakdown by user and flow."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    days = int(request.query.get("days", 30))
    type_filter = request.query.get("type", "")
    name_filter = request.query.get("name", "")
    since = datetime.now(timezone.utc) - timedelta(days=days)

    base_match = {"started_at": {"$gte": since}, "cost": {"$ne": None}}

    async def _user_pipeline():
        match = {**base_match, "source": {"$nin": ["flow", "webhook"]}, "metadata.user_name": {"$ne": None}}
        if name_filter:
            match["metadata.user_name"] = {"$regex": name_filter, "$options": "i"}
        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": "$metadata.user_name",
                "input_tokens": {"$sum": {"$ifNull": ["$cost.input_tokens", 0]}},
                "output_tokens": {"$sum": {"$ifNull": ["$cost.output_tokens", 0]}},
                "conversations": {"$sum": 1},
            }},
            {"$sort": {"input_tokens": -1}},
        ]
        rows = await db.conversations.aggregate(pipeline).to_list(500)
        for r in rows:
            r["type"] = "user"
            r["name"] = r.pop("_id") or "(unknown)"
            r["total_tokens"] = r["input_tokens"] + r["output_tokens"]
        return rows

    async def _flow_pipeline():
        match = {**base_match, "source": {"$in": ["flow", "webhook"]}, "metadata.flow_name": {"$ne": None}}
        if name_filter:
            match["metadata.flow_name"] = {"$regex": name_filter, "$options": "i"}
        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": {"flow_id": "$metadata.flow_id", "flow_name": "$metadata.flow_name"},
                "input_tokens": {"$sum": {"$ifNull": ["$cost.input_tokens", 0]}},
                "output_tokens": {"$sum": {"$ifNull": ["$cost.output_tokens", 0]}},
                "conversations": {"$sum": 1},
            }},
            {"$sort": {"input_tokens": -1}},
        ]
        rows = await db.conversations.aggregate(pipeline).to_list(500)
        for r in rows:
            r["type"] = "flow"
            r["name"] = r["_id"].get("flow_name") or r["_id"].get("flow_id") or "(unknown)"
            r["flow_id"] = r["_id"].get("flow_id")
            del r["_id"]
            r["total_tokens"] = r["input_tokens"] + r["output_tokens"]
        return rows

    if type_filter == "user":
        rows = await _user_pipeline()
    elif type_filter == "flow":
        rows = await _flow_pipeline()
    else:
        user_rows, flow_rows = await asyncio.gather(_user_pipeline(), _flow_pipeline())
        rows = user_rows + flow_rows

    rows.sort(key=lambda r: r["total_tokens"], reverse=True)

    totals = {
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "conversations": sum(r["conversations"] for r in rows),
    }

    return web.json_response({"rows": rows, "totals": totals})


async def handle_list_persons(request: web.Request) -> web.Response:
    """GET /api/persons — distinct user names from conversation metadata."""
    require_admin(request)

    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    persons = await db.conversations.distinct("metadata.user_name")
    # Filter out None/empty values and sort
    persons = sorted([p for p in persons if p])

    return web.json_response({"persons": persons})


async def handle_generate_titles(request: web.Request) -> web.Response:
    """POST /api/conversations/generate-titles — backfill titles and topics.

    Processes conversations that don't have a title or topic yet.
    Uses LLM in batches of 5 for controlled concurrency.
    """
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    # Find conversations missing title or topic
    missing = await db.conversations.find(
        {"$or": [{"title": {"$exists": False}}, {"title": None}, {"title": ""},
                 {"topic": {"$exists": False}}, {"topic": None}, {"topic": ""}]},
        {"conversation_id": 1, "prompt": 1, "final_response": 1, "title": 1, "topic": 1},
    ).limit(200).to_list(200)

    if not missing:
        return web.json_response({"processed": 0, "message": "All conversations already have titles and topics"})

    processed = 0
    batch_size = 5

    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        tasks = [_enrich_conversation(db, conv) for conv in batch]
        await asyncio.gather(*tasks, return_exceptions=True)
        processed += len(batch)

    return web.json_response({
        "processed": processed,
        "message": f"Generated titles/topics for {processed} conversations",
    })


async def handle_chat(request: web.Request) -> web.Response:
    """POST /api/chat — SSE stream for dashboard chat.

    Accepts JSON body:
      - message (str): current user message
      - conversation_history (list[dict], optional): prior messages
        e.g. [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
      - files (list[dict], optional): attached files
        e.g. [{"name": "foo.png", "mimetype": "image/png", "type": "image", "data": "<base64>"}]
    """
    body = await request.json()
    message = body.get("message", "")
    if not message:
        return web.json_response({"error": "Missing message"}, status=400)
    selected_model = body.get("model")
    if selected_model is not None and not isinstance(selected_model, str):
        return web.json_response({"error": "model must be a provider/model string"}, status=400)

    # Build conversation context from history
    history = body.get("conversation_history", [])
    context_parts: list[str] = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            context_parts.append(f"User: {content}")
        elif role == "assistant":
            context_parts.append(f"Assistant: {content}")
    conversation_context = "\n\n".join(context_parts)

    # Parse file attachments
    files = body.get("files") or None

    # Use authenticated identity (from middleware), not the self-reported body value
    user_email = get_user_email(request) or body.get("user_email", "")

    # Set up observability — reuse existing conversation if conversation_id provided
    observer = None
    db = get_db()
    existing_conversation_id = body.get("conversation_id")
    if db is not None:
        metadata = {
            "source": "dashboard",
            "prompt": message,
            "model": selected_model or os.environ.get("AGENT_DEFAULT_MODEL", "opencode-go/deepseek-v4-flash"),
            "user_name": user_email,
        }
        if existing_conversation_id:
            # Check if conversation already exists (resume) or is client-generated (start)
            existing = await db.conversations.find_one(
                {"conversation_id": existing_conversation_id}, {"_id": 1}
            )
            observer = ConversationObserver(
                db, metadata=metadata,
                conversation_id=existing_conversation_id,
            )
            if existing:
                await observer.resume()
            else:
                await observer.start()
        else:
            observer = ConversationObserver(db, metadata=metadata)
            await observer.start()

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    # Emit conversation_id as the first event so the client can track it
    if observer:
        cid_event = json.dumps({"type": "conversation_id", "conversation_id": observer.conversation_id})
        await response.write(f"data: {cid_event}\n\n".encode())

    disconnected = False

    # Send SSE keepalive comments every 10s so proxies (e.g. Next.js dev
    # server) don't kill the connection during the long MCP startup phase.
    keepalive_task: asyncio.Task | None = None

    async def _send_keepalive():
        try:
            while True:
                await asyncio.sleep(10)
                if disconnected:
                    return
                await response.write(b": keepalive\n\n")
                await response.drain()
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            pass

    keepalive_task = asyncio.create_task(_send_keepalive())

    try:
        async for event in stream_agent(
            prompt=message,
            conversation_context=conversation_context,
            files=files,
            observer=observer,
            include_steps=True,
            source="dashboard",
            user_email=user_email,
            selected_model=selected_model,
        ):
            # If the client already disconnected, keep consuming events so the
            # agent runs to completion (observability still records everything)
            # but skip writing to the closed response.
            if disconnected:
                continue

            try:
                if isinstance(event, dict):
                    data = json.dumps(event)
                else:
                    data = json.dumps({"type": "text", "text": event})
                await response.write(f"data: {data}\n\n".encode())
                await response.drain()
            except (ConnectionResetError, ConnectionError, Exception) as write_err:
                if "closing transport" in str(write_err) or "reset" in str(write_err).lower():
                    logger.info("Dashboard chat: client disconnected — agent will continue in background")
                    disconnected = True
                    continue
                raise
    except Exception as e:
        logger.exception("Dashboard chat error")
        if not disconnected:
            try:
                await response.write(f"data: {json.dumps({'error': str(e)})}\n\n".encode())
            except Exception:
                pass
    finally:
        if keepalive_task:
            keepalive_task.cancel()

    # Fire-and-forget: ingest this chat turn as a change-stream event.
    if observer:
        asyncio.create_task(ingest_dashboard_chat(
            observer.conversation_id, message, user_email,
        ))

    if not disconnected:
        try:
            await response.write(b"data: [DONE]\n\n")
        except Exception:
            pass
    return response


async def handle_agent_models(request: web.Request) -> web.Response:
    """GET /api/agent-models — dynamic model catalog for dashboard chat."""
    default_claude_model = ClientPool.default_model()
    default_agent_model = os.environ.get("AGENT_DEFAULT_MODEL", "opencode-go/deepseek-v4-flash")

    def _claude_entry(model_id: str) -> dict:
        return {
            "id": f"anthropic/{model_id}",
            "provider_id": "anthropic",
            "model_id": model_id,
            "label": f"Claude Agent SDK · {model_id}",
            "context_limit": None,
            "supports_attachments": True,
            "supports_reasoning": True,
            "status": "active",
            "cost": {},
            "recommended": True,
        }

    claude_models = [_claude_entry(mid) for mid in SUPPORTED_CLAUDE_MODEL_IDS]

    try:
        catalog = await get_agent_models()
        filtered_models = list(claude_models)
        for model in catalog.get("models", []):
            model_id = model.get("model_id") or ""
            provider_id = model.get("provider_id") or ""
            if provider_id == "opencode-go":
                if os.environ.get("OPENCODE_API_KEY"):
                    filtered_models.append(model)
                continue
            if provider_id == "openai" and model_id.startswith("gpt-"):
                if os.environ.get("OPENAI_API_KEY"):
                    filtered_models.append({
                        **model,
                        "label": f"OpenAI · {model_id}",
                    })

        catalog = {
            **catalog,
            "default_model": default_agent_model,
            "models": _order_agent_models(_ensure_favorite_models(filtered_models)),
        }
        return web.json_response(catalog)
    except Exception as e:
        logger.exception("Failed to load OpenCode model catalog")
        return web.json_response({
            "default_model": default_agent_model,
            "models": _order_agent_models(_ensure_favorite_models(claude_models)),
            "warning": str(e),
        })


async def handle_list_skills(request: web.Request) -> web.Response:
    """GET /api/skills — list all agent skills."""
    skills = []
    if SKILLS_DIR.is_dir():
        for child in sorted(SKILLS_DIR.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            content = skill_md.read_text(encoding="utf-8")
            # Parse YAML frontmatter for description
            description = ""
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    for line in content[3:end].splitlines():
                        if line.strip().startswith("description:"):
                            description = line.split(":", 1)[1].strip()
                            break
            # List extra files in the skill directory
            files = [f.name for f in child.iterdir() if f.name != "SKILL.md"]
            skills.append({
                "name": child.name,
                "description": description,
                "has_extra_files": len(files) > 0,
                "files": files,
            })
    return web.json_response({"skills": skills})


async def handle_get_skill(request: web.Request) -> web.Response:
    """GET /api/skills/{name} — return skill content + extra files."""
    name = request.match_info["name"]
    skill_dir = SKILLS_DIR / name
    if not skill_dir.is_dir():
        return web.json_response({"error": "Skill not found"}, status=404)

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return web.json_response({"error": "Skill not found"}, status=404)

    content = skill_md.read_text(encoding="utf-8")

    # Read extra files
    extra_files = {}
    for f in sorted(skill_dir.iterdir()):
        if f.name == "SKILL.md" or not f.is_file():
            continue
        try:
            extra_files[f.name] = f.read_text(encoding="utf-8")
        except Exception:
            extra_files[f.name] = "(binary file)"

    return web.json_response({
        "name": name,
        "content": content,
        "extra_files": extra_files,
    })


async def handle_get_skill_history(request: web.Request) -> web.Response:
    """GET /api/skills/{name}/history — git log for a skill's files."""
    name = request.match_info["name"]
    skill_dir = SKILLS_DIR / name
    if not skill_dir.is_dir():
        return web.json_response({"error": "Skill not found"}, status=404)

    rel_path = str(skill_dir.relative_to(PROJECT_ROOT))
    proc = await asyncio.create_subprocess_exec(
        "git", "log", "--format=%H|%an|%ae|%aI|%s", "--follow", "--", rel_path,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    commits = []
    for line in stdout.decode().strip().splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            commits.append({
                "sha": parts[0],
                "author": parts[1],
                "email": parts[2],
                "date": parts[3],
                "message": parts[4],
            })

    return web.json_response({"name": name, "commits": commits})


async def handle_get_skill_version(request: web.Request) -> web.Response:
    """GET /api/skills/{name}/version/{sha} — skill content at a specific commit."""
    name = request.match_info["name"]
    sha = request.match_info["sha"]

    # Validate sha is hex-only to prevent injection
    if not re.match(r"^[0-9a-fA-F]+$", sha):
        return web.json_response({"error": "Invalid SHA"}, status=400)

    rel_path = f".claude/skills/{name}/SKILL.md"
    proc = await asyncio.create_subprocess_exec(
        "git", "show", f"{sha}:{rel_path}",
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return web.json_response({"error": "Version not found"}, status=404)

    return web.json_response({
        "name": name,
        "sha": sha,
        "content": stdout.decode("utf-8", errors="replace"),
    })


async def handle_get_skill_diff(request: web.Request) -> web.Response:
    """GET /api/skills/{name}/diff?from=sha1&to=sha2 — unified diff between two versions.

    If ``to`` is omitted, diffs against HEAD.
    """
    name = request.match_info["name"]
    skill_dir = SKILLS_DIR / name
    if not skill_dir.is_dir():
        return web.json_response({"error": "Skill not found"}, status=404)

    sha_from = request.query.get("from", "")
    sha_to = request.query.get("to", "HEAD")

    hex_re = re.compile(r"^[0-9a-fA-F]+$")
    if not hex_re.match(sha_from):
        return web.json_response({"error": "Invalid 'from' SHA"}, status=400)
    if sha_to != "HEAD" and not hex_re.match(sha_to):
        return web.json_response({"error": "Invalid 'to' SHA"}, status=400)

    rel_path = str(skill_dir.relative_to(PROJECT_ROOT))
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--unified=5", sha_from, sha_to, "--", rel_path,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    return web.json_response({
        "name": name,
        "from_sha": sha_from,
        "to_sha": sha_to,
        "diff": stdout.decode("utf-8", errors="replace"),
    })


# Descriptions for known MCP servers (shown in the dashboard)
_MCP_DESCRIPTIONS: dict[str, str] = {
    "docs": "company documentation (GitBook)",
    "mongodb": "MongoDB — customer data, configurations, state",
    "clickhouse": "ClickHouse — analytics, events, campaign metrics",
    "github": "GitHub — company codebase (example-org org)",
    "linear": "Linear — issue tracking and project management",
    "pylon": "Pylon — support ticket API",
}


async def handle_memory_recent(request: web.Request) -> web.Response:
    """GET /api/memory/recent — past 24h conversations with turn-level outcomes."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    hours = int(request.query.get("hours", 24))
    hours = min(hours, 168)  # cap at 7 days
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Fetch conversations from the time window
    conversations = await db.conversations.find(
        {"started_at": {"$gte": since}},
        {
            "conversation_id": 1, "source": 1, "started_at": 1,
            "status": 1, "total_turns": 1, "prompt": 1, "title": 1,
            "topic": 1, "duration_ms": 1,
            "confidence": 1, "cost": 1,
        },
    ).sort("started_at", -1).to_list(200)

    if not conversations:
        return web.json_response({"conversations": [], "tool_summary": {}, "turn_outcomes": {}})

    cids = [c["conversation_id"] for c in conversations]

    # Aggregate turn outcomes per conversation:
    # For each turn, check if any tool_result has is_error=true
    # Aggregate per conversation: unique tools used and error count
    pipeline = [
        {"$match": {"conversation_id": {"$in": cids}}},
        {"$project": {
            "conversation_id": 1,
            "tool_names": {
                "$map": {
                    "input": {"$ifNull": ["$tool_calls", []]},
                    "in": "$$this.tool_name",
                }
            },
            "has_error": {
                "$cond": {
                    "if": {"$gt": [{"$size": {"$ifNull": [
                        {"$filter": {
                            "input": {"$ifNull": ["$tool_results", []]},
                            "cond": {"$eq": ["$$this.is_error", True]},
                        }},
                        [],
                    ]}}, 0]},
                    "then": 1,
                    "else": 0,
                }
            },
        }},
        {"$unwind": {"path": "$tool_names", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$conversation_id",
            "tools": {"$addToSet": "$tool_names"},
            "error_turns": {"$sum": "$has_error"},
            "total_turns": {"$sum": 1},
        }},
    ]
    turn_agg = await db.turns.aggregate(pipeline).to_list(200)
    tool_map = {}
    for item in turn_agg:
        tools = [t for t in (item.get("tools") or []) if t is not None]
        tool_map[item["_id"]] = {
            "tools": sorted(tools),
            "error_turns": item.get("error_turns", 0),
        }

    # Per-turn outcomes in chronological order (for timeline visualization)
    outcome_pipeline = [
        {"$match": {"conversation_id": {"$in": cids}}},
        {"$sort": {"_id": 1}},
        {"$project": {
            "conversation_id": 1,
            "outcome": {
                "$cond": {
                    "if": {"$gt": [{"$size": {"$ifNull": [
                        {"$filter": {
                            "input": {"$ifNull": ["$tool_results", []]},
                            "cond": {"$eq": ["$$this.is_error", True]},
                        }},
                        [],
                    ]}}, 0]},
                    "then": "error",
                    "else": "success",
                }
            },
        }},
        {"$group": {
            "_id": "$conversation_id",
            "outcomes": {"$push": "$outcome"},
        }},
    ]
    outcome_agg = await db.turns.aggregate(outcome_pipeline).to_list(200)
    outcome_map = {item["_id"]: item["outcomes"] for item in outcome_agg}

    return web.json_response({
        "conversations": _serialize(conversations),
        "tool_summary": _serialize(tool_map),
        "turn_outcomes": _serialize(outcome_map),
    })


async def handle_list_mcp_servers(request: web.Request) -> web.Response:
    """GET /api/mcp-servers — list configured MCP server connections."""
    servers = []
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                config = yaml.safe_load(f) or {}
            for name, conf in config.get("mcp_servers", {}).items():
                server_type = conf.get("type", "unknown")
                entry: dict = {
                    "name": name,
                    "type": server_type,
                    "description": _MCP_DESCRIPTIONS.get(name, ""),
                }
                if server_type == "http":
                    entry["url"] = conf.get("url", "")
                elif server_type == "stdio":
                    entry["command"] = conf.get("command", "")
                    entry["args"] = conf.get("args", [])
                # Expose env var *names* only (never values)
                env = conf.get("env", {})
                if env:
                    entry["env_keys"] = list(env.keys())
                servers.append(entry)
        except Exception as e:
            logger.warning("Failed to read config.yaml for MCP servers: %s", e)
    return web.json_response({"servers": servers})


async def handle_pool_status(request):
    """Return agent pool status (available/in_use/queued)."""
    from agent.pool import get_pool
    pool = get_pool()
    try:
        await get_agent_models()
    except Exception:
        logger.debug("OpenCode model catalog unavailable while polling pool status", exc_info=True)
    status = pool.status()
    status["opencode"] = get_opencode_pool_status()
    return web.json_response(status)


# ── Pin / Unpin conversations ─────────────────────────────────────────────

MAX_PINS = 10


def _check_conversation_access(conversation: dict, user_email: str, system_role: str) -> bool:
    """Return True if the user has access to this conversation."""
    owner = (conversation.get("metadata") or {}).get("user_name", "")
    conv_source = conversation.get("source", "")
    if system_role == "admin":
        return True
    if system_role in ("maintainer", "analyst"):
        if owner == user_email:
            return True
        if conv_source in ("flow", "webhook"):
            visibility = (conversation.get("metadata") or {}).get("visibility", "shared")
            return visibility != "private"
        return conv_source == "task_step"
    return owner == user_email


async def handle_pin_conversation(request: web.Request) -> web.Response:
    """POST /api/conversations/{conversation_id}/pin"""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    cid = request.match_info["conversation_id"]
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    # Verify conversation exists and user has access
    conversation = await db.conversations.find_one({"conversation_id": cid, "deleted": {"$ne": True}})
    if not conversation:
        return web.json_response({"error": "Not found"}, status=404)

    system_role = get_system_role(request)
    if not _check_conversation_access(conversation, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    # Check current pin state
    user = request.get("user") or {}
    current_pins = user.get("pinned_conversations") or []
    already_pinned = any(p.get("conversation_id") == cid for p in current_pins)
    if already_pinned:
        return web.json_response({"pinned": True})

    if len(current_pins) >= MAX_PINS:
        return web.json_response(
            {"error": f"Pin limit reached (max {MAX_PINS})"},
            status=409,
        )

    await db.users.update_one(
        {"email": user_email},
        {"$push": {"pinned_conversations": {
            "conversation_id": cid,
            "pinned_at": datetime.now(timezone.utc),
        }}},
    )
    return web.json_response({"pinned": True})


async def handle_unpin_conversation(request: web.Request) -> web.Response:
    """DELETE /api/conversations/{conversation_id}/pin"""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    cid = request.match_info["conversation_id"]
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    await db.users.update_one(
        {"email": user_email},
        {"$pull": {"pinned_conversations": {"conversation_id": cid}}},
    )
    return web.json_response({"pinned": False})


async def handle_update_conversation(request: web.Request) -> web.Response:
    """PATCH /api/conversations/{conversation_id} -- rename or update metadata."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    cid = request.match_info["conversation_id"]
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    conversation = await db.conversations.find_one({
        "conversation_id": cid,
        "deleted": {"$ne": True},
    })
    if not conversation:
        return web.json_response({"error": "Not found"}, status=404)

    system_role = get_system_role(request)
    if not _check_conversation_access(conversation, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    updates = {}
    if "title" in body:
        title = (body["title"] or "").strip()
        if not title:
            return web.json_response({"error": "Title cannot be empty"}, status=400)
        if len(title) > 200:
            return web.json_response({"error": "Title must be 200 characters or less"}, status=400)
        updates["title"] = title
        updates["title_edited"] = True

    if not updates:
        return web.json_response({"conversation": _serialize(conversation)})

    await db.conversations.update_one(
        {"conversation_id": cid},
        {"$set": updates},
    )

    updated = await db.conversations.find_one({"conversation_id": cid})
    return web.json_response({"conversation": _serialize(updated)})


async def handle_delete_conversation(request: web.Request) -> web.Response:
    """DELETE /api/conversations/{conversation_id} -- soft delete a conversation."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    cid = request.match_info["conversation_id"]
    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    conversation = await db.conversations.find_one({
        "conversation_id": cid,
        "deleted": {"$ne": True},
    })
    if not conversation:
        return web.json_response({"error": "Not found"}, status=404)

    system_role = get_system_role(request)
    if not _check_conversation_access(conversation, user_email, system_role):
        return web.json_response({"error": "Not found"}, status=404)

    now = datetime.now(timezone.utc)

    # Soft-delete the conversation
    await db.conversations.update_one(
        {"conversation_id": cid},
        {"$set": {"deleted": True, "deleted_at": now, "deleted_by": user_email}},
    )

    # Soft-delete associated turns
    await db.turns.update_many(
        {"conversation_id": cid},
        {"$set": {"deleted": True, "deleted_at": now}},
    )

    # Remove from any user's pinned_conversations
    await db.users.update_many(
        {"pinned_conversations.conversation_id": cid},
        {"$pull": {"pinned_conversations": {"conversation_id": cid}}},
    )

    return web.json_response({"deleted": True})


async def handle_get_pinned_conversations(request: web.Request) -> web.Response:
    """GET /api/conversations/pinned — user's pinned conversations."""
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    user = request.get("user") or {}
    pins = user.get("pinned_conversations") or []
    pinned_ids = [p["conversation_id"] for p in pins]

    if not pinned_ids:
        return web.json_response({"conversations": [], "pinned_ids": []})

    conversations = await db.conversations.find(
        {"conversation_id": {"$in": pinned_ids}, "deleted": {"$ne": True}}
    ).to_list(MAX_PINS)

    # Role-based access filtering
    system_role = get_system_role(request)
    filtered = [c for c in conversations if _check_conversation_access(c, user_email, system_role)]

    # Sort by pinned_at descending (most recently pinned first)
    pin_order = {p["conversation_id"]: p.get("pinned_at", datetime.min) for p in pins}
    filtered.sort(key=lambda c: pin_order.get(c["conversation_id"], datetime.min), reverse=True)

    return web.json_response({
        "conversations": _serialize(filtered),
        "pinned_ids": [c["conversation_id"] for c in filtered],
    })


def setup_api_routes(app: web.Application):
    """Register API routes on the aiohttp app."""
    # CORS preflight for dashboard
    async def cors_options(request):
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })

    # API routes
    app.router.add_get("/api/conversations", handle_list_conversations)
    app.router.add_get("/api/conversations/pinned", handle_get_pinned_conversations)
    app.router.add_get("/api/conversations/{conversation_id}", handle_get_conversation)
    app.router.add_patch("/api/conversations/{conversation_id}", handle_update_conversation)
    app.router.add_delete("/api/conversations/{conversation_id}", handle_delete_conversation)
    app.router.add_post("/api/conversations/{conversation_id}/pin", handle_pin_conversation)
    app.router.add_delete("/api/conversations/{conversation_id}/pin", handle_unpin_conversation)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_get("/api/cost-stats", handle_cost_stats)
    app.router.add_get("/api/token-usage", handle_token_usage)
    app.router.add_get("/api/persons", handle_list_persons)
    app.router.add_post("/api/conversations/generate-titles", handle_generate_titles)
    app.router.add_get("/api/agent-models", handle_agent_models)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_get("/api/skills", handle_list_skills)
    app.router.add_get("/api/skills/{name}/history", handle_get_skill_history)
    app.router.add_get("/api/skills/{name}/version/{sha}", handle_get_skill_version)
    app.router.add_get("/api/skills/{name}/diff", handle_get_skill_diff)
    app.router.add_get("/api/skills/{name}", handle_get_skill)
    app.router.add_get("/api/memory/recent", handle_memory_recent)
    app.router.add_get("/api/mcp-servers", handle_list_mcp_servers)
    app.router.add_get("/api/pool-status", handle_pool_status)
    app.router.add_get("/api/files/{file_id}", handle_serve_file)

    # Flow routes (scheduled/recurring automations)
    from api.flow_routes import setup_flow_routes
    setup_flow_routes(app)

    # Project routes (chat organization)
    from api.project_routes import setup_project_routes
    setup_project_routes(app)

    # File serving routes (binary artifact previews)
    from api.file_routes import setup_file_routes
    setup_file_routes(app)

    # CORS catch-all must be last
    app.router.add_route("OPTIONS", "/api/{path:.*}", cors_options)
