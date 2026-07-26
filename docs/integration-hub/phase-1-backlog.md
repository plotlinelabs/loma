# Phase 1 backlog

## Objective

Deliver a restricted, manual Integration Hub for pilot users without changing
support workflows or connecting customer communication sources.

## Release scope

Included:

- Feature flag and backend authorization
- Integration Hub navigation
- Portfolio and My Action Center
- Customer workspace
- Accounts, projects, milestones, tasks, and risks
- Manual source links
- Audit trail
- Pilot allowlist

Excluded:

- Slack, Pylon, email, meeting, CRM, or product-data ingestion
- AI classification, summaries, or drafts
- External writes or automatic messages
- Customer portal
- Automatic technical verification

## Prerequisite: authentication hardening

Before Integration Hub code is enabled:

- Name and configure the trusted identity proxy.
- Strip client identity headers at the edge and inject a verified identity.
- Block direct production backend access.
- Fail closed when verified identity is missing.
- Add spoofed-header, invalid-assertion, missing-identity, and direct-access
  tests.

This is a separate platform PR because it affects the trust boundary of existing
Loma APIs.

## PR 0: validation vertical slice

Deliver:

- Feature-gated dashboard route and `/api/integration-hub/health`.
- One `integration_accounts` collection.
- One account-scoped list endpoint.
- One audited account mutation using a MongoDB transaction.
- Request IDs, authorization and mutation metrics, and redaction tests.
- Feature-flag rollback and support-route regression tests.

Acceptance:

- Verified proxy identity reaches the API and spoofed identity is rejected.
- Resource and audit writes either both commit or both fail.
- Assigned users cannot access the other test account.
- Disabling the feature removes UI and API access without changing support.
- The slice works with two test accounts in the deployed environment.

PR 0 is a validation gate. Remaining Phase 1 work does not begin until its
deployment evidence is reviewed.

## Phase 1 PR-sized work items

### PR 1: Authorization and account access grants

Deliver:

- Add explicit account, project, team, ACL, and temporary-delegation grants.
- Add immediate transactional revocation on ownership changes.
- Add account-scoped and global audit permissions.
- Add authorization tests for every grant source and revocation path.

Acceptance:

- Task assignment alone does not grant account access.
- Primary, technical, and backup ownership grants are explicit.
- Expired delegation and reassignment revoke access immediately.
- Existing API and support tests remain unchanged and pass.

Dependencies: authentication prerequisite and PR 0.

### PR 2: Persistence foundation

Deliver:

- Create repository and service modules for projects and versioned playbooks.
- Add validation, UUID generation, timestamps, and optimistic concurrency.
- Ensure required MongoDB indexes idempotently.
- Define stage/status transitions, archive/restore, and health explanations.
- Add unit tests using the repository's existing database test pattern.

Acceptance:

- Project and playbook instantiation works through service tests.
- Duplicate IDs and stale versions are rejected.
- No non-`integration_` collection is written.
- Every mutation includes atomic audit and baseline observability.

Dependencies: PR 1.

### PR 3: Project and portfolio APIs

Deliver:

- Add project REST endpoints and archive/restore operations.
- Enforce account-scoped visibility.
- Add pagination, stage, health, owner, and target-date filters.
- Implement ETag, idempotency, PATCH, stable sort, and rate-limit contracts.

Acceptance:

- Users see only authorized accounts.
- Every mutation records an audit entry.
- `next_action_task_id` never duplicates task state.
- API error responses follow the Phase 0 contract.

Dependencies: PR 2.

### PR 4: Milestone, task, and risk APIs

Deliver:

- CRUD endpoints for milestones, tasks, and risks.
- Validate lifecycle transitions, ownership, and due dates.
- Add Action Center query for due and overdue tasks.

Acceptance:

- Each resource is scoped to a visible project.
- Invalid transitions return `422`.
- Action Center results are ordered by urgency and deduplicated.

Dependencies: PR 3.

### PR 5: Dashboard shell and navigation

Deliver:

- Add restricted Integration Hub navigation.
- Add route-level access handling and disabled/unauthorized states.
- Add typed API client and shared TypeScript types.
- Reuse existing dashboard layout, loading, error, and empty-state patterns.

Acceptance:

- Unauthorized users cannot navigate to or load the module.
- Direct URL access is handled correctly.
- Existing dashboard navigation remains unchanged when the flag is disabled.

Dependencies: PR 1 and PR 3.

### PR 6: Portfolio and Action Center UI

Deliver:

- Portfolio list with owner, stage, health, target date, blocker count, and next
  action.
- Filters for owner, stage, health, and target date.
- My Action Center for due and overdue work.

Acceptance:

- Loading, empty, error, and populated states are tested.
- Filters use server-side query parameters.
- No health status is shown without its reason.

Dependencies: PR 4 and PR 5.

### PR 7: Customer workspace UI

Deliver:

- Overview, Action Plan, Risks, and Activity tabs.
- Forms for project, milestone, task, and risk updates.
- Manual source-reference links.
- Conflict handling when an optimistic concurrency update fails.

Acceptance:

- A pilot owner can manage the full manual onboarding plan.
- Mutations show success or actionable failure feedback.
- Source links open the original system without being copied into Loma.

Dependencies: PR 4 and PR 5.

### PR 8: Pilot validation and operational views

Deliver:

- Authorized, account-scoped audit view.
- Validate metrics, request tracing, mutation/audit coverage, and redaction
  already delivered by earlier PRs.
- Seed script or admin workflow for five pilot customers.
- Security and regression test pass.

Acceptance:

- Pilot activity is traceable by actor and request.
- No secrets or customer message bodies appear in logs.
- Disabling the feature flag removes access without affecting support workflows.

Dependencies: PRs 1 through 7.

## Test strategy

- Unit tests for validation, lifecycle transitions, and authorization.
- Repository tests for indexes, concurrency, and account scoping.
- API tests for status codes and error contracts.
- Dashboard tests for access, loading, empty, error, and conflict states.
- Regression tests for existing support routes and webhook processing.
- Security tests for direct URL access and cross-account resource IDs.

## Pilot plan

1. Validate terminology, workstreams, ownership, blockers, handover, and target
   date rules with onboarding users.
2. Run PR 0 with two test accounts and selected internal users.
3. Review authentication, transaction, authorization, proxy, and rollback
   evidence.
4. Expand to five active onboarding accounts and three to five internal users.
5. Record baseline response and onboarding metrics.
6. Run in parallel with the current process for two weeks.
7. Review support-bot regressions, permission failures, and data gaps weekly.
8. Continue to Phase 2 only when the exit criteria below are met.

## Phase 1 exit criteria

- All pilot projects have an owner, stage, target date, blocker state, and next
  action.
- Pilot users use the Action Center during at least 80% of working days.
- No pilot user can access an unassigned account without `view_all`.
- No Integration Hub operation changes support state.
- No severity-one security or data-isolation defect remains open.
- Product and onboarding owners approve the lifecycle and data fields.

## Initial effort estimate

With two full-stack engineers, partial product/design support, and an onboarding
representative:

- PRs 1 to 4: 2 to 3 weeks
- PRs 5 to 7: 2 to 3 weeks
- PR 8 and pilot hardening: 1 to 2 weeks

Expected Phase 1 elapsed time: 5 to 8 weeks, depending on review turnaround and
the maturity of existing dashboard test patterns.
