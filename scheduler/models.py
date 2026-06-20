import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def create_flow(db, data: dict) -> dict:
    """Create a new flow document and return it."""
    flow_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    trigger_type = data.get("trigger_type", "scheduled")

    flow = {
        "flow_id": flow_id,
        "name": data["name"],
        "description": data.get("description", ""),
        "prompt": data.get("prompt", ""),
        "model": data.get("model"),

        # Trigger type: "scheduled" (cron/one-time) or "webhook" (event-driven)
        "trigger_type": trigger_type,

        # Schedule (used by scheduled flows)
        "schedule_type": data.get("schedule_type", "recurring"),
        "frequency": data.get("frequency", ""),
        "cron": data.get("cron"),
        "timezone": data.get("timezone", "Asia/Kolkata"),
        "start_time": data.get("start_time"),
        "end_time": data.get("end_time"),

        # Channel (used by scheduled flows for Slack posting)
        "channel_id": data.get("channel_id", ""),
        "channel_name": data.get("channel_name", ""),

        # Webhook config (used by webhook flows)
        "prompt_template": data.get("prompt_template", ""),
        "webhook_config": data.get("webhook_config", {}),

        # Status
        "status": data.get("status", "active"),

        # Labels
        "labels": data.get("labels", []),

        # Visibility: "private" (creator + admins only) or "shared" (all users)
        "visibility": data.get("visibility", "shared"),

        # Metadata
        "created_by": data.get("created_by", {}),
        "created_at": now,
        "updated_at": now,

        # Run details
        "last_run_at": None,
        "next_run_at": None,
        "run_count": 0,
        "creation_conversation_id": data.get("creation_conversation_id"),
        "last_run_conversation_id": None,
        "last_error": None,
    }

    await db.flows.insert_one(flow)
    logger.info("Created flow %s: %s", flow_id, flow["name"])
    return flow


async def update_flow(db, flow_id: str, updates: dict) -> dict | None:
    """Update specific fields on a flow. Returns updated doc or None."""
    updates["updated_at"] = datetime.now(timezone.utc)
    result = await db.flows.find_one_and_update(
        {"flow_id": flow_id},
        {"$set": updates},
        return_document=True,
    )
    if result:
        logger.info("Updated flow %s", flow_id)
    return result


async def get_flow(db, flow_id: str) -> dict | None:
    """Get a single flow by flow_id."""
    return await db.flows.find_one({"flow_id": flow_id})


async def list_flows(
    db,
    status: str | None = None,
    trigger_type: str | None = None,
    user_email: str | None = None,
    system_role: str | None = None,
) -> list[dict]:
    """List flows, filtered by status, trigger_type, and visibility.

    Non-admin users only see shared flows plus their own private flows.
    """
    query: dict = {}
    if status:
        query["status"] = status
    if trigger_type:
        query["trigger_type"] = trigger_type

    # Visibility: admins see everything; others see shared + own private
    if system_role != "admin" and user_email:
        query["$or"] = [
            {"visibility": {"$ne": "private"}},
            {"created_by.source": user_email},
            {"created_by.user_name": user_email},
        ]

    return await db.flows.find(query).sort("created_at", -1).to_list(None)


async def delete_flow(db, flow_id: str) -> bool:
    """Delete a flow. Returns True if deleted."""
    result = await db.flows.delete_one({"flow_id": flow_id})
    if result.deleted_count > 0:
        logger.info("Deleted flow %s", flow_id)
        return True
    return False


# --- Label helpers ---

async def add_label_to_flow(db, flow_id: str, label: str) -> dict | None:
    """Add a single label to a flow (idempotent)."""
    result = await db.flows.find_one_and_update(
        {"flow_id": flow_id},
        {
            "$addToSet": {"labels": label},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        return_document=True,
    )
    if result:
        logger.info("Added label '%s' to flow %s", label, flow_id)
    return result


async def remove_label_from_flow(db, flow_id: str, label: str) -> dict | None:
    """Remove a single label from a flow."""
    result = await db.flows.find_one_and_update(
        {"flow_id": flow_id},
        {
            "$pull": {"labels": label},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        return_document=True,
    )
    if result:
        logger.info("Removed label '%s' from flow %s", label, flow_id)
    return result


async def list_all_labels(db) -> list[str]:
    """Return all distinct labels used across flows."""
    return await db.flows.distinct("labels")
