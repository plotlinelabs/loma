"""Personal human-task tools. No CLI decision command: humans respond in Loma.

Create/assign/get requests using the authenticated user's connection. Uses the
existing taskboard Mongo database; never takes a caller-supplied execution user.
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._auth_token import verify_user_auth_token


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--user-email", required=True)
    p.add_argument("--auth-token", required=True)
    commands = p.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    for flag in ("source-conversation-id", "assignee", "title", "details", "request-key"):
        create.add_argument("--" + flag, required=True)
    create.add_argument("--kind", choices=("approval", "information"), required=True)
    create.add_argument("--ticket-url", default="")
    get = commands.add_parser("get")
    get.add_argument("--task-id", required=True)
    assign = commands.add_parser("assign")
    assign.add_argument("--task-id", required=True)
    assign.add_argument("--assignee", required=True)
    assign.add_argument("--version", type=int, required=True)
    commands.add_parser("people")
    return p


async def execute(args):
    if not verify_user_auth_token(args.auth_token, args.user_email):
        raise ValueError("Invalid or expired personal auth token")
    from api import human_tasks as service
    from api.task_routes import _serialize
    from tools.notify import _get_client
    client, db = _get_client()
    try:
        await service.active_user(db, args.user_email)
        created = None
        if args.command == "people":
            docs = await db.users.find({"status": {"$in": ["active", None]}},
                                       {"_id": 0, "email": 1, "name": 1}).to_list(1000)
            return {"people": docs}
        if args.command == "create":
            doc, created = await service.create(db, args.user_email, vars(args))
        elif args.command == "assign":
            doc = await service.assign(db, args.user_email, args.task_id, args.assignee, args.version)
        else:
            doc = await service.get(db, args.user_email, args.task_id)
        return {"task": _serialize(service.view(doc)), "created": created}
    finally:
        client.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    try:
        print(json.dumps(asyncio.run(execute(parser().parse_args()))))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
