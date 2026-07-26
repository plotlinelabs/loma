# Integration Hub

## Purpose

Integration Hub is a restricted Loma module for managing customer onboarding and
integration work. It combines an onboarding plan with communication recency and
technical evidence so an owner can quickly answer:

- What stage is this customer in?
- Who owns the next action?
- Is Plotline or the customer waiting?
- What is blocking go-live?
- Does product data confirm the reported integration progress?

This directory is the Phase 0 design contract for later implementation.

## Goals

- Provide one operational record for onboarding milestones, tasks, risks, and
  commitments.
- Index relevant customer communication without replacing its source system.
- Verify integration milestones using read-only product data.
- Generate briefings, summaries, recommendations, and follow-up drafts.
- Restrict access to explicitly authorized internal users.
- Keep the existing support bot behavior and state unchanged.

## Non-goals

- Replacing Slack, Pylon, email, HubSpot, Grain, Linear, GitHub, or Drive.
- Sending customer messages automatically.
- Updating, assigning, resolving, or closing support tickets.
- Reusing support-bot prompts, memory, state, or workers.
- Building a customer-facing portal in the first release.
- Implementing billing, renewal, or general CRM workflows.

## System of record boundaries

| Information | System of record | Integration Hub responsibility |
| --- | --- | --- |
| Onboarding plans, milestones, risks, decisions | Integration Hub | Create, update, audit |
| Customer Slack conversations | Slack | Index metadata, summarize, link |
| Support conversations | Pylon | Read status and impact, link |
| Email threads | Email provider | Index authorized threads, link |
| Meetings | Calendar and Grain | Read schedule, extract actions |
| Customer identity and contacts | HubSpot | Resolve and display |
| Engineering blockers | Linear and GitHub | Read status, link |
| Shared documents | Google Drive | Index metadata, link |
| Integration activity | Plotline data stores | Read and verify evidence |

## Phase plan

1. **Phase 0: Architecture and contracts**
   - Product boundary, isolation, data contracts, security, rollout, and backlog.
2. **Phase 1: Manual foundation**
   - Restricted portfolio, customer workspace, action center, projects, tasks,
     milestones, risks, and audit trail.
3. **Phase 2: Communication monitoring**
   - Read-only source ingestion, identity mapping, waiting-state classification,
     communication timers, and overdue alerts.
4. **Phase 3: Technical verification**
   - Read-only evidence checks for SDK initialization, users, events,
     attributes, pages, campaigns, impressions, and production activity.
5. **Phase 4: AI copilot**
   - Summaries, commitment extraction, briefings, recommended actions, and
     follow-up drafts with human approval.
6. **Phase 5: Expanded integrations and reporting**
   - Email, HubSpot, Grain, Calendar, Linear, GitHub, Drive, and portfolio
     analytics.

## Design documents

- [Architecture](architecture.md)
- [Data contracts](data-contracts.md)
- [Phase 1 backlog](phase-1-backlog.md)

## Phase 0 exit criteria

- Product and support-bot boundaries are explicit.
- Initial collections, indexes, lifecycle states, and API contracts are defined.
- The authentication proxy contract is implemented and tested before any
  Integration Hub authorization is trusted.
- Access grants, revocation, audit visibility, and temporary delegation are
  explicit and enforced by the backend.
- `account_id` is the canonical identity term throughout the module.
- Resource mutations and their audit records commit atomically or fail together.
- Every lifecycle enum, transition, archival rule, and restore rule is defined.
- Source connectors are read-only in the initial phases.
- A PR 0 vertical slice validates authentication, MongoDB transactions,
  dashboard proxying, authorization, audit, and feature-flag rollback.
- Phase 1 is divided into independently reviewable pull requests after PR 0.
- Pilot metrics and rollback conditions are documented.

## Decisions required from onboarding users

Before PR 0 is promoted beyond two test accounts, product must validate:

- Whether Android, iOS, Web, backend, push, and infosec are separate concurrent
  projects or workstreams within one project.
- Whether milestones require dependency relationships.
- Whether waiting state belongs to a communication, task, project, or a derived
  project summary.
- Whether a later phase needs collaborator roles in addition to the Phase 1
  rule of one accountable task assignee.
- Whether customer contacts are first-class records.
- The operational distinction between a blocker and a risk.
- The handover acceptance criteria and required sign-off.
- Whether target-date changes require a reason and approval.
- Whether reminder and escalation policies vary by account.
