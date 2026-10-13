"""Slash-command prompt templates: built-ins plus each user's own.

Typing `/review <PR link>` in the chat sends that text as the message (so the
transcript stays readable); the backend expands it into the template's full
instructions for the agent. `{input}` in a template body is replaced by
whatever follows the command; a body without it gets the input appended.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from aiohttp import web

COLLECTION = "prompt_templates"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MAX_BODY_CHARS = 8000
MAX_TEMPLATES_PER_USER = 50

BUILTIN_TEMPLATES: list[dict] = [
    {
        "name": "review",
        "description": "Review a pull request",
        "input_hint": "<PR link>",
        "body": (
            "Review this pull request: {input}\n\n"
            "Read the code-review Loma skill first (`python3 tools/loma_skills.py dump --slug code-review`) "
            "and follow it. Check correctness, security, performance, error handling and test coverage. "
            "Report findings ranked by severity, each with file:line and a concrete fix. "
            "Do not post comments on the PR unless I ask."
        ),
    },
    {
        "name": "test",
        "description": "Test a pull request and report results",
        "input_hint": "<PR link>",
        "body": (
            "Test this pull request: {input}\n\n"
            "Read the diff and list the behaviours it changes. Write test cases for each "
            "(happy path, edge cases, regressions) with expected results. Run the PR's automated "
            "tests where you can, then report what passed, what failed (with the output) and "
            "what could not be run and why. Do not push changes or merge."
        ),
    },
    {
        "name": "plan",
        "description": "Plan before building (turns on plan mode)",
        "input_hint": "<what to build>",
        "body": "{input}",
        "plan_mode": True,
    },
    {
        "name": "sonar",
        "description": "Check a PR's SonarQube quality gate",
        "input_hint": "<PR link>",
        "body": (
            "Check the SonarQube quality gate for {input} using the sonarqube Loma skill "
            "(`python3 tools/loma_skills.py dump --slug sonarqube`). List each failing condition "
            "and new issue with file:line and a suggested fix."
        ),
    },
    {
        "name": "ticket",
        "description": "Implement a Linear ticket as a draft PR",
        "input_hint": "<Linear ticket ID or link>",
        "body": (
            "Implement Linear ticket {input} using the implement-ticket Loma skill "
            "(`python3 tools/loma_skills.py dump --slug implement-ticket`). "
            "Open a draft PR and summarise what changed and how you tested it."
        ),
    },
    {
        "name": "summary",
        "description": "Summarise this conversation so far",
        "input_hint": "",
        "body": (
            "Summarise this conversation so far: the goal, decisions made, what was done, "
            "and what is still open. Keep it short. {input}"
        ),
    },
]
_BUILTIN_BY_NAME = {t["name"]: t for t in BUILTIN_TEMPLATES}


@dataclass
class ExpandedCommand:
    name: str
    prompt: str
    plan_mode: bool


def parse_command(message: str) -> tuple[str, str] | None:
    """`/name rest` -> (name, rest); None when the message isn't a command."""
    match = re.match(r"^/([a-z0-9][a-z0-9-]{0,31})(?:\s+([\s\S]*))?$", message.strip())
    if not match:
        return None
    return match.group(1), (match.group(2) or "").strip()


def render(body: str, user_input: str) -> str:
    if "{input}" in body:
        return body.replace("{input}", user_input).strip()
    return f"{body.rstrip()}\n\n{user_input}".strip() if user_input else body.strip()


async def expand_command(db, user_email: str | None, message: str) -> ExpandedCommand | None:
    """Expand a slash command, or None to send the message as typed (unknown
    commands are left alone, so a message that merely starts with / still works)."""
    parsed = parse_command(message)
    if not parsed:
        return None
    name, user_input = parsed
    template = _BUILTIN_BY_NAME.get(name)
    if template is None and db is not None and user_email:
        template = await db[COLLECTION].find_one({"owner": user_email, "name": name})
    if template is None:
        return None
    return ExpandedCommand(
        name=name,
        prompt=render(template["body"], user_input),
        plan_mode=bool(template.get("plan_mode")),
    )


def _public(template: dict, builtin: bool) -> dict:
    return {
        "name": template["name"],
        "description": template.get("description", ""),
        "input_hint": template.get("input_hint", ""),
        "body": template["body"],
        "plan_mode": bool(template.get("plan_mode")),
        "builtin": builtin,
    }


def _validate(payload: dict, *, creating: bool) -> tuple[dict | None, str | None]:
    if not isinstance(payload, dict):
        return None, "Body must be an object"
    out: dict = {}
    if creating:
        name = str(payload.get("name", "")).strip().lower().lstrip("/")
        if not NAME_RE.match(name):
            return None, "Name must be 1-32 lowercase letters, digits or dashes"
        if name in _BUILTIN_BY_NAME:
            return None, f"/{name} is a built-in command"
        out["name"] = name
    body = payload.get("body")
    if creating or body is not None:
        body = str(body or "").strip()
        if not body:
            return None, "Template text is required"
        if len(body) > MAX_BODY_CHARS:
            return None, f"Template text is limited to {MAX_BODY_CHARS} characters"
        out["body"] = body
    for key, limit in (("description", 120), ("input_hint", 60)):
        if key in payload:
            out[key] = str(payload.get(key) or "").strip()[:limit]
    if "plan_mode" in payload:
        out["plan_mode"] = payload.get("plan_mode") is True
    return out, None


def _db_and_user(request: web.Request):
    from api.auth_helpers import get_user_email
    from observability.db import get_db
    return get_db(), get_user_email(request)


async def handle_list(request: web.Request) -> web.Response:
    """GET /api/prompt-templates — built-ins, then the caller's own."""
    db, user_email = _db_and_user(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    own = []
    if db is not None:
        own = await db[COLLECTION].find({"owner": user_email}).sort("name", 1).to_list(MAX_TEMPLATES_PER_USER)
    return web.json_response({
        "templates": [_public(t, True) for t in BUILTIN_TEMPLATES] + [_public(t, False) for t in own],
    })


async def handle_create(request: web.Request) -> web.Response:
    """POST /api/prompt-templates"""
    db, user_email = _db_and_user(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    if db is None:
        return web.json_response({"error": "Database not configured"}, status=503)
    fields, error = _validate(await request.json(), creating=True)
    if error:
        return web.json_response({"error": error}, status=400)
    if await db[COLLECTION].count_documents({"owner": user_email}) >= MAX_TEMPLATES_PER_USER:
        return web.json_response({"error": f"Up to {MAX_TEMPLATES_PER_USER} templates per person"}, status=400)
    if await db[COLLECTION].find_one({"owner": user_email, "name": fields["name"]}):
        return web.json_response({"error": f"You already have /{fields['name']}"}, status=409)
    now = datetime.now(timezone.utc)
    doc = {"owner": user_email, "description": "", "input_hint": "", "plan_mode": False,
           **fields, "created_at": now, "updated_at": now}
    await db[COLLECTION].insert_one(doc)
    return web.json_response({"template": _public(doc, False)}, status=201)


async def handle_update(request: web.Request) -> web.Response:
    """PUT /api/prompt-templates/{name}"""
    db, user_email = _db_and_user(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    if db is None:
        return web.json_response({"error": "Database not configured"}, status=503)
    name = request.match_info["name"]
    fields, error = _validate(await request.json(), creating=False)
    if error:
        return web.json_response({"error": error}, status=400)
    result = await db[COLLECTION].find_one_and_update(
        {"owner": user_email, "name": name},
        {"$set": {**fields, "updated_at": datetime.now(timezone.utc)}},
        return_document=True,
    )
    if not result:
        return web.json_response({"error": "Template not found"}, status=404)
    return web.json_response({"template": _public(result, False)})


async def handle_delete(request: web.Request) -> web.Response:
    """DELETE /api/prompt-templates/{name}"""
    db, user_email = _db_and_user(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    if db is None:
        return web.json_response({"error": "Database not configured"}, status=503)
    result = await db[COLLECTION].delete_one({"owner": user_email, "name": request.match_info["name"]})
    if not result.deleted_count:
        return web.json_response({"error": "Template not found"}, status=404)
    return web.json_response({"deleted": True})


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/prompt-templates", handle_list)
    app.router.add_post("/api/prompt-templates", handle_create)
    app.router.add_put("/api/prompt-templates/{name}", handle_update)
    app.router.add_delete("/api/prompt-templates/{name}", handle_delete)
