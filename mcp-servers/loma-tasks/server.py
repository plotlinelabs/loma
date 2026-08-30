"""loma-tasks MCP server — expose each user's Loma task board to external agents.

A stateless Streamable-HTTP MCP server (JSON responses, no SSE sessions) that
sits next to the Loma backend on the internal Docker network:

    external agent ──Bearer loma_sk_…──> nginx /mcp/tasks ──> this server
                                                              │  (resolve key → user_email in Mongo)
                                                              └──X-User-Email──> loma-backend /api/tasks…

Auth: per-user API keys minted in the dashboard (Settings → API Keys,
`api/api_key_routes.py`). Only the SHA-256 hash lives in Mongo; a revoked key
fails on the next request. The resolved user's identity is forwarded to the
backend as X-User-Email, so the existing owner-scoping applies — every caller
sees only their own board.

Env:
    OBSERVABILITY_MONGODB_URI   Mongo URI (same cluster as the backend)
    OBSERVABILITY_DB_NAME       default "loma_observability"
    BACKEND_URL                 default "http://loma-backend:3000"
    MCP_PORT                    default 3002

Run: python server.py   (deps: aiohttp, motor — same as the backend)
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

from aiohttp import web, ClientSession
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("loma-tasks-mcp")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://loma-backend:3000").rstrip("/")
MCP_PORT = int(os.environ.get("MCP_PORT", "3002"))
DB_NAME = os.environ.get("OBSERVABILITY_DB_NAME", "loma_observability")

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
SERVER_INFO = {"name": "loma-tasks", "version": "1.0.0"}

# Cap how much conversation history get_task returns to the model.
MAX_MESSAGES = 6
MAX_MESSAGE_CHARS = 4000

# ── Tool definitions ───────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "list_tasks",
        "description": (
            "List the authenticated user's Loma task board: staging lanes, tags, "
            "per-column counts, and all tasks with their derived column. Columns are "
            "the user's staging lane ids (e.g. 'todo'), plus 'working' (agent running), "
            "'needs_input' (agent finished, user's turn) and 'done'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Optional text search over title, prompt and messages."},
                "column": {"type": "string", "description": "Optional filter: only return tasks in this column (a lane id, 'working', 'needs_input' or 'done')."},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_task",
        "description": (
            "Get one task in detail: board state, the original prompt, the agent's "
            f"final response, and the last {MAX_MESSAGES} conversation messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "The task's conversation_id (from list_tasks)."},
            },
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "create_task",
        "description": (
            "Create a task on the user's board. By default it is staged as a draft in a "
            "lane; pass start=true to have the Loma agent begin working on it immediately "
            "(prompt required in that case)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Task details / instructions for the agent."},
                "title": {"type": "string", "description": "Short card title. If omitted, one is generated from the prompt."},
                "lane": {"type": "string", "description": "Staging lane id (see list_tasks). Defaults to the first lane."},
                "start": {"type": "boolean", "description": "Start the agent on this task immediately (default false)."},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "description": "Optional priority."},
                "deadline": {"type": "string", "description": "Optional date-only deadline, YYYY-MM-DD."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "update_task",
        "description": (
            "Update a task's board state or fields. Move between columns with "
            "task_status ('todo' parks it in a lane, 'done' closes it), retitle, edit "
            "the draft prompt, or set priority/deadline. Pass an empty string for "
            "priority or deadline to clear them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "The task's conversation_id."},
                "task_status": {"type": "string", "enum": ["todo", "active", "done"], "description": "Board status transition."},
                "lane": {"type": "string", "description": "Staging lane id (with task_status 'todo')."},
                "title": {"type": "string", "description": "New title."},
                "prompt": {"type": "string", "description": "New draft prompt (unstarted drafts only)."},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent", ""], "description": "Priority, or '' to clear."},
                "deadline": {"type": "string", "description": "YYYY-MM-DD, or '' to clear."},
            },
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
    },
]

# ── Backend proxy helpers ──────────────────────────────────────────────────


class ToolError(Exception):
    """A tool-level failure reported in-band to the MCP client."""


async def _backend(request: web.Request, method: str, path: str, *,
                   json_body=None, params=None):
    """Call the Loma backend as the authenticated user."""
    session: ClientSession = request.app["http"]
    headers = {"X-User-Email": request["user_email"]}
    try:
        async with session.request(
            method, f"{BACKEND_URL}{path}", headers=headers,
            json=json_body, params=params,
        ) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = {"error": (await resp.text())[:500]}
            if resp.status >= 400:
                raise ToolError(
                    f"Loma backend returned {resp.status} for {path}: "
                    f"{data.get('error', data)}")
            return data
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Could not reach the Loma backend: {e}")


def _truncate_message(msg: dict) -> dict:
    content = msg.get("content")
    if isinstance(content, str) and len(content) > MAX_MESSAGE_CHARS:
        content = content[:MAX_MESSAGE_CHARS] + f"… [truncated, {len(content)} chars total]"
    return {"role": msg.get("role"), "content": content, "timestamp": msg.get("timestamp")}


# ── Tool implementations ───────────────────────────────────────────────────


async def tool_list_tasks(request, args):
    params = {}
    q = (args.get("q") or "").strip()
    if q:
        params["q"] = q
    data = await _backend(request, "GET", "/api/tasks", params=params)
    column = (args.get("column") or "").strip()
    if column:
        data["tasks"] = [t for t in data.get("tasks", []) if t.get("column") == column]
    return data


async def tool_get_task(request, args):
    cid = (args.get("conversation_id") or "").strip()
    if not cid:
        raise ToolError("conversation_id is required")
    data = await _backend(request, "GET", f"/api/conversations/{cid}")
    conv = data.get("conversation") or {}
    messages = conv.get("messages") or []
    return {
        "conversation_id": conv.get("conversation_id"),
        "title": conv.get("title"),
        "prompt": conv.get("prompt"),
        "status": conv.get("status"),
        "task_status": conv.get("task_status"),
        "task_lane": conv.get("task_lane"),
        "task_priority": conv.get("task_priority"),
        "task_deadline": conv.get("task_deadline"),
        "task_tag_ids": conv.get("task_tag_ids") or [],
        "model": conv.get("model"),
        "total_turns": conv.get("total_turns"),
        "started_at": conv.get("started_at"),
        "finished_at": conv.get("finished_at"),
        "error": conv.get("error"),
        "final_response": conv.get("final_response"),
        "message_count": len(messages),
        "last_messages": [_truncate_message(m) for m in messages[-MAX_MESSAGES:]],
    }


async def tool_create_task(request, args):
    body = {
        "prompt": (args.get("prompt") or "").strip(),
        "title": (args.get("title") or "").strip(),
        "start": bool(args.get("start")),
    }
    if args.get("lane"):
        body["lane"] = args["lane"]
    data = await _backend(request, "POST", "/api/tasks", json_body=body)
    task = data.get("task") or {}

    # Priority/deadline aren't part of the create payload — apply via PATCH.
    patch = {}
    if args.get("priority"):
        patch["task_priority"] = args["priority"]
    if args.get("deadline"):
        patch["task_deadline"] = args["deadline"]
    if patch and task.get("conversation_id"):
        data = await _backend(
            request, "PATCH", f"/api/tasks/{task['conversation_id']}", json_body=patch)
        task = data.get("task") or task
    return {"task": task}


async def tool_update_task(request, args):
    cid = (args.get("conversation_id") or "").strip()
    if not cid:
        raise ToolError("conversation_id is required")
    patch = {}
    if "task_status" in args:
        patch["task_status"] = args["task_status"]
    if "lane" in args:
        patch["task_lane"] = args["lane"]
    if "title" in args:
        patch["title"] = args["title"]
    if "prompt" in args:
        patch["prompt"] = args["prompt"]
    if "priority" in args:
        patch["task_priority"] = args["priority"] or None
    if "deadline" in args:
        patch["task_deadline"] = args["deadline"] or None
    if not patch:
        raise ToolError("No fields to update — pass at least one of task_status, lane, title, prompt, priority, deadline")
    data = await _backend(request, "PATCH", f"/api/tasks/{cid}", json_body=patch)
    return {"task": data.get("task")}


TOOL_HANDLERS = {
    "list_tasks": tool_list_tasks,
    "get_task": tool_get_task,
    "create_task": tool_create_task,
    "update_task": tool_update_task,
}

# ── Auth ───────────────────────────────────────────────────────────────────


async def _authenticate(request: web.Request) -> str | None:
    """Resolve the bearer API key to a user email, or None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    key = auth[len("Bearer "):].strip()
    if not key.startswith("loma_sk_"):
        return None
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    db = request.app["db"]
    doc = await db.api_keys.find_one({"key_hash": key_hash, "revoked": {"$ne": True}})
    if not doc:
        return None
    # Best-effort usage stamp — never block the request on it.
    asyncio.create_task(
        db.api_keys.update_one(
            {"key_id": doc["key_id"]},
            {"$set": {"last_used_at": datetime.now(timezone.utc)}},
        )
    )
    return doc["user_email"]


def _unauthorized():
    return web.json_response(
        {"error": "Unauthorized — pass a valid Loma API key as 'Authorization: Bearer loma_sk_…'. "
                  "Keys are minted in the Loma dashboard under Settings → API Keys."},
        status=401,
        headers={"WWW-Authenticate": 'Bearer realm="loma-tasks-mcp"'},
    )

# ── JSON-RPC / MCP plumbing (stateless streamable HTTP, JSON mode) ─────────


def _rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def _dispatch(request: web.Request, msg: dict):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _rpc_result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Tools operate on the authenticated user's Loma task board. "
                "Call list_tasks first to discover lane ids and existing tasks."
            ),
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _rpc_error(msg_id, -32602, f"Unknown tool: {name}")
        try:
            result = await handler(request, params.get("arguments") or {})
            return _rpc_result(msg_id, {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "structuredContent": result,
                "isError": False,
            })
        except ToolError as e:
            return _rpc_result(msg_id, {
                "content": [{"type": "text", "text": str(e)}],
                "isError": True,
            })
        except Exception:
            logger.exception("Tool %s crashed", name)
            return _rpc_result(msg_id, {
                "content": [{"type": "text", "text": "Internal error while running the tool."}],
                "isError": True,
            })
    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


async def handle_mcp(request: web.Request) -> web.Response:
    user_email = await _authenticate(request)
    if not user_email:
        return _unauthorized()
    request["user_email"] = user_email

    try:
        msg = await request.json()
    except Exception:
        return web.json_response(_rpc_error(None, -32700, "Parse error"), status=400)
    if not isinstance(msg, dict):
        # JSON-RPC batching was removed in the 2025-06-18 MCP revision.
        return web.json_response(
            _rpc_error(None, -32600, "Batch requests are not supported"), status=400)

    # Notifications and client responses get no reply body (202 Accepted).
    if "id" not in msg or msg.get("method", "").startswith("notifications/"):
        return web.Response(status=202)

    response = await _dispatch(request, msg)
    return web.json_response(response)


async def handle_mcp_get(request: web.Request) -> web.Response:
    # Stateless JSON mode: no server-initiated SSE stream.
    return web.json_response({"error": "Method not allowed"}, status=405,
                             headers={"Allow": "POST"})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "server": SERVER_INFO["name"]})


# ── App wiring ─────────────────────────────────────────────────────────────


async def create_app() -> web.Application:
    uri = os.environ.get("OBSERVABILITY_MONGODB_URI", "").strip()
    if not uri.startswith("mongodb"):
        raise SystemExit("OBSERVABILITY_MONGODB_URI is required")

    app = web.Application()
    app["db"] = AsyncIOMotorClient(uri)[DB_NAME]
    app["http"] = ClientSession()

    async def _close_http(app):
        await app["http"].close()
    app.on_cleanup.append(_close_http)

    # /mcp/tasks is the nginx-routed path; / supports direct/local use.
    for path in ("/", "/mcp/tasks"):
        app.router.add_post(path, handle_mcp)
        app.router.add_get(path, handle_mcp_get)
    app.router.add_get("/health", handle_health)
    return app


if __name__ == "__main__":
    logger.info("loma-tasks MCP server starting on :%s (backend: %s, db: %s)",
                MCP_PORT, BACKEND_URL, DB_NAME)
    web.run_app(create_app(), port=MCP_PORT, print=None)
