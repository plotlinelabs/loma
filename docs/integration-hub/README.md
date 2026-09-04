# Integration Hub

Integration Hub is a proposed Loma module for coordinating customer onboarding
and product integration work. This public overview intentionally excludes
internal security design, data contracts, infrastructure details, operational
metrics, and rollout plans.

## Purpose

The module is intended to help authorized teams:

- Maintain a consistent onboarding plan and clear ownership.
- Track milestones, tasks, risks, decisions, and target dates.
- Surface communication recency and pending actions.
- Link back to source systems instead of replacing them.
- Compare reported progress with read-only technical evidence.
- Generate summaries, briefings, recommendations, and follow-up drafts.

## Design principles

- Keep onboarding workflows isolated from support automation.
- Treat connected systems as the source of truth for their own records.
- Start with read-only integrations and human-approved actions.
- Enforce authorization on the server, not only in the interface.
- Minimize copied customer content and preserve source references.
- Keep AI recommendations explainable and reviewable.
- Release behind a disabled-by-default feature flag.

## High-level module boundaries

The module is expected to contain independently testable areas for:

- Onboarding plans and account workspaces.
- Communication metadata and source references.
- Technical progress evidence.
- Recommendations and briefing drafts.
- Access control, auditing, and operational health.

It must not change support conversations, send external messages, or reuse
support automation state.

## Implementation status

The detailed Phase 0 specification is maintained in a restricted company
knowledge base. Implementation remains gated by internal security,
workflow, retention, and validation reviews.

Contributors should request the internal specification from the project owner
before implementing routes, storage, permissions, connectors, or workers.
