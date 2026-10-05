"""Private stars: bookmark any task on a shared board into your own board.

Starring a task makes it also show on the starrer's personal board ("My
tasks"). There it has its own lane, order and done state, kept in the
`task_stars` collection, one doc per (person, task):

  {user_email, conversation_id, lane, done, rank, starred_at, done_at}

Nothing is written on the task itself, so nobody else can tell a task was
starred, and moving or ticking off the starred card never changes the shared
board. It is only for organizing your own work: the card still opens the real
chat, and who can message it follows the task's board as usual.

Anyone who can see a task can star it, viewers included. A star is dropped
when its task is deleted, leaves its shared board, or the person loses access
to that board.
"""

from datetime import datetime, timezone

from aiohttp import web

from api import task_routes

MAX_STARS_PER_USER = 500


def _lane_or_first(lane, lane_ids: list[str]) -> str:
    return lane if lane in lane_ids else (lane_ids[0] if lane_ids else "todo")


async def flag_starred(db, user_email: str, views: list[dict]) -> None:
    """Mark the caller's own stars on a shared board's task views."""
    ids = [view["conversation_id"] for view in views]
    if not ids:
        return
    rows = await db.task_stars.find(
        {"user_email": user_email, "conversation_id": {"$in": ids}},
        {"conversation_id": 1},
    ).to_list(len(ids))
    starred = {row["conversation_id"] for row in rows}
    for view in views:
        view["starred"] = view["conversation_id"] in starred


async def starred_views(db, user_email: str, lane_ids: list[str],
                        search_or: list | None = None) -> list[dict]:
    """The caller's starred tasks, shaped as cards for their personal board.

    Each view is the real task with the caller's private placement on top:
    `column` / `task_lane` / `task_rank` come from the star, and `star`
    carries where the task really is (board, card, real column) plus the
    caller's role there. Stars whose task is gone or out of reach are dropped.
    """
    rows = await db.task_stars.find({"user_email": user_email}).to_list(MAX_STARS_PER_USER)
    if not rows:
        return []
    by_id = {row["conversation_id"]: row for row in rows}
    query: dict = {
        "conversation_id": {"$in": list(by_id)},
        "task_board_id": {"$nin": [None, ""]},
        "task_status": {"$in": ["todo", "active", "done"]},
        "deleted": {"$ne": True},
    }
    if search_or:
        query["$or"] = search_or
    tasks = await db.conversations.find(query, task_routes._TASK_PROJECTION).to_list(len(by_id))

    board_ids = list({task["task_board_id"] for task in tasks})
    board_docs = await db.task_boards.find({"board_id": {"$in": board_ids}}).to_list(len(board_ids)) if board_ids else []
    boards = {doc["board_id"]: doc for doc in board_docs}
    card_ids = list({task["task_card_id"] for task in tasks if task.get("task_card_id")})
    card_docs = await db.task_cards.find(
        {"card_id": {"$in": card_ids}}, {"card_id": 1, "title": 1},
    ).to_list(len(card_ids)) if card_ids else []
    card_titles = {doc["card_id"]: doc.get("title") or "Untitled" for doc in card_docs}

    views: list[dict] = []
    kept: set[str] = set()
    for task in tasks:
        board_doc = boards.get(task["task_board_id"])
        role = task_routes._board_role(board_doc, user_email) if board_doc else None
        if not role:
            continue
        kept.add(task["conversation_id"])
        star = by_id[task["conversation_id"]]
        source_lanes = [lane["id"] for lane in task_routes.get_board_config(board_doc)["lanes"]]
        view = task_routes._task_view(task, source_lanes)
        done = bool(star.get("done"))
        lane = _lane_or_first(star.get("lane"), lane_ids)
        view.update({
            "starred": True,
            "star": {
                "lane": lane,
                "done": done,
                "role": role,
                "board_id": board_doc["board_id"],
                "board_name": board_doc.get("name") or "Untitled board",
                "board_emoji": board_doc.get("emoji") or task_routes._default_board_emoji(board_doc["board_id"]),
                "card_title": card_titles.get(task.get("task_card_id")),
                # Where the task really is on its board.
                "source_column": view["column"],
            },
            "column": "done" if done else lane,
            "task_lane": lane,
            "task_rank": star.get("rank") if star.get("rank") is not None else 0.0,
            # The source board's tags mean nothing on the personal board.
            "task_tag_ids": [],
        })
        views.append(view)

    # Forget stars whose task is gone or out of reach. Skipped while a search
    # narrows the match, since a non-matching task is not a missing one.
    stale = [cid for cid in by_id if cid not in kept]
    if stale and not search_or:
        await db.task_stars.delete_many({"user_email": user_email, "conversation_id": {"$in": stale}})
    return views


async def _star_context(request: web.Request):
    """(db, user_email, conversation) for a star route, or an error response."""
    db = task_routes.get_db()
    if db is None:
        return None, None, None, web.json_response({"error": "Observability not configured"}, status=503)
    user_email = task_routes.get_user_email(request)
    if not user_email:
        return None, None, None, web.json_response({"error": "Authentication required"}, status=401)
    cid = request.match_info["conversation_id"]
    conversation = await db.conversations.find_one({"conversation_id": cid, "deleted": {"$ne": True}})
    if not conversation:
        return None, None, None, web.json_response({"error": "Not found"}, status=404)
    # Board membership is what counts: a star only makes sense for a task on
    # a shared board the caller can see.
    board_id = conversation.get("task_board_id")
    board = await task_routes.resolve_board(db, user_email, board_id) if board_id else None
    if not conversation.get("task_status") or (board_id and not board):
        return None, None, None, web.json_response({"error": "Not found"}, status=404)
    if not board:
        return None, None, None, web.json_response(
            {"error": "Only tasks on shared boards can be starred"}, status=400)
    return db, user_email, conversation, None


async def handle_star_task(request: web.Request) -> web.Response:
    """PUT /api/tasks/{conversation_id}/star — star a shared-board task."""
    db, user_email, conversation, error = await _star_context(request)
    if error:
        return error
    cid = conversation["conversation_id"]
    key = {"user_email": user_email, "conversation_id": cid}
    if not await db.task_stars.find_one(key, {"_id": 1}):
        if await db.task_stars.count_documents({"user_email": user_email}) >= MAX_STARS_PER_USER:
            return web.json_response(
                {"error": f"You can star at most {MAX_STARS_PER_USER} tasks"}, status=400)
        now = datetime.now(timezone.utc)
        lanes = (await task_routes._get_board_config_for(db, user_email))["lanes"]
        await db.task_stars.update_one(key, {"$setOnInsert": {
            **key, "lane": lanes[0]["id"], "done": False,
            # Newest first, same scale as task ranks.
            "rank": -now.timestamp(), "starred_at": now, "done_at": None,
        }}, upsert=True)
    return web.json_response({"starred": True, "conversation_id": cid})


async def handle_unstar_task(request: web.Request) -> web.Response:
    """DELETE /api/tasks/{conversation_id}/star — remove your star.

    Works even when the task is gone or out of reach, so a stale star can
    always be cleared.
    """
    db = task_routes.get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)
    user_email = task_routes.get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)
    cid = request.match_info["conversation_id"]
    await db.task_stars.delete_one({"user_email": user_email, "conversation_id": cid})
    return web.json_response({"starred": False, "conversation_id": cid})


async def handle_update_star(request: web.Request) -> web.Response:
    """PATCH /api/tasks/{conversation_id}/star — place a starred task on your
    own board. Accepts any of: lane (one of your lanes), done, rank. Never
    touches the task itself.
    """
    db, user_email, conversation, error = await _star_context(request)
    if error:
        return error
    cid = conversation["conversation_id"]
    key = {"user_email": user_email, "conversation_id": cid}
    star = await db.task_stars.find_one(key)
    if not star:
        return web.json_response({"error": "Star the task first"}, status=404)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    updates: dict = {}
    if "lane" in body:
        lanes = (await task_routes._get_board_config_for(db, user_email))["lanes"]
        if body["lane"] not in [lane["id"] for lane in lanes]:
            return web.json_response({"error": "Unknown lane"}, status=400)
        updates["lane"] = body["lane"]
    if "done" in body:
        if not isinstance(body["done"], bool):
            return web.json_response({"error": "done must be true or false"}, status=400)
        updates["done"] = body["done"]
        if body["done"] != bool(star.get("done")):
            updates["done_at"] = datetime.now(timezone.utc) if body["done"] else None
    if "rank" in body:
        try:
            updates["rank"] = float(body["rank"])
        except (TypeError, ValueError):
            return web.json_response({"error": "rank must be a number"}, status=400)
    if not updates:
        return web.json_response({"error": "Nothing to update"}, status=400)
    await db.task_stars.update_one(key, {"$set": updates})
    merged = {**star, **updates}
    return web.json_response({"star": {
        "lane": merged.get("lane"), "done": bool(merged.get("done")), "rank": merged.get("rank"),
    }})


def setup_star_routes(app: web.Application):
    app.router.add_put("/api/tasks/{conversation_id}/star", handle_star_task)
    app.router.add_patch("/api/tasks/{conversation_id}/star", handle_update_star)
    app.router.add_delete("/api/tasks/{conversation_id}/star", handle_unstar_task)
