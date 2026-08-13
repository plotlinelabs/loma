"""Telegram update processing — account linking and agent runs.

Handles updates dispatched by ``webhooks/telegram.py``:

  - ``/start <code>``  — completes the dashboard-initiated linking flow by
    consuming a one-time code from ``telegram_link_codes`` and saving the
    ``telegram_user_id -> user_email`` mapping in ``telegram_links``.
  - ``/stop``          — unlinks the Telegram account.
  - any other DM text  — runs the agent as the linked Loma user and sends
    the response back through the Bot API.

Conversations are persisted per Telegram chat: messages in the same chat
reuse the same Loma conversation (``metadata.telegram_chat_id``), mirroring
how Slack threads map to conversations.
"""

import logging
import os
from datetime import datetime, timezone

import aiohttp

from observability.db import get_db
from observability.observer import ConversationObserver

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram sendMessage hard limit is 4096 chars per message.
_MAX_MESSAGE_LEN = 4000

_NOT_LINKED_REPLY = (
    "This Telegram account is not linked to Loma yet.\n\n"
    "Open the Loma dashboard > Integrations > Personal > Telegram and "
    "click Connect to link your account."
)


def _bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


async def _telegram_api(method: str, payload: dict) -> dict:
    """Call a Telegram Bot API method. Returns the parsed JSON response."""
    token = _bot_token()
    if not token:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN not set"}
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning("[TELEGRAM] %s failed: %s", method, data.get("description"))
                return data
    except Exception:
        logger.exception("[TELEGRAM] %s request error", method)
        return {"ok": False, "description": "request error"}


async def send_telegram_message(chat_id: int | str, text: str) -> None:
    """Send a plain-text message, splitting at Telegram's length limit."""
    text = (text or "").strip()
    if not text:
        return
    while text:
        chunk, text = text[:_MAX_MESSAGE_LEN], text[_MAX_MESSAGE_LEN:]
        await _telegram_api("sendMessage", {"chat_id": chat_id, "text": chunk})


async def _is_duplicate_update(db, update_id: int) -> bool:
    """Dedup on update_id — Telegram redelivers on slow/failed responses."""
    result = await db.telegram_updates.update_one(
        {"update_id": update_id},
        {"$setOnInsert": {
            "update_id": update_id,
            "received_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return result.upserted_id is None


async def _handle_start(db, message: dict, payload: str) -> None:
    """Handle ``/start [code]`` — complete the dashboard linking flow."""
    from_user = message.get("from", {})
    chat_id = message["chat"]["id"]
    telegram_user_id = from_user.get("id")

    code = payload.strip()
    if not code:
        await send_telegram_message(chat_id, _NOT_LINKED_REPLY)
        return

    now = datetime.now(timezone.utc)
    code_doc = await db.telegram_link_codes.find_one_and_update(
        {"code": code, "used": False, "expires_at": {"$gt": now}},
        {"$set": {"used": True, "used_at": now}},
    )
    if not code_doc:
        await send_telegram_message(
            chat_id,
            "That connect link is invalid or has expired. Please generate a "
            "new one from the Loma dashboard (Integrations > Personal > Telegram).",
        )
        return

    user_email = code_doc["user_email"]
    await db.telegram_links.update_one(
        {"user_email": user_email},
        {"$set": {
            "user_email": user_email,
            "telegram_user_id": telegram_user_id,
            "telegram_chat_id": chat_id,
            "telegram_username": from_user.get("username"),
            "telegram_first_name": from_user.get("first_name"),
            "linked_at": now,
        }},
        upsert=True,
    )
    # A Telegram account can only be linked to one Loma user — drop any other
    # mapping that points at this same Telegram user.
    await db.telegram_links.delete_many({
        "telegram_user_id": telegram_user_id,
        "user_email": {"$ne": user_email},
    })

    logger.info("[TELEGRAM] Linked telegram_user=%s to %s", telegram_user_id, user_email)
    await send_telegram_message(
        chat_id,
        f"Connected as {user_email}. Send me a message and I'll run it "
        "through your Loma agent. Send /stop to disconnect.",
    )


async def _handle_stop(db, message: dict) -> None:
    """Handle ``/stop`` — unlink this Telegram account."""
    telegram_user_id = message.get("from", {}).get("id")
    chat_id = message["chat"]["id"]
    result = await db.telegram_links.delete_many({"telegram_user_id": telegram_user_id})
    if result.deleted_count:
        await send_telegram_message(chat_id, "Disconnected. You can reconnect anytime from the Loma dashboard.")
    else:
        await send_telegram_message(chat_id, "This Telegram account was not linked to Loma.")


async def _run_agent_for_message(db, link: dict, message: dict) -> None:
    """Run the agent as the linked user and send the response back."""
    from agent.client import stream_agent

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    user_email = link["user_email"]

    await _telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    try:
        # Reuse the existing conversation for this Telegram chat, like Slack
        # threads reuse conversations.
        existing_convo = await db.conversations.find_one({
            "metadata.source": "telegram",
            "metadata.telegram_chat_id": str(chat_id),
        })
        metadata = {
            "source": "telegram",
            "prompt": text,
            "model": os.environ.get("AGENT_DEFAULT_MODEL", "opencode-go/deepseek-v4-flash"),
            "telegram_user_id": str(link.get("telegram_user_id")),
            "telegram_chat_id": str(chat_id),
            "user_name": user_email,
        }
        if existing_convo:
            observer = ConversationObserver(
                db, metadata=metadata,
                conversation_id=existing_convo["conversation_id"],
            )
            await observer.resume()
        else:
            observer = ConversationObserver(db, metadata=metadata)
            await observer.start()

        logger.info("[TELEGRAM] Running agent for %s (chat=%s)", user_email, chat_id)
        async for chunk in stream_agent(
            prompt=text,
            observer=observer,
            source="telegram",
            user_email=user_email,
        ):
            if isinstance(chunk, str):
                await send_telegram_message(chat_id, chunk)
        logger.info("[TELEGRAM] Agent run complete for %s (chat=%s)", user_email, chat_id)
    except Exception as e:
        logger.exception("[TELEGRAM] Agent run failed for %s", user_email)
        await send_telegram_message(chat_id, f"Sorry, something went wrong: {e}")


async def process_telegram_update(update: dict) -> None:
    """Entry point — dispatched as a background task by the webhook handler."""
    db = get_db()
    if db is None:
        logger.warning("[TELEGRAM] DB unavailable, dropping update")
        return

    try:
        update_id = update.get("update_id")
        if await _is_duplicate_update(db, update_id):
            logger.info("[TELEGRAM] Duplicate update_id=%s, skipping", update_id)
            return

        message = update.get("message")
        if not message:
            return  # edited_message, callback_query, etc. — not supported yet

        chat = message.get("chat", {})
        if chat.get("type") != "private":
            return  # DMs only — group chats are out of scope

        from_user = message.get("from", {})
        if from_user.get("is_bot"):
            return

        text = (message.get("text") or "").strip()
        if not text:
            await send_telegram_message(
                chat["id"], "I can only handle text messages right now.")
            return

        if text.startswith("/start"):
            await _handle_start(db, message, text[len("/start"):])
            return
        if text.split("@")[0].strip() == "/stop":
            await _handle_stop(db, message)
            return

        link = await db.telegram_links.find_one({"telegram_user_id": from_user.get("id")})
        if not link:
            await send_telegram_message(chat["id"], _NOT_LINKED_REPLY)
            return

        await _run_agent_for_message(db, link, message)
    except Exception:
        logger.exception("[TELEGRAM] Failed to process update")
