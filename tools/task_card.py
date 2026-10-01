"""Task card tool — a task inside a card reads and updates its own card.

Cards live on card boards (see api/task_routes.py). Every task inside a card
gets these commands in its context. The caller is verified with the same
HMAC auth token as the other personal tools, a task can only touch its own
card, and writes need owner/editor access to the board. Values Loma writes
are marked "Filled by Loma" on the card; notes go to a separate "Loma's
notes" list and never overwrite the user's own notes.

Commands:
  task_card.py --auth-token T --user-email E --conversation-id C get
  task_card.py ... set-fields --fields-json '{"Value": 150000, "Close date": "2026-10-31"}'
  task_card.py ... add-note --text "..."
  task_card.py ... move --column "Negotiation"
  task_card.py ... add-todo --title "Send the proposal"
"""

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

USAGE = [
    "task_card.py --auth-token T --user-email E --conversation-id C get",
    "task_card.py --auth-token T --user-email E --conversation-id C set-fields --fields-json JSON",
    "task_card.py --auth-token T --user-email E --conversation-id C add-note --text TEXT",
    "task_card.py --auth-token T --user-email E --conversation-id C move --column NAME",
    "task_card.py --auth-token T --user-email E --conversation-id C add-todo --title TITLE",
]


def _verify_auth(auth_token: str, user_email: str) -> bool:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _auth_token import verify_user_auth_token
    return verify_user_auth_token(auth_token, user_email)


def _parse_single(args: list[str], flag: str) -> str | None:
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return None


def _command_args(command: str, rest: list[str]) -> dict:
    if command == "set-fields":
        raw = _parse_single(rest, "--fields-json")
        if raw is None:
            raise ValueError("set-fields needs --fields-json")
        try:
            return {"fields": json.loads(raw)}
        except json.JSONDecodeError as e:
            raise ValueError(f"--fields-json is not valid JSON: {e}")
    if command == "add-note":
        return {"text": _parse_single(rest, "--text")}
    if command == "move":
        return {"column": _parse_single(rest, "--column")}
    if command == "add-todo":
        return {"title": _parse_single(rest, "--title")}
    return {}


async def _run(user_email: str, conversation_id: str, command: str, args: dict) -> dict:
    from motor.motor_asyncio import AsyncIOMotorClient
    from api.task_routes import CardToolError, card_tool_run

    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri:
        return {"error": "OBSERVABILITY_MONGODB_URI environment variable is not set"}
    client = AsyncIOMotorClient(uri)
    db = client[os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability").strip()]
    try:
        return await card_tool_run(db, user_email, conversation_id, command, args)
    except CardToolError as e:
        return {"error": str(e)}
    finally:
        client.close()


def main(argv: list[str]) -> int:
    auth_token = _parse_single(argv, "--auth-token")
    user_email = _parse_single(argv, "--user-email")
    conversation_id = _parse_single(argv, "--conversation-id")
    if not auth_token or not user_email or not conversation_id:
        print(json.dumps({"error": "Missing --auth-token, --user-email or --conversation-id",
                          "usage": USAGE}))
        return 1
    if not _verify_auth(auth_token, user_email):
        print(json.dumps({"error": "Authentication failed. The auth token is invalid, expired, or "
                                   "doesn't match the user email. Use the token from this message."}))
        return 1

    rest: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in ("--auth-token", "--user-email", "--conversation-id"):
            skip = True
            continue
        rest.append(arg)
    if not rest:
        print(json.dumps({"usage": USAGE}))
        return 1
    command = rest[0]
    try:
        args = _command_args(command, rest[1:])
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    result = asyncio.run(_run(user_email, conversation_id, command, args))
    print(json.dumps(result, indent=2, default=str))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(main(sys.argv[1:]))
