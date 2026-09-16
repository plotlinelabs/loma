# Remote chat worker migration

## Current status: default-off entrypoint routing implemented; certification pending

Remote mode now includes native runtime adapters, pinned subscription selection
and OAuth refresh, accounted model relay, scoped knowledge/files/connectors,
owner-approved writes, chat/flow/recovery routing, tool-free utilities and scoped
GitHub/Linear webhook automation. Worker/supervisor image build and deployment
configuration are provided. `LOMA_REMOTE_WORKERS` remains off by default; no
production rollout or dedicated-host certification has been performed here.

Provider HTTP 429 responses now update the selected subscription's durable
cooldown. Only the trusted provider's bounded Retry-After hint is used (1 second
to 24 hours, with a 60-second default); date-form hints are also supported. The
relay closes without retry or account failover, and missing usage evidence keeps
the spend reservation. Other backend selectors observe the same cooldown;
concurrent cooldown updates cannot shorten it. Feedback persistence failures
fail closed without disclosing provider details.

**Still required:** native CI installation with workflow-capable credentials;
actual image builds and dedicated Docker/gVisor hostile-worker, cleanup and
live-provider certification; distributed subscription capacity/rate-limit
coordination beyond cooldowns and account-level usage reporting; operator-owned
persistent storage/retention configuration and a drained production rollout.
These are not implied by synthetic tests or local browser screenshots.

The sections below retain historical implementation checkpoints. Statements
such as "not routed yet" describe that checkpoint, not the current default-off
routing implementation. The latest implementation sections are at the bottom.

## Boundary implemented

```
Authenticated backend + tool/model gateway (trusted, holds credentials)
    | authenticated TLS, bidirectional bounded JSON frames
Dedicated worker-host supervisor (trusted, owns dedicated Docker daemon)
    | stdin/stdout, fixed immutable image + command from that image
One disposable gVisor container per run (UNTRUSTED)
    no network, host bind mounts, backend .env, DB, SSH, OAuth or Docker socket
```

The backend owns run identity and permitted tools. A worker can request a tool,
not choose a user or grant itself approval. Every request rechecks authorization
and must pass a trusted per-tool schema and resource/approval policy before any
provider dispatch. There is deliberately no generic `run CLI`, backend file-path,
observer-method or database API in the protocol. A worker's text is untrusted
content, never an execution receipt.

The supervisor uses an operator-pinned image digest and requires Docker's `runsc`
runtime. Its flags select non-root execution, dropped capabilities, read-only
root, private writable tmpfs, no network, and CPU/memory/process/output/time
limits. It does not copy its environment to workers. The worker image itself
must be built from an explicit source allowlist, with no credentials in any layer.
The existing application Dockerfile is NOT an acceptable worker image.

The supervisor's Docker socket is root-equivalent host authority. Keep it on a
**dedicated worker host**, not the production host or a shared preview runner.
Use mutual TLS and the additional control bearer token. Only backend addresses
may reach port 8443. Do not expose this service through public nginx, give the
socket to the backend/worker, or copy production `.env` files to the worker host.
The supervisor must restart automatically after host/process loss; startup removes
labelled orphan containers before accepting new work. Use exactly one supervisor
per dedicated Docker daemon. Operator host/runtime integrity remains trusted;
a runtime name alone is not attestation of the installed binary.

Deadlines, disconnects, invalid frames, cancellations and failures remove the
specific container. Cleanup failure stops new admission until operator repair.
No reconnect/replay occurs automatically after uncertain tool completion. Tool
request IDs are unique within a run; provider idempotency and durable uncertain-
outcome receipts still belong in the gateway, not this transport.

## Required migration work before enabling this boundary

1. **Runtime adapters:** move Claude, Codex and OpenCode into worker-compatible
   images. Their model HTTP/MCP I/O must use the broker protocol because the
   worker has no network. Do not solve this by passing shared model-account
   directories or restoring arbitrary egress. Preserve streaming, errors,
   cancellation, selected models, follow-ups and model accounting.
2. **Credential/tool gateway:** replace direct credential-bearing MCP configs
   and CLI DB access with typed adapters. Derive principal and current grants
   from authenticated server state; validate every resource. Restrict and audit
   model calls too. Never send `created_by`, `run_as`, integration tokens or
   arbitrary argv supplied by a worker to privileged execution unchecked.
   Existing prompt builders insert personal auth tokens: they cannot be copied
   unchanged into this new worker. Scope and sanitize context before transfer.
3. **Files and recall:** transfer input/output bytes through an owner-scoped,
   size-limited artifact API. No backend path read or mounts. Preserve requested
   generated files across disposable runs. Recall and skills need current-user
   access checks at the gateway, not shared credentials in a worker.
4. **Routing and lifecycle:** route all three chat runtimes, legacy scheduled
   flows, recovery/resumes, model utility calls and prewarming off the backend.
   Once enabled, transport outage blocks affected work, never starts a local
   shell. Drain existing runs before switching; do not interrupt live chat by
   enabling a half-migrated runtime.
5. **Staging parity and negative tests:** exercise real worker images and gateway
   adapters with synthetic accounts, then desktop/mobile chat, attachments,
   cancellation/recovery and cross-user denials. Verify no backend/metadata/DB
   network access, credential file access or sibling-run filesystem/process
   access from hostile worker code. Check runtime/cgroup configuration from the
   trusted host, not a worker-produced claim.

Rollout is a separate operator action after those gates. This change deliberately
does not add an incomplete deployment override that could disable existing chat.

## Verification for this change

`tests/test_worker_boundary.py` exercises real aiohttp WebSocket connections and
real synthetic Python subprocesses. The Docker command is replaced in those
transport tests; Docker/gVisor containment itself is **not** verified here.
Coverage includes a streamed reply after a gateway tool request, denied tools,
revoked accounts, malformed frames, duplicate request IDs, worker exits, timeout,
cancellation, admission limits, cleanup failures and absence of a local fallback.
No model calls, provider calls, real emails or Slack sends are made.

Run:

```sh
.venv/bin/python -m pytest tests/test_worker_boundary.py -q
```

No new UI is added or existing UI modified; this is not browser parity evidence.
Do not reuse earlier browser screenshots as proof of isolated chat.

References for the selected primitives: [gVisor Docker runtime](https://gvisor.dev/docs/user_guide/quick_start/docker/)
and [Docker execution controls](https://docs.docker.com/engine/containers/run/).

## File and read-tool data plane (subsequent implementation)

`isolation/artifacts.py`, `workspace.py` and `gateway.py` implement the byte-only
file transfer and typed read-tool boundary. They **do not route existing chat**
and do not add Claude/Codex/OpenCode model-transport adapters. The cutover gate
above is unchanged. No deployment setting or running pool was changed.

- Construct `ArtifactScope` on the backend with authenticated `RunAuthority`,
  the authorized conversation ID, and a **server-selected** list of attached
  artifact IDs. IDs from worker frames are not grants. Namespace hashes bind
  persisted bytes to both the current owner and conversation. A later run may
  reuse an artifact only after the backend attaches it to that run's input set.
- Ingest attachment **bytes**, never a path supplied by a worker. Give the
  worker the manifest (opaque ID, display name, size, SHA-256). The store is a
  private backend directory, never a worker mount. Its operator-managed volume
  and retention policy are required for cross-redeploy persistence; this patch
  does not configure either or promise persistence on an ephemeral volume.
- `Workspace.stage` downloads those inputs into the worker's disposable local
  directory. Duplicate names have different ID directories. `Workspace.publish`
  opens only regular files beneath that directory, rejecting symlinks, traversal
  and FIFOs, then uploads bytes with exact offsets and a final checksum.
- Uploads reserve quota before writing (20 MiB/file, 64 MiB/run, 20 files).
  Metadata is published only after complete bytes are flushed. Incomplete files
  are unavailable to reads. Always close the scope in the run's `finally` block;
  closing removes incomplete uploads and retains successfully committed files.
  Host loss may leave unindexed orphan bytes, which a retention job must remove.
- Use `ToolGateway` as `stream_worker(execute_tool=...)`. Its authorization
  callback must recheck the **current** account, cancellation, conversation and
  grants, not merely echo the immutable run grant. Audit failures block dispatch.
  Reads are rechecked after provider completion so revocation during a read
  prevents returning its contents. Schema checks forbid identity overrides,
  arbitrary argv, URLs and backend paths. Upload mutation is serialized per run.
- The only connector adapters here are `gmail.search`, `gmail.read` and
  `calendar.list`, with bounded arguments and result counts. Credentials stay
  in the existing trusted backend connector. No generic CLI or send fallback is
  registered. Writes need the existing durable action/approval engine, not a new
  immediate-send path. Skills, recall and the other connectors still require
  typed adapters and current resource checks before migration is complete.

The artifact commit receipt currently stays in this internal protocol. Existing
chat download registration and the dashboard's file events have **not** been
switched to the new store. Do not treat worker text naming an artifact or backend
path as a download grant or a provider receipt.

### Fresh verification

`tests/test_worker_artifacts.py` includes a real Python worker subprocess talking
through the supervisor WebSocket to the backend gateway: stage an attachment,
create an output, upload it, and verify its bytes. It also covers cross-owner and
cross-conversation denials, stale grants, audit outages, malformed/oversized
uploads, concurrent offsets, checksums, persistence across scope recreation,
symlinks, FIFOs and cancellation. These are synthetic provider/transport tests,
not real CLI parity, browser parity, or gVisor isolation evidence.

Remaining runtime work is substantial: subscription-account selection without
backend CLI warmup, credential-free provider/model streaming proxies, all three
worker CLI adapters, current tool/skill/recall adapters, trusted output download
registration, then interactive/scheduled/recovery routing and rollout. Do not
mark the PR ready on the strength of data-plane tests alone.

## Streaming model data plane (current continuation)

`isolation/models.py` adds a backend-only `ModelRelay`; `model_bridge.py` adds
its worker-only loopback HTTP adapter. The bridge supports the Responses,
Messages and Chat Completions wire paths. **This is wire-protocol coverage, not
Claude/Codex/OpenCode runtime or subscription-account parity. Chat is still not
routed through it.**

- A server-created `ModelGrant` fixes the exact HTTPS endpoint, model, headers,
  output ceiling and call ceiling. Worker frames contain only request bodies or
  opaque stream IDs. No worker-supplied destination, credentials or HTTP headers.
- A private, cookie-free backend HTTP session does not inherit environment proxy
  settings or follow redirects. Provider response/error headers and error bodies
  are not forwarded. Worker HTTP headers never become provider headers.
- Requests force streaming and bounded output. Responses storage is disabled;
  cross-response references, remote file/image URLs and hosted tool definitions
  are denied. Local function descriptions are allowed, but their execution still
  requires the independently authorized tool gateway. New provider features must
  be explicitly reviewed rather than enabling a generic HTTP tunnel.
- Backend authorization is rechecked before dispatch and after each response read.
  Mandatory audit and durable budget reservation precede dispatch. HTTP failures,
  cancellation, stream limits and revocation close the stream without retrying.
- Settlement callbacks receive transport status only. **EOF is not a usage or
  billing receipt, and must never release a reservation.** Provider-specific usage
  parsing, subscription-account selection/refresh and existing cost accounting
  adapters remain required before cutover. Closed/failed accounting stops further
  use of the relay.
- Run owners must call `ModelRelay.close()` in their teardown, independently of
  whether the worker sent a close request. The loopback bridge closes its stream
  when a runtime disconnects/errors or the bridge shuts down. Neither component
  can start a legacy backend runtime as fallback.

Tests exercise real local HTTP streams for all three wire protocols, and a real
Python child through the existing supervisor WebSocket, gateway and synthetic
provider. They check credentials, owner revocation, budget denial, header/redirect
restrictions and cleanup. Docker/gVisor is substituted; provider responses are
synthetic. These results are not proof of runtime, browser or deployment parity.

The public-content scanner now uses the neutral `io.loma.isolated-worker` label;
synthetic supervisor credentials are generated afresh per test process rather
than embedded constants. No scanner rules or exemptions were relaxed. This change does not deploy or start the supervisor. Operators who have
tested an earlier supervisor must drain its old-labelled containers before
upgrading; the new label does not discover those containers automatically.


## Native Codex adapter (earlier continuation)

`codex_worker.py` and `worker_entry.py` now run the **actual Codex app-server**
through the model relay and framed tool gateway. This is no longer only a
synthetic-worker transport test. It is still **not a chat cutover**, a complete
Codex feature migration, or proof of gVisor containment.

- Codex 0.153.3 gets a fresh ephemeral HOME and CODEX_HOME, a fixed loopback custom
  model provider and no account files, inherited environment, API credentials,
  hooks, plugins, backend paths or arbitrary MCP configuration. No CLI/account
  code from `agent/` is imported. The caller fixes the model.
- Typed function requests map to the supplied gateway catalog. Unknown tools,
  cross-thread calls, namespace overrides and native approval requests are denied.
  A catalog describes capabilities; it never grants them. The backend still
  independently validates the current account, resource policy and action.
- The server-created Responses `ModelGrant` must explicitly enable
  `native_codex`. The adapter strips native cache/session diagnostics and does
  not forward native shell, image, patch or other built-in tools. Only mapped
  gateway functions reach the model. This deliberately narrower capability set
  means **existing coding/file-editing chat does not yet have parity**.
- Text streams through the existing transport. Native model/provider failures
  do not retry, switch accounts or start a local backend worker. Cancellation
  kills the runtime process group; full-run cleanup also closes the model relay.
- Tests cover two successive turns in the same disposable runtime. This is **not
  cross-container resume**: do not restore a native HOME/session/account archive.
  Authorized history replay and artifact/download registration still need wiring.
- `deploy/worker/codex.Dockerfile` is an explicit file-allowlist image recipe, not
  an application image. It pins the CLI and does not copy `agent/`, `tools/`,
  backend config or account directories. The image has not been built or tested
  with Docker/gVisor in this container. No deployment references it yet.
- `scripts/test_native_worker.sh` requires the pinned CLI and fails if it is
  missing or a different version. Tests use only local synthetic provider
  responses; there are no paid requests or personal-account operations. **The
  automatic CI install still needs a maintainer:** the available GitHub token
  cannot edit workflows. Until then, normal CI skips native tests if the CLI is
  absent; a green check alone is not native parity evidence.

Fresh local verification: **1,042 passed, 108 skipped** with the session's
`AGENT_DEFAULT_MODEL` override removed (that override otherwise changes an
unrelated default-runtime test). This includes **29 new tests**, of which four
use the native binary: permitted tool + follow-up, provider failure without retry,
cancellation, and real child process through the supervisor WebSocket/model relay.
Both public-content and secret scans pass. No browser or real-container isolation
verification was performed for this continuation.

Remaining merge gates: Claude/OpenCode native adapters; subscription-account
selection/refresh and authoritative usage settlement; full scoped tool/file/recall
parity; chat/scheduled/recovery routing; backend download registration; then native
browser and hostile-worker containment tests on the dedicated host. Production
chat remains on the legacy execution path and the PR must remain draft.

Configuration follows the [official custom-provider reference](https://developers.openai.com/codex/config-reference),
with app-server request/response fields verified from the pinned native binary's
generated experimental JSON schema and actual protocol exchanges.


## Claude/OpenCode adapters and backend-selected history (latest continuation)

The worker entrypoint now selects the fixed Codex, Claude Code or OpenCode binary
from its image. No executable path, account, environment, native session directory
or arbitrary MCP configuration is accepted from the wire. **This completes these
native adapter components, not the live-chat migration or the PR merge gate.**

- Claude Code **2.1.261** runs in bare, nonpersistent mode with built-in tools,
  skills, automatic memory and inherited settings disabled. Its Messages API and
  fixed MCP catalog go through worker-local bridges. The API-key placeholder has
  no provider authority and is discarded by the model bridge. The real provider
  credential remains in the backend `ModelGrant`.
- OpenCode **1.18.28** uses a fresh HOME and XDG directories, one custom Chat
  Completions provider, no inherited plugins, no project/Claude configuration,
  and a deny-by-default permission policy. Only the fixed gateway MCP catalog is
  enabled. This is **not** parity for all existing OpenCode providers, coding
  tools or runtime features. The native CLIs currently use an 8,192-token output
  ceiling; construct a matching backend grant or the request is denied.
- The local `MCPBridge` exposes only initialize/ping/list/call. Tool names map to
  the server-supplied catalog. Duplicate MCP call IDs never dispatch twice; failed
  results contain no provider diagnostics. Backend resource/approval checks are
  still mandatory. It is not an alternative privileged tool-execution layer.
- A model-bridge failure now poisons that bridge. Native retries receive a
  terminal 400 without another broker/provider dispatch. Cancellation shuts down
  the native process group and closes both loopback bridges. Native success is
  accepted only after a terminal success event and a zero process exit.
- Native Claude compatibility is explicitly enabled on the backend grant. Local
  metadata and cache hints are removed, without rewriting tool schemas or tool
  input data. Unsupported hosted tools and remote references remain denied. Its
  local `?beta=true` query is accepted only in the Messages bridge and never
  forwarded as a worker-chosen upstream query. The current adapter does not
  preserve effort selection; the CLI's default effort field is stripped.
- A backend-selected immutable text transcript may be attached to a model grant
  for a fresh worker. Only user/assistant text is accepted, with message/byte
  limits. It is inserted once per provider request without native account/session
  restore or historical tool re-execution. Chat system instructions keep their
  leading position. **The conversation API must still load and authorize this
  transcript before constructing the grant.** Claude/OpenCode adapter instances
  are single-use, so a second turn cannot silently run without its prior history.
- `deploy/worker/native.Dockerfile` is a pinned-CLI, explicit source-allowlist
  recipe for the three native runtimes. It is not built, deployed or referenced
  by production here. It must be built and containment-tested on the dedicated
  worker host, then addressed by its immutable output digest.
- `scripts/test_native_worker.sh` now requires all three pinned CLIs and fails
  rather than quietly skipping if any is missing. The existing workflow still
  needs a maintainer to install those CLIs before invoking it; no workflow
  permissions were bypassed by moving installation into ordinary unit tests.

### Verification for this continuation

Fresh full-suite result: **1,101 passed, 108 skipped**, with the session model
override unset. Public-content and secret scans pass.

Fresh native tests use actual CLIs against synthetic local HTTP providers. They
cover tool calls and tool-result continuation, unregistered tool rejection,
provider failure without upstream retry, cancellation, real worker subprocesses
through the supervisor transport, and fresh-worker text-history replay for all
three runtimes. Additional tests cover the MCP catalog, duplicate/concurrent IDs,
malformed messages, safe diagnostics and history/cache/schema validation.

No paid provider calls, personal-account operations, browser parity checks or
Docker/gVisor containment validation were performed for this continuation.
The native image recipe has not been built here. These are not substitutes for
those checks, and no old UI screenshots establish the new chat path.

### Remaining migration, not just testing

1. Backend subscription-account selection/refresh and authoritative model usage
   settlement must construct and service the new grants.
2. The read-tool surface has typed adapters (see the connector read-adapter
   section below) and write/send actions now have a worker-visible proposal
   flow (see the proposal section below): a worker can only file a durable
   proposal, and only the owner's signed decision executes it. Database, GitHub
   and other MCP surfaces remain deliberately unavailable to workers.
3. Chat attachments and generated-file download events still need to be connected
   to the artifact broker. File component tests are not dashboard integration.
4. Interactive chat, scheduled flows, recovery/resume, utility calls and pool
   prewarming must be routed to remote workers, with no local fallback and a
   tested drain/cutover procedure that preserves existing chat functionality.
   *(The routing seam now exists behind `LOMA_REMOTE_WORKERS=on` — see the
   entrypoint routing section below. Titles, topics and Slack compression now
   use tool-free remote utilities; other helpers still fail closed. Cutover has
   not been exercised against a real worker host.)*
5. Then run the integrated desktop/mobile chat suite and hostile-worker checks
   using the built images on a dedicated Docker/gVisor host.

The PR remains draft. No deployment or production chat routing was changed.

Native configuration references: [Claude Code CLI](https://code.claude.com/docs/en/cli-reference),
[Claude MCP](https://code.claude.com/docs/en/mcp),
[OpenCode configuration](https://opencode.ai/docs/config/) and
[OpenCode MCP](https://opencode.ai/docs/mcp-servers/).

## Typed connector read adapters (subsequent implementation)

`isolation/gateway.py` (`READ_SCHEMAS`, `validate_read`, `READ_COMMANDS`,
`personal_read`) and `autonomy/connector.py` now cover the legacy chat
read-tool surface with typed, current-access adapters. This does not route
existing chat and grants nothing by itself: a run only sees tools its
backend-supplied `allowed_tools` includes, and every dispatch still passes the
gateway's authorization, audit and revocation checks.

- **Per-user identity tools** (Gmail search/read/inbox, Calendar
  list/search/get, Drive list/search/read, Docs info/read, Sheets
  info/tabs/read, Slack channel read/search via the member's own Slack token,
  Loma notification list): the connector mints a short-lived token for the
  authenticated run owner on the backend. Workers never see tokens; the
  connector passes them as argv to exact first-party scripts with fixed flag
  shapes (`GLOBAL_AUTH` vs trailing `--user-email`).
- **Team integration tools** (Grain search/transcript/recent, Pylon
  issue/messages/teams/issues, PostHog projects/definitions/events, Linear
  velocity/bucket-split): read-only commands against backend-held Fernet
  integration keys. These CLIs take no identity argv, so no owner token is
  minted (`SERVICE`); access is still bound to an authorized run and audited.
- Argument validation is schema-exact per tool: required/optional names,
  bounded integers, date/month patterns, no control characters, and rejection
  of leading dashes for arguments that hand-rolled CLIs consume positionally.
  The model-visible catalog schema and the gateway schema are asserted equal in
  tests, so drift between advertisement and enforcement fails CI.
- Deliberately excluded: every write/send (Gmail send/draft, Slack send/react,
  Docs/Sheets/Drive mutation, Pylon reply/update, notification send) — those
  remain in the durable approval engine; `slack_reader.py` bot-wide reads
  (no per-user scoping); `loma_skills.py` (the run-bound knowledge gateway
  already serves skills with stricter scoping); MongoDB/ClickHouse/GitHub and
  all other MCP or shell surfaces.
- The connector still enforces exact scripts, `-I`, private cwd, env
  allowlist, rlimits, bounded output and a strict JSON-dict result. Failure
  paths (missing integration key, plain-text CLI usage errors, non-dict
  output) fail closed as connector errors, never partial text.

`tests/test_worker_connectors.py` pins the exact argv per tool, the three argv
shapes, no-token-minting for service CLIs, invalid-argument rejection before
dispatch, gateway audit/denial ordering and a real-subprocess adapter path.
No personal accounts, providers or databases are used.

## Worker write/send proposals (subsequent implementation)

`isolation/proposals.py` gives remote workers a way to *propose* an external
write without any send adapter on the worker path. `ToolGateway` routes the
proposal tools to a run-bound `ProposalGateway`; `isolation/run.py` constructs
it with the run's current-access check. This grants nothing by itself: a run
only sees proposal tools its backend-supplied `allowed_tools` includes, and
public entrypoint cutover is still separate.

- **Model-visible tools**: `gmail.propose_send`, `gmail.propose_draft`,
  `slack.propose_send`, `calendar.propose_create`, `docs.propose_append`,
  `sheets.propose_write` (each requires a `reason` shown to the owner), plus
  `proposals.status` and `proposals.list`. The catalog schema and
  `WRITE_SCHEMAS` are asserted equal in tests; the catalog stays under the
  native 64-tool limit (50 tools).
- **What a worker gets back**: a proposal ID, status, note and the normalized
  arguments — never a receipt, provider output, owner audit or digest. An
  identical re-proposal in the same conversation (same action/args) returns the
  existing pending/executed/uncertain proposal flagged `duplicate`; only a
  rejected, expired or cancelled proposal can be filed again. Limits: 10 open
  proposals per conversation, 30 per run; excess and invalid arguments fail
  closed like read-tool denials.
- **Validation** (`validate_write`): exact required/optional names per action;
  exact single recipient, bounded comma-separated `cc`/`attendees`; no line
  breaks in subjects/titles/ranges; no NUL bytes; exact Slack channel IDs and
  `thread_ts` shapes; ISO 8601 datetimes with a UTC offset and `end > start`;
  resource-ID patterns for Docs/Sheets; bounded typed cell matrices for Sheets.
  argparse CLIs receive `--flag=value` so values starting with a dash cannot be
  read as flags; `slack_user.py` (exact-token flag parsing) rejects text that
  starts with `--`.
- **Durable state machine** (`isolated_worker_proposals`, majority writes):
  `pending -> approved -> executing -> executed | uncertain`, or
  `rejected | cancelled | expired`. Every transition is a Mongo CAS on
  `(proposal_id, owner, version, status, expires_at)`. Approval stores
  `approved_digest`; execution claims `approved -> executing` only when the
  stored digest, approved digest and `decided_by == owner` all match, so an
  edited or tampered proposal cannot execute. `executing` never returns to
  `approved`; a claim older than five minutes without a receipt is swept to
  `uncertain`, and an adapter exception records `uncertain` with a do-not-resend
  receipt. Uncertain outcomes are only closed by an owner investigation
  (`reconcile`), which never authorizes a retry.
- **Owner control plane**: the signed bounded-work routes gain
  `GET proposals`, `POST proposals/<id>` (approve/edit/reject/cancel under a
  version CAS; approval executes the exact version in-request, shielded from a
  dropped dashboard connection) and `POST proposals/<id>/reconcile`; the
  `attention` count includes pending/uncertain proposals. The dashboard page
  `/agents/proposals` renders exact arguments, edit-as-new-version, receipts
  and delivery investigations; `AgentAttention` links to it. Creating a
  proposal upserts one idempotent Loma notification for the owner.
- **Execution** (`write_adapter`) re-validates the stored arguments, builds the
  fixed connector argv (`send-email` carries the stable `--rfc-message-id`
  derived from the proposal ID, as bounded work does) and requires a
  provider-specific receipt (`sent`+`messageId`, `created`+`draftId`,
  `sent`+`message_ts`, `created`+`id`, `appended`, `updatedRange`). The owner
  token is minted on the backend at execution time; the worker never holds it.
- Deliberately excluded: automatic Gmail provider checks for uncertain
  proposals (bounded work's reconciliation sweep is scoped to
  `agent_approvals`); Slack reactions/file uploads, Drive mutations, Pylon
  replies/updates and notification sends remain unavailable to workers.

`tests/test_worker_proposals.py` covers schema/argv pinning, receipt checks,
gateway dispatch and revocation, durable creation/notification/scoping, the
duplicate rule, limits, approve/edit/reject/cancel/expiry, exclusive claims,
digest tampering, uncertain outcomes and sweeps, shielded execution across a
cancelled request, and the signed routes. No provider is contacted.

## Provider-side usage evidence

`isolation/usage.py` incrementally inspects the trusted upstream SSE bytes for
Responses, Messages and Chat Completions. It does not accept a worker's usage
claim. The optional `ModelRelay.record_usage` callback receives an immutable
receipt bound to the backend-generated call ID before transport settlement, or
`None` when the stream is interrupted, truncated, malformed or lacks terminal
usage. Accounting failure closes the relay without replay. Receipts carry the
provider-returned model and response ID, input/output counts and cache counts;
no content, credentials or dollar-price assumptions are retained.

Messages output counts are cumulative, not summed across deltas. OpenAI cached
input is a subset of total input; Messages cache creation/read counts remain
separate. Input/output totals, nonnegative integer ranges, event size and
protocol completion are validated. The accounting adapter must still validate
model/rate compatibility and perform durable idempotent settlement. A token
receipt is not a provider invoice and transport EOF alone never refunds a hold.

References: [Messages streaming usage](https://platform.claude.com/docs/en/build-with-claude/streaming),
[Chat stream usage](https://developers.openai.com/api/reference/resources/chat),
and the installed OpenAI SDK's `ResponseCompletedEvent` / `ResponseUsage` types.

**This does not complete account integration or route chat to isolated workers.**
Those migration and deployment gates above remain open. Tests use synthetic
provider streams, including real HTTP relay calls, arbitrary chunk boundaries,
missing or contradictory usage and accounting failures. No new browser or
container-isolation evidence is claimed for this backend-only component.

## Durable API usage budgets (latest continuation)

`isolation/accounting.py` now binds `ModelRelay` to a majority-acknowledged Mongo
budget. Backend callers create a `BudgetSpec`, initialize a `ModelBudget`, then
use its `relay(...)` factory with trusted authorization and audit callbacks.
No backend paths, credentials, request bodies or response content are recorded
in the ledger. This module is excluded from all worker-image source allowlists.

- A single `isolated_model_budgets` document owns a run's immutable owner,
  opaque account reference, exact model/protocol, nanodollar rates, token limits,
  call count, remaining spend and bounded call ledger. Mongo's unique `_id`
  prevents run-ID reuse from replacing an existing owner or budget contract.
- Reservation and call admission are atomic. Repeated reservation IDs fail,
  rather than authorize replay. Majority acknowledgement must succeed before
  the relay contacts the provider. A failed/ambiguous database write aborts
  dispatch, and reopening a backend object cannot reset spend or stop flags.
- OpenAI cached input is part of total input; Messages cache reads/writes are
  additional input. Explicit rates and exact returned model matching are
  required. No model alias, live rate or subscription dollar cost is guessed.
- A verified provider receipt atomically records usage and releases only the
  unused hold. Concurrent duplicate callbacks cannot count or refund twice.
  Conflicting receipts, reused response IDs within a run and usage outside the
  contract block new calls. Missing evidence and transport EOF never refund.
- Stopping admission retains all holds. Late verified receipts may settle once
  but never restart work. There is no automatic TTL or owner-reported refund.
  These are conservative price-ceiling accounting records, not provider invoices.

**Still not a production accounting integration or chat cutover.** The current
chat entrypoints do not construct this ledger. Subscription-account selection,
refresh, provider-specific pricing/contracts, reporting integration, scoped
remaining tools/history/files and entrypoint migration remain unfinished.
The operator must select reviewed price ceilings and a conservative input bound
for each provider; an excessive receipt blocks future spend, but cannot undo
an already billed request. No claim of a universal tokenizer bound is made.

Tests in `tests/test_worker_accounting.py` include real isolated Mongo concurrency,
backend recreation, cross-owner denials, duplicate/conflicting receipts, stopped
runs and real HTTP relay callbacks with synthetic providers. Set `LOMA_LOCAL_E2E=1`
to run the database cases against throwaway databases; ordinary CI skips those
cases. `scripts/test_native_worker.sh` includes the new accounting tests.

The GitHub credential available for this continuation reports `repo, user`, not
`workflow`. Native-CLI CI installation still needs a workflow-capable maintainer.
The local environment has neither Docker nor a Docker socket, so dedicated-host
image builds and hostile-worker/gVisor certification remain unperformed.

## Scoped knowledge and worker file delivery

The latest continuation connects additional runtime-facing components. It does
**not** route existing chat, schedules, recovery or utility calls to remote workers.
The migration and dedicated-host gates remain open.

- `KnowledgeGateway` binds an authenticated run to its owned conversation and
  pins the user ID, project and agent scope. `search_history` and `fetch_history`
  reuse the HTTP endpoints' internal cores, live ownership/exclusion checks,
  sanitizer, cursor binding and durable rate limits. No recall capability or
  issuer credential enters the worker. Scope changes terminate access.
- Skill list/search/get/text-file/asset adapters reuse `skill_service` under the
  current owner's ContextVar, with explicit personal-scope checks and disabled/
  suspended checks. Internal source metadata, credentials and backend paths are
  not returned. Binary assets use bounded, checksum-checked, no-symlink reads
  and enter the run's artifact broker. `workspace.import` stages their bytes.
- The fixed `catalog` is filtered by server grants and does not grant permission
  itself. Tools not in it or not in a run's scope remain unavailable.
- `WorkspaceTools` gives all three native adapters worker-local text editing,
  listing, reading, commands and explicit file publication. Commands run only
  inside the disposable worker, with a minimal environment, a 120-second limit,
  bounded output and process-group cleanup. This is not a new backend shell and
  is not itself a sandbox. gVisor remains the mandatory isolation boundary.
  Native worker images must be rebuilt to include these allowlisted modules.
- The worker entrypoint stages authorized attachments before the first turn
  whenever workspace tools are present. Newly generated files are uploaded as
  bytes; `ToolGateway.on_artifact` receives only a validated commit receipt.
- `DownloadRegistry` persists owner/conversation-bound metadata before emitting
  the dashboard's existing `file_artifact` event. `/api/files/worker-<id>` now
  serves those registered files after live account/conversation checks, including
  after backend recreation. It shares the legacy download handler's pinned-fd,
  Range, no-store, nosniff and sandbox-CSP protections. Text naming a backend path
  or URL cannot register a download.
- Configure `LOMA_WORKER_ARTIFACT_DIR` to a backend-only persistent volume. There
  is no ephemeral default and it must never be mounted into workers. Downloads
  expire after 30 days. Run `python -m isolation.retention` daily in the trusted
  backend environment to remove old blobs/metadata and seven-day-old unfinished
  uploads. It ignores symlinks, unknown names and nonregular files. Operator
  volume and cleanup scheduling are not deployed by this change.
- Native clients can close HTTP immediately after a terminal SSE event. If the
  backend has already drained and settled the stream, a failed HTTP EOF trailer
  no longer poisons the next tool turn. Earlier disconnects still fail closed.

Verification includes an actual native Codex process through the supervisor
transport executing a synthetic file command and publishing its bytes. Docker
is substituted in that test: it is not containment proof. Real throwaway Mongo
and HTTP tests cover private skill/asset and history denials, mid-read revocation,
artifact persistence, expired/revoked download access and Range/CSP behavior.

Remaining: subscription-account selection/refresh and pricing/reporting wiring;
other connector/action adapters; complete native feature parity; ingress
attachment selection and full authenticated prompt/history assembly; actual
interactive/scheduled/recovery/utility/prewarm cutover; deployment configuration,
pinned native CI and dedicated-host hostile-worker/live-provider certification.

## Integrated backend run assembly

`isolation.run.stream_run` now composes the native transport, model relay, durable
budget, scoped knowledge, attachment broker, download registry and live run policy.
It is a backend integration API, **not an entrypoint cutover or subscription-account
implementation**. Existing chat and scheduled/recovery entrypoints remain unchanged.

- The caller supplies an authenticated owner/conversation, a reviewed model grant
  and budget, explicit model-visible tools, current policy callback, cancellation
  event, verified TLS transport and private artifact store. Unknown tools, runtime
  mismatches and contracts below the native 8,192-token output limit fail before
  dispatch. No environment-derived account, endpoint or local runtime fallback.
- `ConversationContext` loads only an owned, running dashboard/task conversation.
  It pins user identity, project and agent scope and rechecks them on output and
  tool/model access. Deleted, transferred, interrupted or scope-changed runs stop.
  Caller policy must additionally check current account and tool grants.
- History comes from the existing visible-message sanitizer, not legacy prompt
  envelopes or native HOME archives. The already-recorded current message is
  removed once; bounded recent history is retained in chronological order. The
  model receives coverage counts for omitted/redacted/excluded messages.
- Attachments are typed filename/bytes pairs accepted by authenticated ingress.
  Reusing an output requires a matching, unexpired owner/conversation download
  receipt plus immutable metadata. No backend path, arbitrary URL or guessed ID
  becomes an input grant. Reused and new inputs share the run's file/byte quota.
- The model-visible catalog and low-level artifact permissions are derived
  separately. Workspace reads do not authorize uploads. `allowed_skills` is
  enforced in `KnowledgeGateway`, including listing and direct get/file/asset
  calls; an empty set denies all skills. It is not just a prompt instruction.
- File events enter a bounded output queue only after durable registration.
  Generator close, cancellation and transport failure stop the producer and
  close model streams, HTTP sessions, budget admission and artifact descriptors.
  Final usage holds survive failure. Callers must close abandoned generators.
- Actual integration exposed two native timing races hidden by fast fake ledgers:
  a follow-up can begin after terminal SSE but before EOF settlement, and native
  completion can arrive while the final broker reply is pending. The model bridge
  now allows one bounded waiting request, never overlapping upstream calls, and
  the worker drains its final exchange before emitting completion. Failed
  settlement denies the waiting request; cancellation still cleans up immediately.

`tests/test_worker_run.py` exercises live throwaway Mongo and an actual Codex
process through the supervisor transport with local synthetic provider responses:
owned history, staged input, workspace command, published download and three
provider-side usage receipts settled exactly once. Additional tests cover scopes,
read-only grants, skill restrictions, cancellation, generator close, settlement
failure and cleanup. Docker is substituted, so these are **not** gVisor or live
subscription-provider certification. Run with `LOMA_LOCAL_E2E=1`; the native test
also requires the pinned Codex executable. It is included in the explicit native
suite, not silently enabled against production accounts.

Remaining scope: subscription-account selection/refresh and reporting; remaining
connector/action adapters and native feature parity; authenticated production
prompt/attachment callers; interactive/scheduled/recovery/utility/prewarm routing;
reviewed deployment configuration; workflow-capable native CI setup; dedicated-host
image/hostile-worker and live-provider certification. PR #191 remains draft.

## Pinned-account credential refresh hook

`stream_run(..., resolve_account_headers=...)` now passes an optional trusted
backend resolver through `ModelBudget` to `ModelRelay`. This is one bounded
account-integration step, not completed subscription-account integration.

- The callback receives the run authority and the account ID from the durable
  budget contract. Workers cannot select an account or replace the callback.
- Each new model call resolves credentials before budget admission, with a
  30-second timeout. The relay lock serializes refresh and provider dispatch.
  Endpoint, model, limits, history and pricing stay pinned to the existing grant.
- Refreshed headers are validated and copied. Invalid/empty headers or refresh
  failures close the relay without dispatch, reservation, retry, or stale-header
  fallback. Callback error details are not returned to the worker.
- Authorization is checked before and after refresh and again after budget
  reservation. Cancellation during refresh cannot dispatch a provider request.
  Existing live policy checks still govern stream reads and output.
- The resolver must enforce the current account grant, manage any provider token
  refresh/rotation safely, and return only headers for that same account. With no
  resolver, existing static-grant behavior remains unchanged. Switching accounts
  requires a new authorized run and budget contract.

Tests cover successive credential rotation, invalid headers, revocation during
refresh, cancellation, secret-safe errors, no stale fallback, durable account-ID
binding, foreign-authority denial and trusted run-assembly wiring. No real
subscription credential is used. Still pending: concrete subscription resolvers,
account selection/cooldown policy and reporting, other adapters, entrypoint
migration and dedicated-host/live-provider verification.

## Subscription selection without local CLI warmup

`isolation/accounts.py` now provides a backend-only subscription selector for
Claude and Codex, separate from the legacy process pools. Authenticated ingress
must supply the approved candidates, current-access callback and provider refresh
adapter. Finding a credential file alone does not grant access to it.

- Selection intersects explicit backend candidates, current per-run policy, an
  existing active Loma user, the existing `claude_pool_enabled` or
  `codex_pool_enabled` admin switch, credential presence and durable cooldowns.
  Unknown users, policy/DB errors, unsupported/API-key files and empty pools
  cannot fall back to local execution or another credential source.
- Account reads are bounded and reject symlinks and non-regular files. Provider
  identity is fingerprinted from local account metadata, not token bytes, so
  token rotation is allowed but re-login to another identity stops an existing
  run. ID-token decoding is only an identity-change detector, not authentication.
- `stream_run(subscription_accounts=...)` selects after conversation admission,
  pins the selected opaque ID into the durable budget, and binds refresh to the
  exact run/account. It rejects combining this selector with a separate resolver.
  The worker receives neither account IDs, paths nor credentials.
- Each credential resolution rechecks policy, disablement, disconnect, cooldown
  and provider identity before and after the provider refresh adapter. There is
  no mid-run account failover. The selected account is also checked on tool
  dispatch, stream reads and user-visible output, so revocation need not wait
  for the next model call. Provider refresh errors are secret-safe and fail
  closed; cancellation propagates.
- Cooldowns live in `isolated_subscription_accounts`, with majority writes and
  atomic `$max` updates so concurrent reports never shorten them. Dates are read
  timezone-aware even when the application's Mongo client defaults otherwise.
  Round-robin ordering is process-local, not deployment-wide capacity control.

Tests use synthetic account files and throwaway Mongo, including run-to-budget
wiring, cross-run denials, credential rotation, re-login, revocation during
refresh, cooldown persistence/expiry, DB failures and no worker dispatch on
selection failure. No personal account or paid model was used.

**Still not complete:** concrete provider OAuth refresh adapters and live-provider
protocol validation, distributed account capacity/rate-limit feedback and usage
reporting, remaining tool adapters and authenticated entrypoint migration. This
selector does not start any CLI or alter existing production routing. Subscription
usage currently retains the supplied priced budget contract; it is not an invoice
or subscription-quota meter. PR #191 remains draft.

## Concrete backend OAuth refresh adapters

`SubscriptionAccounts(..., check_access=...)` now defaults to
`isolation.oauth.OAuthRefresh`. An explicit trusted refresh callback remains
supported for tests or reviewed provider integrations. Claude and Codex adapters
are implemented; this does not switch any production entrypoint to remote runs.

- Reads the selected account's bounded, no-follow credential file only. Fresh
  credentials return provider headers; expiring credentials use a fixed HTTPS
  refresh endpoint and public OAuth client ID. No CLI, environment URL override,
  ambient HTTP proxy, redirect, retry, API-key fallback or account failover.
- Claude refreshes `claudeAiOauth` using the stored scopes and preserves account
  metadata. Codex refreshes `tokens`, preserves omitted refresh/ID tokens, and
  updates `last_refresh`. JWT expiry (five-minute margin) takes precedence over
  an opaque token's persisted expiry/eight-day timestamp fallback. JWT decoding
  detects identity changes only; it does not authenticate a token.
- Checks policy before dispatch, after the exchange and before returning headers;
  rejects changed provider identity/account, reconnect/disconnect observed during
  refresh, malformed credentials/responses and invalid expiries. The existing
  selected-account and relay checks still guard dispatch and stream reads.
- Separate adapter instances/processes serialize with a cancellable `flock` on
  `.loma-oauth.lock`. The refreshed bundle is atomically replaced, mode `0600`,
  with file and directory `fsync`, before any new credentials are returned.
- A durable `.loma-oauth.pending` marker fingerprints the pre-exchange bundle.
  Cancellation, timeout, upstream rejection, malformed reply or persistence
  failure leave that bundle fenced. It is never retried automatically, because
  the provider may already have consumed/rotated its refresh token. Reconnect
  with a changed bundle clears the fence logically; successful refresh removes
  it. A crash after the credential rename can safely reuse the committed bundle.
  Error messages never include provider bodies or credential values.

**Deployment contract:** all backend refreshers for an account must use the same
credential directory on a shared filesystem supporting `flock`, atomic rename and
`fsync`. Do not copy bundles to independent host-local stores. Do not enable a
legacy CLI and this adapter as concurrent credential writers for the same account;
legacy writers do not participate in this lock. External login/disconnect changes
are revalidated but are not transactional with the adapter. Entrypoint cutover
must enforce exclusive ownership before rollout. No shared production files were
changed by this implementation.

Tests cover both providers, real synthetic HTTP transport, rotation persistence,
concurrent instances and separate Python processes, stale-token fencing, restart,
cancellation, timeout, policy revocation, identity/account changes, file failures,
and default-selector/run-to-budget wiring. The native test script includes this
suite. No live subscription credential or paid provider is used; live-provider
compatibility and dedicated-host containment certification remain rollout gates.
Protocol references: Codex `rust-v0.153.3` login/auth implementation and installed
Claude Code `2.1.261` OAuth client. Public credential guidance:
https://developers.openai.com/codex/auth

**Remaining PR scope:** distributed account capacity/rate-limit feedback,
remaining remote utility completions (titles, topics and Slack compression now
use remote utilities; other local `claude -p` helpers still fail closed), native CI permissions, worker image builds,
and dedicated Docker/gVisor plus live-provider verification. The entrypoint
cutover switch now exists (see the entrypoint routing section at the end);
enabling it is a separate operator action after those gates. PR #191 remains
draft.

## Production entrypoint routing (latest continuation)

`isolation/deployment.py` and `isolation/entrypoint.py` implement the cutover
seam for item 4. `agent.client.stream_agent` — the single chokepoint for
dashboard chat, Slack/Telegram/GitHub/Linear webhooks, scheduled flows,
deferred-flow recovery and conversation resume — now routes every run through
`isolation.run.stream_run` when the operator sets `LOMA_REMOTE_WORKERS=on`.
The default is off: nothing changes for existing deployments, and turning the
flag on without full configuration fails runs closed with a visible error.
There is deliberately no per-run, per-user or worker-controllable override and
no local fallback path from remote mode.

What the entrypoint assembles from authenticated backend state, per run:

- **Runtime and model** from the dashboard model id (`codex/…` → Codex,
  Claude ids → Claude, otherwise the configured OpenAI-compatible chat
  endpoint), with `LOMA_REMOTE_DEFAULT_MODEL` for unselected runs.
- **Model grant and pinned budget** from `isolation/deployment.py`: fixed
  HTTPS provider endpoints, pinned integer nanodollar prices (unknown models
  charge deliberately conservative rates), a worst-case reservation check at
  configuration time, and `LOMA_REMOTE_RUN_BUDGET_NUSD` as the per-run cap.
  Subscription runtimes carry no static credentials — headers come from the
  existing selector/OAuth refresh path; only the operator chat endpoint uses a
  configured API key.
- **Accounts** from explicit operator lists (`LOMA_REMOTE_CLAUDE_ACCOUNTS`,
  `LOMA_REMOTE_CODEX_ACCOUNTS`, comma-separated `email=/absolute/dir`), fed to
  the existing `SubscriptionAccounts` selector. No disk scanning, no local CLI.
- **Instructions** built without the legacy prompt envelope: rulebook, skill
  index, source formatting, and an explicit statement that identity is enforced
  server-side. No personal auth tokens are minted or embedded; a test fails if
  any remote path calls the token minter.
- **Tools**: the full reviewed catalog by default. A chat with an explicit
  legacy SDK tool restriction gets a conservative workspace+skills-only set,
  because SDK tool names have no name-for-name remote equivalent; per-chat
  skill allowlists map through unchanged.
- **Attachments and files**: chat upload dicts become validated attachment
  bytes; prior committed outputs of the same owner and conversation are
  re-offered as inputs, bounded by count and by half the run byte quota so one
  large generated file can never block later turns.
- **Lifecycle**: an interrupt shim registers in `active_streams` so the
  existing interrupt endpoint cancels remote runs; observability records
  chunks, artifacts, interruptions and errors through the same observer.
  Structured file/artifact events only flow to consumers that requested
  steps (dashboard SSE); plain-text consumers get text only. Mid-stream
  injection is refused with a clear error rather than silently dropped.

The conversation context now admits the reviewed ingress sources whose
`metadata.user_name` is the creator's authenticated email — dashboard/task
chat, scheduled (`flow`) and `webhook` flows, `telegram`, and `slack*` — while
ownership still requires that email to match the run authority and resolve to
an active platform user, so a Slack requester without a resolvable email fails
closed rather than running under someone else's identity.

Remote mode requires an authenticated, active platform user as the run owner.
Runs that arrive without one — GitHub/Linear webhook automation and legacy
recovery resumes — fail closed with a recorded, visible error instead of
guessing a principal. Those automations also need worker-side GitHub/Linear
surfaces that deliberately do not exist yet, so restoring them under remote
mode is follow-up work, not a routing flag.

Local execution is disabled, not just bypassed, while the flag is on:
`background_cli_env()` (titles, topics, Slack compression, org-learning dedup,
review-quality and skill-organize helpers) raises and each caller degrades to
its existing fallback; the gate verifier CLI refuses; Claude/Codex pool warmup
and OpenCode prewarm are skipped; the model catalog endpoint serves static
entries instead of booting a local OpenCode server. Titles, topics and Slack
compression now bypass that local helper through the tool-free utility path
below. Other utility helpers still degrade in remote mode.

Verification: `tests/test_remote_entrypoint.py` (39 tests, in the pinned
native script) covers flag parsing, fail-closed configuration (transport, TLS,
accounts, budget bounds), pricing pins, grant/budget composition per runtime,
routing by flag with no local fallback, full assembly against throwaway Mongo
with a synthetic `stream_run`, interrupt handling, owner revalidation, the
disabled local-CLI surfaces, and `/api/pool-status` in remote mode.

`scripts/browser/remote-cutover.cjs` boots the real stack with
`LOMA_REMOTE_WORKERS=on` and no worker transport, logs in on desktop and
mobile contexts separately, sends a chat, and asserts that the run is routed
remotely, fails closed with the named missing configuration, leaks no local
runtime output, and that the same message is still rendered after a page
reload. That smoke caught three defects the unit tests could not:

- `/api/pool-status` (polled by every dashboard session) raised because no
  local pool exists in remote mode. It now reports remote mode, the configured
  account emails (never directories or tokens) and any configuration error
  with a 200.
- The persisted conversation `error` was the bare reason while the streamed
  text was the fail-closed message, so a reload showed a different string.
  The entrypoint now persists exactly what it streams; internal exception
  detail stays in server logs.
- The initial `/chat?continue=<id>` loader dropped a persisted error entirely
  (only the recovery poller appended it). Both paths now share
  `dashboard/src/lib/terminal-status.ts`, which also fixes refreshes of legacy
  errored conversations. Covered by `dashboard/tests/terminal-status.test.cjs`.

**Not verified here:** a real supervisor host, built worker images,
Docker/gVisor containment, live providers, or drain under production load.
Enabling the flag in production remains an operator action gated on the
checklist above.


## Tool-free remote utilities

`isolation/utility.py` routes conversation titles, topic classification, staged-task
names and Slack reply compression over `stream_run`, using the configured
`LOMA_REMOTE_DEFAULT_MODEL`. Set that operator model explicitly; missing model,
transport or account configuration keeps the caller's existing text-only fallback.
No local CLI is started on failure. Legacy mode is unchanged.

The backend derives the owner from the stored conversation. `UtilityContext`
permits completed conversations and unstarted tasks without changing their status,
but revalidates active user, owner, deletion and project/agent scope on dispatch
and output. No history, attachments, artifact reuse, knowledge or action tools
are granted. Supplied text passes the history secret sanitizer. Each completion
uses the existing subscription selection/refresh, model relay, durable budget
and audit path, with two provider calls maximum, 8192 output tokens per call,
32 KiB input/output limits and a caller deadline of at most 120 seconds.
Cancellation closes the stream; callers discard partial output on any failure.
These limits retain the native adapter's minimum output-token contract, not a
new per-request pricing policy. Utilities share the pinned deployment rates.

Tests exercise no-tool enforcement in the real assembly, permission revocation,
completed/draft states, unchanged ordinary-run admission, bounded outputs,
no-local-fallback routing and cancellation cleanup. The native assembly test
also runs a tool-free Codex completion against a synthetic provider and checks
usage settlement and absence of history/tools. No live provider is used.

Still separate: remote entity-reference extraction, skill organization,
review-quality analysis, org-learning deduplication and gate-verifier completion;
worker-side GitHub/Linear automation surfaces; worker images and dedicated-host
containment certification; native CI and live-provider certification.

Browser integration also caught an older entrypoint authority mismatch: the live
policy read `owner`, but `RunAuthority` stores `user_email`. Both entrypoint
admission and subscription-policy checks now use the real field; tests construct
actual `RunAuthority` objects rather than permissive lookalike objects.

## Remaining utilities, scoped automation, and deployable image wiring

Remote mode now routes entity-reference extraction, skill organization,
review-quality comparison, org-learning duplicate confirmation and the shadow
support verifier through the same tool-free `isolation.utility` transport.
Stored-conversation callers retain owner, deletion and scope checks. Skill
organization uses the authenticated maintainer's explicit identity, a temporary
message-free context and live maintainer checks; it never selects an arbitrary
admin. Missing ownership/configuration keeps the existing conservative fallback
instead of starting a local CLI. The synchronous offline verifier API refuses
remote execution: the live shadow path uses `RemoteVerifier.assess_remote` on
the backend event loop. Legacy offline backtests remain a separate workflow.

### GitHub / Linear boundaries

Four catalog entries expose fixed operations, not arbitrary URLs or GraphQL:

- `github.read`: PR metadata, changed files, reviews, issue comments, repository
  contents and nonrecursive trees. Paginated lists return 30 items per page.
- `github.propose_write`: issue/PR comment, commit-pinned review, creation of a
  draft PR, creation of a `loma/` branch, or one file create/update on that branch.
  File updates require the provider's existing blob SHA. No merge, force push,
  delete or workflow editing is exposed. Large repository responses fail closed;
  tree traversal is bounded per call, not an unbounded repository clone.
- `linear.read`: issue, comments, team issues and workflow states. Connection
  reads return cursor metadata with a bounded first page.
- `linear.propose_write`: comment, issue creation, title/description update and
  optional state change. Issue identifiers resolve to provider UUIDs; a new
  state must belong to that issue's team.

`LOMA_REMOTE_AUTOMATION_GRANTS` is deny-by-default operator JSON, keyed by exact
owner email, then `github` repository names / `linear` team UUIDs. Active owner
and resource grants are checked before reads, before returning data and before
writes. Credentials stay in the backend; shared integration access is never
mistaken for permission to every repository/team. No worker executes a write.
The durable proposal engine requires the owner's exact-version approval,
checks grants again at execution, persists provider receipts and never retries
an uncertain outcome. Review execution rejects a changed PR head.

Webhook ingress additionally requires `LOMA_REMOTE_WEBHOOK_OWNERS`, an explicit
repository/team-to-owner mapping. No GitHub login or prompt text is treated as
an authenticated email. Binding persists owner/provider/resource on the trusted
conversation; admission rechecks that binding and operator mapping throughout
the run. Existing ownership cannot silently change. These webhook runs do not
receive personal Gmail/Slack/Calendar tools. Writes remain pending proposals,
not autonomous bot sends. Operators must configure grants and mappings before
these automations can run remotely. A proposal is not a posted review or PR.

### Image build and supervisor wiring

`deploy/worker/build.py` builds/publishes native and supervisor images using a
fresh allowlisted context (never `.env`, accounts, .git or the application tree).
It writes digest-only `images.env` atomically after both builds return registry
digests. The supervisor image copies only the supervisor/protocol modules.
`deploy/worker/compose.yaml` consumes those image refs, requires an explicit
private bind address and mounts the control token and TLS material as secrets.
Only the trusted supervisor mounts the dedicated Docker daemon socket. Its
startup still requires runsc and the pinned native image, with no runc fallback.
See [the dedicated-host instructions](../../deploy/worker/README.md).

**Verification boundary:** build orchestration tests mock Docker; they do not
constitute an image build. This editing environment has neither Docker CLI nor
a Docker socket. Actual image builds/publishing, supervisor boot, hostile-worker
containment, live-provider certification and production rollout still require
an approved dedicated host. No host or production configuration was changed.
Native CI workflow installation remains a separate maintainer action.
