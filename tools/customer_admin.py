#!/usr/bin/env python3
"""Customer offboarding via the customer-admin API (Customer Admin integration).

This tool never applies anything. `propose` posts a Slack message with a Confirm button;
only an approver's click (handled by the Loma backend) applies the plan.

Commands:
  python3 tools/customer_admin.py search --query "acme"
  python3 tools/customer_admin.py preview --requested-by u@co.com (--org-id ID | --product-ids ID1,ID2) [--reason TEXT]
  python3 tools/customer_admin.py preview --requested-by u@co.com --org-id ID --mode read-only|read-write [--reason TEXT]
  python3 tools/customer_admin.py status --plan-id PLAN_ID
  python3 tools/customer_admin.py propose --plan-id PLAN_ID --requested-by u@co.com [--channel C123 --thread-ts 1700000000.000100]

`propose` posts to the integration's approval channel unless --channel is given.

All commands print JSON and exit 1 on {"error": ...}.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from customer_offboarding import blocks, client, store  # noqa: E402


async def search(query: str) -> dict[str, Any]:
    if len(query.strip()) < 2:
        return {"error": "--query must be at least 2 characters"}
    return await client.search(query)


MODES = {"offboard": "offboard", "read-only": "read_only", "read-write": "read_write"}


async def preview(requested_by: str, org_id: str | None, product_ids: list[str], reason: str | None,
                  mode: str = "offboard") -> dict[str, Any]:
    if mode not in MODES:
        return {"error": f"--mode must be one of {', '.join(MODES)}"}
    if mode != "offboard" and (not org_id or product_ids):
        return {"error": f"--mode {mode} takes --org-id only"}
    if not org_id and not product_ids:
        return {"error": "pass --org-id or --product-ids"}
    result = await client.create_plan(requested_by, org_id=org_id, product_ids=product_ids, reason=reason, mode=MODES[mode])
    if "error" in result:
        return result
    return {**result, "next_step": "Show the summary to the user, then run `propose` with this planId to post the Confirm button."}


async def status(plan_id: str) -> dict[str, Any]:
    return await client.get_plan(plan_id)


def _connect_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    from config.app_config import OBSERVABILITY_DB_NAME
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("OBSERVABILITY_MONGODB_URI is not set")
    mongo = AsyncIOMotorClient(uri)
    return mongo, mongo[OBSERVABILITY_DB_NAME]


def _slack():
    from slack_sdk.web.async_client import AsyncWebClient
    from tools.slack_reader import _get_bot_token
    return AsyncWebClient(token=_get_bot_token())


async def propose(plan_id: str, requested_by: str, channel: str | None, thread_ts: str | None) -> dict[str, Any]:
    channel = channel or client.approval_channel()
    if not channel:
        return {"error": "no --channel given and the Customer Admin integration has no approval_channel"}
    plan = await client.get_plan(plan_id)
    if "error" in plan:
        return plan
    if plan.get("status") != "PLANNED":
        return {"error": f"plan is {plan.get('status')}; run preview again for a fresh plan"}
    totals = plan["summary"].get("totals", {})
    if not any(totals.get(key) for key in ("campaigns", "memberships", "sdkKeys", "apiSecrets", "dashboardAccessChanges")):
        return {"error": "nothing to change: no live campaigns, removable members or active keys, and dashboard access is already as requested"}

    mongo, db = _connect_db()
    try:
        request = await store.create_request(
            db, plan_id=plan_id, requested_by=requested_by, channel_id=channel, thread_ts=thread_ts,
            summary=plan["summary"], expires_at=str(plan.get("expiresAt") or ""),
        )
        response = await _slack().chat_postMessage(
            channel=channel, thread_ts=thread_ts, blocks=blocks.proposal_blocks(request),
            text=f"{blocks.proposal_title(plan['summary'])} An approver must confirm.",
        )
        await store.set_message_ts(db, request["request_id"], response["ts"])
    finally:
        mongo.close()
    return {
        "proposed": True,
        "request_id": request["request_id"],
        "channel": channel,
        "message_ts": response["ts"],
        "next_step": "Tell the user it was posted for approval (channel above) and an approver must click Confirm within 30 minutes. Do not apply it yourself.",
    }


def _flag(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    if index + 1 >= len(args):
        print(json.dumps({"error": f"{name} requires a value"}))
        sys.exit(1)
    value = args[index + 1]
    del args[index:index + 2]
    return value


def _usage() -> None:
    print(__doc__)
    sys.exit(1)


def main(argv: list[str]) -> dict[str, Any]:
    if not argv:
        _usage()
    command, rest = argv[0], list(argv[1:])
    if command == "search":
        return asyncio.run(search(_flag(rest, "--query") or ""))
    if command == "preview":
        ids = [p.strip() for p in (_flag(rest, "--product-ids") or "").split(",") if p.strip()]
        requested_by = _flag(rest, "--requested-by")
        if not requested_by:
            return {"error": "--requested-by is required"}
        return asyncio.run(preview(requested_by, _flag(rest, "--org-id"), ids, _flag(rest, "--reason"), _flag(rest, "--mode") or "offboard"))
    if command == "status":
        plan_id = _flag(rest, "--plan-id")
        return asyncio.run(status(plan_id)) if plan_id else {"error": "--plan-id is required"}
    if command == "propose":
        plan_id, requested_by, channel = _flag(rest, "--plan-id"), _flag(rest, "--requested-by"), _flag(rest, "--channel")
        if not (plan_id and requested_by):
            return {"error": "--plan-id and --requested-by are required"}
        return asyncio.run(propose(plan_id, requested_by, channel, _flag(rest, "--thread-ts")))
    _usage()
    return {}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    output = main(sys.argv[1:])
    print(json.dumps(output, indent=2, default=str))
    if "error" in output:
        sys.exit(1)
