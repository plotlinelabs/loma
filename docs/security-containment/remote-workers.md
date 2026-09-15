# Remote chat worker migration

## Status: partial native runtime adapter, NOT a production cutover

The approved direction is to retain chat while moving its workers off the
backend. `isolation/` now contains the supervisor, backend transport, narrow
wire protocol and an initial Codex adapter (see the latest section below). **Existing chat is not routed through them yet.** This is not
completion of runtime isolation and must not be used to mark PR #191 ready.
No existing backend, chat, scheduler, model account or production deployment is
changed by adding these modules. Chat still has its previously documented risk.

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


## Native Codex adapter (latest continuation)

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
