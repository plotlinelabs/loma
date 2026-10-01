"""Slack button outcomes: confirm (apply), cancel, undo (restore).

These run in the backend Slack handler, never in the agent, and gate on the clicker's
Slack-verified email being in the integration's approver list.
"""

from __future__ import annotations

import logging
from typing import Any

from customer_offboarding import blocks, client, store

logger = logging.getLogger(__name__)


async def _update(slack, channel: str, ts: str, message_blocks: list[dict[str, Any]], text: str) -> None:
    try:
        await slack.chat_update(channel=channel, ts=ts, blocks=message_blocks, text=text)
    except Exception as e:  # noqa: BLE001 - a failed cosmetic update must not mask the outcome
        logger.warning("[OFFBOARD] chat_update failed: %s", e)


async def _tell(slack, channel: str, user: str, thread_ts: str | None, text: str) -> None:
    try:
        await slack.chat_postEphemeral(channel=channel, user=user, text=text, thread_ts=thread_ts)
    except Exception as e:  # noqa: BLE001
        logger.warning("[OFFBOARD] ephemeral failed: %s", e)


def _is_approver(email: str | None) -> bool:
    return bool(email) and email.lower() in client.approver_emails()


async def handle_confirm(db, slack, request_id: str, slack_user_id: str, email: str | None, channel: str, message_ts: str) -> None:
    request = await store.get_request(db, request_id)
    if request is None:
        await _tell(slack, channel, slack_user_id, None, "This offboarding request no longer exists.")
        return
    thread_ts = request.get("thread_ts")
    if not _is_approver(email):
        await store.audit(db, request_id, "confirm_denied", email, slack_user_id=slack_user_id)
        await _tell(slack, channel, slack_user_id, thread_ts, "Only offboarding approvers can confirm this. Ask an approver to click Confirm.")
        return

    approver = email.lower()
    request = await store.transition(db, request_id, ["pending"], "applying", approved_by=approver)
    if request is None:
        await _tell(slack, channel, slack_user_id, thread_ts, "This request was already handled.")
        return
    await store.audit(db, request_id, "confirmed", approver, slack_user_id=slack_user_id)
    await _update(slack, channel, message_ts, blocks.status_blocks(request, f":hourglass_flowing_sand: Offboarding in progress, confirmed by {approver}…"), "Offboarding in progress")

    result = await client.apply_plan(request["plan_id"], approver)
    if "error" in result and "status" not in result:
        # Timeout or network error: the apply may still have landed, so ask the service.
        plan = await client.get_plan(request["plan_id"])
        if plan.get("status") == "APPLIED":
            result = {"result": plan.get("result") or {}}

    if "error" in result:
        restorable = result.get("status", 500) >= 500
        await store.transition(db, request_id, ["applying"], "failed", error=result["error"], restorable=restorable)
        await store.audit(db, request_id, "apply_failed", approver, error=result["error"])
        detail = f"Error: {result['error']}"
        message_blocks = blocks.status_blocks(request, ":x: *Offboarding failed*", detail)
        if restorable:
            message_blocks = blocks.applied_blocks(request, {}, approver)
            message_blocks[0] = blocks.status_blocks(request, ":x: *Offboarding failed part-way*", f"{detail}\nUndo reverts whatever was applied.")[0]
        await _update(slack, channel, message_ts, message_blocks, "Offboarding failed")
        return

    outcome = result.get("result") or {}
    await store.transition(db, request_id, ["applying"], "applied", result=outcome)
    await store.audit(db, request_id, "applied", approver, result=outcome)
    await _update(slack, channel, message_ts, blocks.applied_blocks(request, outcome, approver), "Customer offboarded")


async def handle_cancel(db, slack, request_id: str, slack_user_id: str, email: str | None, channel: str, message_ts: str) -> None:
    request = await store.get_request(db, request_id)
    if request is None:
        return
    is_requester = bool(email) and email.lower() == request["requested_by"]
    if not (is_requester or _is_approver(email)):
        await _tell(slack, channel, slack_user_id, request.get("thread_ts"), "Only the requester or an approver can cancel this.")
        return
    request = await store.transition(db, request_id, ["pending"], "cancelled", cancelled_by=email.lower())
    if request is None:
        await _tell(slack, channel, slack_user_id, None, "This request was already handled.")
        return
    await store.audit(db, request_id, "cancelled", email.lower())
    await _update(slack, channel, message_ts, blocks.status_blocks(request, f":no_entry_sign: Offboarding cancelled by {email.lower()}. Nothing was changed."), "Offboarding cancelled")


async def handle_undo(db, slack, request_id: str, slack_user_id: str, email: str | None, channel: str, message_ts: str) -> None:
    request = await store.get_request(db, request_id)
    if request is None:
        return
    thread_ts = request.get("thread_ts")
    if not _is_approver(email):
        await store.audit(db, request_id, "undo_denied", email, slack_user_id=slack_user_id)
        await _tell(slack, channel, slack_user_id, thread_ts, "Only offboarding approvers can undo this.")
        return
    approver = email.lower()
    previous = request["status"]
    request = await store.transition(db, request_id, ["applied", "failed"], "restoring", restore_requested_by=approver)
    if request is None:
        await _tell(slack, channel, slack_user_id, thread_ts, "This offboarding is not in a state that can be undone.")
        return
    await _update(slack, channel, message_ts, blocks.status_blocks(request, f":hourglass_flowing_sand: Restoring, requested by {approver}…"), "Restoring")

    result = await client.restore_plan(request["plan_id"], approver)
    if "error" in result:
        await store.transition(db, request_id, ["restoring"], previous, error=result["error"])
        await store.audit(db, request_id, "restore_failed", approver, error=result["error"])
        await _update(slack, channel, message_ts, blocks.applied_blocks(request, request.get("result") or {}, request.get("approved_by") or approver), "Restore failed")
        await _tell(slack, channel, slack_user_id, thread_ts, f"Restore failed: {result['error']}. It is safe to click Undo again.")
        return
    await store.transition(db, request_id, ["restoring"], "restored", restored=result.get("restored"))
    await store.audit(db, request_id, "restored", approver, restored=result.get("restored"))
    restored = result.get("restored") or {}
    detail = (
        f"Restored {restored.get('campaigns', 0)} campaign(s), {restored.get('memberships', 0)} membership(s), "
        f"{restored.get('sdkKeys', 0)} SDK key(s) and {restored.get('apiSecrets', 0)} API secret(s)."
    )
    await _update(slack, channel, message_ts, blocks.status_blocks(request, f":leftwards_arrow_with_hook: *Offboarding undone* by {approver}", detail), "Offboarding undone")
