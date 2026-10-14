from agent.client import _detect_artifacts
from agent.plan_mode import (
    PLAN_MODE_BLOCK,
    plan_from_tool_call,
    plan_mode_requested,
    with_plan_mode,
)


def test_plan_block_is_appended_last_and_detected():
    context = with_plan_mode("User: hi\nAssistant: hello")
    assert context.endswith(PLAN_MODE_BLOCK)
    assert plan_mode_requested(context)


def test_plan_block_alone_when_no_history():
    assert with_plan_mode("") == PLAN_MODE_BLOCK


def test_plan_mode_off_without_marker():
    assert not plan_mode_requested("User: please plan this")
    assert not plan_mode_requested(None)


def test_short_plan_fence_still_becomes_an_artifact():
    text = "Here's the plan:\n\n```plan\n# Ship win cards\n1. Add flag\n```\n"
    [artifact] = _detect_artifacts(text)
    assert artifact["language"] == "plan"
    assert artifact["title"] == "Ship win cards"
    assert artifact["content"].startswith("# Ship win cards")


def test_short_code_fence_is_not_promoted():
    assert _detect_artifacts("```python\nprint(1)\n```") == []


def test_plan_from_exit_plan_mode():
    assert plan_from_tool_call("ExitPlanMode", {"plan": "  # Plan\n1. Do it  "}) == "# Plan\n1. Do it"


def test_other_tools_and_empty_plans_are_ignored():
    assert plan_from_tool_call("Bash", {"plan": "# Plan"}) is None
    assert plan_from_tool_call("ExitPlanMode", {"plan": "   "}) is None
    assert plan_from_tool_call("ExitPlanMode", "not a dict") is None


def test_plan_mode_asks_before_planning_and_plans_at_a_high_level():
    assert ":::clarify" in PLAN_MODE_BLOCK
    assert "high-level solution document" in PLAN_MODE_BLOCK
    for section in ("## Scope", "## Proposed solution", "## Risks and edge cases", "## Rollout and verification"):
        assert section in PLAN_MODE_BLOCK


def test_ask_user_question_becomes_a_clarify_block():
    import json

    from agent.plan_mode import clarify_from_tool_call

    text = clarify_from_tool_call("AskUserQuestion", {"questions": [
        {"question": "Which platforms?", "header": "Platforms", "multiSelect": True,
         "options": [{"label": "Web", "description": "SDK"}, {"label": "iOS"}]},
        {"header": "no question text"},
    ]})
    assert text.startswith(":::clarify\n") and text.endswith("\n:::")
    payload = json.loads(text[len(":::clarify\n"):-len("\n:::")])
    assert payload == {"questions": [{"question": "Which platforms?", "multiSelect": True,
                                      "options": [{"label": "Web", "description": "SDK"}, {"label": "iOS"}]}]}
    assert clarify_from_tool_call("Bash", {"questions": []}) is None
    assert clarify_from_tool_call("AskUserQuestion", {"questions": []}) is None
