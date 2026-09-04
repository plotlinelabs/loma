from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_app.handlers import _capture_loma_task, _task_title
from api.task_service import create_staged_task


def _event(reaction="loma-task"):
    return {
        "reaction": reaction,
        "user": "U_REACTOR",
        "item": {"type": "message", "channel": "C123", "ts": "1700000000.000001"},
    }


def test_task_title_uses_message_text():
    assert _task_title({"text": "  Please   review this  "}) == "Slack: Please review this"


@pytest.mark.asyncio
async def test_create_staged_task_uses_first_lane_and_source_metadata():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=None)
    db.conversations.insert_one = AsyncMock()
    with patch("api.task_service._get_board_config_for", AsyncMock(return_value={
        "lanes": [{"id": "inbox", "name": "Inbox", "order": 0}],
    })):
        task, created = await create_staged_task(
            db, "owner@example.com", "Do the work",
            title="Slack: Do the work", metadata={"source": "slack_task"},
            dedupe_filter={"metadata.capture_key": "key"},
        )

    assert created is True
    assert task["task_status"] == "todo"
    assert task["task_lane"] == "inbox"
    assert task["source"] == "slack_task"
    assert task["metadata"]["user_name"] == "owner@example.com"
    db.conversations.insert_one.assert_awaited_once_with(task)


@pytest.mark.asyncio
async def test_create_staged_task_returns_existing_duplicate():
    existing = {"conversation_id": "existing"}
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=existing)
    db.conversations.insert_one = AsyncMock()
    task, created = await create_staged_task(
        db, "owner@example.com", "Do the work",
        dedupe_filter={"metadata.capture_key": "key"},
    )
    assert (task, created) == (existing, False)
    db.conversations.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_creates_owned_task_with_thread_and_permalink():
    bot = MagicMock()
    bot.users_info = AsyncMock(return_value={"user": {"profile": {"email": "owner@example.com"}}})
    bot.chat_postEphemeral = AsyncMock()
    bot.reactions_add = AsyncMock()
    user = MagicMock()
    user.conversations_history = AsyncMock(return_value={"messages": [{
        "ts": "1700000000.000001", "thread_ts": "1699999999.000001",
        "user": "U_AUTHOR", "text": "Please review this",
    }]})
    user.conversations_replies = AsyncMock(return_value={"messages": [
        {"ts": "1699999999.000001", "user": "U_PARENT", "text": "Context"},
        {"ts": "1700000000.000001", "user": "U_AUTHOR", "text": "Please review this"},
    ]})
    user.chat_getPermalink = AsyncMock(return_value={"permalink": "https://slack.test/message"})
    db = MagicMock()

    with patch("slack_app.handlers.get_user_slack_token", AsyncMock(return_value="xoxp-token")), \
         patch("slack_sdk.web.async_client.AsyncWebClient", return_value=user), \
         patch("slack_app.handlers.get_db", return_value=db), \
         patch("api.task_service.create_staged_task", AsyncMock(return_value=({"title": "Slack: Please review this"}, True))) as create:
        await _capture_loma_task(bot, _event())

    args, kwargs = create.await_args
    assert args[1] == "owner@example.com"
    assert "[U_AUTHOR] [selected]: Please review this" in args[2]
    assert "https://slack.test/message" in args[2]
    assert kwargs["metadata"]["slack_thread_ts"] == "1699999999.000001"
    assert kwargs["dedupe_filter"]["metadata.user_name"] == "owner@example.com"
    bot.reactions_add.assert_not_awaited()
    bot.chat_postEphemeral.assert_awaited_once()
    assert bot.chat_postEphemeral.await_args.kwargs["text"].startswith(":inbox_tray: Added")


@pytest.mark.asyncio
async def test_capture_does_not_acknowledge_duplicate_with_reaction():
    bot = MagicMock()
    bot.users_info = AsyncMock(return_value={"user": {"profile": {"email": "owner@example.com"}}})
    bot.chat_postEphemeral = AsyncMock()
    bot.reactions_add = AsyncMock()
    user = MagicMock()
    user.conversations_history = AsyncMock(return_value={"messages": [{
        "ts": "1700000000.000001", "user": "U_AUTHOR", "text": "Do it",
    }]})
    user.conversations_replies = AsyncMock(return_value={"messages": []})
    user.chat_getPermalink = AsyncMock(return_value={"permalink": "https://slack.test/message"})

    with patch("slack_app.handlers.get_user_slack_token", AsyncMock(return_value="xoxp-token")), \
         patch("slack_sdk.web.async_client.AsyncWebClient", return_value=user), \
         patch("slack_app.handlers.get_db", return_value=MagicMock()), \
         patch("api.task_service.create_staged_task", AsyncMock(return_value=({"title": "Slack: Do it"}, False))):
        await _capture_loma_task(bot, _event())

    bot.reactions_add.assert_not_awaited()
    assert bot.chat_postEphemeral.await_args.kwargs["text"].startswith(":inbox_tray: Already added")


@pytest.mark.asyncio
async def test_capture_falls_back_to_replies_for_thread_reply_messages():
    """Thread replies are invisible to conversations.history — the capture must
    fall back to conversations.replies so reacting inside a thread still works."""
    bot = MagicMock()
    bot.users_info = AsyncMock(return_value={"user": {"profile": {"email": "owner@example.com"}}})
    bot.chat_postEphemeral = AsyncMock()
    bot.reactions_add = AsyncMock()
    user = MagicMock()
    # history returns nothing for thread replies
    user.conversations_history = AsyncMock(return_value={"messages": []})

    reply_message = {
        "ts": "1700000000.000001", "thread_ts": "1699999999.000001",
        "user": "U_AUTHOR", "text": "Reply inside a thread",
    }

    async def replies_side_effect(channel=None, ts=None, **kwargs):
        if ts == "1700000000.000001":  # fetch of the reply itself
            return {"messages": [reply_message]}
        return {"messages": [  # full-thread fetch by parent ts
            {"ts": "1699999999.000001", "user": "U_PARENT", "text": "Parent context"},
            reply_message,
        ]}

    user.conversations_replies = AsyncMock(side_effect=replies_side_effect)
    user.chat_getPermalink = AsyncMock(return_value={"permalink": "https://slack.test/reply"})

    with patch("slack_app.handlers.get_user_slack_token", AsyncMock(return_value="xoxp-token")), \
         patch("slack_sdk.web.async_client.AsyncWebClient", return_value=user), \
         patch("slack_app.handlers.get_db", return_value=MagicMock()), \
         patch("api.task_service.create_staged_task", AsyncMock(return_value=({"title": "Slack: Reply inside a thread"}, True))) as create:
        await _capture_loma_task(bot, _event())

    args, kwargs = create.await_args
    assert args[1] == "owner@example.com"
    assert "[U_AUTHOR] [selected]: Reply inside a thread" in args[2]
    assert "[U_PARENT]: Parent context" in args[2]
    assert kwargs["metadata"]["slack_thread_ts"] == "1699999999.000001"
    assert bot.chat_postEphemeral.await_args.kwargs["text"].startswith(":inbox_tray: Added")


@pytest.mark.asyncio
async def test_capture_reports_error_when_message_unreadable_everywhere():
    bot = MagicMock()
    bot.users_info = AsyncMock(return_value={"user": {"profile": {"email": "owner@example.com"}}})
    bot.chat_postEphemeral = AsyncMock()
    user = MagicMock()
    user.conversations_history = AsyncMock(return_value={"messages": []})
    user.conversations_replies = AsyncMock(return_value={"messages": []})

    with patch("slack_app.handlers.get_user_slack_token", AsyncMock(return_value="xoxp-token")), \
         patch("slack_sdk.web.async_client.AsyncWebClient", return_value=user), \
         patch("slack_app.handlers.get_db", return_value=MagicMock()):
        await _capture_loma_task(bot, _event())

    assert "Couldn't read the reacted Slack message" in bot.chat_postEphemeral.await_args.kwargs["text"]
