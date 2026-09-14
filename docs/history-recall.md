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
  pytest pytest-asyncio
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

## Mandatory live chat regression gate (every recall PR)

Run on each PR's exact head, not only once for the series. Login and synthetic
recall fixtures are not sufficient. A mocked responder, HTTP 200, or `[DONE]`
without a valid answer is **not** a passing agent test.

1. Follow `run-loma-local` with a fresh `loma_local_*` DB, isolated asset and
   provider-account directories, Slack/scheduler off and offset ports. Never copy
   other users' credentials, histories or integrations. Connect the requesting
   user's provider in the isolated stack; empty provider pools are blockers, not
   reasons to substitute canned replies. For this test no external tools are needed.
2. Run the unchanged `app.py` and dashboard with recall **disabled**, as shipped.
   Use one real-model pool worker (the zero-pool setup above is only for retrieval
   fixtures). Set a valid model in both the backend and test environment.
3. Install Playwright in a separate test environment if unavailable; point
   `NODE_PATH` to that environment's `node_modules` and install its Chromium.
4. Export the local first-admin login inputs without logging them, then run:

```bash
LOMA_CHAT_E2E=1 \
LOMA_CHAT_BASE_URL=http://localhost:13001 \
LOMA_CHAT_MODEL="$TEST_MODEL" \
LOMA_EMAIL="$TEST_EMAIL" LOMA_PASSWORD="$TEST_PASSWORD" LOMA_TOKEN="${TEST_SETUP_TOKEN}" \
LOMA_CHAT_EVIDENCE=/tmp/loma-chat-evidence \
node scripts/browser/chat-smoke.cjs
```

Optional `LOMA_CHROMIUM_PATH` selects an installed Chromium executable. The script
rejects non-local URLs and requires explicit opt-in because it uses live inference.

It verifies real UI submission, expected non-canned assistant content, successful
SSE completion without provider errors, visible reply, persistence after reload,
a second real response using previous context, same conversation ID and follow-up
persistence. Evidence is a content-minimal JSON result and screenshots. Provider
failure must fail the run even when delivered as an ordinary assistant message.

Record the exact commit, model, flag state and test result in the PR. Inspect
screenshots for credentials or private content before attaching. Do not publish
raw request payloads, provider logs, environment files, or auth storage. Teardown
by saved PID/cwd, remove copied credentials and drop only the throwaway DB.

PR 2 and PR 3 must rerun this baseline. PR 3 additionally needs recall-enabled
chat **and task** execution, search/fetch tool invocation, citations, isolation,
revocation, error recovery and runtime-budget tests. Passing the baseline does
not prove autonomous recall or cover every provider/model. Run the baseline for
each runtime affected by a PR; explicitly mark unavailable runtimes as unverified.

## CI dependency and local environment parity

CI installs `requirements.txt` plus `pytest` and `pytest-asyncio`. The Mongo
mock and aiohttp test fixtures are pinned in that shared requirements file so test collection works without
changing the workflow. This also installs these test packages in runtime environments; they
are not used by production code. A separate test dependency file can be introduced
later with a workflow update.

The default-model test assumes `AGENT_DEFAULT_MODEL` is unset, as on GitHub
Actions. When running from a deployed Loma environment, use
`env -u AGENT_DEFAULT_MODEL .venv/bin/python -m pytest -q` for CI parity.

## Search/indexing batch (same PR, September 14)

`POST /api/recall/search` now shares fetch's signed identity and source policy.
Input: `query` (1..1000 characters), `match_mode` (`keywords`, `phrase`, `literal`),
`limit` (1..20, default 8), `cursor`, optional `filters` (`project_id`, `agent_id`,
`after`, `before`, `kind=any|chat|task`). Caller filters can narrow but never widen
signed scope. Dates require explicit timezones; `before` is exclusive. Legacy
naive stored Mongo dates are interpreted as UTC. Drafts remain excluded.

Keyword ranking weights message matches over title matches, then uses stable
conversation/message order. Phrase mode normalizes whitespace and case; literal
mode preserves both punctuation and case. Regex metacharacters are escaped.
Search returns 600-character excerpts and revision-compatible fetch anchors.
URLs remain redacted, so exact URL recall is intentionally not supported yet.

The separate `recall_index` contains sanitized text only. No source writes occur
on search. Every candidate is checked against its live owner/scope/revision;
selected sources and the user are checked again before returning. Stale candidates
are omitted and labelled `index_delayed`, not presented as current evidence.
Search caps at 50 candidate conversations and 8 MiB source JSON, with explicit
`processing_limit_reached`. Ranking is only within that candidate window, not a
claim of global relevance. Processing and Mongo query timeouts return 503.

### Offline indexing / reconciliation

Run the worker outside the agent runtime, one worker per owner at a time:

```bash
python -m scripts.recall_backfill --user-id USER_OBJECT_ID --confirm-db DATABASE_NAME
# Resume the returned next_cursor with --after; repeat until null.
```

A full pass rebuilds eligible messages and purges deleted, excluded, moved-owner
or oversized entries. Start a new full pass to reconcile edits. Each batch reads
at most 100 IDs and one size-guarded source at a time. Retries are idempotent.
Do not run overlapping passes for one owner: projection writes are last-writer-wins,
not source-ordered. Live revision checks protect reads if an old writer wins.
Coverage reports the last completed pass, not real-time freshness or completeness.
Scheduling the worker and event-driven updates are NOT wired yet. No production
backfill has been run. Index retention/deletion SLO must be set before enablement.

### Remaining release gates

This is a bounded lexical baseline, not all of planned PR 2: distributed cursors,
automatic indexing, exhaustive pagination beyond the candidate cap, immutable
legacy ownership migration and production performance evaluation remain pending.
Process-local cursors still expire on a different worker or restart. These limits
must be resolved before claiming the full approved plan is complete.

## Execution-local MCP adapter batch

`mcp-servers/loma-recall/server.py` exposes `search_history` and `fetch_history`
over stdio using the MCP Python SDK 2.x. The launcher supplies
`LOMA_RECALL_BACKEND_URL` and `LOMA_RECALL_CAPABILITY` in the process environment;
these are not model-callable arguments. The adapter has no signing key, no Mongo
connection and no credential-issuing endpoint. Plain HTTP is restricted to loopback
or the internal `loma-backend` host; redirects are never followed with credentials.
Errors are allowlisted, response size is bounded, and each adapter process allows
at most eight calls and 24,000 serialized response characters. That is a character
budget, not an exact tokenizer count, and is not a distributed abuse limit.

Tool instructions label history as untrusted reference data, require source links,
and forbid treating old approvals as new authorization. Actual stdio tests perform
initialize, list_tools, search, anchored fetch and budget exhaustion through the
real HTTP handlers against mock storage. Separate opt-in tests cover real Mongo.

**Not registered in live chats/tasks yet.** Code inspection found blockers to
safe per-execution credential delivery:

- OpenCode `_write_managed_opencode_config` caches by override **names**, not full
  credentials, so differently scoped credentials under one connector name can
  reuse a cached configuration.
- Codex writes managed MCP configuration into the account's shared CODEX_HOME.
- The trusted issuer/session gateway is still absent; agent-readable signing keys
  or forwarded email headers would violate this feature's approved security model.

Do not add this adapter to global config or a shared account pool. Integration
requires an isolated execution launcher, verified session/task identity, capability
renewal and destruction of execution-local configuration. The adapter is usable in
an isolated test process now, but does not complete PR 3. No live agent recall or
latest-head ordinary provider-chat test is claimed.

Reusable browser proxy smoke: `scripts/browser/recall-smoke.cjs`. It requires an
isolated stack, a local JSON state file (`email`, `password`, `setup`, optional
`capability`), and `LOMA_RECALL_SCREENSHOT`. Never commit the state file or a real
credential. Screenshot output is explicitly labelled as an API response, not an
AI reply. Existing `chat-smoke.cjs` remains the separate live-model merge gate.
