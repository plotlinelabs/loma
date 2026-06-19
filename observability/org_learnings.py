"""
Org Learnings — deduplicated learning storage with embedding-based dedup.

Atlas vector search indexes must be created manually:
    Collection: learnings
    Index name: learning_embedding_index
    Field: embedding (vector, 1024 dimensions, cosine similarity)
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

import voyageai

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.70
VOYAGE_MODEL = "voyage-3"
HAIKU_MODEL = "haiku"
HAIKU_TIMEOUT = 30  # seconds

_voyage_client: voyageai.AsyncClient | None = None
_dedup_lock = asyncio.Lock()

# Set to True to enable verbose logging of the dedup pipeline.
verbose = False


def _get_voyage_client() -> voyageai.AsyncClient:
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.AsyncClient(api_key=os.environ.get("VOYAGE_API_KEY"))
    return _voyage_client


def _normalize_learning_text(learning_data) -> tuple[str, str | None, list[str]]:
    """Normalize learning_data into (text, context, types).

    Accepts either a dict {"context": ..., "lesson": ..., "types": [...]}
    or a plain string.
    """
    if isinstance(learning_data, str):
        return learning_data, None, ["general"]

    context = learning_data.get("context") or None
    lesson = learning_data.get("lesson") or ""
    types = learning_data.get("types") or ["general"]

    text = f"{context}: {lesson}" if context else lesson
    return text, context, types


async def _embed_text(text: str) -> list[float]:
    """Embed a single text string using Voyage."""
    client = _get_voyage_client()
    result = await client.embed([text], model=VOYAGE_MODEL, input_type="document")
    return result.embeddings[0]


async def _vector_search(
    db, collection_name: str, index_name: str, embedding: list[float], limit: int = 3,
) -> list[dict]:
    """Search for similar docs using Atlas vector search on a given collection."""
    pipeline = [
        {
            "$vectorSearch": {
                "index": index_name,
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": 20,
                "limit": limit,
            }
        },
        {
            "$addFields": {"score": {"$meta": "vectorSearchScore"}}
        },
        {
            "$project": {"embedding": 0}
        },
    ]
    collection = db[collection_name]
    return await collection.aggregate(pipeline).to_list(limit)


async def _haiku_confirm_duplicate(new_text: str, existing_text: str) -> bool:
    """Ask Haiku whether two learnings are saying the same thing."""
    prompt = f"""Do these two learnings convey the same core insight? Ignore differences in wording, detail level, or examples — focus only on whether the KEY TAKEAWAY is the same.

LEARNING A:
{new_text}

LEARNING B:
{existing_text}

Answer ONLY "yes" or "no". If they cover the same topic and teach the same lesson, answer "yes" even if one has more detail than the other."""

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            "--model", HAIKU_MODEL,
            "--max-turns", "1",
            "--allowedTools", "",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=HAIKU_TIMEOUT)

        if proc.returncode != 0:
            err_msg = stderr.decode().strip()[:200] if stderr else "unknown"
            logger.warning("[ORG-LEARNINGS] Haiku CLI failed (rc=%d): %s", proc.returncode, err_msg)
            return False

        answer = stdout.decode().strip().lower()
        result = "yes" in answer
        if verbose:
            logger.info(
                "[ORG-LEARNINGS] Haiku verdict=%s (raw=%r)\n  A: %s\n  B: %s",
                "DUPLICATE" if result else "DIFFERENT",
                answer,
                new_text[:300],
                existing_text[:300],
            )
        return result
    except asyncio.TimeoutError:
        logger.warning("[ORG-LEARNINGS] Haiku confirmation timed out after %ds", HAIKU_TIMEOUT)
        return False
    except Exception as e:
        logger.warning("[ORG-LEARNINGS] Haiku confirmation failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Shared dedup-and-store pipeline
# ---------------------------------------------------------------------------

async def _dedup_and_store(
    db,
    collection_name: str,
    index_name: str,
    text: str,
    embedding: list[float],
    doc_template: dict,
    outcome: str | None,
    now: datetime,
    text_field: str = "learning",
    id_field: str = "learning_id",
    conversation_id: str | None = None,
    review_id: str | None = None,
) -> str | None:
    """Search for duplicates and store. Must be called under _dedup_lock."""
    collection = db[collection_name]
    label = collection_name.upper()

    # Vector search for similar existing docs.
    candidates = await _vector_search(db, collection_name, index_name, embedding)

    if verbose:
        if candidates:
            scores = ", ".join(f"{c.get('score', 0):.3f}" for c in candidates)
            logger.info("[%s] Vector search returned %d candidates (scores: %s)", label, len(candidates), scores)
            for i, c in enumerate(candidates):
                logger.info(
                    "[%s]   candidate[%d] score=%.3f: %s",
                    label, i, c.get("score", 0), c.get(text_field, "")[:100],
                )
        else:
            logger.info("[%s] Vector search returned 0 candidates (collection empty?)", label)

    # Check candidates for duplicates.
    for candidate in candidates:
        score = candidate.get("score", 0)
        if score < SIMILARITY_THRESHOLD:
            if verbose:
                logger.info("[%s] Score %.3f below threshold %.2f, skipping remaining", label, score, SIMILARITY_THRESHOLD)
            break

        if verbose:
            logger.info("[%s] Score %.3f >= threshold, asking Haiku to confirm...", label, score)

        is_duplicate = await _haiku_confirm_duplicate(text, candidate.get(text_field, ""))
        if is_duplicate:
            existing_id = candidate[id_field]
            update: dict = {
                "$inc": {"occurrence_count": 1},
                "$set": {"last_seen": now, "updated_at": now},
            }

            if conversation_id:
                update["$addToSet"] = {"source_conversations": conversation_id}
            if review_id:
                update.setdefault("$addToSet", {})["source_reviews"] = review_id

            if outcome == "positive":
                update["$inc"]["positive_count"] = 1
            elif outcome == "negative":
                update["$inc"]["negative_count"] = 1

            await collection.update_one({id_field: existing_id}, update)
            logger.info("[%s] Merged into existing %s=%s (score=%.3f)", label, id_field, existing_id, score)
            return existing_id

    # No duplicate — insert new doc.
    new_id = str(uuid.uuid4())
    doc = {
        **doc_template,
        id_field: new_id,
        text_field: text,
        "embedding": embedding,
        "occurrence_count": 1,
        "positive_count": 1 if outcome == "positive" else 0,
        "negative_count": 1 if outcome == "negative" else 0,
        "first_seen": now,
        "last_seen": now,
        "created_at": now,
        "updated_at": now,
    }
    await collection.insert_one(doc)
    logger.info("[%s] Created new %s=%s", label, id_field, new_id)
    return new_id


# ---------------------------------------------------------------------------
# Domain learnings (learnings collection)
# ---------------------------------------------------------------------------

async def add_learning(
    db,
    learning_data,
    conversation_id: str | None = None,
    conversation_title: str | None = None,
    improvement_target: str | None = None,
    review_id: str | None = None,
    outcome: str | None = None,
) -> str | None:
    """Deduplicate and store a domain learning. Returns learning_id (new or existing).

    Args:
        db: MongoDB database handle.
        learning_data: Either a dict {"context", "lesson", "types"} or a plain string.
        conversation_id: Source conversation ID.
        conversation_title: Human-readable title for attribution.
        improvement_target: e.g. "skill:pr-review", "flow:pylon", "general".
        review_id: Source review/quality ID if from a review.
        outcome: "positive", "negative", or "neutral".
    """
    text, context, types = _normalize_learning_text(learning_data)
    if not text or not text.strip():
        return None

    target = improvement_target or "general"
    now = datetime.now(timezone.utc)

    if verbose:
        logger.info("[LEARNINGS] Processing: %s (target=%s)", text[:100], target)

    try:
        embedding = await _embed_text(text)
        if verbose:
            logger.info("[LEARNINGS] Embedding complete (dim=%d)", len(embedding))

        async with _dedup_lock:
            if verbose:
                logger.info("[LEARNINGS] Acquired dedup lock")
            result = await _dedup_and_store(
                db,
                collection_name="learnings",
                index_name="learning_embedding_index",
                text=text,
                embedding=embedding,
                doc_template={
                    "context": context,
                    "improvement_target": target,
                    "types": types,
                    "status": "active",
                    "source_conversations": [conversation_id] if conversation_id else [],
                    "source_reviews": [review_id] if review_id else [],
                },
                outcome=outcome,
                now=now,
                text_field="learning",
                id_field="learning_id",
                conversation_id=conversation_id,
                review_id=review_id,
            )
            if verbose:
                logger.info("[LEARNINGS] Released dedup lock")
            return result

    except Exception as e:
        logger.warning("[LEARNINGS] Failed to add learning: %s", e)
        return None
