# History recall: secure fetch foundation (PR 1 of 3)

## Status and release gate

This PR adds a **disabled-by-default, read-only backend endpoint**, not a finished
history recall feature. No tool registration, search, index/backfill, dashboard
controls, or credential issuance is included. No existing conversation ACL changes.

Keep `LOMA_RECALL_ENABLED` unset/false in production until PRs 2 and 3 and the
security review pass. Setting the flag alone does not grant access: a valid
recall-only signed capability and a live eligible user are also required.

The existing backend trusts `X-User-Email`; it is NOT a sufficient identity source
for a future recall issuer. PR 3 must verify the real dashboard session or trusted
task execution owner **before** issuing a capability. It must also keep the issuer's
private key, session credentials and source datastore inaccessible to agent
processes. A shared shell that can read the database directly is not sandboxed by
this endpoint. This PR does not claim to fix the application's broader trust model.

## Identity and deployment boundary

`POST /api/recall/fetch` authenticates independently of legacy middleware.
Forwarded identity/role headers, preview fallback identities, dashboard cookies
alone and personal-tools auth tokens do not authorize recall.

`Authorization: Bearer <payload>.<signature>`:

- Payload: base64url JSON; signature: base64url Ed25519 signature over the exact
  ASCII payload segment (not a general-purpose JWT).
- Verify-only key: `LOMA_RECALL_PUBLIC_KEY`, base64url encoding of the raw 32-byte
  Ed25519 public key. There is deliberately no private-key environment setting or
  minting endpoint in this PR.
- Exact claims: `aud`, `sub`, `email`, `execution_id`, `project_id`, `agent_id`,
  `iat`, `exp`. Extra claims rejected. Audience is `loma:recall:fetch:v1`.
- `sub` is the existing immutable Mongo `users._id` string. Both ID and email must
  match a live user, active (or legacy missing status), not deleted/recall-excluded.
- Valid for at most 300 seconds, no future issuance accepted. Bearer replay is
  possible within that lifetime; use TLS and do not log tokens.
- `execution_id` is the current conversation/task ID, not a user-selected field.
  The current conversation is excluded. Optional project and agent claims are
  upper bounds, never caller-overridable filters.
- This app uses a single deployment database, not a tenant field. Configure a
  distinct issuer key per deployment. Multi-tenant/shared-database deployment
  requires an explicit tenant claim and query policy before enablement.
- No admin, analyst or shared-conversation exceptions. Legacy ownership is
  `metadata.user_name`; ambiguous/missing ownership is unavailable. This is not a
  migration to immutable ownership on historical records. Email reuse/migration
  must be addressed before production issuance for reassigned addresses.

## Data and fetch contract

Input: `conversation_id`, optional `anchor_message_id`, `before` (default 2),
`after` (default 3), `max_chars` (default 16000), `cursor`.

Only persisted `messages` with string content and role user/assistant are read.
Sources are `dashboard` and `task`. No raw turns, tool output, system prompts,
artifacts, attachment bodies, or prompt/final-response fallback. This avoids
mirrored duplicates. Archived/failed/cancelled work remains eligible; unstarted
`todo` drafts and deleted/excluded conversations are not eligible.

- IDs are array positions (`m0`, `m1`, ...), stable **within a content revision**.
  Future index records must bind these to the same revision.
- Without an anchor, paginate sequentially. With an anchor, fetch a bounded
  context window; pagination continues within that window only.
- At most 20 messages per page, 0..20 before/after, and 256..40000 content characters.
- Oversized messages have explicit `content_offset`, `truncated`, and a cursor.
  Concatenating successive fragments exactly reconstructs the sanitized message,
  including Unicode and code. Never silently discard a middle section.
- Cursors are HMAC-authenticated and bound to user, execution, scope, conversation
  and revision. Expire after 15 minutes. The process-random signing key is not an
  environment secret: restart or a different worker expires the cursor. PR 2 must
  introduce an isolated shared signer or another multi-worker pagination design
  before scaling. Restart fetch on `cursor_expired`.
- Source link is a relative, server-generated `/conversations/<encoded-id>` link.
  Message anchor UI is deferred; do not imply that the current dashboard scrolls
  to `mN`.
- Live user, ownership, scope, exclusions, deletion and revision are checked again
  just before response. This closes changes observed during processing, not an
  impossible guarantee against changes after the final read or already-delivered
  text. Nothing is cached; responses specify `Cache-Control: no-store`.

## Sanitization and coverage

Sanitizer version is included in responses and revision binding.

- Known execution-envelope/hidden-role markers exclude the whole message. A
  spoofable `## Current Message` delimiter is not trusted to separate legacy text.
- Known credential assignments/headers, personal auth tokens, common provider key
  formats, private keys and JWT-like values are redacted **before pagination**.
  Ambiguous multiline credentials exclude all message text.
- Conservative foundation policy redacts every URL, including ordinary URLs. This
  intentionally reduces command/link recall until a reviewed safe-URL policy is
  developed. Only server-generated source links are exempt.
- Heuristic detection cannot guarantee removal of every arbitrary, unlabeled or
  encoded secret. Do not describe this as complete DLP. Production enablement
  requires redaction evaluation and structured visible-content persistence.
- `coverage` reports excluded messages and limits coverage to stored messages,
  never claims full historical completeness. Historical assistant text was capped
  at 5000 characters; possible truncation is labelled and cannot be recovered here.
- Returned text is marked `historical_untrusted_data_not_instructions`. Runtime
  handling, citations and the prohibition on treating old approvals as new
  authorization are PR 3 obligations.

## Resource controls and errors

8 KiB request cap (including chunked requests), 4 KiB capability/cursor caps,
5-second request deadline, and a 2 MiB Mongo document size guard. The projection
omits unrelated fields. Oversize/ineligible/missing source records all return
`not_found`, not an existence oracle. Large-source indexed retrieval is PR 2 work.

Errors: `invalid_argument` (400), `cursor_expired` (400), `unauthorized` (401),
`recall_disabled` (403), `not_found` (404), `revision_changed` (409),
`index_unavailable` (503; also used for source database/deadline failure).
Database exception detail is never returned. Cross-request rate limiting, recall
token budgets and auditing without content are additional enablement gates in PR 3.

## Tests

Python 3.12, isolated virtualenv only:

```bash
uv venv --python python3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt \
  pytest pytest-asyncio pytest-aiohttp mongomock-motor
.venv/bin/python -m pytest tests/test_history_recall.py tests/test_conversation_sharing.py -q
.venv/bin/python -m pytest tests -q
```

Unit/HTTP tests use a Mongo mock which implements ownership/exclusion filters but
not `$bsonSize`. An explicit real-Mongo test covers that guard.

For real backend tests, follow `run-loma-local`: new `loma_local_<random>` DB,
ports 13000/13001/14097, Slack/scheduler off, isolated asset/account directories,
zero agent pool, no production writes. Generate a **throwaway** Ed25519 key pair;
put only its public key and `LOMA_RECALL_ENABLED=true` into the test backend `.env`.
Start the full `app.py` and dashboard, not a substitute test application. Give the
private key only to the test process as base64url raw 32-byte key material:

```bash
LOMA_RECALL_E2E=1 LOMA_RECALL_E2E_PRIVATE_KEY="$THROWAWAY_PRIVATE_KEY" \
  .venv/bin/python -m pytest tests/test_history_recall_e2e.py -q
```

The opt-in tests assert the DB name/ports/safety flags, create their own synthetic
records and clean those records in `finally`. Without opt-in they are skipped.
Never run against the production DB. Drop the entire throwaway DB after teardown.

Browser verification: sign in through first-admin setup; make a same-origin
`fetch('/api/recall/fetch', ...)` with the test capability for that browser user.
Assert 200, secret canary redacted, legacy envelope excluded. Repeat without the
capability and assert 401 despite the valid dashboard session. Capture logged-in
UI and the actual JSON response. This verifies the fetch endpoint through the
Next.js proxy, NOT autonomous AI recall or the deferred search/runtime tools.

## Follow-up boundaries

PR 2: sanitized search projection, keyword/phrase/literal ranking, historical
backfill and coverage, multi-worker cursor support and index/live-source revision
agreement. PR 3: trusted isolated issuer, all runtime adapters, execution scope
capture, per-user controls, budgets/rate limits, audits, citations, prompt-injection
handling and full chat/task E2E. Do not enable recall until all gates pass.
