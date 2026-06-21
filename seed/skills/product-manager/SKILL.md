---
name: product-manager
description: Product management playbook — use when discovering recurring customer problems, writing user stories, prioritizing features with RICE scoring, planning roadmaps (Now/Next/Later), or drafting structured Linear tickets from cross-channel data (Pylon, Linear, Grain, Slack, HubSpot)
user-invocable: true
---

# Product Manager Playbook

A unified product management skill with 5 modes: Problem Discovery, User Story Writing, Prioritization, Roadmap Planning, and Linear Ticket Operations.

**Scope boundary**: This skill handles the *strategic product layer* — discovering patterns across channels, writing stories, prioritizing, and planning. It complements (does not replace):
- **feature-request-triage**: Investigates individual feature requests (docs/code lookup, workaround detection)
- **bug-triage**: Investigates individual bugs (root cause analysis, code search)
- **use-case-advisory**: Designs specific campaign/journey configurations for customers

---

## Mode Selection

Determine the mode based on the user's request:

| User Says | Mode |
|---|---|
| "What are the top problems?", "recurring issues", "what are customers asking for?", "pain points" | Problem Discovery |
| "Write a user story", "create stories for X", "acceptance criteria" | User Story Writing |
| "Prioritize these", "RICE score", "what should we build next?", "rank these features" | Prioritization |
| "Plan the roadmap", "what goes in next sprint?", "Now/Next/Later", "quarterly plan" | Roadmap Planning |
| "Create a ticket for X", "draft a Linear issue", "turn this into a ticket" | Linear Ticket Ops |
| "Help me plan what to build" (general) | Start with Problem Discovery, then flow through all modes |

If the mode is ambiguous, ask:

```
Which product management activity do you need help with?

1. *Problem Discovery* — Mine Pylon, Linear, Grain, Slack, and HubSpot for recurring customer pain points
2. *User Story Writing* — Write structured user stories with acceptance criteria
3. *Prioritization* — RICE-score and rank a set of features/problems
4. *Roadmap Planning* — Sequence priorities into Now/Next/Later buckets
5. *Linear Ticket Ops* — Draft and create structured Linear tickets
```

---

## Mode 1: Problem Discovery

### Purpose
Mine all available data channels to identify recurring customer problems, rank them by frequency and impact, and surface the top problems worth solving.

### Step 1: Gather Data from All Channels

Run these data-gathering queries in parallel:

#### 1a. Pylon Support Tickets
Load the **pylon-support** skill and search for recent tickets. Look for:
- Recurring themes in ticket subjects and messages
- Tickets tagged with specific categories
- High-volume ticket patterns over the last 30/60/90 days

#### 1b. Linear Issues
Use **Linear MCP** tools:
- Search for issues in "Product Backlog" team with state "Triage" or "Backlog"
- Search for issues labeled "feature-request" or "customer-request"
- Look for issues with multiple customer references or duplicates
- Check comment threads for additional context and customer names

```
Search queries to run:
- list_issues with team "Product Backlog", states: Triage, Backlog
- list_issues with label "feature-request"
- search for issues with high comment counts (indicates discussion/demand)
```

#### 1c. Grain Meeting Recordings
Load the **grain** skill and search for:
- Meetings mentioning "feature request", "pain point", "blocker", "need", "want"
- Customer call recordings from the last 30-60 days
- Action items from customer calls that reference product gaps

#### 1d. Slack Channels
Load the **slack-reader** skill and read recent messages from:
- `#feature-requests-clients` — direct customer feature requests
- `#bugs` — recurring bugs that indicate product gaps
- `#customer-feedback` — general customer feedback
- `#sales` or `#deals` — feature gaps blocking deals

#### 1e. HubSpot
Use **HubSpot MCP** tools to check:
- Deal notes mentioning "missing feature", "blocker", "competitor"
- Lost deal reasons related to product gaps
- Customer feedback logged in contact/company notes

### Step 2: Aggregate and Deduplicate

Group findings into problem themes. For each theme:

1. **Problem Statement** (use the format below):
   ```
   [Customer segment] struggles with [problem]
   when trying to [goal/job-to-be-done],
   which results in [negative outcome].
   ```

2. **Evidence**:
   - Number of unique customers mentioning it
   - Channels where it appeared (Pylon, Linear, Grain, Slack, HubSpot)
   - Specific customer names and quotes
   - Related Linear ticket IDs

3. **Frequency Score** (1-5):
   - 5: 10+ customers, multiple channels
   - 4: 5-9 customers, 2+ channels
   - 3: 3-4 customers, any channel
   - 2: 2 customers
   - 1: Single customer request

4. **Intensity Score** (1-5):
   - 5: Blocking revenue / causing churn
   - 4: Significant workflow disruption
   - 3: Moderate inconvenience, workaround exists
   - 2: Minor annoyance
   - 1: Nice-to-have

### Step 3: Rank Problems

Calculate **Impact Score** = Frequency x Intensity (max 25).

Present the top problems in a ranked table:

```
*Top Customer Problems — [Date Range]*

| # | Problem | Impact | Freq | Intensity | Customers | Channels | Linear |
|---|---------|--------|------|-----------|-----------|----------|--------|
| 1 | [problem] | 20 | 5 | 4 | [names] | Support, Calls, Slack | TICKET-123 |
| 2 | [problem] | 16 | 4 | 4 | [names] | Tracker, Slack | TICKET-456 |
| ... | ... | ... | ... | ... | ... | ... | ... |

*Data sources queried*: Pylon (last 60 days), Linear (Backlog), Grain (last 30 days), Slack (#feature-requests-clients, #bugs), HubSpot (open deals)
```

### Step 4: Deep-Dive (Optional)

If the user asks to go deeper on a specific problem:
- Pull all related Pylon tickets and summarize the thread
- Read full Grain transcripts mentioning the problem
- Search the codebase for related feature flags or partial implementations
- Check if any workaround exists (load **feature-request-triage** or **use-case-advisory**)

---

## Mode 2: User Story Writing

### Format: Mike Cohn + Gherkin

Write every user story in this structure:

```markdown
### [Story Title]

**As a** [specific user persona],
**I want to** [action/capability],
**So that** [measurable business outcome].

#### Acceptance Criteria (Gherkin)

**Scenario 1: [Happy path name]**
Given [precondition]
When [action]
Then [expected result]
And [additional expectation]

**Scenario 2: [Edge case name]**
Given [precondition]
When [action]
Then [expected result]

#### Notes
- [Implementation hints, constraints, or dependencies]
- [Related stories or tickets]
```

### INVEST Validation Checklist

Before finalizing any story, validate against INVEST:

| Criterion | Check | Pass? |
|---|---|---|
| **I**ndependent | Can be developed without depending on other stories in the same sprint | |
| **N**egotiable | Details can be discussed — not a rigid spec | |
| **V**aluable | Delivers value to the user or business | |
| **E**stimable | Team can estimate the effort | |
| **S**mall | Completable within one sprint (1-2 weeks) | |
| **T**estable | Acceptance criteria are verifiable | |

If a story fails the **S** (Small) check, split it using the 9 Splitting Patterns below.

### 9 Story Splitting Patterns

When a story is too large, apply these patterns (in order of preference):

1. **Workflow Steps**: Split by sequential steps in a user workflow
   - Example: "User completes checkout" -> "User adds to cart", "User enters shipping", "User pays"

2. **Business Rule Variations**: Split by different business rules
   - Example: "Apply discount" -> "Apply percentage discount", "Apply flat discount", "Apply BOGO"

3. **Happy Path / Edge Cases**: Separate the core flow from error handling
   - Example: "Upload file" -> "Upload valid file", "Handle invalid file type", "Handle oversized file"

4. **Input Methods / Channels**: Split by platform or input method
   - Example: "Send notification" -> "Send push (Android)", "Send push (iOS)", "Send email"

5. **Data Variations**: Split by data type or format
   - Example: "Import users" -> "Import from CSV", "Import from API", "Import from segment sync"

6. **Interface Variations**: Split UI from API from backend
   - Example: "Add webhook retry" -> "Backend retry logic", "Dashboard retry config UI", "Retry analytics"

7. **Operations (CRUD)**: Split by create, read, update, delete
   - Example: "Manage cohorts" -> "Create cohort", "View cohort members", "Edit cohort rules", "Delete cohort"

8. **Performance / Scale**: Separate basic functionality from performance optimization
   - Example: "Load dashboard" -> "Load dashboard (basic)", "Optimize dashboard for 10K+ campaigns"

9. **Spike / Research**: Extract unknowns into a research spike
   - Example: "Integrate with CleverTap" -> "Spike: Evaluate CleverTap API capabilities", "Implement CleverTap event sync"

### Epic Hypothesis Format

When creating epics (groups of related stories), use this hypothesis structure:

```markdown
## Epic: [Epic Name]

**We believe that** [building this capability]
**for** [target user segment]
**will achieve** [expected outcome/metric].

**We will know this is true when** [measurable signal/KPI].

**Key risks**: [what could invalidate this hypothesis]
```

### Writing Guidelines

- **Personas**: Use specific personas relevant to your product, not generic "user":
  - *Growth Marketer*: Creates campaigns, analyzes metrics, runs A/B tests
  - *Product Manager*: Defines targeting rules, plans journeys, reviews analytics
  - *Developer*: Integrates SDK, configures events, manages technical setup
  - *CSM (Customer Success Manager)*: Troubleshoots issues, monitors customer health
  - *End User*: The customer's user who sees nudges, widgets, stories in the app

- **Outcomes over outputs**: "So that conversion rate increases by X%" not "so that a button appears"

- **One behavior per story**: Each story should describe exactly one user behavior change

- **Include "so that"**: Never skip the business outcome — it drives prioritization

---

## Mode 3: Prioritization

### Framework: RICE with Strategic Overrides

Score each item on four dimensions:

#### R — Reach (How many users/customers are affected?)

| Score | Definition |
|---|---|
| 10 | All customers / platform-wide |
| 7 | Most customers (70%+) |
| 5 | Many customers (30-70%) |
| 3 | Some customers (10-30%) |
| 1 | Few customers (<10%) or single enterprise |

*Data source*: Problem Discovery frequency data, HubSpot customer count, Pylon ticket volume.

#### I — Impact (How much does it move the needle per user?)

| Score | Definition |
|---|---|
| 3 | Massive — unlocks new revenue, prevents churn, competitive must-have |
| 2 | High — significant workflow improvement, measurable metric lift |
| 1 | Medium — nice improvement, moderate time savings |
| 0.5 | Low — minor quality-of-life, cosmetic |
| 0.25 | Minimal — very slight improvement |

*Data source*: Problem Discovery intensity data, deal values at risk (HubSpot), customer segment (enterprise vs SMB).

#### C — Confidence (How sure are we about R, I, and effort?)

| Score | Definition |
|---|---|
| 100% | High — validated by data, multiple customer confirmations, clear requirements |
| 80% | Medium — some data, reasonable assumptions, partially validated |
| 50% | Low — gut feel, single customer request, unclear scope |

*Boost confidence by*: More customer evidence, prototype/spike results, competitive analysis.

#### E — Effort (Person-weeks to ship)

Estimate in person-weeks. Include: design, development, testing, documentation, rollout.

| Estimate | Typical Scope |
|---|---|
| 0.5 | Config change, small UI tweak |
| 1 | Single feature, 1 developer |
| 2 | Medium feature, 1-2 developers |
| 4 | Large feature, multiple developers |
| 8+ | Epic-level, cross-team |

### RICE Score Formula

```
RICE Score = (Reach x Impact x Confidence) / Effort
```

### Strategic Overrides

After calculating RICE scores, apply these multipliers:

| Override | Multiplier | When to Apply |
|---|---|---|
| Revenue blocker | 2x | Feature gap blocking signed deals (check HubSpot) |
| Churn risk | 2x | Existing customer threatening to leave without this |
| Competitive threat | 1.5x | Competitor just shipped this, customers are comparing |
| Platform bet | 1.5x | Aligns with a strategic platform direction (e.g., off-app expansion) |
| Tech debt paydown | 0.75x | Important but no direct customer value |
| Single-customer request | 0.5x | Only one customer wants it, no strategic alignment |

### Output Format

Present the prioritized backlog:

```
*Prioritized Backlog — [Date]*

| # | Item | Reach | Impact | Confidence | Effort | RICE | Override | Final |
|---|------|-------|--------|------------|--------|------|----------|-------|
| 1 | [feature] | 7 | 3 | 80% | 2 | 8.4 | Revenue 2x | 16.8 |
| 2 | [feature] | 10 | 2 | 100% | 4 | 5.0 | — | 5.0 |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |

*Top 3 Recommendation*:
1. [Feature] — [1-line rationale]
2. [Feature] — [1-line rationale]
3. [Feature] — [1-line rationale]
```

---

## Mode 4: Roadmap Planning

### Framework: Now / Next / Later

Sequence the prioritized backlog into time horizons:

| Horizon | Timeframe | Criteria |
|---|---|---|
| **Now** | This sprint / next 2 weeks | High RICE, clear requirements, no blockers, team capacity available |
| **Next** | Next 2-6 weeks (1-3 sprints) | High RICE but needs design/spike, or blocked by a "Now" item |
| **Later** | 1-3 months out | Medium RICE, needs research, strategic but not urgent |
| **Icebox** | No timeline | Low RICE, single-customer, or superseded by other items |

### Step 1: Capacity Check

Ask the user:
- How many developers are available this sprint?
- Any ongoing commitments that reduce capacity? (on-call, tech debt sprints, etc.)
- Any hard deadlines? (customer commitments, regulatory, partnership launches)

### Step 2: Dependency Mapping

For each "Now" item, check:
- Does it depend on another item being completed first?
- Does it block other high-priority items?
- Does it require coordination across teams (SDK + Backend + Dashboard)?

Present dependencies as a simple list:

```
*Dependencies*:
- [Feature A] blocks [Feature B] — A must ship first
- [Feature C] requires SDK release — coordinate with SDK team
- [Feature D] and [Feature E] are independent — can parallelize
```

### Step 3: Theme Grouping

Group related items into themes for stakeholder communication:

```
*Roadmap Themes*

*Theme 1: [Theme Name]* (e.g., "Off-App Channel Expansion")
- Now: [item 1], [item 2]
- Next: [item 3]
- Later: [item 4]

*Theme 2: [Theme Name]* (e.g., "Gamification Enhancements")
- Now: [item 5]
- Next: [item 6], [item 7]
```

### Step 4: Present the Roadmap

```
*Roadmap — [Quarter / Sprint]*

*NOW (This Sprint)*
| Item | Owner | Est. | Dependencies | Status |
|------|-------|------|-------------|--------|
| [feature] | [team] | [weeks] | None | Not started |
| [feature] | [team] | [weeks] | Blocked by X | Waiting |

*NEXT (Next 1-3 Sprints)*
| Item | Owner | Est. | Why Not Now? |
|------|-------|------|-------------|
| [feature] | [team] | [weeks] | Needs design spike |
| [feature] | [team] | [weeks] | Blocked by [Now item] |

*LATER (This Quarter)*
| Item | Owner | Est. | Notes |
|------|-------|------|-------|
| [feature] | [team] | [weeks] | Needs research |

*ICEBOX*
- [item] — [reason for deprioritization]
```

---

## Mode 5: Linear Ticket Operations

### Purpose
Draft and create well-structured Linear tickets from any of the above modes, or from ad-hoc requests.

### Ticket Structure

Every Linear ticket created by this skill must follow this template:

```markdown
## Problem Statement

[Customer segment] struggles with [problem]
when trying to [goal/job-to-be-done],
which results in [negative outcome].

**Evidence**: [X customers across Y channels — list names and ticket/call refs]

## User Stories

### Story 1: [Title]
**As a** [persona], **I want to** [action], **So that** [outcome].

#### Acceptance Criteria
- [ ] Given [context], When [action], Then [result]
- [ ] Given [context], When [action], Then [result]

### Story 2: [Title] (if applicable)
...

## Scope

**In scope**:
- [Specific deliverable 1]
- [Specific deliverable 2]

**Out of scope**:
- [Explicitly excluded item]

## Technical Notes
- [Affected repos/services]
- [Related tickets: TICKET-xxx, TICKET-yyy]
- [Known constraints or dependencies]

## Success Metrics
- [How we'll measure if this solved the problem]
- [Target metric and timeframe]

---
*Generated by Product Manager skill from: [data sources used]*
```

### Creating the Ticket

1. **Determine the team**: Ask the user, or default based on context:
   - Feature requests -> "Product Backlog" team
   - Bugs -> "Engineering" team
   - If unsure, ask

2. **Determine the state**: Default to "Triage" unless the user specifies otherwise

3. **Set priority**: Use RICE score if available, otherwise ask:
   - Urgent (P0): Blocking revenue or causing outage
   - High (P1): Significant impact, needed this quarter
   - Medium (P2): Important but not urgent
   - Low (P3): Nice-to-have

4. **Add labels**: Based on content:
   - "feature-request" for new features
   - "customer-request" if tied to specific customer asks
   - "improvement" for enhancements to existing features
   - "tech-debt" for internal improvements

5. **Create via Linear MCP**:
   ```
   Use mcp__linear__save_issue with:
   - title: Concise, action-oriented (e.g., "Add webhook retry with configurable backoff")
   - description: Full template above in Markdown
   - teamId: [resolved team ID]
   - stateId: [resolved "Triage" state ID]
   - priority: [1-4 mapping]
   - labelIds: [resolved label IDs]
   ```

6. **Confirm with user**: Share the created ticket URL and summary

### Bulk Ticket Creation

When the user wants to create multiple tickets (e.g., from a Problem Discovery session):

1. Draft all tickets first and present them in a summary table
2. Ask for confirmation: "I've drafted X tickets. Want me to create all of them, or review individually?"
3. Create on confirmation, reporting each created ticket's URL

---

## Cross-Mode Workflows

### Full Planning Cycle

When a user asks for end-to-end planning help (e.g., "help me plan what to build next quarter"):

1. **Problem Discovery** -> Surface top 10 problems
2. **User Story Writing** -> Write stories for top 5 problems
3. **Prioritization** -> RICE-score all items
4. **Roadmap Planning** -> Sequence into Now/Next/Later
5. **Linear Ticket Ops** -> Create tickets for "Now" items

At each transition, present findings and ask if the user wants to continue to the next mode.

### From Individual Triage to Strategic

When feature-request-triage or bug-triage identifies a pattern (e.g., "this is the 5th request for X"):
- Suggest running Problem Discovery mode to validate the pattern
- If confirmed, flow into User Story Writing and Prioritization

---

## Guardrails

### Data-Driven Decisions
- Never prioritize based on intuition alone — always cite evidence from at least one data source
- When data is insufficient, say so: "I found evidence from X customers, but this may not represent the full picture. Consider validating with [suggested method]."

### Avoid Scope Creep
- Each user story must describe exactly one behavior change
- If a story covers multiple behaviors, split it using the 9 patterns
- "Out of scope" section is mandatory in every ticket

### Customer Attribution
- Always attribute problems to specific customers when possible
- Never fabricate customer names — only use names found in actual data sources
- Use customer names from Pylon tickets, Linear issues, Grain recordings, or HubSpot

### Bias Awareness
- Recency bias: Don't over-weight problems from the last week — check historical data
- Loudness bias: A vocal customer doesn't mean a common problem — verify frequency
- Sales bias: Features requested by prospects in active deals may not serve the broader customer base — flag this

### Effort Estimation
- This skill provides rough estimates only — always note that engineering team should refine estimates
- When in doubt, estimate higher (it's better to over-estimate than under-estimate)

---

## Quick Reference: Adapting to Your Product

The modes above are product-agnostic. To get the most out of this skill, keep a
short product-specific cheat sheet handy (or add it to this skill) covering:

- **Common problem categories** — the recurring themes your customers' problems
  cluster into (e.g. targeting, display/UX, analytics, integration, performance),
  so discovery can tag and group them quickly.
- **Tracker teams** — which team in your issue tracker owns which kind of work
  (e.g. Engineering for bugs, a Product Backlog for features), so tickets route
  correctly.
- **Key success metrics** — the metrics you reference when writing ticket success
  criteria (e.g. support-ticket volume, activation rate, time-to-value, NPS/CSAT,
  churn, feature adoption).
