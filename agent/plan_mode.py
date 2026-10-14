"""Plan mode: the agent researches read-only and proposes a plan for review.

The dashboard sends ``plan_mode: true``. The instruction block travels in the
run's conversation context, so it reaches every runtime and every path (local,
remote workers, deploy-queued runs) without new parameters. Claude runs also
switch the SDK client into its own read-only ``plan`` permission mode.

The plan reaches the dashboard as an artifact with language ``plan``: from a
fenced ```plan block, or from Claude's ExitPlanMode tool call.
"""

import json

PLAN_MODE_MARKER = "[Plan Mode: on]"

CLARIFY_FORMAT = (
    ':::clarify\n'
    '{"questions": [{"question": "Which platforms first?", "multiSelect": true, '
    '"options": [{"label": "Web", "description": "Our web SDK"}, {"label": "Android"}, {"label": "iOS"}]}]}\n'
    ':::'
)

# Section order follows the team's solution-document template, trimmed for a
# plan reviewed in chat. Sections that don't apply are dropped, not padded.
PLAN_SECTIONS = """# <Title>
**Summary:** 3-5 sentences: the problem, the proposed solution, and the outcome.
## Problem and goal
## Scope
Must have / Nice to have / Out of scope.
## Current state
How it works today, only what the change touches.
## Proposed solution
The high-level design: components, responsibilities, data flow and contracts
between them. A short diagram (text) if it helps.
## Key decisions
Each with the alternative considered and why it lost.
## Risks and edge cases
| Risk | Likelihood | Impact | Mitigation |
## Rollout and verification
Order of work, how it ships (flags, migrations, rollback), how we'll know it works.
## Open questions"""

PLAN_MODE_BLOCK = f"""{PLAN_MODE_MARKER}
The user turned on plan mode for this message. Plan, do not act.

Step 1. Understand the requirement. Research read-only as needed: read files,
search, query, look things up. Do NOT change anything: no file edits, no
state-changing commands, no messages, comments, tickets, deploys, or writes to
any external system.

Step 2. If anything that would change the plan is ambiguous or missing, ask
before planning. Ask 1-4 focused questions with suggested answers, as a block
in exactly this format (valid JSON inside), then stop and wait:
{CLARIFY_FORMAT}
Don't ask about things you can find out yourself, and don't ask again once the
answers are in the conversation. If nothing material is unclear, skip this step.

Step 3. Write the plan as a high-level solution document, not a code change
list: describe components, responsibilities, interfaces and data flow; avoid
file-by-file edits, code snippets and line-level detail unless the user asks.
Use this structure:
{PLAN_SECTIONS}

Present the plan by calling ExitPlanMode with it if you have that tool;
otherwise write it as a single fenced code block with the language `plan`
(no ``` fences inside it: indent any diagram by four spaces instead).
When revising after feedback, present the full updated plan, not a diff.
After presenting the plan, stop. The user reviews it in the dashboard and
either asks for changes or approves it, which ends plan mode."""


def with_plan_mode(conversation_context: str) -> str:
    """Append the plan-mode instructions, last so they sit next to the message."""
    if not conversation_context:
        return PLAN_MODE_BLOCK
    return f"{conversation_context}\n\n{PLAN_MODE_BLOCK}"


def plan_mode_requested(conversation_context: str | None) -> bool:
    return bool(conversation_context) and PLAN_MODE_MARKER in conversation_context


def plan_from_tool_call(tool_name: str, tool_input: object) -> str | None:
    """The plan text from Claude's ExitPlanMode tool call, if this is one."""
    if tool_name != "ExitPlanMode" or not isinstance(tool_input, dict):
        return None
    plan = tool_input.get("plan")
    return plan.strip() if isinstance(plan, str) and plan.strip() else None


def clarify_from_tool_call(tool_name: str, tool_input: object) -> str | None:
    """Claude's AskUserQuestion call as a `:::clarify` block the dashboard renders
    as a question card. Returned as text so it is persisted with the turn."""
    if tool_name != "AskUserQuestion" or not isinstance(tool_input, dict):
        return None
    questions = []
    for q in tool_input.get("questions") or []:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        options = [
            {"label": str(o.get("label")), **({"description": str(o["description"])} if o.get("description") else {})}
            for o in (q.get("options") or []) if isinstance(o, dict) and o.get("label")
        ]
        questions.append({
            "question": str(q["question"]),
            "multiSelect": bool(q.get("multiSelect")),
            "options": options,
        })
    if not questions:
        return None
    return ":::clarify\n" + json.dumps({"questions": questions}) + "\n:::"
