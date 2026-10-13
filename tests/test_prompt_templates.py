from unittest.mock import AsyncMock, MagicMock

import pytest

from api.prompt_templates import _validate, expand_command, parse_command, render


@pytest.mark.parametrize("message,expected", [
    ("/review https://github.com/o/r/pull/1", ("review", "https://github.com/o/r/pull/1")),
    ("  /summary  ", ("summary", "")),
    ("/plan add SSO\nfor admins", ("plan", "add SSO\nfor admins")),
    ("review this", None),
    ("/ not a command", None),
    ("/Review x", None),
])
def test_parse_command(message, expected):
    assert parse_command(message) == expected


def test_render_places_input_or_appends_it():
    assert render("Review {input} now", "PR-1") == "Review PR-1 now"
    assert render("Be brief.", "PR-1") == "Be brief.\n\nPR-1"
    assert render("Be brief.", "") == "Be brief."


def _db(user_template=None):
    db = MagicMock()
    db.__getitem__.return_value.find_one = AsyncMock(return_value=user_template)
    return db


@pytest.mark.asyncio
async def test_builtin_review_expands_with_the_link():
    cmd = await expand_command(_db(), "a@b.c", "/review https://github.com/o/r/pull/7")
    assert cmd.name == "review" and not cmd.plan_mode
    assert "https://github.com/o/r/pull/7" in cmd.prompt
    assert "code-review" in cmd.prompt


@pytest.mark.asyncio
async def test_plan_command_turns_on_plan_mode_and_sends_only_the_requirement():
    cmd = await expand_command(_db(), "a@b.c", "/plan add SSO for admins")
    assert cmd.plan_mode and cmd.prompt == "add SSO for admins"


@pytest.mark.asyncio
async def test_user_template_is_looked_up_by_owner():
    db = _db({"name": "standup", "body": "Write my standup from {input}", "plan_mode": False})
    cmd = await expand_command(db, "a@b.c", "/standup yesterday's PRs")
    assert cmd.prompt == "Write my standup from yesterday's PRs"
    db.__getitem__.return_value.find_one.assert_awaited_once_with({"owner": "a@b.c", "name": "standup"})


@pytest.mark.asyncio
async def test_unknown_commands_are_sent_as_typed():
    assert await expand_command(_db(None), "a@b.c", "/etc/hosts looks wrong") is None
    assert await expand_command(None, "a@b.c", "/nope") is None


@pytest.mark.parametrize("payload,error", [
    ({"name": "review", "body": "x"}, "built-in"),
    ({"name": "Bad Name", "body": "x"}, "lowercase"),
    ({"name": "ok", "body": "  "}, "required"),
    ({"name": "ok", "body": "x" * 9000}, "limited"),
])
def test_validation_rejects_bad_templates(payload, error):
    fields, message = _validate(payload, creating=True)
    assert fields is None and error in message


def test_validation_normalises_name_and_fields():
    fields, error = _validate({"name": "/Standup", "body": " Do it ", "plan_mode": True, "description": "d"}, creating=True)
    assert error is None
    assert fields == {"name": "standup", "body": "Do it", "description": "d", "plan_mode": True}
