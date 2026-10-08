"""Plan mode: the agent researches read-only and proposes a plan for review.

The dashboard sends ``plan_mode: true``. The instruction block travels in the
run's conversation context, so it reaches every runtime and every path (local,
remote workers, deploy-queued runs) without new parameters. Claude runs also
switch the SDK client into its own read-only ``plan`` permission mode.

The plan reaches the dashboard as an artifact with language ``plan``: from a
fenced ```plan block, or from Claude's ExitPlanMode tool call.
"""

PLAN_MODE_MARKER = "[Plan Mode: on]"

PLAN_MODE_BLOCK = f"""{PLAN_MODE_MARKER}
The user turned on plan mode for this message. Plan, do not act:
- Research read-only: read files, search, query, and look things up as needed.
- Do NOT change anything: no file edits, no state-changing commands, no messages,
  comments, tickets, deploys, or writes to any external system.
- When ready, present the complete plan in markdown, starting with a `# Title`
  heading, then numbered steps, risks, and how you'll verify it. If you have the
  ExitPlanMode tool, call it with the plan. Otherwise write the plan as a single
  fenced code block with the language `plan`.
- When revising after feedback, present the full updated plan, not a diff.
- After presenting the plan, stop. The user reviews it in the dashboard and
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
