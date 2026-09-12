"""Thread-context trimming and the soft compress pass for Slack replies."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_app import brevity
from slack_app.brevity import maybe_compress_slack_reply, should_compress
from slack_app.handlers import _stream_response
from slack_app.utils import (
    ASSISTANT_CONTEXT_LABEL,
    ASSISTANT_CONTEXT_MAX_CHARS,
    format_assistant_context,
    get_dm_context,
    get_thread_context,
)

LONG_REPLY = "*Summary*\n" + "- finding line with detail\n" * 60 + "\nWant me to implement these as a follow-up PR?"


# -- thread context: earlier bot replies -------------------------------------


def test_format_assistant_context_trims_and_labels_older_replies():
    text = "x" * (ASSISTANT_CONTEXT_MAX_CHARS + 500)
    out = format_assistant_context(text)
    assert out.startswith(f"{ASSISTANT_CONTEXT_LABEL}: ")
    assert "_(trimmed 500 chars)_" in out
    assert len(out) < len(text)


def test_format_assistant_context_keeps_latest_reply_whole():
    text = "y" * (ASSISTANT_CONTEXT_MAX_CHARS + 500)
    assert format_assistant_context(text, keep_full=True) == f"{ASSISTANT_CONTEXT_LABEL}: {text}"


def test_format_assistant_context_short_reply_untouched():
    assert format_assistant_context("all good") == f"{ASSISTANT_CONTEXT_LABEL}: all good"


def _thread(*messages):
    client = MagicMock()
    client.conversations_replies = AsyncMock(return_value={"messages": list(messages)})
    return client


@pytest.mark.asyncio
async def test_thread_context_trims_old_bot_replies_but_keeps_latest():
    old_bot = "A" * 3000
    latest_bot = "B" * 3000
    client = _thread(
        {"ts": "1", "user": "U1", "text": "please check"},
        {"ts": "2", "bot_id": "B1", "text": old_bot},
        {"ts": "3", "user": "U1", "text": "and this"},
        {"ts": "4", "bot_id": "B1", "text": latest_bot},
        {"ts": "5", "user": "U1", "text": "confirm"},
    )
    context, files = await get_thread_context(client, "C1", "1", current_ts="5")

    assert files == []
    assert context.count(ASSISTANT_CONTEXT_LABEL) == 2
    assert old_bot not in context and "_(trimmed 1800 chars)_" in context
    assert latest_bot in context
    assert "**User (U1)**: please check" in context
    assert "confirm" not in context  # current message excluded
    assert len(context) < len(old_bot) + len(latest_bot)


@pytest.mark.asyncio
async def test_dm_context_applies_same_trimming():
    old_bot = "A" * 3000
    client = MagicMock()
    client.conversations_history = AsyncMock(return_value={"messages": [
        {"ts": "4", "user": "U1", "text": "current"},
        {"ts": "3", "bot_id": "B1", "text": "latest short reply"},
        {"ts": "2", "bot_id": "B1", "text": old_bot},
        {"ts": "1", "user": "U1", "text": "hi"},
    ]})
    context, _ = await get_dm_context(client, "D1")

    assert "_(trimmed 1800 chars)_" in context
    assert f"{ASSISTANT_CONTEXT_LABEL}: latest short reply" in context
    assert "current" not in context


# -- compress pass: gating ----------------------------------------------------


def test_should_compress_only_long_prose_replies():
    assert should_compress(LONG_REPLY, "explain the ask")
    assert not should_compress("Done. Deployed at 16:34 UTC.", "confirm")
    assert not should_compress(LONG_REPLY + "\n```bash\nls\n```", "how do I run it")
    assert not should_compress(LONG_REPLY, "give me the full detailed write-up")
    assert not should_compress(LONG_REPLY, "walk me through it step by step")
    assert not should_compress(LONG_REPLY, "explain", threshold=0)
    assert not should_compress("", "explain")


# -- compress pass: behaviour -------------------------------------------------


@pytest.mark.asyncio
async def test_compress_rewrites_long_reply_and_passes_request_tail():
    seen = {}

    async def fake_model(message):
        seen["message"] = message
        return "Short answer. Outcome: deployed. Blocker: none."

    with patch.object(brevity, "_run_compress_model", fake_model):
        out = await maybe_compress_slack_reply(LONG_REPLY, "PREFIX " * 400 + "explain the ask")

    assert out == "Short answer. Outcome: deployed. Blocker: none."
    assert "explain the ask" in seen["message"]
    assert LONG_REPLY in seen["message"]
    assert "Want me to...?" in seen["message"]  # instruction to drop trailing offers


@pytest.mark.asyncio
async def test_compress_skips_short_replies_without_calling_model():
    model = AsyncMock()
    with patch.object(brevity, "_run_compress_model", model):
        assert await maybe_compress_slack_reply("All good.", "confirm") == "All good."
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_compress_honours_explicit_detail_request_even_with_flow_prefix():
    model = AsyncMock()
    with patch.object(brevity, "_run_compress_model", model):
        out = await maybe_compress_slack_reply(LONG_REPLY, "TRIAGE PREAMBLE\n\nMessage:\nsend me the detailed report")
    assert out == LONG_REPLY
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("cli rc=1"), asyncio.TimeoutError()])
async def test_compress_falls_back_to_original_on_failure(failure):
    with patch.object(brevity, "_run_compress_model", AsyncMock(side_effect=failure)):
        assert await maybe_compress_slack_reply(LONG_REPLY, "explain") == LONG_REPLY


@pytest.mark.asyncio
@pytest.mark.parametrize("rewritten", ["", "   ", LONG_REPLY + " and more"])
async def test_compress_falls_back_when_model_output_is_empty_or_not_shorter(rewritten):
    with patch.object(brevity, "_run_compress_model", AsyncMock(return_value=rewritten)):
        assert await maybe_compress_slack_reply(LONG_REPLY, "explain") == LONG_REPLY


# -- handler wiring -----------------------------------------------------------


async def _events(*chunks):
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_stream_response_compresses_final_text_with_prompt():
    client = MagicMock()
    client.reactions_remove = AsyncMock()
    client.chat_postMessage = AsyncMock()

    with patch("slack_app.handlers.maybe_compress_slack_reply", AsyncMock(return_value="short")) as compress:
        await _stream_response(client, "C1", "1.0", "2.0", _events("draft", LONG_REPLY), prompt="explain the ask")

    compress.assert_awaited_once_with(LONG_REPLY, "explain the ask")
    client.chat_postMessage.assert_awaited_once_with(channel="C1", text="short", thread_ts="1.0")


@pytest.mark.asyncio
async def test_handle_agent_request_forwards_prompt_to_stream_response():
    from slack_app.handlers import _handle_agent_request

    client = MagicMock()
    client.reactions_add = AsyncMock()
    with patch("slack_app.handlers.is_draining", return_value=False), \
         patch("slack_app.handlers.get_db", return_value=None), \
         patch("slack_app.handlers.stream_agent", return_value=_events("ok")), \
         patch("slack_app.handlers._stream_response", AsyncMock()) as stream:
        await _handle_agent_request(client, "C1", "1.0", "1.0", "explain the ask", "", [], "slack_flow", "U1")

    assert stream.await_args.kwargs["prompt"] == "explain the ask"
