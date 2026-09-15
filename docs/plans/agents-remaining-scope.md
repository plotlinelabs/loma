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
- Shared per-run USD model budgets, atomic conservative reservations, SDK usage
  accounting and persistent unknown-charge holds across retries and delegates.
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
- Bounded Gmail, Slack send and read-only Calendar actions. This does not retrofit the legacy chat/SDK/shell runtime with
  enforcement. Those runtimes must not be described as protected by these policies.
- Broker and legacy runtimes still share infrastructure. Isolating their secrets and
  operating-system authority is required against a compromised legacy runtime.
- Planner calls use a separately configured Anthropic model. No live model or Gmail
  delivery was exercised in QA; the planner and delivery adapter were stubbed.
- USD model budgets use configured operator price ceilings, not live billing rates.
  They exclude connector charges and require pricing configuration before provider calls.
  Connector calls with unknown outcomes are never automatically retried.
- Event keys provide deduplicated enqueue via the signed API; external ticket/webhook
  subscription wiring and a general event outbox are not implemented.
- Unknown email outcomes now have an owner investigation UI and versioned audit.
  Reports are not provider-verified receipts and cannot clear the replay barrier;
  provider-backed Gmail Sent checks are now implemented for new correlated actions;
  controlled retry authorization remains intentionally unavailable. Held-charge owner reviews are implemented; they do not release funds.
- Private notes and an author-controlled, explicitly attached playbook library are implemented.
  The library supports text/Markdown, named active readers, revision checks and access revocation;
  it is not a Drive/PDF retrieval system.
- Agent work cards now appear on the personal taskboard in read-only status lanes,
  linked to the original work records. Existing chat tasks remain a separate object type;
  dragging a chat card never authorizes or executes work.

## Enablement and trust boundary

Default: off. Set `LOMA_BOUNDED_WORK_ENABLED=true` in the backend, a matching
`LOMA_WORK_GATEWAY_SECRET` (at least 32 random characters) in backend/dashboard,
`LOMA_WORK_MODEL`, `ANTHROPIC_API_KEY`, `LOMA_WORK_PRICING_JSON`, and enable the scheduler only after review.
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
process. Provider charges for failed calls are unknown; their full USD reservation stays held.

UI: creation exposes deadline and retry limits with conservative defaults; Work cards
show the saved limits, and run details show deadline, attempts and retry history. Open
run details refresh with the overview instead of displaying a stale running state.

## USD model budgets and usage accounting

New bounded jobs default to **$1 per root run**, configurable from $0.01 to $100.
Older bounded job/run snapshots without the field also use $1; legacy unrestricted
Flows are unchanged. Every recurring invocation has its own budget. This is not a
monthly account cap. Safe tests consume model budget even though connectors are off.

The root holds an atomic integer-nanodollar ledger shared with all delegates. Before
any provider call, reserve 131,072 input tokens and all 2,048 possible output tokens
at the configured price ceilings. Requests are limited to 100,000 UTF-8 bytes across
the system prompt and serialized context. No prompt caching, tools or thinking mode
are requested. Conservative reservation can stop a small job before its actual spend
reaches the cap. No provider request is made without budget or configured pricing.

Set `LOMA_WORK_PRICING_JSON` to a JSON object keyed by the **exact** model identifier,
with positive decimal `input_usd_per_million` and `output_usd_per_million` strings.
Administrators must supply and maintain ceilings covering that model's provider rate;
there are deliberately no hardcoded market prices. Each reservation records its rates.
This bounds **calculated model cost**, not the provider's invoice or connector fees.
A fixed Anthropic API origin avoids environment-driven alternate provider endpoints.

Only SDK-reported usage settles a reservation, atomically and once. Model-generated
JSON and owner reports cannot release funds. Malformed model output still costs money
and is accounted before JSON parsing. Timeouts, crashes and missing/unrecognized usage
retain the maximum reservation, including across retries/restarts. Usage exceeding the
reserved input/output ceiling blocks further root spending. Terminal cancellation does
not refund an unresolved charge; a late valid usage response can settle without reviving
work. Approval and email receipts are independent of the model-cost ledger.

UI: setup shows the USD limit and per-invocation semantics; work cards show the saved
cap; run details separate recorded cost and reservations, reported input/output tokens,
and shared parent/delegate totals. Reserved funds are not labeled confirmed spend.
Live provider billing verification remains a release gate. Automated reconciliation of
held model charges is not implemented; they are never optimistically refunded.

## Unknown email investigation (manual, no retry authority)

Unknown Gmail send outcomes appear in Needs you and on Work-card badges, even when
older than the recent-history window. The owner reviews the account, exact recipient,
subject, message and original receipt, then records sent / not sent / still unknown
with evidence. The UI explicitly labels this an **owner report**, not a provider receipt.
Missing search results alone are not proof of a failed send. Still-unknown reports stay
in Needs you; conclusive reports remain accessible in Run history.

The signed API accepts only active owners and uncertain Gmail-send records. Investigation
remains possible after agent deletion, work revocation or run expiry because this is an
owner audit write, not execution. Competing reports use a separate review-version CAS;
corrections preserve the entire earlier audit. The provider receipt, proposal version,
execution status, approval digest and terminal run state are never overwritten.

**No resend, auto-resume or retry permission is granted, including for “not sent”.** The
uncertain status remains the replay barrier. A future provider-backed reconciliation
and separately authorized retry mechanism is still required before clearing it. This
change does not call Gmail or trust owner notes as machine-verified delivery evidence.

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


## Continuation: provider-backed Sent checks and connector hygiene

New bounded Gmail sends receive a deterministic RFC Message-ID derived from the
approval ID, persisted atomically with the execution claim before dispatch. This
is a lookup correlation key, **not Gmail idempotency support**. Older uncertain
sends without this marker remain manual; never synthesize evidence for them.

A separate bounded worker loop checks uncertain sends without waking the model.
It uses the owner's personal Gmail CLI, searches by exact RFC Message-ID under
`SENT`, and compares recipient, subject, body and absence of CC/BCC. Only one exact
candidate becomes "Verified in Gmail Sent". That confirms a matching Sent entry,
not recipient delivery. Missing, ambiguous, changed or unavailable results remain
unresolved. No negative result can authorize retry, no model cost hold is refunded,
and no stopped run is resumed. Original receipt, uncertain replay barrier and human
investigation history remain intact. Concurrent checks use a claim CAS and stale
responses cannot overwrite newer evidence.

Both saved and current Gmail search/read policies must be `allow`. Active owner,
agent visibility and work revocation are checked before and after lookup; previews
never call the provider. Checks can continue after the run ends, but revoke blocks
future calls. Each action permits at most five automatic attempts at five-minute
intervals. A failed worker attempt consumes its slot and is recoverable after the
claim interval. Ten candidates per sweep keep memory bounded; ineligible entries
are deferred so they do not monopolize the queue. No account read permission is
implicitly upgraded from `ask` to `allow`.

The personal connector bridge now starts Python in isolated import mode (`-I`),
with a private temporary working directory, explicit OAuth/DB environment allowlist,
dotenv loading disabled, stdin/stderr discarded, 45-second timeout and bounded stdout.
Timeouts and oversized responses kill/reap the child. No inherited model, GitHub,
proxy or Python injection environment is passed. **This is subprocess hygiene, not
OS/runtime isolation**: the helper still needs DB/OAuth authority and shares the OS
user/filesystem with legacy execution. Separate deployment, secret authority and
network/filesystem restrictions remain release blockers.

UI separates provider evidence from owner notes, moves positively verified entries
out of Needs you, and keeps the original unknown receipt collapsed and labeled as
historical so it does not contradict the current verified status.

Verification: 667 backend tests, 131 opt-in policy/database checks, TypeScript, and
real local backend/Next.js browser checks at desktop/mobile widths. Provider/model
calls were simulated. No real account read, send, delivery or production deployment
was tested. Gmail behavior follows the official message-list API:
https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list

Still outstanding: full runtime isolation; additional connectors and external event
subscriptions; held model-charge reconciliation and safe retry authorization; full
personal taskboard and workspace knowledge integration. Do not mark the full Agents
roadmap completed.

## Merge-readiness audit (2026-09-15)

Current main was merged into this branch; the Google helper conflict keeps the
centralized database default from main. The connector still disables dotenv and
supplies an explicit isolated database name when tested.

Found and fixed a release-blocking authority gap: normal proposal creation,
human approval, automatic approval and final dispatch now intersect the pinned
grant with the current job grant. Delegates additionally intersect the root's
saved/current grants. Root revocation, lost agent visibility or principal mismatch
blocks child work before its model call. A policy-derived approval cannot satisfy
a later human-approval requirement. The execution boundary independently rejects
safe-preview sends, and revoked work cannot enqueue another preview.

Fresh verification after syncing main:
- Full backend: 862 passed, 95 skipped; skipped cases include opt-in DB suites.
- Opt-in isolated Mongo suites: 139 passed, including eight new authority tests.
- TypeScript and diff whitespace checks passed.
- Real local backend plus Next.js browser workflow passed at desktop/mobile widths,
  with synthetic login, fresh throwaway DB and no external integration credentials.
  Model and provider calls were simulated, not live-provider validation.
- Browser evidence disables animations and waits for responsive navigation so a
  viewport transition is not mistaken for clipped navigation.

**Not merge-ready.** This audit does not complete the remaining roadmap. Runtime
isolation is still absent in deployment; legacy shell execution shares the broker's
OS/filesystem and DB/secret authority. There is no Docker socket in this test
container, and `unshare --user --map-root-user --net true` fails with
`Operation not permitted`. A dedicated non-production isolation-capable runner is
needed for the negative filesystem/network/credential tests. Merely adding a
container declaration, an environment toggle or mocked tests is not proof of an
isolation boundary. Additional connectors/external subscriptions, held-charge
reconciliation and full taskboard/workspace-knowledge integration are still open.

---

## Update (commit 3862206): remaining scope implemented

The four items previously tracked as open are now implemented on this branch:

1. **Broader connectors and external events.** `slack.send` (exact allowed
   channel IDs) and read-only `calendar.list` join the bounded action set under
   the same default-deny policy, digests, receipts and uncertainty handling.
   Per-job hashed event tokens plus `POST /api/bounded-work-hooks/{work_id}`
   provide deduplicated external wake-ups; event notes are bounded, stored as
   untrusted data and carry no authority.
2. **Held-charge reconciliation.** Owner charge reviews (billed / not billed /
   unknown, version-CAS, append-only history) surface in Needs you for ended
   top-level runs with held reservations. Audit-only: no refunds, no unblocks,
   no restarts.
3. **Taskboard integration.** A read-only "Agent work needs you" banner on
   /tasks (new `attention` endpoint) deep-links to /agents/work?tab=needs.
   Nothing on the board can approve or execute an action.
4. **Runtime isolation (implementation).** Connector children run in their own
   session with conservative rlimits, plus an optional operator-configured
   `LOMA_CONNECTOR_SANDBOX` argv prefix for namespace isolation on capable
   hosts. Verification on an isolation-capable runner is still required; the
   legacy shell runtime's shared OS authority is unchanged.

Still outstanding before release: isolation verification, a browser/UX pass
over the new surfaces and live-provider validation. See the follow-up below for
the completed browser checks and shared playbook implementation.


## Shared knowledge and board follow-up (2026-09-15)

Implemented and verified in the isolated local stack:
- Playbook library with private-by-default text/Markdown sources, sharing to up to
  20 named active accounts, author-only writes and optimistic revision checks.
- Up to ten explicit source attachments per job. Sources are fetched under the same
  execution principal on every planner step, not copied into a shared agent definition.
  Current source access is checked again before dispatch, including after approval
  waits and on delegated work. Deleted/revoked sources block dependent actions.
- Source IDs and revisions are recorded in run history without copying source bodies.
  Personal notes stay distinct; source deletion cannot erase already-generated results.
- Live agent-work cards on the personal taskboard, grouped by status, with deep links
  to the original records. Expansion is height-limited on mobile. No drag-to-approve,
  copied work records, implicit permissions or credential changes.
- Needs-you retrieval includes older unresolved Slack sends, questions and held-charge
  reviews outside the recent-history window. Unknown outcomes stay blocked from replay.
- Fixed the real-app auth middleware path for external event POSTs. Only the exact
  hook shape bypasses session auth; the handler still enforces its per-job secret.
  The signed control plane and non-POST paths retain their normal authentication.
- Browser checks include source save-error preservation, attachment/deletion, desktop
  and mobile layouts, board links, charge-review recovery, token rotation/revocation,
  duplicate event enqueue, Slack approval and manual unknown-delivery investigation.
  Model and delivery boundaries remain simulated; there are no live sends.

Still not established: a separately deployed OS/credential boundary isolating legacy
shell runners from the broker and its database. Resource limits and an optional sandbox
prefix are not a substitute. Do not mark this PR production-ready on the strength of
these functional tests. Live-provider smoke validation also remains outstanding.
Manual billing evidence is not provider accounting: unknown model charges stay reserved.

## Chat-preserving isolation decision and transport foundation (2026-09-15)

The user chose to keep existing chat and move its workers to a separately
restricted environment, rather than disable chat. Added `isolation/` with a
trusted dedicated-host supervisor, backend transport and strict worker protocol.
This is **not yet connected to the three chat runtimes**; current chat and
production deployment are unchanged. See
[remote worker migration](../security-containment/remote-workers.md) for exact
implemented boundaries, remaining adapters and rollout gates.

Verification in this change: 36 new protocol/real subprocess tests pass; the full
backend suite passes 922 tests with 108 skipped. Docker execution is substituted
in these tests. No Docker/gVisor runtime containment, browser parity, model or
live-provider validation is claimed. No new UI was added.

Still open: runtime/model adapters, typed credential gateway parity, owner-scoped
file/recall bridges, all entrypoint routing and staging containment verification.
Do not mark isolation complete or this PR merge-ready based on transport tests.
