"""Plan mode: the agent researches read-only and proposes a plan for review.

The dashboard sends ``plan_mode: true``. The instruction block travels in the
run's conversation context, so it reaches every runtime and every path (local,
remote workers, deploy-queued runs) without new parameters. Claude runs also
switch the SDK client into its own read-only ``plan`` permission mode.

The plan reaches the dashboard as an artifact with language ``plan``: from a
fenced ````plan block (four backticks, so Mermaid fences inside survive), or
from Claude's ExitPlanMode tool call.
"""

import json

PLAN_MODE_MARKER = "[Plan Mode: on]"

CLARIFY_FORMAT = (
    ':::clarify\n'
    '{"questions": [{"question": "Which platforms first?", "multiSelect": true, '
    '"options": [{"label": "Web", "description": "Our web SDK"}, {"label": "Android"}, {"label": "iOS"}]}]}\n'
    ':::'
)

# One structure for every plan and solution doc, the same numbered RFC skeleton
# the team uses outside Loma, so a reader always finds things in the same place.
# The dashboard reads it: the meta line and "Decision asked" go in the panel's
# header, ## headings make its outline, section 13 gives the run's checklist and
# section 16 the questions for the reviewer.
PLAN_SECTIONS = """# <Title>

**Author** Loma · **Status** Draft, awaiting review · **Reviewers** <who asked> · **PR** not raised · **Ticket** <link, or not created>

<Lead: 2-4 sentences. What we add or change, and the outcome.>

**Decision asked:** <the one thing the reviewer approves, e.g. the semantics in section 10>

## 1. Problem statement
What is broken or missing today, with evidence (file:line, numbers, the customer's words). A table when comparing cases.
## 2. Goals
A numbered list of outcomes.
## 3. Non-goals
What this deliberately leaves out, and why.
## 4. Scale and parameters
| Parameter | Value | Source |
## 5. Requirements and invariants
| ID | Invariant |  (I1, I2, ...: what must stay true, including "setting off = today's behaviour")
## 6. Proposed architecture
One or two sentences, then the architecture diagram, then what it shows.
## 7. Components
| Layer | File | Change | PR |
## 8. APIs and config
| Surface | Before | After |
## 9. Data model
| Store | Record | Use in this design |  (and whether a migration is needed)
## 10. Semantics
The exact behaviour: ordering, consistency, edge cases, formulas. A sequence diagram for the main interaction. Then **Failure modes**: | Case | Behaviour |
## 11. Security and abuse
| Threat | Today | With this change | Mitigation |
## 12. Observability and testing
**Metric**, **Logs**, then **Tests**: | Layer | Where | Cases |
## 13. Rollout and migration
| PR | Scope | Safe alone because |, then a **Checklist** (- [ ] items), then **Rollback.**
## 14. Alternatives considered
| Option | What it is | Why not |
## 16. Open questions and follow-ups
**Open questions** (- [ ] each with your suggested answer), **Follow-ups** (not in this change), **Sources** (what you read)."""

DIAGRAM_RULES = """Diagrams are what make a plan reviewable at a glance; the dashboard draws
Mermaid fenced blocks inline. Use them where they explain more than prose:
- an architecture diagram (`flowchart LR`) in section 6 whenever two or more components are involved;
- a `sequenceDiagram` in section 10 for the main interaction between components, services or people;
- a `stateDiagram-v2` when something has a lifecycle, an `erDiagram` when data changes shape.
Keep each to about 12 nodes with short labels. Quote labels that contain punctuation,
e.g. A["Sync worker (existing)"], and keep # and ; out of labels (Mermaid reads them as code). Mark new parts with `classDef new fill:#E7F3EF,stroke:#055147`
and `class X new`. Write diagrams as ```mermaid fenced blocks inside the plan."""

PLAN_MODE_BLOCK = f"""{PLAN_MODE_MARKER}
The user turned on plan mode for this message. Plan, do not act.

Step 1. Research before you plan. Read the code, configs, docs, tickets and data
that the change touches, read-only: read files, search, query, look things up.
Do NOT change anything: no file edits, no state-changing commands, no messages,
comments, tickets, deploys, or writes to any external system. Ground the plan in
what you found; never invent files, APIs or numbers. If you couldn't verify
something, say so in section 16.

Step 2. If anything that would change the plan is ambiguous or missing, ask
before planning. Ask 1-4 focused questions with suggested answers, as a block
in exactly this format (valid JSON inside), then stop and wait:
{CLARIFY_FORMAT}
Don't ask about things you can find out yourself, and don't ask again once the
answers are in the conversation. If nothing material is unclear, skip this step.

Step 3. Write the plan as a high-level solution document in exactly this
structure: the same numbered sections, in this order, with these table columns,
every time. Each section's first sentence is its point. Describe components,
responsibilities, interfaces and data flow; avoid code snippets unless the user
asks. Match depth to the ask: drop a section that truly has nothing to say
(review findings, section 15, is for reviews only), but keep every other
section's number, so section 10 is always Semantics:
{PLAN_SECTIONS}

{DIAGRAM_RULES}

Present the plan by calling ExitPlanMode with it if you have that tool;
otherwise write it as one fenced block opened with FOUR backticks and the
language `plan` (````plan) and closed with four backticks, so the ```mermaid
blocks inside it stay intact. When revising after feedback, present the full
updated plan, not a diff, with a "**Changed:** <what changed>" line under the
"Decision asked" line.
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
