"""Alert messages edited to `resolved` (Grafana IRM style) get a reaction and a thread note."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slack_app.handlers import _alert_status, _handle_alert_status_change, register_handlers

ALERTS_CFG = {"name": "alerts", "source": "slack_flow", "flow_id": "f1",
              "prompt_prefix": "", "allow_bot_messages": True}
TS = "1700000000.000100"


def _alert(status, **extra):
    msg = {"type": "message", "bot_id": "B_GRAFANA", "ts": TS,
           "text": f"*<https://irm/alert|#369 Airflow Prod Task Failures>* via Grafana *Status*: {status}\n*Labels:*"}
    msg.update(extra)
    return msg


def _edit(old, new, channel="C_ALERTS"):
    return {"type": "message", "subtype": "message_changed", "channel": channel,
            "ts": "1700000999.000001", "message": new, "previous_message": old}


def _client(reaction_error=None):
    client = MagicMock()
    client.reactions_add = AsyncMock(side_effect=reaction_error)
    client.chat_postMessage = AsyncMock()
    return client


async def _run(event, client, cfg=ALERTS_CFG):
    with patch("slack_app.handlers.get_channel_config", AsyncMock(return_value=cfg)):
        await _handle_alert_status_change(client, event)


@pytest.mark.parametrize("text,expected", [
    ("... *Status*: firing\n*Labels:*", "firing"),
    ("... *Status*: Resolved", "resolved"),
    ("... *Status*: acknowledged", "acknowledged"),
    (":x: *E2E Suite (us1) - Failed*", None),
    ("On-call shift for schedule Oncall Rotation has changed", None),
    ("", None),
])
def test_alert_status_parsing(text, expected):
    assert _alert_status({"text": text}) == expected


def test_alert_status_handles_missing_message():
    assert _alert_status(None) is None
    assert _alert_status({"text": None}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("old_status", ["firing", "acknowledged"])
async def test_open_to_resolved_reacts_and_posts_once(old_status):
    client = _client()
    await _run(_edit(_alert(old_status), _alert("resolved")), client)
    client.reactions_add.assert_awaited_once_with(channel="C_ALERTS", timestamp=TS, name="white_check_mark")
    client.chat_postMessage.assert_awaited_once()
    kwargs = client.chat_postMessage.await_args.kwargs
    assert kwargs["thread_ts"] == TS and kwargs["channel"] == "C_ALERTS"
    assert f"`{old_status}`" in kwargs["text"] and "Resolved" in kwargs["text"]


@pytest.mark.asyncio
async def test_duplicate_delivery_is_skipped_via_already_reacted():
    client = _client(reaction_error=Exception("The request to the Slack API failed. error: already_reacted"))
    await _run(_edit(_alert("firing"), _alert("resolved")), client)
    client.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_reaction_failure_still_posts_note():
    client = _client(reaction_error=Exception("missing_scope"))
    await _run(_edit(_alert("firing"), _alert("resolved")), client)
    client.chat_postMessage.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("old,new", [
    (_alert("resolved"), _alert("resolved")),          # later edit of an already-resolved alert
    (_alert("firing"), _alert("firing")),              # alert-count edit while firing
    (_alert("firing"), _alert("acknowledged")),        # ack, not a resolve
    (_alert("resolved"), _alert("firing")),            # re-fire: no new investigation
    ({}, _alert("resolved")),                          # no previous_message
    ({"text": "E2E Suite"}, {"text": "E2E Suite v2", "bot_id": "B1", "ts": TS}),  # not an alert
    (_alert("firing"), _alert("resolved", bot_id=None)),                          # human edit
    (_alert("firing"), _alert("resolved", thread_ts="1699999999.000001")),        # thread reply
])
async def test_ignored_edits(old, new):
    client = _client()
    await _run(_edit(old, new), client)
    client.reactions_add.assert_not_awaited()
    client.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cfg", [None, {**ALERTS_CFG, "allow_bot_messages": False}])
async def test_unmonitored_or_bot_disallowed_channel_is_ignored(cfg):
    client = _client()
    await _run(_edit(_alert("firing"), _alert("resolved")), client, cfg=cfg)
    client.reactions_add.assert_not_awaited()
    client.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_routes_edits_without_starting_agent_run():
    callbacks = {}
    app = MagicMock()

    def register(name):
        def decorate(fn):
            callbacks[name] = fn
            return fn
        return decorate

    app.event.side_effect = register
    register_handlers(app)
    client = _client()
    with patch("slack_app.handlers.get_channel_config", AsyncMock(return_value=ALERTS_CFG)), \
         patch("slack_app.handlers._handle_agent_request", AsyncMock()) as run:
        await callbacks["message"](_edit(_alert("firing"), _alert("resolved")), client, AsyncMock())
    run.assert_not_awaited()
    client.reactions_add.assert_awaited_once()
    client.chat_postMessage.assert_awaited_once()
