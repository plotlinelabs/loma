"""Regression coverage for attachment-only requests and follow-up image reuse."""
import base64
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.chat_attachments import cache_chat_images, validate_attachments, MAX_FILES
from api.routes import handle_chat

IMAGE = {"name": "test.png", "type": "image", "mimetype": "image/png",
         "data": base64.b64encode(b"test image bytes").decode()}


@pytest.mark.parametrize("files", [None, {}, [None], [{"type": "image"}],
    [{**IMAGE, "data": "not base64!"}], [{**IMAGE, "mimetype": "image/svg+xml"}],
    [IMAGE] * (MAX_FILES + 1)])
def test_invalid_attachments(files):
    with pytest.raises(ValueError):
        validate_attachments(files)


def test_valid_images_and_empty_text_files():
    validate_attachments([IMAGE, {"name": "empty.txt", "type": "text", "data": ""}])


@pytest.mark.asyncio
@pytest.mark.parametrize("message,files,expected", [("", [], 400), ("  ", [], 400),
    (42, [], 400), ("", [IMAGE], 200), ("  ", [IMAGE], 200), ("Inspect this", [IMAGE], 200)])
async def test_attachment_only_route(message, files, expected):
    request = MagicMock()
    request.json = AsyncMock(return_value={"message": message, "files": files})
    response = MagicMock(status=200)
    response.prepare = AsyncMock()
    response.write = AsyncMock()
    response.write_eof = AsyncMock()
    seen = {}

    async def stream(**kwargs):
        seen.update(kwargs)
        if False:
            yield None

    with patch("api.routes.get_user_email", return_value="owner@example.com"), \
         patch("api.routes.get_db", return_value=None), \
         patch("api.routes.is_draining", return_value=False), \
         patch("api.routes.stream_agent", side_effect=stream), \
         patch("api.routes.web.StreamResponse", return_value=response), \
         patch("api.recall_session.launch_recall", new=AsyncMock(return_value=None)):
        result = await handle_chat(request)
    assert result.status == expected
    if expected == 200:
        assert seen["files"] == files
        assert seen["prompt"].strip()


@pytest.mark.asyncio
async def test_cache_rehydrates_and_scopes_followups():
    db = MagicMock()
    db.chat_images.update_one = AsyncMock()
    stored = []
    cursor = db.chat_images.find.return_value
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(side_effect=lambda _: list(stored))
    await cache_chat_images(db, "owner@example.com", "chat-1", [IMAGE])
    update = db.chat_images.update_one.call_args.args[1]["$set"]
    assert update["data"] == b"test image bytes"
    assert (update["expires_at"] - update["created_at"]).days == 7
    stored.append(update)
    assert await cache_chat_images(db, "owner@example.com", "chat-1", []) == [IMAGE]
    # A repeated image is not duplicated in the model input.
    assert await cache_chat_images(db, "owner@example.com", "chat-1", [IMAGE]) == [IMAGE]
    scope = db.chat_images.find.call_args.args[0]
    assert scope["user_email"] == "owner@example.com"
    assert scope["conversation_id"] == "chat-1"
    assert scope["expires_at"]["$gt"] <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_no_cache_without_authenticated_scope():
    db = MagicMock()
    assert await cache_chat_images(db, "", "chat-1", [IMAGE]) == [IMAGE]
    assert await cache_chat_images(db, "owner@example.com", None, [IMAGE]) == [IMAGE]
    db.chat_images.find.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted,allowed", [(False, False), (True, True)])
async def test_cache_never_runs_for_inaccessible_or_deleted_conversation(deleted, allowed):
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value={"deleted": deleted})
    request = MagicMock()
    request.json = AsyncMock(return_value={"message": "follow up", "conversation_id": "private-chat"})
    with patch("api.routes.get_user_email", return_value="other@example.com"), \
         patch("api.routes.get_db", return_value=db), \
         patch("api.routes.is_draining", return_value=False), \
         patch("api.routes._check_conversation_access", return_value=allowed), \
         patch("api.routes.cache_chat_images", new_callable=AsyncMock) as cache:
        result = await handle_chat(request)
    assert result.status == 404
    cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_clears_image_cache():
    from api.routes import handle_delete_conversation
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value={"conversation_id": "chat-1"})
    db.conversations.update_one = AsyncMock()
    db.turns.update_many = AsyncMock()
    db.users.update_many = AsyncMock()
    db.chat_images.delete_many = AsyncMock()
    request = MagicMock(match_info={"conversation_id": "chat-1"})
    with patch("api.routes.get_db", return_value=db), \
         patch("api.routes.get_user_email", return_value="owner@example.com"), \
         patch("api.routes._check_conversation_manage_access", return_value=True):
        result = await handle_delete_conversation(request)
    assert result.status == 200
    db.chat_images.delete_many.assert_awaited_once_with({"conversation_id": "chat-1"})


@pytest.mark.asyncio
async def test_cache_failure_does_not_start_agent_or_observer():
    db = MagicMock()
    db.conversations.find_one = AsyncMock(return_value=None)
    request = MagicMock()
    request.json = AsyncMock(return_value={"message": "", "files": [IMAGE], "conversation_id": "new-chat"})
    with patch("api.routes.get_db", return_value=db), \
         patch("api.routes.get_user_email", return_value="owner@example.com"), \
         patch("api.routes.is_draining", return_value=False), \
         patch("api.routes.cache_chat_images", new=AsyncMock(side_effect=RuntimeError("cache offline"))), \
         patch("api.routes.ConversationObserver") as observer:
        result = await handle_chat(request)
    assert result.status == 503
    observer.assert_not_called()
