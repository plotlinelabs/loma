"""Title cadence counts user messages, retries fallback titles, and preserves renames."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from observability.observer import ConversationObserver


def _db(existing):
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=existing)
    db.conversations.update_one = AsyncMock()
    return db


def _patches(title=("Updated title", True), topic="engineering"):
    return (
        patch("api.routes.generate_title", new_callable=AsyncMock, return_value=title),
        patch("api.routes._classify_topic_llm", new_callable=AsyncMock, return_value=topic),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", range(17))
async def test_llm_titled_thread_refreshes_every_five_user_messages(count):
    db = _db({
        "title": "Earlier title", "title_source": "llm",
        "messages": [{"role": role, "content": "first ask" if role == "user" else "x"}
                     for _ in range(count) for role in ("user", "assistant", "tool")],
    })
    observer = ConversationObserver(db, {"prompt": "first ask"}, "chat")
    gen_patch, topic_patch = _patches()
    with gen_patch as generate, topic_patch as classify:
        await observer._run_title_topic_enrichment("reply")

    classify.assert_awaited_once()
    db.conversations.update_one.assert_any_await(
        {"conversation_id": "chat"}, {"$set": {"topic": "engineering"}})
    if count in (1, 6, 11, 16):
        generate.assert_awaited_once_with("first ask", "reply", db=db, conversation_id="chat")
        db.conversations.update_one.assert_any_await(
            {"conversation_id": "chat", "title_edited": {"$ne": True}},
            {"$set": {"title": "Updated title", "title_source": "llm"}},
        )
    else:
        generate.assert_not_awaited()
        assert db.conversations.update_one.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [
    {"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]},
    {"title": "Can you look at the", "title_source": "fallback",
     "messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]},
])
async def test_untitled_or_fallback_threads_are_titled_on_any_run(existing):
    # User message 2 is off-cadence, but an untitled or fallback-titled thread
    # keeps trying until the LLM names it.
    db = _db(existing)
    observer = ConversationObserver(db, {"prompt": "b"}, "chat")
    gen_patch, topic_patch = _patches()
    with gen_patch as generate, topic_patch:
        await observer._run_title_topic_enrichment("reply")
    generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_title_is_marked_for_retry():
    db = _db({"messages": [{"role": "user", "content": "fix the login bug"}]})
    observer = ConversationObserver(db, {"prompt": "fix the login bug"}, "chat")
    gen_patch, topic_patch = _patches(title=("fix the login bug", False))
    with gen_patch, topic_patch:
        await observer._run_title_topic_enrichment("reply")
    db.conversations.update_one.assert_any_await(
        {"conversation_id": "chat", "title_edited": {"$ne": True}},
        {"$set": {"title": "fix the login bug", "title_source": "fallback"}},
    )


@pytest.mark.asyncio
async def test_title_uses_the_first_message_when_the_stored_prompt_is_empty():
    # Board drafts can be staged with an empty prompt; the thread is about
    # the first message actually sent.
    db = _db({"messages": [{"role": "user", "content": "Import Apollo contacts"}]})
    observer = ConversationObserver(db, {"prompt": ""}, "chat")
    gen_patch, topic_patch = _patches()
    with gen_patch as generate, topic_patch:
        await observer._run_title_topic_enrichment("reply")
    assert generate.await_args.args[0] == "Import Apollo contacts"


@pytest.mark.asyncio
async def test_later_runs_title_from_the_first_and_latest_messages():
    db = _db({"messages": [{"role": "user", "content": "first"}, {"role": "user", "content": "x"},
                           {"role": "user", "content": "x"}, {"role": "user", "content": "x"},
                           {"role": "user", "content": "x"}, {"role": "user", "content": "latest"}],
              "title": "Old", "title_source": "llm"})
    observer = ConversationObserver(db, {"prompt": "latest"}, "chat")
    gen_patch, topic_patch = _patches()
    with gen_patch as generate, topic_patch:
        await observer._run_title_topic_enrichment("reply")
    assert generate.await_args.args[0] == "first\n\nLatest: latest"


@pytest.mark.asyncio
async def test_title_only_skips_topic():
    db = _db({"messages": [{"role": "user", "content": "a"}]})
    observer = ConversationObserver(db, {"prompt": "a"}, "chat")
    gen_patch, topic_patch = _patches()
    with gen_patch as generate, topic_patch as classify:
        await observer._run_title_topic_enrichment("", title_only=True)
    generate.assert_awaited_once()
    classify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [None, {}, {
    "title_edited": True, "messages": [{"role": "user", "content": "a"}],
}])
async def test_manual_titles_and_missing_history_are_not_replaced(existing):
    db = _db(existing)
    observer = ConversationObserver(db, {"prompt": "prompt"}, "chat")
    gen_patch, topic_patch = _patches(topic="general")
    with gen_patch as generate, topic_patch:
        await observer._run_title_topic_enrichment("reply")
    generate.assert_not_awaited()
    db.conversations.update_one.assert_awaited_once_with(
        {"conversation_id": "chat"}, {"$set": {"topic": "general"}})


@pytest.mark.asyncio
async def test_rename_during_generation_is_guarded_at_write_time():
    db = MagicMock()
    document = {"messages": [{"role": "user", "content": "prompt"}]}
    db.conversations.find_one = AsyncMock(side_effect=lambda *args: dict(document))

    async def rename_during_generation(*args, **kwargs):
        document.update(title="My manual title", title_edited=True)
        return "Generated title", True

    async def update(query, operation):
        if query.get("title_edited") == {"$ne": True} and document.get("title_edited"):
            return
        document.update(operation["$set"])

    db.conversations.update_one = AsyncMock(side_effect=update)
    observer = ConversationObserver(db, {"prompt": "prompt"}, "chat")
    with patch("api.routes.generate_title", side_effect=rename_during_generation), patch(
        "api.routes._classify_topic_llm", new_callable=AsyncMock, return_value="general",
    ):
        await observer._run_title_topic_enrichment("reply")
    assert document["title"] == "My manual title"
    assert document["topic"] == "general"


@pytest.mark.asyncio
async def test_failed_runs_still_get_a_title():
    db = MagicMock()
    db.conversations.update_one = AsyncMock()
    observer = ConversationObserver(db, {"prompt": "a"}, "chat")
    with patch.object(observer, "_run_title_topic_enrichment", new_callable=AsyncMock) as enrich, \
            patch("observability.push.fire_task_push"):
        await observer.record_error("boom")
        await __import__("asyncio").sleep(0)
    enrich.assert_awaited_once_with("", title_only=True)
