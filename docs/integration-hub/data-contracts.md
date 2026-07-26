# Integration Hub data contracts

## Conventions

- Collection names use the `integration_` prefix.
- Public IDs are UUID strings and are separate from MongoDB `_id`.
- All timestamps are UTC BSON datetimes.
- Mutable records include `created_at`, `created_by`, `updated_at`, and
  `updated_by`.
- Records use optimistic concurrency through an integer `version`.
- Source payloads are not copied unless required for a documented workflow.

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
  "source_mappings": [
    {
      "source": "slack",
      "tenant_id": "T123",
      "external_id": "C123",
      "status": "confirmed"
    }
  ],
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "version": 1
}
```

Indexes:

- Unique: `account_id`
- Unique partial: `source_mappings.source`, `source_mappings.tenant_id`,
  `source_mappings.external_id` where mapping status is confirmed
- `owners.primary_email`, `status`

### `integration_projects`

One onboarding project per account and implementation scope.

```json
{
  "project_id": "prj_uuid",
  "account_id": "acc_uuid",
  "name": "Mobile SDK onboarding",
  "stage": "kickoff",
  "health": "on_track",
  "target_go_live_at": "BSON datetime",
  "next_action": {
    "summary": "Confirm Android SDK installation",
    "owner_type": "customer",
    "owner": "Customer engineering",
    "due_at": "BSON datetime"
  },
  "playbook_key": "android_standard",
  "created_at": "BSON datetime",
  "created_by": "user@plotline.so",
  "updated_at": "BSON datetime",
  "updated_by": "user@plotline.so",
  "version": 1
}
```

Indexes:

- Unique: `project_id`
- `account_id`, `stage`, `health`, `target_go_live_at`
- `next_action.owner`, `next_action.due_at`

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
  "due_at": "BSON datetime",
  "completed_at": null,
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
  "owner_type": "plotline",
  "owner": "user@plotline.so",
  "due_at": "BSON datetime",
  "source_references": [],
  "version": 1
}
```

Indexes:

- Unique: `task_id`
- `project_id`, `status`
- `owner`, `status`, `due_at`

### `integration_risks`

```json
{
  "risk_id": "rsk_uuid",
  "project_id": "prj_uuid",
  "title": "Infosec approval pending",
  "severity": "high",
  "status": "open",
  "owner": "user@plotline.so",
  "mitigation": "Schedule security review",
  "source_references": [],
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
`first_campaign`, `handover`, `complete`, `paused`, `cancelled`

### Project health

`on_track`, `needs_attention`, `blocked`, `silent`, `at_risk`, `escalated`

### Conversation state

`waiting_on_plotline`, `waiting_on_customer`, `internally_blocked`, `resolved`,
`monitoring`, `no_action_required`

### Milestone status

`not_started`, `in_progress`, `blocked`, `complete`, `skipped`

### Verification state

`unverified`, `verified`, `partial`, `conflicting`, `stale`, `unavailable`

## Initial REST API contract

All routes are under `/api/integration-hub` and return JSON. Mutations require an
`If-Match` version or a version in the request body to prevent lost updates.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/accounts` | List accounts visible to the user |
| `POST` | `/accounts` | Create an account |
| `GET` | `/accounts/{account_id}` | Get Customer 360 foundation |
| `PATCH` | `/accounts/{account_id}` | Update identity or ownership |
| `GET` | `/projects` | List portfolio projects |
| `POST` | `/projects` | Create onboarding project |
| `GET` | `/projects/{project_id}` | Get project and summary counts |
| `PATCH` | `/projects/{project_id}` | Update stage, target, or next action |
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
- `409` duplicate or optimistic concurrency conflict
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
- Audit entries are written in the same service operation as each mutation.
- AI proposals cannot directly change official project, task, milestone, risk, or
  commitment state.
