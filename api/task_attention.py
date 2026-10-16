"""Why an active board task stopped, and whether it is waiting on the user.

A task whose run is over lands in one of two board columns:
  needs_input  - blocked on the user: Loma asked a question, a plan or a
                 human task waits for a decision, or the run failed or was
                 cut off (restart, disconnect)
  review       - finished, or stopped by the user: worth a look, never urgent

Only "needs_input" pings, counts in the tab title and badges the sidebar.
Questions and plans are recorded on the conversation when a run ends
(`needs_you_reason`); everything else is read from fields that already exist.
"""

QUESTION = "question"
PLAN = "plan"
APPROVAL = "approval"
INFORMATION = "information"
FAILED = "failed"
STOPPED = "stopped"
FINISHED = "finished"
STOPPED_BY_YOU = "stopped_by_you"

NEEDS_YOU_REASONS = (QUESTION, PLAN, APPROVAL, INFORMATION, FAILED, STOPPED)
# Recorded on the conversation when a run ends with a question or a plan.
RECORDED_REASONS = (QUESTION, PLAN)

# mark_interrupted reasons for a stop the user pressed themselves.
USER_STOP_REASONS = ("Stopped by user", "Interrupted by user")

CLARIFY_MARKER = ":::clarify"
PLAN_FENCE = "```plan"


def reason_in_text(text: str | None) -> str | None:
    """A question or plan the run ended on, read from its reply text."""
    if not text:
        return None
    if CLARIFY_MARKER in text:
        return QUESTION
    if PLAN_FENCE in text:
        return PLAN
    return None


def waiting_reason(task: dict) -> str | None:
    """Why an active task is not running, or None while it runs or isn't active."""
    if task.get("task_status") != "active":
        return None
    status = task.get("status")
    if status in (None, "running", "queued"):
        return None
    human = task.get("human_task") or {}
    if human.get("state") == "pending":
        return INFORMATION if human.get("kind") == INFORMATION else APPROVAL
    if status == "error":
        return FAILED
    if status == "interrupted":
        return STOPPED_BY_YOU if task.get("error") in USER_STOP_REASONS else STOPPED
    recorded = task.get("needs_you_reason")
    return recorded if recorded in RECORDED_REASONS else FINISHED


def needs_you(task: dict) -> bool:
    return waiting_reason(task) in NEEDS_YOU_REASONS


def needs_you_filter() -> dict:
    """Mongo clause for active tasks blocked on the user. Combine with $and."""
    return {"$or": [
        {"human_task.state": "pending"},
        {"status": "error"},
        {"status": "interrupted", "error": {"$nin": list(USER_STOP_REASONS)}},
        {"status": "completed", "needs_you_reason": {"$in": list(RECORDED_REASONS)}},
    ]}


async def backfill_recorded_reasons(db) -> int:
    """Tag tasks that finished on a question or plan before reasons were
    recorded, so they stay in Needs you. Idempotent; only touches tasks still
    waiting (active, completed, no reason yet)."""
    if db is None:
        return 0
    updated = 0
    cursor = db.conversations.find(
        {
            "task_status": "active",
            "status": "completed",
            "needs_you_reason": {"$exists": False},
            "final_response": {"$regex": ":::clarify|```plan"},
        },
        {"conversation_id": 1, "final_response": 1},
    )
    async for doc in cursor:
        reason = reason_in_text(doc.get("final_response"))
        if not reason:
            continue
        result = await db.conversations.update_one(
            {"_id": doc["_id"], "status": "completed", "needs_you_reason": {"$exists": False}},
            {"$set": {"needs_you_reason": reason}},
        )
        updated += result.modified_count
    return updated
