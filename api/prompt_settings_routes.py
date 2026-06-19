"""Prompt settings API — edit core agent prompt sections stored in MongoDB."""

from datetime import datetime, timezone

from aiohttp import web

from agent.prompt import (
    PROMPT_SETTING_KEYS,
    PROMPT_SETTING_TITLES,
    refresh_prompt_settings_from_db,
)
from api.auth_helpers import get_user_email, require_maintainer_or_above
from api.prompt_setting_defaults import get_default_prompt_setting
from observability.db import get_db


def _serialize(doc):
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if key == "_id":
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


async def handle_list_prompt_settings(request: web.Request) -> web.Response:
    """GET /api/prompt-settings — list editable core prompt sections."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    docs = await db.prompt_settings.find({"setting_key": {"$in": list(PROMPT_SETTING_KEYS)}}).to_list(10)
    by_key = {doc.get("setting_key"): doc for doc in docs}
    settings = []
    for key in PROMPT_SETTING_KEYS:
        doc = by_key.get(key) or {
            "setting_key": key,
            "title": PROMPT_SETTING_TITLES[key],
            "content": "",
            "updated_at": None,
            "updated_by": None,
        }
        doc["title"] = PROMPT_SETTING_TITLES[key]
        serialized = _serialize(doc)
        serialized["default_content"] = get_default_prompt_setting(key)
        settings.append(serialized)

    return web.json_response({"settings": settings})


async def handle_update_prompt_setting(request: web.Request) -> web.Response:
    """PATCH /api/prompt-settings/{setting_key} — update one prompt section."""
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "DB not configured"}, status=503)

    setting_key = request.match_info["setting_key"]
    if setting_key not in PROMPT_SETTING_KEYS:
        return web.json_response({"error": "Unknown prompt setting"}, status=404)

    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str):
        return web.json_response({"error": "content must be a string"}, status=400)

    now = datetime.now(timezone.utc)
    await db.prompt_settings.update_one(
        {"setting_key": setting_key},
        {"$set": {
            "setting_key": setting_key,
            "title": PROMPT_SETTING_TITLES[setting_key],
            "content": content,
            "updated_at": now,
            "updated_by": get_user_email(request),
        }},
        upsert=True,
    )
    await refresh_prompt_settings_from_db()
    try:
        from agent.pool import get_pool

        await get_pool().reload_prompt()
    except RuntimeError:
        pass
    doc = await db.prompt_settings.find_one({"setting_key": setting_key})
    return web.json_response({"setting": _serialize(doc)})


def setup_prompt_settings_routes(app: web.Application):
    app.router.add_get("/api/prompt-settings", handle_list_prompt_settings)
    app.router.add_patch("/api/prompt-settings/{setting_key}", handle_update_prompt_setting)
