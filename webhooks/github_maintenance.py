"""Async maintenance flow for preexisting code issues.

When PR reviews detect issues that existed before the PR (preexisting),
this module aggregates them and can create maintenance tickets.
"""

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def record_preexisting_issue(
    db,
    repo_full_name: str,
    pr_number: int,
    file_path: str,
    line_start: int,
    line_end: int | None,
    issue_type: str,
    issue_description: str,
) -> str:
    """Record a preexisting issue found during PR review.

    Args:
        db: MongoDB database handle.
        repo_full_name: Full repository name.
        pr_number: PR where issue was detected.
        file_path: Path to the file with the issue.
        line_start: Start line number.
        line_end: End line number (optional).
        issue_type: Type of issue (security, performance, style, etc.).
        issue_description: Description of the issue.

    Returns:
        The issue_id of the recorded issue.
    """
    issue_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Check if this issue already exists (same file, similar line range)
    # Use line_end for upper bound to catch overlapping multi-line issues
    effective_end = line_end or line_start
    existing = await db.preexisting_issues.find_one({
        "repo_full_name": repo_full_name,
        "file_path": file_path,
        "line_range.0": {"$lte": effective_end + 5},
        "line_range.1": {"$gte": line_start - 5},
        "status": {"$ne": "fixed"},
    })

    if existing:
        # Increment detection count
        await db.preexisting_issues.update_one(
            {"issue_id": existing["issue_id"]},
            {
                "$inc": {"detection_count": 1},
                "$set": {"updated_at": now, "last_detected_pr": pr_number},
            },
        )
        logger.info(
            "[MAINTENANCE] Existing issue %s detected again in PR %d (count=%d)",
            existing["issue_id"], pr_number, existing.get("detection_count", 1) + 1,
        )
        return existing["issue_id"]

    # Create new issue record
    doc = {
        "issue_id": issue_id,
        "repo_full_name": repo_full_name,
        "file_path": file_path,
        "line_range": [line_start, line_end or line_start],
        "issue_type": issue_type,
        "issue_description": issue_description,
        "first_detected_pr": pr_number,
        "last_detected_pr": pr_number,
        "detection_count": 1,
        "linear_ticket_id": None,
        "status": "open",  # open, ticketed, fixed, ignored
        "created_at": now,
        "updated_at": now,
    }

    await db.preexisting_issues.insert_one(doc)
    logger.info(
        "[MAINTENANCE] Recorded new preexisting issue %s in %s:%d",
        issue_id, file_path, line_start,
    )
    return issue_id


async def get_open_issues_for_repo(
    db,
    repo_full_name: str,
    limit: int = 50,
) -> list[dict]:
    """Get open preexisting issues for a repository.

    Args:
        db: MongoDB database handle.
        repo_full_name: Full repository name.
        limit: Maximum number of issues to return.

    Returns:
        List of issue documents.
    """
    cursor = db.preexisting_issues.find(
        {
            "repo_full_name": repo_full_name,
            "status": "open",
        },
        {"_id": 0},
    ).sort([("detection_count", -1), ("created_at", -1)]).limit(limit)

    return await cursor.to_list(limit)


async def aggregate_maintenance_issues(
    db,
    repo_full_name: str,
    min_detection_count: int = 2,
) -> dict:
    """Aggregate preexisting issues for batch cleanup.

    Groups issues by type and file, prioritizing frequently detected issues.

    Args:
        db: MongoDB database handle.
        repo_full_name: Full repository name.
        min_detection_count: Minimum detection count to include.

    Returns:
        Dict with aggregated issues by type and file.
    """
    pipeline = [
        {
            "$match": {
                "repo_full_name": repo_full_name,
                "status": "open",
                "detection_count": {"$gte": min_detection_count},
            }
        },
        {
            "$group": {
                "_id": {
                    "issue_type": "$issue_type",
                    "file_path": "$file_path",
                },
                "issues": {"$push": "$$ROOT"},
                "total_detections": {"$sum": "$detection_count"},
            }
        },
        {
            "$sort": {"total_detections": -1}
        },
    ]

    results = await db.preexisting_issues.aggregate(pipeline).to_list(100)

    aggregated = {
        "repo_full_name": repo_full_name,
        "total_groups": len(results),
        "groups": [],
    }

    for r in results:
        aggregated["groups"].append({
            "issue_type": r["_id"]["issue_type"],
            "file_path": r["_id"]["file_path"],
            "total_detections": r["total_detections"],
            "issues": [
                {
                    "issue_id": i["issue_id"],
                    "line_range": i["line_range"],
                    "description": i["issue_description"],
                    "detection_count": i["detection_count"],
                }
                for i in r["issues"]
            ],
        })

    return aggregated


async def create_maintenance_ticket(
    db,
    issue_ids: list[str],
    ticket_title: str,
    ticket_description: str,
) -> str | None:
    """Create a Linear ticket for maintenance issues.

    Args:
        db: MongoDB database handle.
        issue_ids: List of issue_ids to link to the ticket.
        ticket_title: Title for the Linear ticket.
        ticket_description: Description for the ticket.

    Returns:
        The Linear ticket ID if created, None on failure.
    """
    from webhooks.linear_api import _graphql_request

    # Create the ticket via Linear API
    query = """
    mutation CreateIssue($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue {
                id
                identifier
                url
            }
        }
    }
    """

    # Get the team ID for the maintenance team
    team_id = None  # Would need to be configured

    if not team_id:
        logger.warning("[MAINTENANCE] No team ID configured for maintenance tickets")
        return None

    variables = {
        "input": {
            "teamId": team_id,
            "title": ticket_title,
            "description": ticket_description,
            "labelIds": [],  # Could add a "maintenance" label
        }
    }

    result = await _graphql_request(query, variables)
    issue_data = result.get("data", {}).get("issueCreate", {})

    if issue_data.get("success"):
        issue = issue_data.get("issue", {})
        ticket_id = issue.get("identifier")

        # Update all linked issues
        now = datetime.now(timezone.utc)
        await db.preexisting_issues.update_many(
            {"issue_id": {"$in": issue_ids}},
            {
                "$set": {
                    "status": "ticketed",
                    "linear_ticket_id": ticket_id,
                    "updated_at": now,
                }
            },
        )

        logger.info(
            "[MAINTENANCE] Created ticket %s for %d issues",
            ticket_id, len(issue_ids),
        )
        return ticket_id
    else:
        logger.error("[MAINTENANCE] Failed to create ticket: %s", result)
        return None


async def mark_issue_fixed(
    db,
    issue_id: str,
    fixed_in_pr: int | None = None,
) -> bool:
    """Mark a preexisting issue as fixed.

    Args:
        db: MongoDB database handle.
        issue_id: The issue to mark as fixed.
        fixed_in_pr: PR number where the fix was merged (optional).

    Returns:
        True if updated, False if not found.
    """
    now = datetime.now(timezone.utc)
    result = await db.preexisting_issues.update_one(
        {"issue_id": issue_id},
        {
            "$set": {
                "status": "fixed",
                "fixed_in_pr": fixed_in_pr,
                "updated_at": now,
            }
        },
    )

    if result.modified_count > 0:
        logger.info("[MAINTENANCE] Marked issue %s as fixed", issue_id)
        return True
    return False


async def mark_issue_ignored(
    db,
    issue_id: str,
    reason: str,
) -> bool:
    """Mark a preexisting issue as ignored (won't fix).

    Args:
        db: MongoDB database handle.
        issue_id: The issue to mark as ignored.
        reason: Why the issue is being ignored.

    Returns:
        True if updated, False if not found.
    """
    now = datetime.now(timezone.utc)
    result = await db.preexisting_issues.update_one(
        {"issue_id": issue_id},
        {
            "$set": {
                "status": "ignored",
                "ignore_reason": reason,
                "updated_at": now,
            }
        },
    )

    if result.modified_count > 0:
        logger.info("[MAINTENANCE] Marked issue %s as ignored: %s", issue_id, reason)
        return True
    return False
