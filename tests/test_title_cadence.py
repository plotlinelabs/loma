"""Title cadence counts user messages, not agent turns, and preserves renames."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from observability.observer import ConversationObserver


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(17))
async def test_title_refreshes_every_five_user_messages(count):
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value={
        "messages": [{"role": role} for _ in range(count)
                     for role in ("user", "assistant", "tool")],
    })
    db.conversations.update_one = AsyncMock()
    observer = ConversationObserver(db, {"prompt": "latest prompt"}, "chat")
    observer.turn_offset = 37
    observer.turn_count = 12
    with patch("api.routes._generate_title_llm", new_callable=AsyncMock,
               return_value="Updated title") as generate, patch(
        "api.routes._classify_topic_llm", new_callable=AsyncMock,
        return_value="engineering",
    ) as classify:
        await observer._run_title_topic_enrichment("reply")

    classify.assert_awaited_once_with("latest prompt", "reply")
    db.conversations.update_one.assert_any_await(
        {"conversation_id": "chat"}, {"$set": {"topic": "engineering"}})
    if count in (1, 6, 11, 16):
        generate.assert_awaited_once_with("latest prompt", "reply")
        db.conversations.update_one.assert_any_await(
            {"conversation_id": "chat", "title_edited": {"$ne": True}},
            {"$set": {"title": "Updated title"}},
        )
    else:
        generate.assert_not_awaited()
        assert db.conversations.update_one.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [None, {}, {
    "title_edited": True, "messages": [{"role": "user"}],
}])
async def test_manual_titles_and_missing_history_are_not_replaced(existing):
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=existing)
    db.conversations.update_one = AsyncMock()
    observer = ConversationObserver(db, {"prompt": "prompt"}, "chat")
    with patch("api.routes._generate_title_llm", new_callable=AsyncMock) as generate, patch(
        "api.routes._classify_topic_llm", new_callable=AsyncMock,
        return_value="general",
    ):
        await observer._run_title_topic_enrichment("reply")
    generate.assert_not_awaited()
    db.conversations.update_one.assert_awaited_once_with(
        {"conversation_id": "chat"}, {"$set": {"topic": "general"}})


@pytest.mark.asyncio
async def test_rename_during_generation_is_guarded_at_write_time():
    db = MagicMock()
    document = {"title": "Auto title", "messages": [{"role": "user"}]}
    db.conversations.find_one = AsyncMock(side_effect=lambda *args: dict(document))

    async def rename_during_generation(*args):
        document.update(title="My manual title", title_edited=True)
        return "Generated title"

    async def update(query, operation):
        if query.get("title_edited") == {"$ne": True} and document.get("title_edited"):
            return
        document.update(operation["$set"])

    db.conversations.update_one = AsyncMock(side_effect=update)
    observer = ConversationObserver(db, {"prompt": "prompt"}, "chat")
    with patch("api.routes._generate_title_llm", side_effect=rename_during_generation), patch(
        "api.routes._classify_topic_llm", new_callable=AsyncMock, return_value="general",
    ):
        await observer._run_title_topic_enrichment("reply")
    assert document["title"] == "My manual title"
    assert document["topic"] == "general"
