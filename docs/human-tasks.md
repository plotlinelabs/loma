# Human tasks

Human handoffs are ordinary taskboard conversations with an immutable
`human_task` request. No new database, App Ninja storage or workflow service.
Pylon links are references only; these tools do not change Pylon tickets.

## Agent tools

Use the current user's personal auth token, never the assignee's credentials:

```sh
python3 tools/human_tasks.py --user-email "$USER_EMAIL" --auth-token "$AUTH_TOKEN" people
python3 tools/human_tasks.py --user-email "$USER_EMAIL" --auth-token "$AUTH_TOKEN" create \
  --source-conversation-id "$CONVERSATION_ID" \
  --assignee finance@example.com --kind approval \
  --title 'Review invoice correction' \
  --details 'Exact customer, invoice IDs, amounts, proposed changes and evidence' \
  --request-key invoice-123-correction \
  --ticket-url https://app.usepylon.com/issues/example
python3 tools/human_tasks.py --user-email "$USER_EMAIL" --auth-token "$AUTH_TOKEN" get --task-id "$TASK_ID"
python3 tools/human_tasks.py --user-email "$USER_EMAIL" --auth-token "$AUTH_TOKEN" assign \
  --task-id "$TASK_ID" --assignee reviewer@example.com --version 1
```

Use `--kind information` for documents or missing information. Include the
specific required action in details. Reuse the request key on retries. Reusing
it for different content is rejected, even after the original is answered.
A new approval scope needs a new request/key. The requester must be the source
conversation's execution identity (`metadata.run_as`, else `metadata.user_name`).
Shared read access and admin status do not authorize acting as another user.
Assignment shares only the task details, not the source's private conversation.

The personal CLI cannot submit human decisions. It can create, inspect and
reassign pending requests to active Loma users. Do not mark a Pylon ticket as
waiting on the customer merely because a human task was created.

## Human response

The assignee gets a task in **Needs input** and a persistent notification.
Open it on desktop/mobile, enter notes, then select **Approve**, **Reject** or
**Provide information** (information requests expose only the latter).
Only the assignee's authenticated dashboard session may respond. The server
checks request/assignment version and records identity, date and exact decision.
Identical retries return the saved answer; conflicting decisions are rejected.
Marking a card done, dragging it or sending a chat message cannot approve it.

The originating agent is queued after any explicit response, using the original
requester's credentials, model and tool scope, with the conversation history
and currently valid agent identity. Rejection/information are not approval.
The agent must recheck external records and follow the existing procedure.
Final text is saved to the source conversation, not automatically sent to Pylon.
The requester receives a completion notification or a review-needed warning.

## Deployment

- Deploy backend and dashboard together.
- Open **Admin > Environment > Human task approvals** and click **Set up approvals**.
  A managed key in the existing Loma database connects the shared session gateway;
  no manual backend/dashboard secret copying is needed. Legacy environment keys
  remain supported until setup. See [setup and security details](human-task-setup.md).
  No separate approval service or bounded-work feature flag is required.
- The existing scheduler defaults to enabled. An explicit `LOMA_ENABLE_SCHEDULER=false` pauses notification delivery and continuation; no new variable is needed.
  Isolated local tests must keep it false and drive the worker with fixtures.
- Existing conversations/notifications collections hold requests and receipts.
  Mongo `_id` uniqueness deduplicates creation and notifications atomically.
- The source's active-run claim serializes continuation with normal chat runs.
  This follows Loma's existing single-backend process assumption.

## Failure and recovery

Pending responses and queued runs survive restart. Once dispatch begins, a
crash, stop or execution failure is **not** automatically replayed because an
external action could already have succeeded. The task becomes `needs_review`,
and the requester must inspect the source and external records, then continue
manually. The general recovery loop also avoids replaying these continuations.
This is not an exactly-once guarantee for external billing operations.

Approval is a scoped human receipt, not a grant to bypass tool/provider checks.
The signed session boundary prevents accidental CLI/API approvals; this is not
an isolation boundary against an arbitrary-code agent with host secrets/database
access. Such isolation requires Loma's separate restricted-worker deployment.

## Tests

`python -m pytest tests/test_human_tasks.py tests/test_task_create.py tests/test_task_list.py tests/test_task_fork.py`

Fixtures use `mongomock-motor`; integration/browser checks must use an isolated
`loma_local_*` Mongo database, scheduler/Slack disabled and fake task data.
