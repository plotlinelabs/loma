"""Tests for webhooks/telegram.py and webhooks/telegram_ingestion.py.

Covers:
- handle_telegram_webhook: secret verification, config gating, bad payloads
- process_telegram_update: dedup, /start linking, /stop unlink,
  unlinked-sender reply, linked-sender agent run
- send_telegram_message: message splitting at the length limit
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

from webhooks.telegram import handle_telegram_webhook
from webhooks import telegram_ingestion
from webhooks.telegram_ingestion import (
    process_telegram_update,
    send_telegram_message,
    _MAX_MESSAGE_LEN,
)


def _mocked_request(body: dict, headers: dict | None = None):
    raw = json.dumps(body).encode()
    req = make_mocked_request(
        "POST", "/webhooks/telegram", headers=headers or {},
    )
    req.read = AsyncMock(return_value=raw)
    return req


def _make_db():
    """Build a mock db with the collections the ingestion module touches."""
    db = MagicMock()
    # Dedup: default to "new update" (upserted_id set)
    upsert_result = MagicMock()
    upsert_result.upserted_id = "new-id"
    db.telegram_updates.update_one = AsyncMock(return_value=upsert_result)
    db.telegram_link_codes.find_one_and_update = AsyncMock(return_value=None)
    db.telegram_links.find_one = AsyncMock(return_value=None)
    db.telegram_links.update_one = AsyncMock()
    delete_result = MagicMock()
    delete_result.deleted_count = 0
    db.telegram_links.delete_many = AsyncMock(return_value=delete_result)
    db.conversations.find_one = AsyncMock(return_value=None)
    return db


def _dm_update(text: str, update_id: int = 1, user_id: int = 42):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "from": {"id": user_id, "is_bot": False, "first_name": "V", "username": "vaibhav"},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# handle_telegram_webhook — auth and gating
# ---------------------------------------------------------------------------


class TestTelegramWebhookHandler:
    @pytest.mark.asyncio
    async def test_not_configured_returns_503(self):
        req = _mocked_request(_dm_update("hi"))
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": ""}):
            resp = await handle_telegram_webhook(req)
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_secret_mismatch_returns_401(self):
        req = _mocked_request(
            _dm_update("hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_WEBHOOK_SECRET": "right"}
        with patch.dict("os.environ", env):
            resp = await handle_telegram_webhook(req)
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_secret_match_accepts(self):
        req = _mocked_request(
            _dm_update("hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "right"},
        )
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_WEBHOOK_SECRET": "right"}
        with patch.dict("os.environ", env), \
             patch("webhooks.telegram.process_telegram_update", new=AsyncMock()):
            resp = await handle_telegram_webhook(req)
        assert resp.status == 200
        assert json.loads(resp.body)["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_no_secret_configured_accepts(self):
        req = _mocked_request(_dm_update("hi"))
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_WEBHOOK_SECRET": ""}
        with patch.dict("os.environ", env), \
             patch("webhooks.telegram.process_telegram_update", new=AsyncMock()):
            resp = await handle_telegram_webhook(req)
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_missing_update_id_returns_400(self):
        req = _mocked_request({"message": {}})
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:abc"}):
            resp = await handle_telegram_webhook(req)
        assert resp.status == 400


# ---------------------------------------------------------------------------
# process_telegram_update — routing
# ---------------------------------------------------------------------------


class TestProcessTelegramUpdate:
    @pytest.mark.asyncio
    async def test_duplicate_update_skipped(self):
        db = _make_db()
        db.telegram_updates.update_one.return_value.upserted_id = None  # already seen
        send = AsyncMock()
        with patch.object(telegram_ingestion, "get_db", return_value=db), \
             patch.object(telegram_ingestion, "send_telegram_message", send):
            await process_telegram_update(_dm_update("hello"))
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_chat_ignored(self):
        db = _make_db()
        update = _dm_update("hello")
        update["message"]["chat"]["type"] = "group"
        send = AsyncMock()
        with patch.object(telegram_ingestion, "get_db", return_value=db), \
             patch.object(telegram_ingestion, "send_telegram_message", send):
            await process_telegram_update(update)
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_with_valid_code_links_account(self):
        db = _make_db()
        db.telegram_link_codes.find_one_and_update.return_value = {
            "code": "abc", "user_email": "user@example.com",
        }
        send = AsyncMock()
        with patch.object(telegram_ingestion, "get_db", return_value=db), \
             patch.object(telegram_ingestion, "send_telegram_message", send):
            await process_telegram_update(_dm_update("/start abc"))

        db.telegram_links.update_one.assert_awaited_once()
        filter_arg, update_arg = db.telegram_links.update_one.await_args.args
        assert filter_arg == {"user_email": "user@example.com"}
        assert update_arg["$set"]["telegram_user_id"] == 42
        assert "Connected as user@example.com" in send.await_args.args[1]

    @pytest.mark.asyncio
    async def test_start_with_invalid_code_replies_error(self):
        db = _make_db()
        send = AsyncMock()
        with patch.object(telegram_ingestion, "get_db", return_value=db), \
             patch.object(telegram_ingestion, "send_telegram_message", send):
            await process_telegram_update(_dm_update("/start expired-code"))
        db.telegram_links.update_one.assert_not_called()
        assert "invalid or has expired" in send.await_args.args[1]

    @pytest.mark.asyncio
    async def test_stop_unlinks(self):
        db = _make_db()
        db.telegram_links.delete_many.return_value.deleted_count = 1
        send = AsyncMock()
        with patch.object(telegram_ingestion, "get_db", return_value=db), \
             patch.object(telegram_ingestion, "send_telegram_message", send):
            await process_telegram_update(_dm_update("/stop"))
        db.telegram_links.delete_many.assert_awaited_once_with({"telegram_user_id": 42})
        assert "Disconnected" in send.await_args.args[1]

    @pytest.mark.asyncio
    async def test_unlinked_sender_gets_connect_prompt(self):
        db = _make_db()
        send = AsyncMock()
        with patch.object(telegram_ingestion, "get_db", return_value=db), \
             patch.object(telegram_ingestion, "send_telegram_message", send):
            await process_telegram_update(_dm_update("what's up"))
        assert "not linked" in send.await_args.args[1]

    @pytest.mark.asyncio
    async def test_linked_sender_runs_agent(self):
        db = _make_db()
        db.telegram_links.find_one.return_value = {
            "user_email": "user@example.com",
            "telegram_user_id": 42,
            "telegram_chat_id": 42,
        }

        async def fake_stream(**kwargs):
            assert kwargs["user_email"] == "user@example.com"
            assert kwargs["source"] == "telegram"
            yield "part one"
            yield "part two"

        observer = MagicMock()
        observer.start = AsyncMock()
        observer.resume = AsyncMock()

        send = AsyncMock()
        api = AsyncMock(return_value={"ok": True})
        with patch.object(telegram_ingestion, "get_db", return_value=db), \
             patch.object(telegram_ingestion, "send_telegram_message", send), \
             patch.object(telegram_ingestion, "_telegram_api", api), \
             patch.object(telegram_ingestion, "ConversationObserver", return_value=observer), \
             patch("agent.client.stream_agent", side_effect=fake_stream):
            await process_telegram_update(_dm_update("summarize my day"))

        # Both agent chunks were sent back to the chat
        sent_texts = [c.args[1] for c in send.await_args_list]
        assert "part one" in sent_texts
        assert "part two" in sent_texts
        observer.start.assert_awaited_once()


# ---------------------------------------------------------------------------
# send_telegram_message — splitting
# ---------------------------------------------------------------------------


class TestSendTelegramMessage:
    @pytest.mark.asyncio
    async def test_long_message_is_split(self):
        api = AsyncMock(return_value={"ok": True})
        with patch.object(telegram_ingestion, "_telegram_api", api):
            await send_telegram_message(42, "x" * (_MAX_MESSAGE_LEN + 10))
        assert api.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_message_not_sent(self):
        api = AsyncMock(return_value={"ok": True})
        with patch.object(telegram_ingestion, "_telegram_api", api):
            await send_telegram_message(42, "   ")
        api.assert_not_called()
