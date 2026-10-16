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
    numbered = ["## 1. Problem statement", "## 2. Goals", "## 3. Non-goals", "## 4. Scale and parameters",
                "## 5. Requirements and invariants", "## 6. Proposed architecture", "## 7. Components",
                "## 8. APIs and config", "## 9. Data model", "## 10. Semantics", "## 11. Security and abuse",
                "## 12. Observability and testing", "## 13. Rollout and migration",
                "## 14. Alternatives considered", "## 16. Open questions and follow-ups"]
    positions = [PLAN_MODE_BLOCK.index(section) for section in numbered]
    assert positions == sorted(positions), "sections out of order"
    for header in ("**Author** Loma", "**Status**", "**Reviewers**", "**PR**", "**Ticket**", "**Decision asked:**"):
        assert header in PLAN_MODE_BLOCK


def test_plan_tables_use_the_teams_columns():
    for columns in ("| Parameter | Value | Source |", "| ID | Invariant |", "| Layer | File | Change | PR |",
                    "| Surface | Before | After |", "| Store | Record | Use in this design |", "| Case | Behaviour |",
                    "| Threat | Today | With this change | Mitigation |", "| Layer | Where | Cases |",
                    "| PR | Scope | Safe alone because |", "| Option | What it is | Why not |"):
        assert columns in PLAN_MODE_BLOCK
    assert "keep every other\nsection's number" in PLAN_MODE_BLOCK


def test_plan_mode_asks_for_evidence_and_diagrams_in_a_four_backtick_fence():
    assert "never invent files" in PLAN_MODE_BLOCK
    for kind in ("flowchart LR", "sequenceDiagram", "stateDiagram-v2", "erDiagram", "```mermaid"):
        assert kind in PLAN_MODE_BLOCK
    assert "````plan" in PLAN_MODE_BLOCK
    assert '"**Changed:** <what changed>"' in PLAN_MODE_BLOCK


def test_four_backtick_plan_keeps_its_mermaid_diagrams():
    plan = "# Alerts\n\n## How it flows\n```mermaid\nsequenceDiagram\n  A->>B: event\n```\n\n## Phases\n1. **One**: ship"
    [artifact] = _detect_artifacts(f"Here's the plan.\n\n````plan\n{plan}\n````\n\nApprove when ready.")
    assert artifact["language"] == "plan" and artifact["content"] == plan
    assert artifact["title"] == "Alerts"


def test_longer_fences_close_only_on_the_same_length():
    text = "`````markdown\n" + "line\n" * 10 + "````\nstill inside\n`````\n"
    [artifact] = _detect_artifacts(text)
    assert artifact["content"].endswith("still inside")


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
