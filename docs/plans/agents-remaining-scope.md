# Agents completion checklist

## Status and delivery constraint

PRs #187 and #190 are merged. This change implements **legacy identity compatibility only**.
The items below are a design/acceptance checklist, **not implemented functionality**.
The requested all-in-one implementation exceeds the automated implementation playbook's
scope limit (10+ files or a large refactor). Deliver these as bounded, reviewed changes.

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
