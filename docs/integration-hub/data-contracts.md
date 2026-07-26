# Integration Hub data contracts

## Conventions

- Collection names use the `integration_` prefix.
- Public IDs are UUID strings and are separate from MongoDB `_id`.
- All timestamps are UTC BSON datetimes.
- Mutable operational records include `created_at`, `created_by`, `updated_at`,
  and `updated_by`. This applies to accounts, source mappings, access grants,
  projects, milestones, tasks, risks, and commitments.
- Append-only records are exempt from `updated_at` and `updated_by`:
  interactions, evidence observations, audit logs, and completed AI-run
  records. Corrections create a superseding record or an audit-linked review
  record rather than mutating the original observation.
- Archivable records include `archived_at` and `archived_by`; APIs do not
  hard-delete operational records.
- Records use optimistic concurrency through an integer `version`.
- Source payloads are not copied unless required for a documented workflow.
- `account_id` is canonical. The term `customer_id` is not used in storage or
  API contracts.

## Collections

### `integration_accounts`

Canonical customer identity and ownership.

```json
{
  "account_id": "acc_uuid",
  "name": "Acme",
  "status": "active",
  "owners": {
    "primary_email": "owner@plotline.so",
    "technical_email": "engineer@plotline.so",
    "backup_email": "manager@plotline.so"
  },
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "archived_at": null,
  "archived_by": null,
  "version": 1
}
```

Indexes:

- Unique: `account_id`
- `owners.primary_email`, `status`

### `integration_source_mappings`

Source identifiers, approval history, and synchronization health are normalized
outside the account record.

```json
{
  "mapping_id": "map_uuid",
  "account_id": "acc_uuid",
  "source": "slack",
  "tenant_id": "T123",
  "external_id": "C123",
  "status": "confirmed",
  "proposed_by": "resolver:domain-v1",
  "confirmed_by": "user@plotline.so",
  "confirmed_at": "BSON datetime",
  "exception_reason": null,
  "source_metadata": {},
  "sync": {
    "cursor": "opaque provider cursor",
    "last_success_at": null,
    "last_error_at": null,
    "last_error_code": null
  },
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "archived_at": null,
  "archived_by": null,
  "version": 1
}
```

Indexes:

- Unique: `mapping_id`
- Unique partial: `source`, `tenant_id`, `external_id` where `status` is
  `confirmed` and `archived_at` is null
- `account_id`, `source`, `status`

Raw API keys and credentials are prohibited. Only safe provider identifiers or
irreversible, versioned fingerprints may be stored.

### `integration_account_access`

Explicit access grants are separate from operational ownership fields so access
can be delegated, expired, and revoked deterministically.

```json
{
  "grant_id": "grt_uuid",
  "account_id": "acc_uuid",
  "principal_type": "user",
  "principal_id": "user@plotline.so",
  "scope": "manage",
  "source": "account_technical_owner",
  "source_id": "acc_uuid",
  "reason": "Technical onboarding owner",
  "starts_at": "BSON datetime",
  "expires_at": null,
  "revoked_at": null,
  "revoked_by": null,
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "version": 1
}
```

Enums:

- `principal_type`: `user`, `team`
- `scope`: `view`, `manage`, `audit`
- `source`: `account_primary_owner`, `account_technical_owner`,
  `account_backup_owner`, `project_owner`, `team_membership`, `explicit_acl`,
  `temporary_delegation`

Indexes:

- Unique: `grant_id`
- `account_id`, `principal_type`, `principal_id`, `revoked_at`, `expires_at`
- Unique partial: `account_id`, `principal_type`, `principal_id`, `scope`,
  `source`, `source_id` where `revoked_at` is null

### `integration_projects`

One onboarding project per account and implementation scope.

```json
{
  "project_id": "prj_uuid",
  "account_id": "acc_uuid",
  "name": "Mobile SDK onboarding",
  "owner_emails": ["owner@plotline.so"],
  "stage": "kickoff",
  "status": "active",
  "health": "on_track",
  "health_reason": "Kickoff completed and no overdue actions",
  "health_source": "manual",
  "health_updated_at": "BSON datetime",
  "health_updated_by": "user@plotline.so",
  "target_go_live_at": "BSON datetime",
  "target_date_change_reason": null,
  "next_action_task_id": "tsk_uuid",
  "playbook_key": "android_standard",
  "playbook_version": 3,
  "instantiated_at": "BSON datetime",
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "archived_at": null,
  "archived_by": null,
  "version": 1
}
```

Indexes:

- Unique: `project_id`
- `account_id`, `stage`, `status`, `health`, `target_go_live_at`
- `next_action_task_id`

`next_action_task_id` is optional and must reference a non-terminal task
(`open`, `in_progress`, or `blocked`) in the same project. Moving the referenced
task to `done` or `cancelled` must atomically clear or replace the reference.
Its display assignee and due date are always derived from that task. A
future executive narrative, if needed, must be named `next_action_summary` and
must not represent task state.

### `integration_milestones`

```json
{
  "milestone_id": "mil_uuid",
  "project_id": "prj_uuid",
  "key": "custom_events_received",
  "title": "Custom events received",
  "status": "not_started",
  "verification": {
    "mode": "manual",
    "state": "unverified",
    "last_checked_at": null,
    "evidence_ids": []
  },
  "owner_type": "customer",
  "owner": "Customer engineering",
  "depends_on_milestone_ids": [],
  "due_at": "BSON datetime",
  "completed_at": null,
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "version": 1
}
```

Indexes:

- Unique: `milestone_id`
- Unique: `project_id`, `key`
- `project_id`, `status`, `due_at`

### `integration_tasks`

```json
{
  "task_id": "tsk_uuid",
  "project_id": "prj_uuid",
  "milestone_id": "mil_uuid",
  "summary": "Validate missing payment event",
  "status": "open",
  "assignee": {
    "type": "plotline_user",
    "email": "user@plotline.so",
    "display_name": "Vamsi Madhav"
  },
  "due_at": "BSON datetime",
  "source_references": [],
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "version": 1
}
```

Phase 1 uses one accountable assignee per task. `assignee.type` is
`plotline_user` or `customer_contact`; `email` is required for a Plotline user
and optional for a customer contact, while `display_name` is always required.
Collaborators may be represented by source references but are not task owners.
Multiple accountable assignees require a future contract change.

Indexes:

- Unique: `task_id`
- `project_id`, `status`
- `assignee.email`, `status`, `due_at`

### `integration_risks`

```json
{
  "risk_id": "rsk_uuid",
  "project_id": "prj_uuid",
  "title": "Infosec approval pending",
  "severity": "high",
  "status": "open",
  "mitigation": "Schedule security review",
  "source_references": [],
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "version": 1
}
```

Indexes:

- Unique: `risk_id`
- `project_id`, `status`, `severity`

### `integration_interactions`

Introduced in Phase 2. Stores normalized metadata and classification.

```json
{
  "interaction_id": "int_uuid",
  "account_id": "acc_uuid",
  "source": "slack",
  "tenant_id": "T123",
  "source_id": "message_or_thread_id",
  "source_url": "https://source.example/item",
  "occurred_at": "BSON datetime",
  "direction": "customer_to_plotline",
  "classification": "technical_question",
  "requires_response": true,
  "meaningful_contact": true,
  "conversation_state": "waiting_on_plotline",
  "summary": "Customer asked for event validation.",
  "confidence": 0.94,
  "classifier_version": "rules-v1",
  "human_status": "unreviewed"
}
```

Indexes:

- Unique: `source`, `tenant_id`, `source_id`
- `account_id`, `occurred_at`
- `conversation_state`, `occurred_at`

### `integration_commitments`

Introduced in Phase 2 and human-confirmed before becoming official.

```json
{
  "commitment_id": "com_uuid",
  "project_id": "prj_uuid",
  "summary": "Deploy Android SDK to production",
  "owner_type": "customer",
  "owner": "Customer engineering",
  "due_at": "BSON datetime",
  "status": "proposed",
  "source_references": [],
  "confidence": 0.87,
  "confirmed_by": null,
  "confirmed_at": null,
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "version": 1
}
```

### `integration_evidence`

Introduced in Phase 3.

```json
{
  "evidence_id": "evd_uuid",
  "account_id": "acc_uuid",
  "project_id": "prj_uuid",
  "milestone_key": "custom_events_received",
  "environment": "production",
  "state": "partial",
  "observed_at": "BSON datetime",
  "fresh_until": "BSON datetime",
  "source": "clickhouse",
  "summary": "6 of 8 expected events detected",
  "details": {
    "expected_count": 8,
    "detected_count": 6
  },
  "check_version": "events-v1"
}
```

### `integration_audit_logs`

Append-only mutation record.

```json
{
  "audit_id": "aud_uuid",
  "occurred_at": "BSON datetime",
  "actor_email": "user@plotline.so",
  "request_id": "request_uuid",
  "action": "project.update",
  "target_type": "project",
  "target_id": "prj_uuid",
  "account_id": "acc_uuid",
  "before": {"stage": "sdk_installation"},
  "after": {"stage": "event_validation"}
}
```

Indexes:

- Unique: `audit_id`
- `account_id`, `occurred_at`
- `actor_email`, `occurred_at`
- `target_type`, `target_id`, `occurred_at`

### `integration_ai_runs`

Introduced in Phase 4. Stores metadata, not unrestricted source content.

```json
{
  "run_id": "air_uuid",
  "account_id": "acc_uuid",
  "purpose": "daily_briefing",
  "prompt_version": "briefing-v1",
  "source_references": [],
  "result_reference": "generated_artifact_id",
  "confidence": 0.89,
  "status": "completed",
  "review": {
    "decision": "approved",
    "reviewer": "user@plotline.so",
    "reviewed_at": "BSON datetime"
  },
  "created_at": "BSON datetime"
}
```

## Lifecycle enums

### Project stage

`kickoff`, `sdk_installation`, `identification`, `events_attributes`,
`pages_elements`, `test_validation`, `production_deployment`,
`first_campaign`, `handover`

### Project lifecycle status

`active`, `paused`, `completed`, `cancelled`

### Project health

`on_track`, `needs_attention`, `blocked`, `silent`, `at_risk`, `escalated`

Health is explained state, never a bare label. `health_source` is `manual`,
`rule`, or `ai`; AI health remains advisory until human confirmation.

### Account status

`active`, `inactive`

### Source mapping status

`proposed`, `confirmed`, `rejected`

### Conversation state

`waiting_on_plotline`, `waiting_on_customer`, `internally_blocked`, `resolved`,
`monitoring`, `no_action_required`

### Milestone status

`not_started`, `in_progress`, `blocked`, `complete`, `skipped`

### Task status

`open`, `in_progress`, `blocked`, `done`, `cancelled`

### Risk status

`open`, `mitigated`, `accepted`, `closed`

### Commitment status

`proposed`, `confirmed`, `rejected`, `in_progress`, `fulfilled`, `cancelled`

### Verification state

`unverified`, `verified`, `partial`, `conflicting`, `stale`, `unavailable`

## Transition and archival rules

- Project stage may move forward or backward while status is `active`; every
  backward move requires a reason in the audit record.
- `active -> paused -> active` preserves the last stage.
- `active|paused -> completed|cancelled`; reopening either terminal status
  requires `manage_all`, a reason, and an audit entry.
- Archival is represented only by `archived_at` and `archived_by`, not by a
  lifecycle status. Archive is reversible by an administrator and does not
  alter the operational `status`.
- Archiving an account hides all children from normal queries but does not
  mutate their lifecycle status. Restore restores visibility, not prior task
  state.
- Completed projects are read-only except for archive, restore, and authorized
  reopen operations.
- `task: open -> in_progress|blocked|done|cancelled`; blocked tasks may return to
  open or in progress; done and cancelled require an authorized reopen.
- `risk: open -> mitigated|accepted|closed`; terminal risks require an
  authorized reopen to become open.
- `commitment: proposed -> confirmed|rejected`; confirmed may move to in
  progress, fulfilled, or cancelled. Rejected commitments are immutable.
- Confirming or rejecting a source mapping requires an authorized human actor.
- Hard delete is not exposed. Retention cleanup is a separately approved,
  audited administrative process.

## Initial REST API contract

All routes are under `/api/integration-hub` and return JSON.

- Lists use opaque cursor pagination, default 50, maximum 100.
- Sorting is stable and ends with the resource public ID as a tie-breaker.
- Unknown filters return `400`; multiple values for one filter use OR, while
  different filter fields use AND.
- `ETag` is the quoted integer record version. `PATCH` requires `If-Match`;
  stale versions return `412`.
- `POST` requires an `Idempotency-Key`. A key is scoped to actor, route, and
  account for 24 hours; replay returns the original result.
- `PATCH` uses JSON Merge Patch semantics. Immutable IDs, audit fields, and
  derived fields cannot be patched.
- Initial limits are 120 reads and 30 mutations per user per minute, returning
  `429` with `Retry-After`.
- Bulk playbook instantiation is one atomic endpoint with a maximum of 100 child
  records. General bulk mutation is out of scope for Phase 1.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/accounts` | List accounts visible to the user |
| `POST` | `/accounts` | Create an account |
| `GET` | `/accounts/{account_id}` | Get Customer 360 foundation |
| `PATCH` | `/accounts/{account_id}` | Update identity or ownership |
| `POST` | `/accounts/{account_id}/archive` | Archive account |
| `POST` | `/accounts/{account_id}/restore` | Restore account |
| `GET` | `/projects` | List portfolio projects |
| `POST` | `/projects` | Create onboarding project |
| `GET` | `/projects/{project_id}` | Get project and summary counts |
| `PATCH` | `/projects/{project_id}` | Update stage, target, health, or next-action reference |
| `POST` | `/projects/{project_id}/archive` | Archive project without changing lifecycle status |
| `POST` | `/projects/{project_id}/restore` | Restore archived project visibility |
| `POST` | `/projects/{project_id}/instantiate-playbook` | Create versioned milestones and tasks atomically |
| `GET` | `/projects/{project_id}/milestones` | List milestones |
| `POST` | `/projects/{project_id}/milestones` | Add milestone |
| `PATCH` | `/milestones/{milestone_id}` | Update milestone |
| `GET` | `/projects/{project_id}/tasks` | List tasks |
| `POST` | `/projects/{project_id}/tasks` | Add task |
| `PATCH` | `/tasks/{task_id}` | Update task |
| `GET` | `/projects/{project_id}/risks` | List risks |
| `POST` | `/projects/{project_id}/risks` | Add risk |
| `PATCH` | `/risks/{risk_id}` | Update risk |
| `GET` | `/actions` | Current user's due and overdue actions |
| `GET` | `/audit` | Authorized audit query |

### Error shape

```json
{
  "error": {
    "code": "version_conflict",
    "message": "The record changed since it was loaded.",
    "request_id": "request_uuid"
  }
}
```

Expected status codes:

- `400` invalid request
- `401` unauthenticated
- `403` feature disabled or unauthorized
- `404` not found or not visible to the caller
- `409` duplicate or idempotency conflict
- `412` ETag/version precondition failed
- `429` rate limit exceeded
- `422` valid JSON with invalid domain transition
- `503` dependency unavailable

## Validation rules

- Account and project names are required and length-limited.
- Emails are normalized to lowercase.
- Source URLs must use `https`.
- Stage transitions are explicit; terminal projects cannot silently return to an
  active stage.
- Completion timestamps are server-generated.
- Account-scoped resources must reference an account visible to the caller.
- Resource, access-grant, audit, and optional outbox writes use one MongoDB
  transaction. Any failure aborts the mutation.
- AI proposals cannot directly change official project, task, milestone, risk, or
  commitment state.
