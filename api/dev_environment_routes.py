"""Dev environment management routes.

Stores encrypted repo-specific env bundles and browser-login profiles that
future coding runners can materialize into isolated task worktrees.
"""

import re
import uuid
from datetime import datetime, timezone

from aiohttp import web

from api.auth_helpers import get_user_email, require_maintainer_or_above
from api.oauth_helpers import encrypt_token
from observability.db import get_db


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")


def _now():
    return datetime.now(timezone.utc)


def _serialize(doc: dict) -> dict:
    env_files = []
    for item in doc.get("env_files") or []:
        env_files.append({
            "path": item.get("path", ""),
            "configured": bool(item.get("content_encrypted")),
            "updated_at": item.get("updated_at").isoformat() if item.get("updated_at") else None,
        })

    browser_auth = doc.get("browser_auth") or {}
    return {
        "environment_id": doc.get("environment_id", ""),
        "name": doc.get("name", ""),
        "repo": doc.get("repo", ""),
        "default_branch": doc.get("default_branch", "main"),
        "worktree_base_path": doc.get("worktree_base_path", ""),
        "service_commands": doc.get("service_commands", []),
        "health_urls": doc.get("health_urls", []),
        "env_files": env_files,
        "browser_auth": {
            "login_url": browser_auth.get("login_url", ""),
            "username_configured": bool(browser_auth.get("username_encrypted")),
            "password_configured": bool(browser_auth.get("password_encrypted")),
            "success_url_contains": browser_auth.get("success_url_contains", ""),
            "allowed_domains": browser_auth.get("allowed_domains", []),
            "updated_at": browser_auth.get("updated_at").isoformat() if browser_auth.get("updated_at") else None,
        },
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_by": doc.get("updated_by"),
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
    }


def _clean_str_list(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


async def _list_dev_environments(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Database not available"}, status=503)

    docs = await db.dev_environments.find({}).sort("updated_at", -1).to_list(100)
    return web.json_response({"environments": [_serialize(doc) for doc in docs]})


async def _upsert_dev_environment(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Database not available"}, status=503)

    body = await request.json()
    environment_id = str(body.get("environment_id") or "").strip()
    name = str(body.get("name") or "").strip()
    repo = str(body.get("repo") or "").strip()
    default_branch = str(body.get("default_branch") or "main").strip() or "main"
    worktree_base_path = str(body.get("worktree_base_path") or "").strip()

    if not environment_id:
        environment_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not SLUG_RE.match(environment_id):
        return web.json_response(
            {"error": "environment_id must be 3-64 chars of lowercase letters, numbers, '-' or '_'"},
            status=400,
        )
    if not name or not repo:
        return web.json_response({"error": "name and repo are required"}, status=400)

    existing = await db.dev_environments.find_one({"environment_id": environment_id}) or {}
    existing_env = {item.get("path"): item for item in existing.get("env_files") or []}
    now = _now()

    env_files = []
    for item in body.get("env_files") or []:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        current = existing_env.get(path, {})
        next_item = {"path": path}
        if "content" in item:
            next_item["content_encrypted"] = encrypt_token(str(item.get("content") or ""))
            next_item["updated_at"] = now
        elif current.get("content_encrypted"):
            next_item["content_encrypted"] = current["content_encrypted"]
            next_item["updated_at"] = current.get("updated_at")
        env_files.append(next_item)

    existing_auth = existing.get("browser_auth") or {}
    auth_body = body.get("browser_auth") or {}
    browser_auth = {
        "login_url": str(auth_body.get("login_url") or existing_auth.get("login_url") or "").strip(),
        "success_url_contains": str(auth_body.get("success_url_contains") or existing_auth.get("success_url_contains") or "").strip(),
        "allowed_domains": _clean_str_list(auth_body.get("allowed_domains", existing_auth.get("allowed_domains", []))),
        "updated_at": existing_auth.get("updated_at"),
    }
    if "username" in auth_body:
        browser_auth["username_encrypted"] = encrypt_token(str(auth_body.get("username") or ""))
        browser_auth["updated_at"] = now
    elif existing_auth.get("username_encrypted"):
        browser_auth["username_encrypted"] = existing_auth["username_encrypted"]
    if "password" in auth_body:
        browser_auth["password_encrypted"] = encrypt_token(str(auth_body.get("password") or ""))
        browser_auth["updated_at"] = now
    elif existing_auth.get("password_encrypted"):
        browser_auth["password_encrypted"] = existing_auth["password_encrypted"]

    user_email = get_user_email(request) or "unknown"
    doc = {
        "environment_id": environment_id,
        "name": name,
        "repo": repo,
        "default_branch": default_branch,
        "worktree_base_path": worktree_base_path,
        "service_commands": _clean_str_list(body.get("service_commands")),
        "health_urls": _clean_str_list(body.get("health_urls")),
        "env_files": env_files,
        "browser_auth": browser_auth,
        "updated_by": user_email,
        "updated_at": now,
    }
    if not existing:
        doc["created_by"] = user_email
        doc["created_at"] = now

    await db.dev_environments.update_one(
        {"environment_id": environment_id},
        {"$set": doc, "$setOnInsert": {"record_id": str(uuid.uuid4())}},
        upsert=True,
    )
    saved = await db.dev_environments.find_one({"environment_id": environment_id})
    return web.json_response({"environment": _serialize(saved)})


async def _delete_dev_environment(request: web.Request) -> web.Response:
    require_maintainer_or_above(request)
    db = get_db()
    if db is None:
        return web.json_response({"error": "Database not available"}, status=503)

    environment_id = request.match_info["environment_id"]
    result = await db.dev_environments.delete_one({"environment_id": environment_id})
    if result.deleted_count == 0:
        return web.json_response({"error": "Dev environment not found"}, status=404)
    return web.json_response({"deleted": True, "environment_id": environment_id})


def setup_dev_environment_routes(app: web.Application):
    app.router.add_get("/api/dev-environments", _list_dev_environments)
    app.router.add_post("/api/dev-environments", _upsert_dev_environment)
    app.router.add_delete("/api/dev-environments/{environment_id}", _delete_dev_environment)
