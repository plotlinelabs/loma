"""Saved views on card boards: a named set of card filters.

A view stores the filters, their match mode ("all" / "any"), the search text
and the "Assigned to me" toggle, so a board opens filtered in one click.
Views live in the `task_board_views` collection, one doc per view:

  {view_id, board_id, owner, name, shared, match, filters, search,
   assigned_to_me, created_at, updated_at}

A view is personal (only its creator sees it) unless `shared`, in which case
everyone on the board sees it. Sharing needs edit access to the board. The
creator can change or delete a view; board owners can also manage shared ones.
Filters are checked for shape only: they reference field ids, and a filter on
a field that was later deleted is simply ignored by the dashboard.
"""

import uuid
from datetime import datetime, timezone

from aiohttp import web

from api import task_routes

MAX_VIEW_NAME_LEN = 60
MAX_VIEWS_PER_BOARD = 30  # per person, per board
MAX_VIEW_FILTERS = 20
MAX_VIEW_SEARCH_LEN = 200
MAX_FILTER_KEY_LEN = 40
MAX_FILTER_VALUES = 50
MAX_FILTER_TEXT_LEN = 500
VIEW_MATCH_MODES = ("all", "any")
VIEW_FILTER_OPS = (
    "any_of", "none_of",          # select, multi-select, person, stage, assignee
    "eq", "gt", "lt", "between",  # number (and date "between")
    "before", "after", "on",      # date
    "contains", "not_contains",   # text, link
    "checked", "not_checked",     # checkbox
    "empty", "not_empty",         # any field
)

_VIEW_PROJECTION = {"_id": 0}


class ViewError(ValueError):
    pass


def _clean_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_FILTER_TEXT_LEN:
            raise ViewError(f"Filter values must be at most {MAX_FILTER_TEXT_LEN} characters")
        return value
    raise ViewError("Invalid filter value")


def clean_filters(filters_in) -> list[dict]:
    if filters_in is None:
        return []
    if not isinstance(filters_in, list) or len(filters_in) > MAX_VIEW_FILTERS:
        raise ViewError(f"filters must be a list of at most {MAX_VIEW_FILTERS} filters")
    filters = []
    for item in filters_in:
        if not isinstance(item, dict):
            raise ViewError("Invalid filter")
        field = item.get("field")
        if not isinstance(field, str) or not field or len(field) > MAX_FILTER_KEY_LEN:
            raise ViewError("Each filter needs a field")
        op = item.get("op")
        if op not in VIEW_FILTER_OPS:
            raise ViewError(f"Unknown filter condition: {op}")
        value = item.get("value")
        if isinstance(value, list):
            if len(value) > MAX_FILTER_VALUES:
                raise ViewError(f"A filter can hold at most {MAX_FILTER_VALUES} values")
            value = [_clean_scalar(v) for v in value]
        else:
            value = _clean_scalar(value)
        filter_id = item.get("id")
        if not isinstance(filter_id, str) or not filter_id or len(filter_id) > MAX_FILTER_KEY_LEN:
            filter_id = uuid.uuid4().hex[:8]
        filters.append({"id": filter_id, "field": field, "op": op, "value": value})
    return filters


def clean_view_body(body: dict, partial: bool) -> dict:
    """Validated view fields from a request body (only the given keys when partial)."""
    out: dict = {}
    if not partial or "name" in body:
        name = body.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > MAX_VIEW_NAME_LEN:
            raise ViewError(f"View names must be 1-{MAX_VIEW_NAME_LEN} characters")
        out["name"] = name
    if not partial or "filters" in body:
        out["filters"] = clean_filters(body.get("filters"))
    if not partial or "match" in body:
        match = body.get("match") or "all"
        if match not in VIEW_MATCH_MODES:
            raise ViewError("match must be all or any")
        out["match"] = match
    if not partial or "search" in body:
        search = body.get("search") or ""
        if not isinstance(search, str) or len(search) > MAX_VIEW_SEARCH_LEN:
            raise ViewError(f"search must be at most {MAX_VIEW_SEARCH_LEN} characters")
        out["search"] = search
    for key in ("assigned_to_me", "shared"):
        if not partial or key in body:
            value = body.get(key, False)
            if not isinstance(value, bool):
                raise ViewError(f"{key} must be a boolean")
            out[key] = value
    return out


def _view_out(doc: dict, user_email: str, board_role: str | None) -> dict:
    mine = doc.get("owner") == user_email
    shared = bool(doc.get("shared"))
    return {
        "id": doc["view_id"],
        "board_id": doc["board_id"],
        "name": doc.get("name") or "Untitled view",
        "owner": doc.get("owner"),
        "shared": shared,
        "match": doc.get("match") or "all",
        "filters": doc.get("filters") or [],
        "search": doc.get("search") or "",
        "assigned_to_me": bool(doc.get("assigned_to_me")),
        "mine": mine,
        "can_edit": mine or (shared and board_role == "owner"),
    }


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _card_board(request: web.Request):
    """(db, user_email, board, None) for the ?board= card board, or (..., error response)."""
    db = task_routes.get_db()
    if db is None:
        return None, None, None, _error("Observability not configured", 503)
    user_email = task_routes.get_user_email(request)
    if not user_email:
        return None, None, None, _error("Authentication required", 401)
    board = await task_routes.resolve_board(db, user_email, request.query.get("board"))
    if board is None:
        return None, None, None, _error("Board not found", 404)
    if not board["card_mode"]:
        return None, None, None, _error("Only card boards have views", 400)
    return db, user_email, board, None


async def _json_body(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


async def handle_list_views(request: web.Request) -> web.Response:
    """GET /api/tasks/views?board= — your views on a card board plus the shared ones."""
    db, user_email, board, error = await _card_board(request)
    if error:
        return error
    docs = await db.task_board_views.find(
        {"board_id": board["id"], "$or": [{"owner": user_email}, {"shared": True}]},
        _VIEW_PROJECTION,
    ).sort("created_at", 1).to_list(200)
    return web.json_response({"views": [_view_out(d, user_email, board["role"]) for d in docs]})


async def handle_create_view(request: web.Request) -> web.Response:
    """POST /api/tasks/views?board= — save the current filters as a new view."""
    db, user_email, board, error = await _card_board(request)
    if error:
        return error
    body = await _json_body(request)
    if body is None:
        return _error("Expected a JSON object", 400)
    try:
        fields = clean_view_body(body, partial=False)
    except ViewError as e:
        return _error(str(e), 400)
    if fields["shared"] and board["role"] not in task_routes.EDIT_ROLES:
        return _error("View-only members can only save personal views", 403)
    owned = await db.task_board_views.count_documents({"board_id": board["id"], "owner": user_email})
    if owned >= MAX_VIEWS_PER_BOARD:
        return _error(f"You can have at most {MAX_VIEWS_PER_BOARD} views on a board", 400)
    now = datetime.now(timezone.utc)
    doc = {"view_id": uuid.uuid4().hex[:12], "board_id": board["id"], "owner": user_email,
           **fields, "created_at": now, "updated_at": now}
    await db.task_board_views.insert_one(dict(doc))
    return web.json_response({"view": _view_out(doc, user_email, board["role"])}, status=201)


async def _editable_view(request: web.Request):
    """(db, user_email, board, view_doc, None) for a view the caller may change, or an error."""
    db, user_email, board, error = await _card_board(request)
    if error:
        return None, None, None, None, error
    doc = await db.task_board_views.find_one(
        {"view_id": request.match_info["view_id"], "board_id": board["id"]}, _VIEW_PROJECTION)
    if not doc or not (doc.get("owner") == user_email or doc.get("shared")):
        return None, None, None, None, _error("View not found", 404)
    if not _view_out(doc, user_email, board["role"])["can_edit"]:
        return None, None, None, None, _error(
            "Only the view's creator or a board owner can change it", 403)
    return db, user_email, board, doc, None


async def handle_update_view(request: web.Request) -> web.Response:
    """PATCH /api/tasks/views/{view_id}?board= — rename, re-filter or (un)share a view."""
    db, user_email, board, doc, error = await _editable_view(request)
    if error:
        return error
    body = await _json_body(request)
    if body is None:
        return _error("Expected a JSON object", 400)
    try:
        updates = clean_view_body(body, partial=True)
    except ViewError as e:
        return _error(str(e), 400)
    if not updates:
        return _error("Nothing to update", 400)
    if updates.get("shared") and board["role"] not in task_routes.EDIT_ROLES:
        return _error("View-only members can only save personal views", 403)
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.task_board_views.update_one({"view_id": doc["view_id"]}, {"$set": updates})
    return web.json_response({"view": _view_out({**doc, **updates}, user_email, board["role"])})


async def handle_delete_view(request: web.Request) -> web.Response:
    """DELETE /api/tasks/views/{view_id}?board= — delete a view."""
    db, _user_email, _board, doc, error = await _editable_view(request)
    if error:
        return error
    await db.task_board_views.delete_one({"view_id": doc["view_id"]})
    return web.json_response({"deleted": True})


def setup_view_routes(app: web.Application):
    app.router.add_get("/api/tasks/views", handle_list_views)
    app.router.add_post("/api/tasks/views", handle_create_view)
    app.router.add_patch("/api/tasks/views/{view_id}", handle_update_view)
    app.router.add_delete("/api/tasks/views/{view_id}", handle_delete_view)
