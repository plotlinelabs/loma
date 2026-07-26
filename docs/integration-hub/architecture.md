# Integration Hub architecture

## Context

Loma currently has an aiohttp backend, a Next.js dashboard, MongoDB-backed
state, an integration registry, scheduled and webhook flows, and existing
support workflows. Integration Hub will be added as an isolated application
module within the same deployment.

## Logical components

```text
Slack / Pylon / Email / Grain / Calendar / HubSpot / Linear / GitHub / Drive
                                  |
                         read-only source adapters
                                  |
                      source event normalization
                                  |
                       customer identity resolver
                                  |
          +-----------------------+-----------------------+
          |                                               |
 communication state and SLA engine              integration evidence workers
          |                                               |
          +-----------------------+-----------------------+
                                  |
                       Integration Hub collections
                                  |
             REST API + server-side authorization + audit
                                  |
                 Portfolio / Customer 360 / Action Center
                                  |
             AI summaries and drafts with approval gates
```

## Module boundary

Phase 1 should introduce dedicated modules rather than adding Integration Hub
logic to `api/routes.py` or support handlers:

```text
api/integration_hub_routes.py
integration_hub/
  models.py
  repository.py
  service.py
  authorization.py
  audit.py
dashboard/src/app/integration-hub/
```

Route registration may occur in the existing API router, but handlers and
business logic remain in the dedicated module.

## Support bot isolation

Integration Hub and the support bot may read the same external source, but they
must not share mutable workflow state.

### Required controls

1. **Separate routes**
   - Integration Hub APIs use `/api/integration-hub/*`.
   - Existing support and webhook routes are not modified for Phase 1.
2. **Separate collections**
   - All new collections use the `integration_` prefix.
   - No Integration Hub fields are added to support ticket records.
3. **Separate workers**
   - Future ingestion, evidence, and alert jobs have distinct worker identities,
     leases, retry policies, and dead-letter records.
4. **Separate AI policies**
   - Integration Hub prompts do not inherit support reply instructions.
   - The Integration Hub tool allowlist excludes external send, reply, assign,
     close, and resolve operations.
5. **Read-only source adapters**
   - Slack, Pylon, email, CRM, ticketing, and meeting connectors only read during
     the initial rollout.
6. **Independent idempotency**
   - Normalized source records are uniquely keyed by source, tenant, and source
     object ID. Reading the same event in two Loma consumers does not make one
     consumer acknowledge the other.

### Explicitly prohibited behavior

- Mutating a Pylon ticket or support conversation.
- Posting to a customer Slack channel.
- Sending an email or calendar invitation.
- Updating HubSpot, Linear, GitHub, or Drive.
- Marking a support issue resolved because an onboarding task is complete.
- Using support-bot memory to determine official onboarding state.

## Feature flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `LOMA_ENABLE_INTEGRATION_HUB` | `false` | Enables routes and dashboard entry |
| `LOMA_INTEGRATION_HUB_ALLOWED_EMAILS` | empty | Comma-separated pilot allowlist |
| `LOMA_ENABLE_INTEGRATION_HUB_INGESTION` | `false` | Enables future source ingestion |
| `LOMA_ENABLE_INTEGRATION_HUB_AI` | `false` | Enables future AI suggestions |

The backend must enforce flags and permissions independently of UI visibility.
An empty allowlist denies access unless a future admin-configured role grants it.

## Authorization model

Phase 1 uses defense in depth:

1. The existing authentication middleware resolves the user and system role.
2. The Integration Hub route guard verifies the feature flag.
3. The route guard verifies explicit access through an allowlist or stored
   permission.
4. Customer-scoped authorization limits non-manager users to assigned accounts.
5. Every mutation records actor, timestamp, request ID, action, target, and a
   redacted before/after summary.

Proposed permissions:

- `integration_hub:view_assigned`
- `integration_hub:view_all`
- `integration_hub:manage_assigned`
- `integration_hub:manage_all`
- `integration_hub:admin`

Roles must map to permissions in configuration or governance data. API handlers
must check permissions, not role names.

## Customer identity

Every record resolves to a canonical `customer_id`. Source mappings support
multiple identifiers per customer, including:

- HubSpot company IDs and related company IDs
- Email domains
- Slack workspace and channel IDs
- Pylon organization IDs
- Plotline organization, environment, and API keys
- Linear project or label IDs
- Drive folder IDs

Automatic matching may propose mappings, but a human must confirm ambiguous
matches. A source identifier cannot be actively mapped to two customers unless
an administrator records an explicit exception.

## Source adapter contract

Each future adapter should implement:

```python
class IntegrationSourceAdapter(Protocol):
    source: str

    async def health(self) -> SourceHealth: ...
    async def backfill(self, cursor: str | None) -> SourceBatch: ...
    async def poll(self, cursor: str | None) -> SourceBatch: ...
    async def fetch_reference(self, source_id: str) -> SourceReference: ...
```

Adapters return normalized source records and never write to the provider. Each
batch contains an opaque continuation cursor. Reprocessing is safe.

## Failure handling

- Source outages mark the source stale instead of marking customers healthy.
- Each source mapping stores the last successful sync and latest error summary.
- Retries use bounded exponential backoff and a dead-letter record after the
  configured maximum.
- AI failures never block access to source records or manual onboarding work.
- Low-confidence classifications remain unconfirmed and do not trigger
  escalations.
- Technical evidence includes `fresh`, `stale`, `partial`, `conflicting`, and
  `unavailable` states.

## Data minimization and retention

- Prefer metadata, summaries, extracted actions, and source links over copying
  full message bodies.
- Encrypt connector credentials using the existing integration credential
  mechanism.
- Redact secrets and sensitive values from AI inputs and audit records.
- Respect source deletion and access changes.
- Make retention configurable per source and record type.
- Do not include customer data in logs, test fixtures, or committed files.

## Observability

Required metrics:

- API request count, latency, and authorization failures
- Source sync success, lag, errors, and dead-letter count
- Identity mapping coverage and ambiguity rate
- Waiting-state classification confidence and correction rate
- Evidence freshness and verification failures
- Alert volume, dismissal rate, and false-positive rate
- AI run latency, cost, confidence, approval, and rejection rate

All automated decisions must retain the input source references, rule or prompt
version, result, confidence, and human correction.

## Rollout and rollback

1. Enable the base feature for 3 to 5 internal users.
2. Add 5 active onboarding customers through manual entry.
3. Run alongside the existing process for two weeks.
4. Validate identity mapping and communication classifications before enabling
   alerts.
5. Expand to 20 customers, then to the onboarding team.

Rollback disables the feature flag and workers. Existing support workflows
continue because no support routes, collections, or state are changed.

## Phase metrics

Baseline and compare:

- Median customer first-response time
- Percentage of questions answered within SLA
- Accounts without meaningful contact for 7 days
- Median kickoff-to-SDK-initialization time
- Median kickoff-to-first-production-campaign time
- Number of accounts missing an owner or next action
- Time spent preparing for customer calls
