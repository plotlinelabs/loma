"""Review quality assessment for PR reviews.

Compares agent reviews against human reviews to detect:
- Agent missed issues that humans caught
- Agent flagged false positives that humans dismissed
- Humans disagreed with agent's recommendations

When human intervention is detected, this module extracts learnings
and promotes them to org_learnings for continuous improvement.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

QUALITY_ASSESSMENT_PROMPT = """You are analyzing the quality of an automated PR code review.

AGENT REVIEW (submitted first):
State: {agent_state}
Body: {agent_body}
Comments count: {agent_comments_count}

HUMAN REVIEW (submitted AFTER agent):
Reviewer: {human_reviewer}
State: {human_state}
Body: {human_body}

Analyze whether the agent's review was effective or needed human correction.

Categories:
1. EFFECTIVE - Human review confirms agent findings OR adds minor complementary feedback
2. MISSED_ISSUES - Human found blocking issues the agent missed entirely
3. FALSE_POSITIVES - Human explicitly dismissed or contradicted agent's concerns
4. CONTRADICTED - Human recommended opposite action (e.g., agent said REQUEST_CHANGES, human APPROVED without addressing agent's concerns)

Important: Only mark as MISSED_ISSUES or FALSE_POSITIVES if there's clear evidence.
If the human just added their own perspective without contradicting, that's EFFECTIVE.

Return ONLY valid JSON (no markdown):
{{
    "quality": "effective" | "missed_issues" | "false_positives" | "contradicted",
    "explanation": "Brief explanation of your assessment",
    "learnings": [
        {{
            "context": "When reviewing <specific pattern/file type/situation>",
            "lesson": "The agent should <specific actionable improvement>"
        }}
    ]
}}

If quality is "effective", learnings should be empty [].
Only include learnings if there's a clear mistake to learn from.
"""


async def assess_review_quality(
    db,
    conversation_id: str,
    agent_review: dict,
    human_review: dict,
) -> dict | None:
    """Assess agent review quality against human review.

    Args:
        db: MongoDB database handle.
        conversation_id: The agent review conversation ID.
        agent_review: Dict with agent review details (state, body, comments_count).
        human_review: Dict with human review details (reviewer, state, body).

    Returns:
        Assessment dict with quality, explanation, learnings, or None on failure.
    """
    prompt = QUALITY_ASSESSMENT_PROMPT.format(
        agent_state=agent_review.get("state") or "COMMENT",
        agent_body=(agent_review.get("body") or "")[:2000],
        agent_comments_count=agent_review.get("comments_count") or 0,
        human_reviewer=human_review.get("reviewer") or "unknown",
        human_state=human_review.get("state") or "COMMENT",
        human_body=(human_review.get("body") or "")[:2000],
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            "--model", "claude-opus-4-8",
            "--max-turns", "1",
            "--output-format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        if proc.returncode != 0:
            logger.error(
                "[REVIEW-QUALITY] CLI failed (rc=%d): %s",
                proc.returncode, stderr.decode()[:200],
            )
            return None

        output = stdout.decode().strip()

        # Parse CLI JSON envelope
        try:
            envelope = json.loads(output)
            raw = envelope.get("result", output)
        except json.JSONDecodeError:
            raw = output

        # Strip markdown fences if present
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        assessment = json.loads(raw)
        logger.info(
            "[REVIEW-QUALITY] Assessment for %s: %s",
            conversation_id, assessment.get("quality"),
        )
        return assessment

    except asyncio.TimeoutError:
        logger.error("[REVIEW-QUALITY] Assessment timed out for %s", conversation_id)
        return None
    except json.JSONDecodeError as e:
        logger.error("[REVIEW-QUALITY] Failed to parse assessment JSON: %s", e)
        return None
    except FileNotFoundError:
        logger.error("[REVIEW-QUALITY] claude CLI not found")
        return None
    except Exception as e:
        logger.error("[REVIEW-QUALITY] Assessment failed: %s", e)
        return None


async def store_quality_assessment(
    db,
    conversation_id: str,
    pr_number: int,
    repo_full_name: str,
    agent_review: dict,
    human_review: dict,
    assessment: dict,
) -> str:
    """Store a quality assessment in the database.

    Args:
        db: MongoDB database handle.
        conversation_id: The agent review conversation ID.
        pr_number: Pull request number.
        repo_full_name: Full repository name.
        agent_review: Agent review details.
        human_review: Human review details.
        assessment: The quality assessment result.

    Returns:
        The quality_id of the stored assessment.
    """
    quality_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    doc = {
        "quality_id": quality_id,
        "conversation_id": conversation_id,
        "pr_number": pr_number,
        "repo_full_name": repo_full_name,
        "agent_review": {
            "state": agent_review.get("state"),
            "comments_count": agent_review.get("comments_count", 0),
            "created_at": agent_review.get("created_at"),
        },
        "human_review": {
            "reviewer": human_review.get("reviewer"),
            "state": human_review.get("state"),
            "submitted_at": human_review.get("submitted_at"),
        },
        "assessment": assessment,
        "learnings_promoted": False,
        "created_at": now,
    }

    await db.pr_review_quality.insert_one(doc)
    logger.info(
        "[REVIEW-QUALITY] Stored assessment %s for conversation %s (quality=%s)",
        quality_id, conversation_id, assessment.get("quality"),
    )
    return quality_id


async def promote_review_learnings(
    db,
    quality_id: str,
) -> list[str]:
    """Promote learnings from a review quality assessment to org_learnings.

    Args:
        db: MongoDB database handle.
        quality_id: The quality assessment ID.

    Returns:
        List of created learning_ids.
    """
    from observability.org_learnings import add_learning

    # Fetch the quality assessment
    doc = await db.pr_review_quality.find_one({"quality_id": quality_id})
    if not doc:
        logger.error("[REVIEW-QUALITY] Quality assessment %s not found", quality_id)
        return []

    if doc.get("learnings_promoted"):
        logger.info("[REVIEW-QUALITY] Learnings already promoted for %s", quality_id)
        return []

    assessment = doc.get("assessment", {})
    learnings = assessment.get("learnings", [])

    if not learnings:
        logger.info("[REVIEW-QUALITY] No learnings to promote for %s", quality_id)
        return []

    conversation_id = doc.get("conversation_id", "")
    repo_full_name = doc.get("repo_full_name", "")
    pr_number = doc.get("pr_number", 0)

    learning_ids = []
    for learning in learnings:
        context = learning.get("context", "")
        lesson = learning.get("lesson", "")

        if not context or not lesson:
            continue

        # Add PR review context to the learning
        learning_data = {
            "context": f"[PR Review] {context}",
            "lesson": lesson,
            "types": ["pr_review_quality"],
        }

        learning_id = await add_learning(
            db,
            learning_data,
            conversation_id=conversation_id,
            conversation_title=f"PR Review Quality: {repo_full_name}#{pr_number}",
        )

        if learning_id:
            learning_ids.append(learning_id)

    # Mark as promoted
    if learning_ids:
        await db.pr_review_quality.update_one(
            {"quality_id": quality_id},
            {"$set": {"learnings_promoted": True, "learning_ids": learning_ids}},
        )
        logger.info(
            "[REVIEW-QUALITY] Promoted %d learnings from %s",
            len(learning_ids), quality_id,
        )

    return learning_ids


async def process_human_review_for_quality(
    db,
    repo_full_name: str,
    pr_number: int,
    human_review: dict,
) -> str | None:
    """Process a human review and assess quality against prior agent review.

    This is the main entry point called from the webhook handler.

    Args:
        db: MongoDB database handle.
        repo_full_name: Full repository name.
        pr_number: Pull request number.
        human_review: Human review details (reviewer, state, body, submitted_at).

    Returns:
        The quality_id if assessment was created, None otherwise.
    """
    # Find the most recent agent review conversation for this PR
    # Only match actual PR reviews (trigger_type: "pr_review"), not slash commands or replies
    agent_conversation = await db.conversations.find_one(
        {
            "metadata.github_repo": repo_full_name,
            "metadata.github_pr_number": pr_number,
            "source": "github_webhook",
            "status": "completed",
            "metadata.trigger_type": "pr_review",
        },
        sort=[("started_at", -1)],
    )

    if not agent_conversation:
        logger.info(
            "[REVIEW-QUALITY] No agent review found for %s#%d, skipping quality check",
            repo_full_name, pr_number,
        )
        return None

    conversation_id = agent_conversation.get("conversation_id", "")

    # Check if we already assessed this human review
    existing = await db.pr_review_quality.find_one({
        "conversation_id": conversation_id,
        "human_review.reviewer": human_review.get("reviewer"),
        "human_review.submitted_at": human_review.get("submitted_at"),
    })

    if existing:
        logger.info(
            "[REVIEW-QUALITY] Already assessed human review from %s on %s#%d",
            human_review.get("reviewer"), repo_full_name, pr_number,
        )
        return existing.get("quality_id")

    # Build agent review info from conversation metadata
    agent_review = {
        "state": "COMMENT",  # Default, could extract from conversation if stored
        "body": agent_conversation.get("final_response", "")[:2000],
        "comments_count": agent_conversation.get("total_turns", 0),
        "created_at": agent_conversation.get("started_at"),
    }

    # Assess quality
    assessment = await assess_review_quality(
        db, conversation_id, agent_review, human_review,
    )

    if not assessment:
        return None

    # Store the assessment
    quality_id = await store_quality_assessment(
        db, conversation_id, pr_number, repo_full_name,
        agent_review, human_review, assessment,
    )

    # If quality is not "effective", promote learnings
    quality = assessment.get("quality", "effective")
    if quality != "effective":
        logger.info(
            "[REVIEW-QUALITY] Quality=%s for %s#%d, promoting learnings",
            quality, repo_full_name, pr_number,
        )
        await promote_review_learnings(db, quality_id)

    return quality_id
