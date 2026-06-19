"""Canonical Fibonacci sizing rubric for Linear tickets.

Single source of truth for the points-sizing prompt. Consumed by:
  - webhooks/linear.py — fires on `Released to Production` / `Done` state entry
  - flows collection in loma_observability — "Daily Linear Ticket Sizing" sweep
  - optional DB-backed Loma skills that mirror this rubric

If you mirror this rubric into skills, update those skill files from the
dashboard or `tools/loma_skills.py` when this source changes.
"""

ESTIMATION_RUBRIC = """\
1 point  - Single module, leaf code, no migrations, no SDK release.
           Dashboard bug fix, copy change, config tweak.

2 points - Single module, touches some shared code OR adds a new straightforward
           feature end-to-end in one layer. New API endpoint + dashboard page.

3 points - Crosses 2 systems (e.g., backend + dashboard), or single SDK change
           that requires a release. Moderate diff, well-understood pattern.

5 points - Crosses 2-3 systems, touches core abstractions, involves migrations or
           SDK releases, or has meaningful uncertainty. New campaign type
           end-to-end, SDK behavior change.

8 points - Crosses multiple systems including SDKs, involves breaking changes or
           data model shifts, requires sequenced deploys, high coordination.
           Think GCash-level integrations, infra migrations.
"""


# Loop-prevention marker — MUST match webhooks/linear.py:AGENT_COMMENT_MARKER.
# Any Linear comment authored by the agent starts with this so the
# comment-trigger webhook handler can ignore it (otherwise commenting on a
# ticket would re-fire the webhook in a loop).
AGENT_COMMENT_MARKER = "<!-- loma -->"


_COMMON_GUARD = """\
Idempotency guard (CRITICAL):
- BEFORE writing an estimate, re-fetch the ticket via `mcp__linear__get_issue`.
- If the ticket's current `estimate` is anything other than null/0, SKIP it and
  do not call `save_issue` for that ticket. We never overwrite a human's value.

Default when there is no PR:
- If no PR can be found that references the ticket, assign **1 point** (smallest
  bucket) rather than refusing. Note "no PR found" in the reasoning.
"""


_PER_TICKET_WRITE_STEPS = """\
For EACH ticket you size, complete BOTH of these writes in order:

a) **Set the estimate** via `mcp__linear__save_issue` with the ticket's id
   and the chosen estimate value (one of 1, 2, 3, 5, 8).

b) **Post a comment** on the ticket via `mcp__linear__save_comment` so
   engineers see why their estimate was set. The comment body MUST start with
   the literal marker `{marker}` on its own line (this prevents the
   comment-trigger webhook from looping). Format:

   ```
   {marker}
   🤖 Auto-sized as **N pts**.

   *Reasoning:* <one to two lines: which systems/modules touched, PR diff
   scope, key uncertainty>.

   *Rubric:* `prompts/estimation_rubric.py` · 1/2/3/5/8 (Fibonacci).
   ```

Do NOT attempt to write to MongoDB yourself — the audit-log record is
written by the Python caller after parsing your final output line.
"""


_SINGLE_TICKET_TEMPLATE = """\
You are an engineering-estimation agent. Size ONE Linear ticket using this rubric:

{rubric}

Ticket to size: **{identifier}**
Trigger context (use this as the `trigger` field in the audit log): `{trigger}`

Workflow:
1. Read the ticket via `mcp__linear__get_issue` — capture title, description, state, current `estimate`.
2. {guard}
3. Search GitHub PRs that reference `{identifier}` (try `mcp__github__search_pull_requests`
   with q=`{identifier} in:title,body org:example-org`). For each related PR, inspect
   `files_changed`, additions/deletions, and which systems/modules are touched.
4. Apply the rubric. Pick exactly one of: 1, 2, 3, 5, 8.
5. Perform the two writes in order:

{write_steps}

6. As your VERY LAST line, output exactly this format (the Python caller
   parses this to write the audit-log record — keep the spacing and the
   `->` literal):

   `{identifier} -> N pts | <one-line reason>`

   Example: `ISSUE-3502 -> 3 pts | Dashboard + api-go change, 8 files in PR #648.`

If the guard tripped (ticket already had an estimate), do NOT post a comment.
Return as your last line: `{identifier} -> skipped (already sized: K pts)`.
"""


_SWEEP_TEMPLATE = """\
You are an engineering-estimation agent. Run a daily sweep to assign Fibonacci
estimates to Linear tickets that lack one.

{rubric}

Sweep scope:
- Target classes: {classes}
- Find tickets matching ALL of:
    a) (any of the target class labels),
    b) `estimate IS NULL`,
    c) state name IS EXACTLY one of `Released to Production` OR `Done`.
- Cap at the top {cap} oldest-updated tickets per run.

Why only RtP / Done: sizing a ticket before it ships is guesswork — the rubric
relies on PR scope. Mid-flight tickets (`In Design`, `In Progress`,
`Ready for Dev`, `QA done`, `Merged To Staging`) either have no PR yet or have
incomplete scope; the resulting estimates were noisy and the agent's reasoning
hallucinated topics that didn't match the ticket. This narrows the sweep to
the same surface the webhook trigger uses (state-entry to RtP/Done), so the
sweep is purely a backstop for tickets the webhook missed.

For EACH ticket you size, the `trigger` field for the audit-log doc is: `sweep`.

Per-ticket workflow:
1. {guard}
2. When you call `mcp__linear__get_issue` for each ticket, also capture the
   ticket's title and assignee name — you'll need them in the Slack output.
3. Search GitHub PRs that reference the ticket identifier (`mcp__github__search_pull_requests`
   with q=`<identifier> in:title,body org:example-org`). Inspect PR diff scope:
   files_changed count, systems/modules touched.
4. Apply the rubric. Pick exactly one of: 1, 2, 3, 5, 8.
5. Perform the two writes in order:

{write_steps}

Slack output (mrkdwn). After sizing all eligible tickets, produce a single
summary message in EXACTLY this format. The Python post-processor parses
each bullet block into the audit log, so format matters — emit bullets (not
Markdown tables). Indented sub-lines must use a single tab or 4 spaces.

```
*🤖 Daily Linear Ticket Sizing — N tickets estimated*

• `ISSUE-XXXX` — *3 pts*
    _What:_ <one-line description of what the ticket is about, ≤ 100 chars>
    _Owner:_ <assignee full name, or "unassigned">
    _Why:_ <one to two lines applying the rubric — which systems touched,
            PR diff scope, key uncertainty>

• `ISSUE-YYYY` — *5 pts*
    _What:_ ...
    _Owner:_ ...
    _Why:_ ...
```

Rules for the output:
- The bullet header line must be exactly: bullet (•), space, backtick,
  identifier, backtick, space, em-dash (—), space, asterisk, `N pts`, asterisk.
  Example: `• `ISSUE-3502` — *3 pts*` (where ` is the literal backtick character).
- The three sub-lines (`_What:_`, `_Owner:_`, `_Why:_`) must appear in this order,
  on separate lines, each starting with one tab or 4 spaces of indentation.
- Do NOT use a Markdown table (`| ... | ... |`) — the parser does not handle
  tables; bullets only.

If you find zero tickets that match the sweep criteria, output exactly:
__EMPTY__

(That sentinel suppresses the Slack post. Do not output anything else in that case.)

If a ticket was skipped due to the idempotency guard, do NOT post a comment, do
NOT write an audit-log doc, and do NOT include it in the Slack summary — only
list tickets you actually sized this run.
"""


def _write_steps() -> str:
    return _PER_TICKET_WRITE_STEPS.format(marker=AGENT_COMMENT_MARKER)


def build_sizing_prompt(
    identifier: str | None = None,
    target_classes: list[str] | None = None,
    cap: int = 20,
    trigger: str = "webhook",
) -> str:
    """Assemble the full sizing prompt.

    Args:
      identifier: When given, prompt is scoped to one Linear ticket (e.g. "ISSUE-3402").
                  This is the webhook-handler / CLI mode. No Slack output expected.
      target_classes: Used only in sweep mode (identifier=None). Defaults to
                      ["Roadmap", "Adhoc", "Bug"]. Tickets matching any of these
                      labels are eligible.
      cap: Max tickets per sweep run, to bound output length and cost. Sweep mode only.
      trigger: String written into the audit-log doc's `trigger` field. Use
                      `"webhook"` from the Linear state-transition handler, `"cli"`
                      from scripts/size_one_ticket.py, or `"sweep"` (implicit in
                      sweep mode — caller doesn't need to pass it).

    Returns:
      Full prompt string ready for `stream_agent(...)` or embedding in a flow doc.
    """
    if identifier:
        return _SINGLE_TICKET_TEMPLATE.format(
            rubric=ESTIMATION_RUBRIC,
            identifier=identifier,
            guard=_COMMON_GUARD,
            write_steps=_write_steps(),
            trigger=trigger,
        )
    classes = target_classes or ["Roadmap", "Adhoc", "Bug"]
    return _SWEEP_TEMPLATE.format(
        rubric=ESTIMATION_RUBRIC,
        classes=", ".join(f"`{c}`" for c in classes),
        cap=cap,
        guard=_COMMON_GUARD,
        write_steps=_write_steps(),
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1].startswith(("ISSUE-", "PB-", "DES-")):
        print(build_sizing_prompt(identifier=sys.argv[1], trigger="cli"))
    else:
        print(build_sizing_prompt())
