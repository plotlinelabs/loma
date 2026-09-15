# History recall

## What ships

Session-authenticated dashboard chats and quick-start background board tasks get
`search_history` and `fetch_history` automatically. Continuations get a fresh
execution credential. Recall defaults to enabled; `LOMA_RECALL_ENABLED=false`
disables both endpoints and new runtime registration. No production data migration
or setting change is performed by this PR.

The API, issuer, MCP adapter, indexing and all three runtime adapters are in this
PR. Live verification covers OpenCode chat and headless tasks; Claude/Codex adapter
regressions are automated, but live subscription tests require correctly connected
accounts. Do not label those paths live-tested without running them.

## Authentication and lifecycle

1. The chat/task handler creates or resumes the source conversation normally.
2. It forwards the real browser session cookie to the **fixed internal dashboard
   origin**, `/api/recall-session`, requesting a grant for that conversation.
3. The dashboard verifies NextAuth, resolves the live user and conversation owner,
   and captures project/agent scope from storage. Forwarded emails, roles, preview
   identities and body-supplied identities do not authorize issuance.
4. The dashboard's Ed25519 private key is generated in process memory. It is never
   placed in backend environment variables, shared account homes, files, or agent
   prompts. Only a scoped capability and opaque renewal grant reach the adapter.
5. Capabilities last at most five minutes. The grant lasts at most two hours;
   renewal cannot change user, execution, project or agent. The adapter renews
   before expiry without resetting its budget.
6. The API validates runtime capabilities with the issuer on every request, then
   verifies the signature and applies the existing live-user/source policy and
   shared limits. Revocation, owner/scope changes and exclusions fail closed.
7. The turn wrapper revokes the grant on completion, error or cancellation. An
   issuer outage cannot grant access; ordinary chat can continue without recall
   when initial issuance is unavailable. A process crash is bounded by grant
   expiry; a dashboard restart revokes all runtime grants immediately.

The issuer stores only hashed grants/capabilities plus scoped claims in memory,
with expiry pruning, a 32-grant per-user cap and 10,000-grant process cap. Requests
and responses must not be logged with cookies, grants or capabilities. Historical
text is untrusted reference material, never new instructions or action approval.

### Deployment

The default issuer URL is `http://loma-dashboard:3001`, matching Compose. Nginx
routes `/api/recall-session` to that service. For bare-process local development,
set `LOMA_RECALL_ISSUER_URL=http://localhost:13001` and
`LOMA_RECALL_BACKEND_URL=http://localhost:13000`. URLs are deployment configuration,
not model/request arguments; redirects are rejected and non-local HTTP is denied.

Leave `LOMA_RECALL_PUBLIC_KEY` empty/unset for the built-in issuer. A nonempty value
selects legacy externally signed capability verification (used by API fixture
regressions), rather than built-in runtime issuance. Do not combine that static
mode with automatic dashboard issuance.

**Supported issuer topology: one dashboard process per deployment**, as in Compose.
Multiple dashboard replicas need a separately isolated shared issuer before use;
do not share a private key through the backend to work around that requirement.
Backend workers can share Mongo cursors/limits. Pin the internal issuer origin and
keep the dashboard process namespace inaccessible to agent containers.

This feature is NOT a sandbox for unrestricted agent shells. Existing deployments
may give agents direct database access or host secret mounts. It does not remove
that broader trust. The new private signing key is not in those mounts. Operators
must protect session authentication and deployment administration as before.

## Runtime isolation

- Claude: recall forces an ephemeral SDK client, with a per-turn stdio MCP config.
- OpenCode: config-cache keys include all override values (hashed), not just
  connector names. Scoped runs get their own managed server, even when a shared
  external server is configured. Files are private, and server/config cleanup runs
  on success, cancellation and setup failure. Concurrent startup stays under the
  startup lock so another request cannot kill a server still becoming healthy.
- Codex: holds a bounded pool slot but connects a new worker using a temporary
  0700 home. Only provider `auth.json` is copied; execution MCP config never goes
  into the shared account home. Worker and temporary files are removed before the
  borrowed pool slot is released. No other account's history/config is copied.

The common lifecycle handles both `handle_chat` and headless board-task execution.
Non-dashboard sources without a verified browser session do not get an invented
identity or a broadly scoped credential. Long-running tasks beyond two hours must
start a new authenticated turn to recall more history.

## Indexing and retrieval

A bounded, serialized reconciliation pass runs automatically before and after an
eligible turn. It uses the existing per-owner Mongo lock and sanitized projection.
A pass has a 20-second budget; incomplete passes retain partial coverage and do not
abort normal chat. Live-source revision checks reject stale results. For larger
histories, the separate `scripts.recall_reconcile` maintenance worker can complete
full passes; never run uncoordinated direct index writers concurrently.

Sources are persisted user-visible messages from owned `dashboard`/`task`
conversations. Shared conversations owned by someone else, ambiguous ownership,
deleted/excluded records, unstarted drafts, raw tool output, runtime envelopes,
hidden instructions, reasoning and attachment bodies are excluded. Current
conversation is excluded. Historical ownership uses `metadata.user_name`; immutable
ownership/email-reassignment migration remains a separate data-governance concern.

Sanitization happens before indexing and retrieval. Known credentials are redacted;
ambiguous hidden-context/multiline content is excluded. URLs are conservatively
redacted. This is heuristic filtering, not comprehensive DLP. Legacy replies were
stored with a 5,000-character cap; missing historical content is not recoverable.

`search_history`: `query`, `match_mode=keywords|phrase|literal`, `limit` (default 8,
max 20), optional project/agent/date/kind filters and cursor. Filters only narrow
signed scope. Literal mode preserves punctuation/case; arbitrary regex is forbidden.
Results carry excerpts, dates, roles, revision, scope and server-generated source
links. Candidates are limited to 50 conversations/8 MiB with explicit coverage;
this is bounded lexical recall, not exhaustive semantic search of an unlimited
archive. Empty results never establish that something was never discussed.

`fetch_history`: conversation ID, optional anchor message, before/after context,
max characters and cursor. Message IDs are stable within a revision. Long messages
have explicit offsets and continuation. Responses are bounded (40,000 characters
maximum, 20 messages/page). Source links open the conversation; message scrolling
is not implemented. Both endpoints enforce owner/scope again before returning.

Opaque cursor digests and content-free state live in Mongo with 15-minute expiry,
bound to user, execution, scope and query/revision. Shared counters enforce 60
requests/user/minute, 120 requests/execution/15 minutes and 240,000 serialized
characters/execution/15 minutes. The adapter additionally enforces eight calls and
24,000 serialized characters per turn. These are character budgets, not exact
model-token counts. Renewal does not reset them.

## Testing

Use Python 3.12 in an isolated virtualenv:

```bash
env -u AGENT_DEFAULT_MODEL .venv/bin/python -m pytest tests -q
```

Follow `run-loma-local`: a fresh `loma_local_*` DB, ports 13000/13001/14097, Slack
and scheduler disabled, isolated account/asset directories. Never borrow another
user's provider account. Always rerun on the final commit.

- `tests/test_history_recall.py`, `test_history_search.py`: API/security controls.
- `tests/test_history_recall_mcp.py`: real MCP stdio protocol with mock storage.
- `tests/test_recall_runtime.py`: launch identity, cookie handling, URL policy,
  lifecycle cleanup/cancellation, renewal, isolated Codex home, OpenCode cache and
  concurrent startup. Mocked runtime tests are not live inference evidence.
- `tests/test_history_recall_e2e.py`: seven opt-in full-backend/Mongo checks with a
  throwaway static signing key, configured only in the isolated test backend.
- `tests/test_recall_runtime_e2e.py`: real session issuer + backend checks. Set
  `LOMA_RECALL_RUNTIME_E2E=1`, `LOMA_RECALL_TEST_EMAIL`, and
  `LOMA_RECALL_BROWSER_STATE` to a local Playwright storageState from test login.
  Remove that state after testing; never upload it or the environment files.
- `scripts/browser/chat-smoke.cjs`: real UI submission, response, reload,
  follow-up context and persistence. Run with recall on and off.
- `scripts/browser/recall-agent-smoke.cjs`: real authenticated chat and headless
  task, actual search/fetch calls, an unpredictable synthetic historical fact,
  citations and screenshots. It seeds/cleans only synthetic records. Set
  `LOMA_RECALL_AGENT_E2E=1` plus the same `LOMA_EMAIL`, `LOMA_PASSWORD`, `LOMA_TOKEN`,
  `LOMA_CHAT_MODEL`, and `LOMA_CHAT_EVIDENCE` used by the normal chat smoke test.

Live tests are explicit opt-in and use real inference. A 200 response, login-only
screenshot or fake assistant answer is not a passing live test. Attach only inspected
screenshots and content-minimal result summaries to the PR. Tear down the test PIDs,
drop only the throwaway DB, and remove test credentials. Independent security review
and provider-specific live checks are still required before merge approval.
