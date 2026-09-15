# Agents completion checklist

## Current delivery status

PRs #187 and #190 are merged. This single draft PR retains the legacy migration
and adds an experimental bounded-work vertical slice across the eight areas below.
It is **not yet the entire roadmap or a production release**. Unchecked checklist
items below remain release targets, not claims of completion.

Implemented in this branch:
- Text-only planner with no SDK tools, shell or supplied credentials. A fixed broker
  supports Gmail search/read/send, exact recipient allowlists and allow/ask/deny.
- Signed human control plane; owner-scoped jobs, runs, approvals and notes.
- Versioned approval edits with prior payload audit, 24-hour expiry, explicit decisions,
  single execution claim and receipts. Unknown outcomes are not retried automatically.
- Agent work page, Work and Needs you views, question/approval distinction, saved
  permission details, run history, desktop/mobile review dialogs and setup templates.
- Separate jobs/runs, checkpointed decisions, 120-second leases, one active run per job,
  shared model-call limits, delayed wake-ups and cancelled child propagation.
- Configurable run deadlines (1 minute to 30 days; default 24 hours), inherited by
  delegates and capped approval expiry. Waiting, sleeping and retries do not reset time.
- Configurable text-planner transport retries (0 to 3; default 2), using 30/60/120-second
  durable backoff, charged against shared model-call limits. No connector replay.
- Periodic deadline/terminal cleanup repairs partial writes, closes pending approvals
  and stops descendants; stale claimed actions remain visibly uncertain.
- Private recurring schedules, explicit enable/pause, schedule editing that pauses before
  re-enablement, next-run/error visibility, and no fallback to unrestricted execution.
- One-level delegation to selected agents under the same principal and parent grant.
- Up to ten private, editable notes per agent, including small text/Markdown attachments.

Important limitations requiring follow-up before broad rollout:
- Gmail-only actions. This does not retrofit the legacy chat/SDK/shell runtime with
  enforcement. Those runtimes must not be described as protected by these policies.
- Broker and legacy runtimes still share infrastructure. Isolating their secrets and
  operating-system authority is required against a compromised legacy runtime.
- Planner calls use a separately configured Anthropic model. No live model or Gmail
  delivery was exercised in QA; the planner and delivery adapter were stubbed.
- Model-call/output limits are not dollar budgets or full token accounting.
  Connector calls with unknown outcomes are never automatically retried.
- Event keys provide deduplicated enqueue via the signed API; external ticket/webhook
  subscription wiring and a general event outbox are not implemented.
- Unknown delivery outcomes require manual investigation; no reconciliation UI yet.
- Private notes are a small knowledge store, not a Drive/PDF retrieval system or separate
  workspace knowledge and long-term memory services.
- Agent work has its own focused card view. The existing personal taskboard and chats
  have not been migrated or unified with it.

## Enablement and trust boundary

Default: off. Set `LOMA_BOUNDED_WORK_ENABLED=true` in the backend, a matching
`LOMA_WORK_GATEWAY_SECRET` (at least 32 random characters) in backend/dashboard,
`LOMA_WORK_MODEL`, `ANTHROPIC_API_KEY`, and enable the scheduler only after review.
Never put the gateway secret in a `NEXT_PUBLIC_*` variable. The planner receives only
job context, permitted private notes and prior results; it has no arbitrary tool API.
The broker mints the existing personal CLI token only at dispatch, not in model context.
Use a separately isolated worker deployment before enabling for sensitive workloads.

Generic Flows API mutations cannot detach or manage bounded schedules. Manage these
through Agent work. Approval waiting does not require an active worker lease; execution
still requires a fresh lease and authority check. Approval is never a guarantee of send:
provider timeouts are visibly uncertain, and cancellation cannot undo an in-flight send.

## Deadline and retry semantics

Limits are pinned when saving a job. Each new root run gets a deadline from enqueue
(including time queued), not from the first model call. Delegates inherit the earlier
of their own limit and their parent's exact deadline. Existing bounded runs without
this field derive it from their original creation time, never from worker restart.
This changes only bounded work, not the legacy creator-identity migration.

The broker rechecks the deadline immediately before dispatch. Approval edits cannot
extend it. Expiry blocks future dispatch but cannot recall an already in-flight external
action. A claimed action without a receipt after 120 seconds becomes outcome unknown;
no automatic retry is allowed. Cleanup uses repeatable, bounded sweeps so a crash
between marking a run terminal and closing its approvals is repaired on later ticks.

Retry policy applies **only** to model transport errors, HTTP 429 and provider 5xx.
Invalid model output, policy errors and authentication failures are not retried.
Every attempted call, including a failed one, consumes the root call budget. Retries
are capped per run and require both remaining calls and enough time before the deadline.
Parent and child calls share the existing root budget. Waiting for retry has no model
process. Provider charges for failed calls are unknown; this is not a currency cap.

UI: creation exposes deadline and retry limits with conservative defaults; Work cards
show the saved limits, and run details show deadline, attempts and retry history. Open
run details refresh with the overview instead of displaying a stale running state.

## Legacy identity compatibility (implemented here)

Before the scheduler starts or webhook routes accept requests, backfill workflows that:
- Were created before 2026-09-14 21:43:12 UTC (the #187 merge boundary).
- Have missing, null or empty `run_as`, no new identity marker, no agent link and no prior migration marker.
- Have exactly one distinct recorded creator email in `created_by.source` / `created_by.user_name`.
- Have a matching, explicitly active Loma user.

Assign that email to `run_as` and atomically record the migration ID, account and UTC time
in `run_as_backfill`. Preserve explicit accounts, workflow status, schedules and prompts.
Use 100-record batches and conditional updates so concurrent admin edits win.
New workflows receive `identity_version: 1` and cannot qualify for the migration even
if their timestamp is later backdated. Runtime execution remains fail-closed with a
fresh user-status check; there is no creator fallback in the executor.

**Trust caveat:** old creator fields were caller-supplied. This is the operator-requested
migration of recorded ownership, not verification of historical authenticated ownership.
Conflicting creator emails, missing dates, unknown/inactive users and malformed explicit
accounts are not guessed. Logs identify unresolved workflows for an admin to repair in
Flows > Execution account > Save account. Paused/completed workflows stay paused/completed.
No job is fired by migration. Missed one-time jobs are not replayed.

Deploying this PR runs the backfill; this implementation task does not change production
workflow data or merge/deploy the PR. For a read-only preflight, invoke
`scheduler.legacy_identity.backfill_legacy_identities(db, dry_run=True)` with the selected
Loma database. The result reports eligible/assigned/unresolved counts. Dry-run never writes.

## 1. Execution-layer permissions

- [ ] Separate agent identity/config version, owner, execution principal, trigger and grant.
- [ ] Allow/ask/deny policies for action and resource, enforced at one broker boundary.
- [ ] No agent-visible connector credentials, signing secrets or unrestricted host shell.
- [ ] Close every alternate SDK, OpenCode, Codex, shell, MCP and direct-network bypass.
- [ ] Short-lived run-scoped grants, revocation, audited decisions and safe default-deny.
- [ ] Shared agents share configuration only, not owner access.
- [ ] Tests: forbidden paths, credential isolation, revocation and cross-user access.

## 2. Exact-action approval engine

- [ ] Immutable versioned proposals with account, recipients, body, attachments and resources.
- [ ] Authorized human approval, expiry, rejection, edit/re-propose and cancellation.
- [ ] Atomic claim, policy/access recheck, action receipt and reconciliation for unknown outcomes.
- [ ] Start with one email adapter, using controlled accounts/test adapters in E2E.
- [ ] Never interpret chat or card movement as approval; agents cannot self-approve.
- [ ] Tests: payload tampering, stale approval, unauthorized approver, concurrent clicks,
      worker crash during send, no blind resend on uncertain provider response.

## 3. Needs you / kanban UX

- [ ] Work cards show progress; linked Approval cards show the exact proposed action.
- [ ] Neutral Work label/icon; amber Approval label/icon, never color-only meaning.
- [ ] Main board stays work-focused, with pending-approval count badges.
- [ ] Needs you view aggregates approvals and clearly separate Needs input questions.
- [ ] Review includes full content, recipient, account, reason, expiry and parent task.
- [ ] Explicit Approve & send; editing requires renewed approval. Rejection resumes parent
      with a rejected result, not repeated requests for the same action.
- [ ] Pending, approved, executing, executed, rejected, expired, cancelled, failed and
      outcome-unknown remain distinguishable. Approved is not Done.
- [ ] Tests: keyboard/focus, mobile scrolling, long text, empty/loading/error states,
      retries preserving input, badges linked to the same record, no drag-to-authorize.

## 4. Durable work and runs

- [ ] Persistent task/job definition separate from execution attempts.
- [ ] Checkpoints, worker leases, recovery, idempotency keys and completed-action ledger.
- [ ] Time/cost/work budgets, retry/concurrency/overlap limits and missed-schedule policy.
- [ ] Pause future schedule versus stop running work, with truthful status and cancellation.
- [ ] Sleeping/waiting has no model process; worker heartbeat is not an LLM call.
- [ ] Tests: process kill/restart, lease expiry, checkpoint recovery, exhausted budget,
      overlapping triggers and no repeated completed side effect.

## 5. Setup and agent work page

- [ ] Job > inputs > output > permissions > trigger > connection checks > safe test.
- [ ] Support Triager, Meeting Prep and Invoice Follow-up templates.
- [ ] Enforced dry-run via the same permission boundary, not a prompt-only instruction.
- [ ] Work page shows current/queued work, next wake-up/timezone, account, blockers,
      last success, results, failures and costs; Run now / Assign / Pause / Stop / Retry.
- [ ] Tests: new-user setup without system-prompt knowledge, missing connection recovery,
      policy changes/config pinning, no writes during test and desktop/mobile usability.

## 6. Event and delayed wake-ups

- [ ] Approval result, task assignment, approved external events and follow-up deadlines.
- [ ] Durable outbox/inbox, deduplication, cancellation and idempotent resume.
- [ ] Tests: duplicate and out-of-order events, crash between persistence and dispatch,
      sleeping task resumes once with correct principal and checkpoint.

## 7. Bounded delegation

- [ ] Structured parent/child task, deliverable, success criteria, deadline and result.
- [ ] Allowed agent pairs, one level initially, child-count/concurrency limits and shared budget.
- [ ] Child authority cannot exceed the parent grant or change execution principal.
- [ ] Context filtering, result access controls, cancellation propagation and parent wake-up.
- [ ] Tests: cycles, forbidden pairs, permission escalation, leaked context, timeout,
      duplicate completion, exhausted shared budget and cancelled parent.

## 8. Knowledge and memory

- [ ] Separate attached knowledge, task checkpoints and private personal memory.
- [ ] Inspect/edit/delete controls; source/version provenance; retrieval-time access checks.
- [ ] Shared agent knowledge must not contain a user's private memory by default.
- [ ] Tests: cross-user isolation, access revocation, deleted sources, cached retrieval,
      delegated context and shared-agent visibility changes.

## Cross-feature release gate

Configure > enforced safe test > scheduled wake-up > invoice draft > Needs you approval >
exact send once > receipt/result > checkpoint > sleep. Test rejection, expiry, revocation,
duplicates, worker failure and unknown send outcome in an isolated local stack.
Attach real-browser desktop/mobile screenshots and test outputs. Keep new autonomous
capabilities behind feature flags until the boundary and full workflow pass.
Do not mark this checklist complete based only on unit tests or mocked UI endpoints.
