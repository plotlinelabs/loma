"""Deliver durable handoffs using existing notifications and agent runner.

Pending decisions survive restart. Runs interrupted after dispatch are NOT
blindly replayed: external writes may have succeeded. Requester must inspect
and continue the source conversation manually in that case.
"""
import asyncio
import logging
import os
from contextlib import suppress

from api import human_tasks as service
from observability.notifications import create_notification

logger = logging.getLogger(__name__)


async def notify_assignment(db, doc):
    h = doc["human_task"]
    await create_notification(
        db, user_email=doc["metadata"]["user_name"], title=doc["title"],
        body=f"**{h['kind'].capitalize()} requested by {h['requested_by']}**\n\nOpen the task to review and respond.",
        conversation_id=doc["conversation_id"], source="system",
        dedupe_key=f"human-task:{doc['conversation_id']}:assigned:{h['version']}")
    await db.conversations.update_one(
        {"_id": doc["_id"], "human_task.version": h["version"]},
        {"$set": {"human_task.assignment_notice": "sent"}})


async def run_source(db, source, task):
    from agent.client import stream_agent
    from observability.observer import ConversationObserver
    from api.task_routes import build_board_context
    from api.agent_identity_routes import resolve_agent_for_chat, build_agent_context_block
    h = task["human_task"]
    owner = h["requested_by"]
    await service.active_user(db, owner)
    if service.source_owner(source) != owner:
        raise ValueError("Source execution owner changed")
    metadata = source.get("metadata") or {}
    context = []
    # Revalidate current flow execution identity; do not resume a disabled flow.
    if metadata.get("flow_id"):
        from scheduler.run_identity import require_execution_account
        from scheduler.agent_work import validate_agent_work
        flow = await db.flows.find_one({"flow_id": metadata["flow_id"]})
        if not flow or not flow.get("enabled", True) or flow.get("status") != "active":
            raise ValueError("Source flow is no longer enabled")
        if await require_execution_account(db, flow) != owner:
            raise ValueError("Flow execution account changed")
        if flow.get("agent_id"):
            await validate_agent_work(db, flow)
    if source.get("task_status"):
        context.append(await build_board_context(db, owner))
    if metadata.get("agent_id"):
        agent = await resolve_agent_for_chat(db, metadata["agent_id"], owner)
        if not agent:
            raise ValueError("Originating agent is no longer accessible")
        context.append(await build_agent_context_block(db, agent))
    context.append("Original request:\n" + (source.get("prompt") or ""))
    context.extend(f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in source.get("messages", []))
    prompt = (f"Human task {task['conversation_id']} received a response from {h['responded_by']}.\n"
              f"Request type: {h['kind']}\nExact request: {h['details']}\n"
              f"Decision: {h['decision']}\nResponse: {h['response']}\n"
              f"Ticket: {h['ticket_url']}\n"
              "Continue the original request. Approval applies only to the exact request above; "
              "recheck current records before acting. Rejection does not authorize the action. "
              "Information is not approval. Do not repeat completed actions. "
              "Your final response is saved to this conversation, not automatically sent externally.")
    observer = ConversationObserver(db, metadata={**metadata, "prompt": prompt},
                                    conversation_id=source["conversation_id"])
    await db.conversations.update_one(
        {"conversation_id": source["conversation_id"]},
        {"$set": {"metadata.human_task_resume_id": task["conversation_id"]}})
    await observer.resume()
    try:
        async for _ in stream_agent(
            prompt=prompt, conversation_context="\n\n".join(context), observer=observer,
            user_email=owner, source="dashboard" if source.get("source") == "dashboard" else "slack",
            selected_model=source.get("model") or None, tool_config=source.get("tool_config"),
            raise_on_opencode_error=True,
        ):
            pass
        result = await db.conversations.find_one({"conversation_id": source["conversation_id"]})
        if result.get("status") != "completed":
            raise ValueError("Agent continuation did not complete")
    finally:
        observer._stop_heartbeat()


async def dispatch(db, doc, runner=run_source):
    from agent.active_streams import try_claim, release_claim
    from api.drain import is_draining
    if is_draining():
        return
    h = doc["human_task"]
    cid = h["source_conversation_id"]
    claim = f"human-task:{doc['conversation_id']}"
    if not await try_claim(cid, claim):
        return  # Remains queued until the current turn finishes.
    try:
        claimed = await db.conversations.update_one(
            {"_id": doc["_id"], "human_task.resume_state": "queued", "deleted": {"$ne": True}},
            {"$set": {"human_task.resume_state": "running"}})
        if not claimed.modified_count:
            return
        try:
            source = await service.source_for(db, h["requested_by"], cid)
            if source.get("error") == "Stopped by user":
                raise ValueError("Source was stopped by its owner")
            await runner(db, source, doc)
            state = "completed"
        except Exception:
            logger.exception("Human task continuation failed: %s", doc["conversation_id"])
            state = "needs_review"
        await db.conversations.update_one(
            {"_id": doc["_id"]}, {"$set": {"human_task.resume_state": state}})
    finally:
        await release_claim(cid, claim)


async def tick(db, runner=run_source, schedule=None):
    docs = await db.conversations.find({"human_task": {"$exists": True},
                                        "deleted": {"$ne": True}, "$or": [
        {"human_task.assignment_notice": "pending"},
        {"human_task.resume_state": {"$in": ["queued", "completed", "needs_review"]},
         "human_task.result_notice": {"$ne": "sent"}},
    ]}).limit(50).to_list(50)
    for doc in docs:
        try:
            h = doc["human_task"]
            if h["assignment_notice"] == "pending":
                await notify_assignment(db, doc)
            if h["resume_state"] == "queued":
                if schedule:
                    schedule(doc)
                else:
                    await dispatch(db, doc, runner)
            elif h["resume_state"] in ("completed", "needs_review"):
                await create_notification(
                    db, user_email=h["requested_by"], title="Human task: " + doc["title"],
                    body=("The agent continued after the human response." if h["resume_state"] == "completed" else
                          "The response is saved, but continuation needs review. Check the source conversation and external records before continuing manually; actions may already have succeeded."),
                    conversation_id=h["source_conversation_id"], source="system",
                    dedupe_key=f"human-task:{doc['conversation_id']}:result")
                await db.conversations.update_one({"_id": doc["_id"]},
                                                  {"$set": {"human_task.result_notice": "sent"}})
        except Exception:
            logger.exception("Human task delivery failed: %s", doc["conversation_id"])


async def lifecycle(app):
    from observability.db import get_db
    worker = None
    app["human_task_worker_running"] = False
    running = {}
    db = get_db()
    if db is not None and os.getenv("LOMA_ENABLE_SCHEDULER", "true").lower() == "true":
        await db.conversations.update_many(
            {"human_task.resume_state": "running"},
            {"$set": {"human_task.resume_state": "needs_review"}})
        def schedule(doc):
            cid = doc["conversation_id"]
            if cid in running or len(running) >= 4:
                return
            async def run():
                try:
                    await dispatch(db, doc)
                except Exception:
                    logger.exception("Human task dispatch failed: %s", cid)
                finally:
                    running.pop(cid, None)
            running[cid] = asyncio.create_task(run())

        async def loop():
            while True:
                try:
                    await tick(db, schedule=schedule)
                except Exception:
                    logger.exception("Human task sweep failed")
                await asyncio.sleep(5)
        worker = asyncio.create_task(loop())
        app["human_task_worker_running"] = True
    yield
    app["human_task_worker_running"] = False
    if worker:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
    pending = list(running.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
