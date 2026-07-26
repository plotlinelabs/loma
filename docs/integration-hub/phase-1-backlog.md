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

## PR-sized work items

### PR 1: Feature gate and authorization

Deliver:

- Add `LOMA_ENABLE_INTEGRATION_HUB`.
- Add pilot allowlist configuration.
- Add a reusable Integration Hub authorization guard.
- Register a protected `/api/integration-hub/health` endpoint.
- Add authorization tests for disabled, unauthorized, and authorized states.

Acceptance:

- Feature is disabled by default.
- Hiding the UI is not the only access control.
- Existing API and support tests remain unchanged and pass.

Dependencies: none.

### PR 2: Persistence foundation

Deliver:

- Create repository and service modules for accounts and projects.
- Add validation, UUID generation, timestamps, and optimistic concurrency.
- Ensure required MongoDB indexes idempotently.
- Add unit tests using the repository's existing database test pattern.

Acceptance:

- Account and project CRUD works through service tests.
- Duplicate IDs and stale versions are rejected.
- No non-`integration_` collection is written.

Dependencies: PR 1.

### PR 3: Account and portfolio APIs

Deliver:

- Add account and project REST endpoints.
- Enforce account-scoped visibility.
- Add pagination, stage, health, owner, and target-date filters.
- Write append-only audit records for mutations.

Acceptance:

- Users see only authorized accounts.
- Every mutation records an audit entry.
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

### PR 8: Audit, observability, and pilot hardening

Deliver:

- Authorized audit view.
- Metrics for API usage, latency, authorization failures, and mutation errors.
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

1. Select five active onboarding accounts and three to five internal users.
2. Record baseline response and onboarding metrics.
3. Enter the projects manually and run in parallel with the current process.
4. Review data accuracy and usage daily for the first week.
5. Review support-bot regressions, permission failures, and data gaps weekly.
6. Continue to Phase 2 only when the exit criteria below are met.

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
