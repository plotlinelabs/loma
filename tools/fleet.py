"""Crew tool: a member of the owner's crew hands out and checks work.

Who a member can hand work to, who reviews it and whether it may ask the
owner are all set by the owner on the Crew page; the server enforces them.

Only conversations that are part of the caller's own crew can use it (see
fleet/service.py). Caps (depth, fan-out, daily tasks) are enforced server
side. There is no command that approves anything: escalations go to the owner
as human tasks and only the owner answers them in Loma.

Commands:
  fleet.py --auth-token T --user-email E --conversation-id C status
  fleet.py ... members
  fleet.py ... create --to MEMBER_ID --title T --brief B --acceptance A --request-key K [--depends-on ID,ID]
  fleet.py ... children
  fleet.py ... review --verdict accept|rework|escalate --notes N
  fleet.py ... escalate --kind approval|information --title T --details D --request-key K
"""

import argparse
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def parser():
    p = argparse.ArgumentParser(description="Loma crew tool")
    p.add_argument("--user-email", required=True)
    p.add_argument("--auth-token", required=True)
    p.add_argument("--conversation-id", required=True)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("children")
    commands.add_parser("members")
    create = commands.add_parser("create")
    create.add_argument("--to", required=True, help="member id to hand the task to")
    for flag in ("title", "brief", "acceptance", "request-key"):
        create.add_argument("--" + flag, required=True)
    create.add_argument("--depends-on", default="")
    review = commands.add_parser("review")
    review.add_argument("--verdict", choices=("accept", "rework", "escalate"), required=True)
    review.add_argument("--notes", required=True)
    esc = commands.add_parser("escalate")
    esc.add_argument("--kind", choices=("approval", "information"), required=True)
    for flag in ("title", "details", "request-key"):
        esc.add_argument("--" + flag, required=True)
    return p


def command_args(args) -> dict:
    data = {k: v for k, v in vars(args).items()
            if k not in ("user_email", "auth_token", "conversation_id", "command")}
    if "depends_on" in data:
        data["depends_on"] = [d for d in data["depends_on"].split(",") if d.strip()]
    return data


async def execute(args) -> dict:
    from tools._auth_token import verify_user_auth_token
    if not verify_user_auth_token(args.auth_token, args.user_email):
        return {"error": "Authentication failed. Use the auth token from this message."}
    from motor.motor_asyncio import AsyncIOMotorClient
    from fleet.service import FleetError, tool_run
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        return {"error": "OBSERVABILITY_MONGODB_URI environment variable is not set"}
    client = AsyncIOMotorClient(uri)
    db = client[os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability").strip()]
    try:
        return await tool_run(db, args.user_email, args.conversation_id, args.command,
                              command_args(args))
    except FleetError as e:
        return {"error": str(e)}
    finally:
        client.close()


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    result = asyncio.run(execute(args))
    print(json.dumps(result, indent=2, default=str))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(main(sys.argv[1:]))
