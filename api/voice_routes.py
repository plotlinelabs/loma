"""Voice mode routes — a conversational voice layer over the tasks board.

The dashboard's /voice page records an utterance (existing /api/transcribe
turns it into text) and posts the text here. We answer with a short spoken
reply plus an optional board action that we execute server-side.

Design notes:
  - The endpoint is stateless: the client sends the recent voice-session
    history with every request, so nothing new is persisted for a session.
  - One LLM call per utterance (same `claude -p` CLI pattern as
    _generate_title_llm in api/routes.py): the model sees a compact snapshot
    of the caller's board and must reply with strict JSON —
    {"speech": "...", "action": {"type": ...}}.
  - Actions reuse the tasks-board semantics from api/task_routes.py
    (create drafts / quick-start, follow-up input, done/park/priority/
    deadline). Task references are the first 8 chars of the conversation id
    so the model copies ids verbatim instead of inventing them.
  - Risky operations are deliberately not exposed: no deletes, no board
    removal, no access to other users' tasks.
"""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from aiohttp import web

from observability.db import get_db
from api.auth_helpers import get_user_email
from api.task_routes import (
    TASK_PRIORITIES,
    DEADLINE_RE,
    _auto_title_task,
    _get_board_config_for,
    _run_task_headless,
    derive_column,
)

logger = logging.getLogger(__name__)

VOICE_LLM_TIMEOUT_S = 45
MAX_UTTERANCE_LEN = 4000
MAX_HISTORY_TURNS = 24
MAX_SNAPSHOT_TASKS = 60
# Recently-done window shown to the model ("what finished today?").
DONE_LOOKBACK_HOURS = 72
# Follow-up context sent to a resumed task: last N stored messages.
FOLLOWUP_CONTEXT_MESSAGES = 12
FOLLOWUP_CONTEXT_CHARS = 1500

VOICE_ACTIONS = (
    "none", "create_task", "start_task", "add_input",
    "mark_done", "park_task", "set_priority", "set_deadline",
)

_SNAPSHOT_PROJECTION = {
    "conversation_id": 1, "title": 1, "prompt": 1, "status": 1,
    "task_status": 1, "task_lane": 1, "task_priority": 1, "task_deadline": 1,
    "task_created_at": 1, "task_started_at": 1, "task_done_at": 1,
    "finished_at": 1, "final_response": 1, "total_turns": 1,
}


def _fmt_dt(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return ""


async def _board_snapshot(db, user_email: str) -> tuple[list[dict], dict[str, str]]:
    """Compact task list for the LLM + short-ref -> conversation_id map."""
    board = await _get_board_config_for(db, user_email)
    lane_names = {lane["id"]: lane["name"] for lane in board["lanes"]}
    lane_ids = list(lane_names)

    done_cutoff = datetime.now(timezone.utc) - timedelta(hours=DONE_LOOKBACK_HOURS)
    tasks = await db.conversations.find(
        {
            "metadata.user_name": user_email,
            "deleted": {"$ne": True},
            "$or": [
                {"task_status": {"$in": ["todo", "active"]}},
                {"task_status": "done", "task_done_at": {"$gte": done_cutoff}},
            ],
        },
        _SNAPSHOT_PROJECTION,
    ).sort("task_created_at", 1).to_list(MAX_SNAPSHOT_TASKS)

    refs: dict[str, str] = {}
    snapshot: list[dict] = []
    for task in tasks:
        cid = task.get("conversation_id") or ""
        ref = cid[:8]
        if ref in refs:  # extremely unlikely prefix collision — use full id
            ref = cid
        refs[ref] = cid
        column = derive_column(task, lane_ids)
        entry = {
            "ref": ref,
            "title": task.get("title") or (task.get("prompt") or "")[:80] or "Untitled",
            "state": column if column not in lane_names else f"staged ({lane_names[column]})",
            "priority": task.get("task_priority"),
            "deadline": task.get("task_deadline"),
            "created": _fmt_dt(task.get("task_created_at")),
            "done_at": _fmt_dt(task.get("task_done_at")),
            "last_activity": _fmt_dt(task.get("finished_at")),
        }
        latest = (task.get("final_response") or "").strip()
        if latest:
            entry["latest_update"] = re.sub(r"\s+", " ", latest)[:220]
        snapshot.append(entry)
    return snapshot, refs


def _llm_prompt(snapshot: list[dict], lanes: list[dict], history: list[dict],
                utterance: str) -> str:
    now = datetime.now(timezone.utc)
    lane_list = ", ".join(f"{lane['id']} ({lane['name']})" for lane in lanes)
    history_text = "\n".join(
        f"{'User' if m.get('role') == 'user' else 'Assistant'}: {m.get('content', '')}"
        for m in history
    ) or "(none)"
    board_text = "\n".join(json.dumps(t, ensure_ascii=False) for t in snapshot) or "(no tasks)"
    return f"""You are Loma's voice assistant for a personal task board. The user is speaking hands-free (often driving), so replies must be SHORT, natural spoken sentences — no markdown, no lists, no ids read aloud.

Current time: {now.strftime("%Y-%m-%d %H:%M UTC")}
Staging lanes: {lane_list}

The user's tasks (one JSON object per line; "state" is one of: staged (<lane>) = not started or parked, working = agent currently running, needs_input = agent finished and waits on the user, done = completed):
{board_text}

Recent voice conversation:
{history_text}

User's new utterance: {utterance}

Reply with ONLY a JSON object, no other text:
{{"speech": "<what to say out loud, 1-3 short sentences>", "action": {{"type": "<one of: none, create_task, start_task, add_input, mark_done, park_task, set_priority, set_deadline>", ...}}}}

Action payloads (include only the fields for the chosen type):
- none: answer questions about tasks (status, what finished, summaries) using the snapshot above. No extra fields.
- create_task: {{"prompt": "<full task instructions>", "title": "<short title>", "start": true|false}} — start=true runs it immediately, false stages a draft. Before creating, check the snapshot for an existing similar task; if one plausibly matches, ask instead of creating a duplicate (type none).
- start_task: {{"ref": "<ref>"}} — run a staged draft now.
- add_input: {{"ref": "<ref>", "input": "<the extra instructions>"}} — send follow-up input to an existing task. Not allowed while the task is "working".
- mark_done: {{"ref": "<ref>"}}
- park_task: {{"ref": "<ref>", "lane": "<lane id>"}} — shelve an active task back to a staging lane (lane optional).
- set_priority: {{"ref": "<ref>", "priority": "low|medium|high|urgent|null"}}
- set_deadline: {{"ref": "<ref>", "deadline": "YYYY-MM-DD or null"}}

Rules:
- "ref" MUST be copied exactly from the snapshot. Never invent refs.
- If the user's reference to a task is ambiguous (several plausible matches), use type none and ask which one they mean, naming the candidate titles.
- If the user asks for something outside these actions, use type none and say briefly what you can do.
- Phrase speech assuming the action succeeds (e.g. "Done — I've started that task.")."""


async def _call_voice_llm(prompt: str, model: str) -> dict | None:
    """Run the utterance through the app's configured OpenCode runtime."""
    try:
        from agent.opencode_runtime import (
            PROJECT_ROOT,
            _create_session,
            _request_json,
            _split_model,
        )
        provider_id, model_id = _split_model(model)
        session_id = await _create_session("Loma voice command")
        response = await _request_json(
            "POST",
            f"/session/{session_id}/message",
            json_body={
                "model": {"providerID": provider_id, "modelID": model_id},
                "system": "Return only the JSON object requested by the user prompt.",
                "parts": [{"type": "text", "text": prompt}],
            },
            params={"directory": str(PROJECT_ROOT)},
            timeout=VOICE_LLM_TIMEOUT_S,
        )
        raw = "".join(
            part.get("text", "")
            for part in response.get("parts", [])
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
        # The model may wrap the JSON in code fences or prose — take the
        # outermost object.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception as e:
        logger.warning("[VOICE] LLM call failed: %s", e)
        return None


async def _run_followup_headless(db, conversation: dict, input_text: str,
                                 owner: str):
    """Send follow-up input to an existing task and run it in the background.

    Mirrors handle_chat's resume path (observer resume, board prompt, stored
    message history as context) without an SSE client watching the stream.
    """
    try:
        from agent.client import stream_agent
        from observability.observer import ConversationObserver

        cid = conversation["conversation_id"]
        owner_doc = await db.users.find_one({"email": owner}, {"task_board": 1})
        board_prompt = ((owner_doc or {}).get("task_board") or {}).get("prompt", "").strip()

        context_parts: list[str] = []
        if board_prompt:
            context_parts.append(
                "## User's role & working context (apply to this task)\n"
                f"{board_prompt}"
            )
        for msg in (conversation.get("messages") or [])[-FOLLOWUP_CONTEXT_MESSAGES:]:
            role = "User" if msg.get("role") == "user" else "Assistant"
            content = str(msg.get("content") or "")[:FOLLOWUP_CONTEXT_CHARS]
            if content:
                context_parts.append(f"{role}: {content}")

        observer = ConversationObserver(
            db,
            metadata={
                "source": "dashboard",
                "prompt": input_text,
                "model": conversation.get("model")
                or os.environ.get("AGENT_DEFAULT_MODEL", ""),
                "user_name": owner,
            },
            conversation_id=cid,
        )
        await observer.resume()

        async for _ in stream_agent(
            prompt=input_text,
            conversation_context="\n\n".join(context_parts),
            observer=observer,
            include_steps=True,
            source="dashboard",
            user_email=owner,
            selected_model=conversation.get("model") or None,
        ):
            pass  # observer records; the voice client isn't watching
    except Exception as e:
        logger.warning("[VOICE] follow-up run failed for %s: %s",
                       conversation.get("conversation_id"), e)


async def _execute_action(db, user_email: str, action: dict,
                          refs: dict[str, str]) -> tuple[bool, str | None]:
    """Apply the LLM's action. Returns (ok, error_speech_override)."""
    action_type = action.get("type") or "none"
    if action_type == "none":
        return True, None

    now = datetime.now(timezone.utc)

    if action_type == "create_task":
        prompt = (action.get("prompt") or "").strip()
        title = (action.get("title") or "").strip() or None
        if not prompt:
            return False, "I didn't catch enough detail to create that task. Could you say it again?"
        start = bool(action.get("start"))
        board = await _get_board_config_for(db, user_email)
        lane = board["lanes"][0]["id"]
        doc = {
            "conversation_id": str(uuid.uuid4()),
            "source": "dashboard",
            "started_at": now if start else None,
            "finished_at": None,
            "duration_ms": None,
            "status": None,
            "metadata": {"user_name": user_email, "created_via": "voice"},
            "prompt": prompt,
            "model": "",
            "total_turns": 0,
            "final_response": "",
            "messages": [],
            "confidence": None,
            "cost": None,
            "savings": None,
            "claude_account": None,
            "error": None,
            "deleted": False,
            "title": title,
            "title_edited": bool(title),
            "task_status": "active" if start else "todo",
            "task_lane": lane,
            "task_rank": -now.timestamp(),
            "task_created_at": now,
            "task_staged_at": now,
            "task_started_at": now if start else None,
            "task_done_at": None,
            "task_tag_ids": [],
            "task_priority": None,
            "task_deadline": None,
        }
        await db.conversations.insert_one(doc)
        if not title:
            asyncio.create_task(_auto_title_task(db, doc["conversation_id"], prompt))
        if start:
            asyncio.create_task(_run_task_headless(
                db, doc["conversation_id"], prompt, "", [], user_email))
        return True, None

    # Everything below needs a valid task reference owned by the caller.
    ref = action.get("ref") or ""
    cid = refs.get(ref)
    if not cid:
        return False, "I couldn't match that to one of your tasks. Could you say which task you mean?"
    conversation = await db.conversations.find_one({
        "conversation_id": cid,
        "metadata.user_name": user_email,
        "deleted": {"$ne": True},
    })
    if not conversation:
        return False, "I couldn't find that task anymore."

    status = conversation.get("status")
    task_status = conversation.get("task_status")

    if action_type == "start_task":
        prompt = (conversation.get("prompt") or "").strip()
        if task_status != "todo" or status is not None:
            return False, "That task has already been started."
        if not prompt:
            return False, "That draft has no details yet, so I can't start it. Tell me what it should do."
        await db.conversations.update_one(
            {"conversation_id": cid},
            {"$set": {
                "task_status": "active",
                "started_at": now,
                "task_started_at": now,
            }, "$unset": {"draft_files": ""}},
        )
        asyncio.create_task(_run_task_headless(
            db, cid, prompt, conversation.get("model") or "", [], user_email))
        return True, None

    if action_type == "add_input":
        input_text = (action.get("input") or "").strip()
        if not input_text:
            return False, "I didn't catch what you wanted to add. Could you repeat that?"
        if status == "running":
            return False, "That task is still running. I'll hold off — ask me again once it finishes."
        if status is None:
            # Never-run draft: start it with the combined instructions.
            base_prompt = (conversation.get("prompt") or "").strip()
            combined = f"{base_prompt}\n\n{input_text}" if base_prompt else input_text
            await db.conversations.update_one(
                {"conversation_id": cid},
                {"$set": {
                    "task_status": "active",
                    "started_at": now,
                    "task_started_at": now,
                    "prompt": combined,
                }, "$unset": {"draft_files": ""}},
            )
            asyncio.create_task(_run_task_headless(
                db, cid, combined, conversation.get("model") or "", [], user_email))
            return True, None
        if task_status in ("todo", "done"):
            # Parked or completed task getting new input flips back to active
            # (same as sending a message from the chat view).
            await db.conversations.update_one(
                {"conversation_id": cid},
                {"$set": {"task_status": "active", "task_done_at": None}},
            )
        asyncio.create_task(_run_followup_headless(
            db, conversation, input_text, user_email))
        return True, None

    if action_type == "mark_done":
        if status is None and task_status == "todo":
            return False, "That task hasn't started yet — I can only mark started tasks as done."
        await db.conversations.update_one(
            {"conversation_id": cid},
            {"$set": {"task_status": "done", "task_done_at": now}},
        )
        return True, None

    if action_type == "park_task":
        board = await _get_board_config_for(db, user_email)
        lane_ids = [lane["id"] for lane in board["lanes"]]
        lane = action.get("lane") or conversation.get("task_lane") or lane_ids[0]
        if lane not in lane_ids:
            lane = lane_ids[0]
        await db.conversations.update_one(
            {"conversation_id": cid},
            {"$set": {
                "task_status": "todo",
                "task_lane": lane,
                "task_staged_at": now,
                "task_done_at": None,
            }},
        )
        return True, None

    if action_type == "set_priority":
        priority = action.get("priority")
        if priority in ("null", "none", ""):
            priority = None
        if priority is not None and priority not in TASK_PRIORITIES:
            return False, "I can set priority to low, medium, high or urgent."
        await db.conversations.update_one(
            {"conversation_id": cid}, {"$set": {"task_priority": priority}})
        return True, None

    if action_type == "set_deadline":
        deadline = action.get("deadline")
        if deadline in ("null", "none", ""):
            deadline = None
        if deadline is not None:
            if not isinstance(deadline, str) or not DEADLINE_RE.match(deadline):
                return False, "I couldn't work out that date. Could you say the deadline again?"
            try:
                date.fromisoformat(deadline)
            except ValueError:
                return False, "That doesn't seem to be a real date. Could you say it again?"
        await db.conversations.update_one(
            {"conversation_id": cid}, {"$set": {"task_deadline": deadline}})
        return True, None

    return False, "I can't do that yet from voice mode."


async def handle_voice_command(request: web.Request) -> web.Response:
    """POST /api/voice/command — one voice-session turn.

    Body: {"text": "<utterance>", "history": [{"role", "content"}, ...]}
    Returns: {"speech": "...", "action": {...}, "executed": bool}
    """
    db = get_db()
    if db is None:
        return web.json_response({"error": "Observability not configured"}, status=503)

    user_email = get_user_email(request)
    if not user_email:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    text = (body.get("text") or "").strip()
    model = (body.get("model") or "").strip()
    if not text:
        return web.json_response({"error": "Missing text"}, status=400)
    if not model or "/" not in model:
        return web.json_response({"error": "Select a model first"}, status=400)
    if len(text) > MAX_UTTERANCE_LEN:
        text = text[:MAX_UTTERANCE_LEN]

    history_in = body.get("history") or []
    history = [
        {"role": m.get("role"), "content": str(m.get("content") or "")[:1000]}
        for m in history_in
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ][-MAX_HISTORY_TURNS:]

    board = await _get_board_config_for(db, user_email)
    snapshot, refs = await _board_snapshot(db, user_email)

    parsed = await _call_voice_llm(
        _llm_prompt(snapshot, board["lanes"], history, text), model)
    if not parsed or not isinstance(parsed.get("speech"), str):
        return web.json_response({
            "speech": "Sorry, I had trouble processing that. Could you say it again?",
            "action": {"type": "none"},
            "executed": False,
        })

    speech = parsed["speech"].strip()
    action = parsed.get("action") if isinstance(parsed.get("action"), dict) else {"type": "none"}
    if action.get("type") not in VOICE_ACTIONS:
        action = {"type": "none"}

    try:
        ok, override = await _execute_action(db, user_email, action, refs)
    except Exception:
        logger.exception("[VOICE] action execution failed")
        ok, override = False, "Something went wrong applying that change. Please try again."
    if not ok and override:
        speech = override

    return web.json_response({
        "speech": speech or "Okay.",
        "action": action,
        "executed": ok and action.get("type") != "none",
    })


def setup_voice_routes(app: web.Application):
    """Register voice-mode routes on the aiohttp app."""
    app.router.add_post("/api/voice/command", handle_voice_command)
