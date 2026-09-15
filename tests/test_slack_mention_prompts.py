"""Exercise real mention routing without sending Slack messages or running agents."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_app.handlers import register_handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["confirm", "any update?", "investigate this failure", "explain in detail with code"])
@pytest.mark.parametrize("monitored", [True, False])
@pytest.mark.parametrize("thread_ts", [None, "100.000001", "101.000001"])
async def test_mention_prompt_routing(message, monitored, thread_ts):
    callbacks = {}
    app = MagicMock()

    def register(name):
        def decorate(fn):
            callbacks[name] = fn
            return fn
        return decorate

    app.event.side_effect = register
    register_handlers(app)
    config = {"prompt_prefix": "RUN FULL TRIAGE: ", "source": "slack_bugs", "name": "bugs", "flow_id": "flow-1"} if monitored else None
    event = {"channel": "C1", "user": "U1", "text": f"<@UBOT> {message}", "ts": "101.000001"}
    if thread_ts:
        event["thread_ts"] = thread_ts
    with patch("slack_app.handlers.get_channel_config", AsyncMock(return_value=config)), \
         patch("slack_app.handlers.get_thread_context", AsyncMock(return_value=("Earlier investigation", []))) as context, \
         patch("slack_app.handlers._handle_agent_request", AsyncMock()) as run:
        await callbacks["app_mention"](event, MagicMock(), AsyncMock())

    args = run.await_args.args
    followup = thread_ts is not None and thread_ts != event["ts"]
    expected = "RUN FULL TRIAGE: " + message if monitored and not followup else message
    assert args[4] == expected
    assert args[5] == ("Earlier investigation" if thread_ts else "")
    assert args[7] == ("slack_bugs" if monitored else "slack_mention")
    assert run.await_args.kwargs["flow_id"] == ("flow-1" if monitored else None)
    assert context.await_count == bool(thread_ts)
