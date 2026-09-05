# Execution isolation

## Status

**P0 remains incomplete. Do not deploy this branch as a security isolation boundary.**
The current implementation provides environment filtering, per-run capabilities,
private workspaces and broker admission, but it is not sufficient to contain
arbitrary agent code. Production worker separation, subscription-protocol validation,
renderer and plugin adapter coverage and deployed adversarial validation
remain release blockers. Unit tests do not establish isolation between workers.

## Architecture

```
backend (trusted)                          worker (untrusted, per run)
─────────────────────────────              ───────────────────────────
broker/controller.py  ── issues ──────►  run capability (opaque, TTL,
  per-run capability + workspace           call budget, revocable)
broker/service.py + broker/http.py  ◄──  tools/<name>.py shims call
  POST /v1/invoke (loopback)               tool.invoke via the broker;
  executes real tools server-side          the capability is the worker's
  with the run owner's identity            broker credential
broker/gateway.py (loopback)        ◄──  MCP + model traffic; the gateway
  injects org/user/provider secrets        injects real credentials
  server-side, never into the worker       server-side
```

### Worker boundary (implemented)

Every runtime subprocess and terminal is spawned through `broker/worker.py`:

- **Fresh private workspace per run** (0700) under `LOMA_WORKER_ROOT`;
  HOME/TMPDIR/cwd point inside it; it is deleted when the run ends.
- **No backend environment inheritance.** Workers receive only an
  allowlist-built minimal env (PATH/HOME/TMPDIR/TERM/locale + explicitly
  validated runtime settings). A denylist of backend secret names AND their
  values is enforced fail-closed at build and spawn time. For the Claude
  Agent SDK (which merges `os.environ` into its CLI subprocess), a generated
  launcher re-execs the CLI through `env -i` with the scrubbed allowlist.
- **Non-root, per-run identity.** With `LOMA_WORKER_UID_RANGE` set (e.g.
  `200000-200999`) and the backend running as root, every workspace is
  bound to its own uid from the range: two concurrent runs of different
  users are different Unix identities and cannot read or write each other's
  workspaces, temp files, or shims. Uids are allocated at workspace
  creation under a process-shared file lock, cross-checked against workspace
  owners and live process uids (including detached children), and released only after the workspace is
  deleted. Exhausting the range, configuring a range without root, or a
  malformed range fails closed.
  **Fallback (weaker boundary):** without a range, workers drop to the
  single shared `LOMA_WORKER_UID`/`LOMA_WORKER_GID`. Concurrent runs then
  share one uid, so directory modes cannot separate them — a compromised
  run could read a concurrent run's workspace. The fallback enforces strict
  0700 ownership verification of every workspace before spawn (fail closed
  on foreign owner or group/world access bits), which protects against
  backend-side tampering but NOT against a live same-uid sibling worker.
  Deployments that run concurrent multi-user workloads must configure the
  uid range (or provide kernel-level per-worker sandboxes).
- **Resource limits**: CPU time, address space, file size, open files,
  process count, no core dumps (`RLIMIT_*`), Linux no-new-privileges, own session/process group,
  wall-clock watchdog, umask 077. Optional bubblewrap wrapping
  (`LOMA_WORKER_BWRAP=1`) adds mount/pid/ipc/uts namespace isolation where
  bubblewrap is installed. Explicitly requesting bubblewrap now fails closed
  if it is unavailable; both SDK launchers and terminals apply the same wrapper.
  The blanket `/opt` mount is removed. A configured privilege drop cannot silently
  revert to the backend identity.
- Runs are revoked and workers/workspaces torn down at run end; pool clients
  and Codex workers are single-use (one conversation per process).

### Broker (implemented)

- `tool.invoke` accepts only commands and exact flags enumerated in
  `broker/tool_policy.py`. Registration or an integration grant alone does not
  authorize an arbitrary command. Unknown commands/flags, duplicate flags, local
  directory imports and unreviewed renderer utilities fail closed before I/O.
  File arguments must refer to inline uploads from the same request, never an
  existing backend path. Scalar text is not rewritten as a file. CLI output is
  bounded while reading, and cancellation/timeouts terminate the tool process
  group and remove its uploaded inputs. Identity tokens are explicitly redacted.
- Direct organization-integration CLI entrypoints require authenticated user
  credentials and independently check active status, ownership and team/user
  sharing before dispatch, even when an environment API key exists. No cache is
  used for this authorization. The broker injects its authenticated owner's
  identity for these commands as well as personal tools.
- Skill CLI reads now require an active authenticated user and hide other users'
  personal skills; direct reads and writes enforce personal ownership.
- `model.request` — admission for the model gateway. Providers configured
  with server-side keys (Anthropic/OpenAI/OpenRouter/OpenCode Zen) are
  reached via `/model/<provider>/…` on the gateway; the worker authenticates
  with its run capability and the real key is injected server-side. Model routes
  allow only inference and model catalog operations, not arbitrary provider APIs.
- `grain.transcript` — unchanged personal-OAuth read-only adapter.
- `mcp.request` binds each proxy to a run capability and rechecks account status,
  integration ownership/sharing and personal connection availability per call.
- MCP grants are pinned to the exact registered endpoint and supported transport
  methods. Worker-controlled suffixes and queries are denied. URL validation uses
  the parsed authority rather than a string-prefix check.
- MCP: HTTP MCP servers are re-pointed at `/mcp/<proxy-token>` on the
  gateway; real upstream URLs and auth headers stay in backend memory,
  proxy tokens are revoked with the owning client/run. **Stdio MCP servers
  are disabled in worker configs (fail closed)** — their env vars would
  carry credentials into the worker; they are logged as disabled and never
  silently fall back to org credentials.
- Grants come from controller policy at issue time: personal tools for the
  authenticated owner, integration tools filtered by integration sharing
  rules, configured model providers. Runs without an authenticated owner
  (anonymous webhooks) receive **no capability**: every brokered call is
  denied. Controller/broker startup failure also fails closed.
- Capabilities: opaque, stored as SHA-256 digests, ≤2h TTL (default 1h),
  bounded call budget, atomic admission, active-account recheck per call,
  revoked at run end. Broker + gateway bind to loopback only.

## Command compatibility

The reviewed command schemas cover the original Google/personal tools plus
Telegram, real PostHog event commands, Ashby, Grafana command groups, Sentry,
Pylon commands, Zoho Books, MonetizeNow, PhantomBuster, Linear and GitHub thread
operations. Only explicitly declared flags may repeat. Unlisted commands and
renderer utilities still fail closed. This is not full feature parity.

Uploads support bounded UTF-8 text and explicitly tagged, strictly validated
base64 binary inputs. Worker paths are replaced by request-owned backend paths.
Reviewed output flags are rewritten to request-owned files and returned as bounded
base64 artifacts; workers refuse output paths outside their workspace. Symlinks,
hardlinks and special files are rejected. Stdin is forwarded only for explicitly
approved commands, including Pylon notes/replies, Slack bot messages and Zoho email
commands. Zoho invoice/estimate PDF downloads use the artifact channel.

## Deployment requirements (NOT provided by this repo)

The subprocess boundary is real but not kernel-grade. Production must add:

1. Separate hardened sandboxes for each worker, distinct from the backend
   (gVisor, Kata, or Firecracker class isolation). Placing the backend and all
   workers inside one hardened container does not provide this separation. No docker socket, no host mounts beyond
   the declared volumes, no cloud metadata service reachability.
2. Network policy: workers may reach only the loopback broker/gateway and
   approved model endpoints. Adapter/gateway destination pinning is not
   DNS/IP-level egress control.
3. In multi-host setups, private TLS/mTLS ingress for broker/gateway and
   worker network identity binding; bearer capabilities alone are
   transferable if stolen.
4. File permissions: keep `.env`, secrets and SSH mounts at 0600/0700 so
   the non-root worker uid cannot read them (the image sets `/root` to 0700).

## Known caveats

- Claude and Codex now use fresh worker configuration and run-bound subscription
  proxy references. Real credentials are read only by the backend gateway, which
  restricts subscription calls to inference routes and rechecks run admission.
  Anonymous proxy registration and credential-file fallback do not exist.
  Backend refresh now renews expiring Claude tokens and expiry-bearing Codex
  access tokens with pinned provider endpoints, a per-account process-shared lock,
  bounded responses and atomic 0600 credential replacement. Revocation is checked
  again after refresh. Non-refreshable expired tokens fail closed. Opaque tokens
  without expiry metadata cannot be proactively renewed; upstream rejection still
  requires reconnecting. **Live vendor compatibility remains unverified**, including
  subscription-only OpenCode plugins. No refresh token enters a worker. Codex uses a custom Responses provider
  as described in the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
- OpenCode no longer stages provider auth.json into workers or catalog storage.
  Gateway-configured API providers remain supported; subscription-only OpenCode
  plugins still require reviewed adapters. Existing legacy catalog auth files cause
  a startup error and require operator cleanup. Managed servers use separate
  random passwords so another worker cannot anonymously query their API.
- OpenCode/Codex warm-session and prewarm optimizations that shared server
  processes across runs are disabled/removed; each run pays a worker start.
  Conversation continuity is carried by the textual conversation context.
- Fixed-argv admin utilities (`claude auth status/logout`, `codex logout`
  in auth/usage routes) still run backend-side; they execute no
  model-driven code. Background `claude -p` utility calls (titles/topics)
  require a current authenticated run and use the same subscription proxy with
  fresh configuration and a scratch workspace. Unscoped calls use the caller's
  deterministic fallback, never ambient account credentials.
- Tool outputs that are files are produced server-side; the existing file
  delivery path serves them. Worker-side files can be passed INTO tools
  only via the bounded inline upload in the shim.
- `OPENCODE_SERVER_URL` (operator-managed external server) bypasses the
  managed worker spawn; isolation of an external server is the operator's
  responsibility.

## Validation

`tests/test_worker_isolation.py`, `tests/test_tool_invoke.py`,
`tests/test_broker_gateway.py`, `tests/test_runtime_isolation.py`, and
`tests/test_adversarial_worker.py` cover: no backend secrets in worker envs
(including real subprocess env dumps and /proc scrapes), launcher scrub,
privilege-drop file isolation (cross-user artifact reads and backend config
reads fail), forged/expired/revoked capability rejection over HTTP,
fail-closed authorization on datastore errors, identity-flag stripping,
sharing-rule admission filtering, MCP gateway admission, model-key
injection, and source-level guards that the legacy env-inheriting execution
path stays gone. These are synthetic tests: they do not replace deployed
adversarial end-to-end verification under the production runtime/network
policy, which remains a release gate.

## Current implementation validation

The additional synthetic subscription tests exercise proxy admission, credential
injection, route limits, revocation, malformed auth, fresh runtime configuration,
binary input validation and per-server OpenCode authentication. They deliberately
make no calls using real provider credentials. The local environment rejects
namespace creation (`unshare: Operation not permitted`) and has no container
runtime; it cannot supply the production isolation release evidence.

## Trusted proxy identity and account setup

The backend now requires a short-lived HMAC identity assertion from the dashboard
or task-MCP sidecar in addition to `X-User-Email`. Nginx forwards the assertion
from its authenticated whoami subrequest and overwrites client-supplied headers.
The signature is purpose-bound; active-account and role checks still run after
verification. Unsigned internal requests fail before database lookup.

Compose supplies `BACKEND_PROXY_SECRET` to the dashboard and task-MCP sidecar,
falling back to the existing `OAUTH_ENCRYPTION_KEY`. The backend uses the same
selection. Non-Compose deployments must explicitly configure the matching key
in all three services. Assertions expire after 60 seconds and require synchronized
clocks. This control does not replace worker network isolation.

Account setup terminals now execute only `claude auth login` or
`codex login --device-auth`, with a one-use owner-bound grant and minimal
environment. They run as trusted credential setup utilities, never as an arbitrary
backend shell or model invocation. The UI no longer sends a shell command to a
generic terminal. Interactive agent terminals remain isolated workers.

The backend tightens the mounted dotenv file to 0600 before starting runtimes;
environment edits preserve that mode. Preview-generated secrets are also private.
Read-only secret mounts and all other backend files still require the production
sandbox boundary described above.

### Remaining merge gates after the current changes

- Production per-worker hardened sandbox and egress policy are not implemented or
  deployed by this branch. The local host cannot run namespace/container tests.
- Vendor subscription end-to-end validation remains outstanding. Proactive backend
  refresh is implemented for expiring Claude/Codex credentials; opaque token
  recovery and OpenCode subscription-only plugins still need compatibility work.
- Apollo, Dataroom, CDN, Stitch and agreement commands now have reviewed schemas.
  Renderer utilities remain denied rather than executing untrusted rendering
  specifications beside backend secrets. Safe worker-local rendering needs the
  independent sandbox deployment.
- Preview deployment testing, including deployed browser/OAuth and signed-proxy
  validation, is explicitly skipped at the requester's direction for this handoff.
  It has not passed. No preview host changes or deployment retries are part of
  this validation pass.

The preview-testing waiver does not close the implementation gaps above. Passing
local tests/CI does not establish production isolation. Keep the PR draft until
the remaining implementation scope and compatibility risks are reviewed.

### Reproducible local verification

Run the backend suite without inherited model or signing-key configuration:

```bash
env -u AGENT_DEFAULT_MODEL -u BACKEND_PROXY_SECRET -u OAUTH_ENCRYPTION_KEY python3 -m pytest -q
```

Request-signing tests supply synthetic keys through scoped fixtures. They must
not depend on local credentials; missing production configuration still fails
closed. The integration-access suite explicitly imports the signing fixture used
by its shared request helper so it also works in a clean CI environment.


## Additional adapter and personal webhook implementation

Apollo and Dataroom commands have static schemas, including declared repeatable
flags. Stitch generation and bounded download, CDN upload/download, and agreement
read/annotate/upload/download are covered. Explicit output paths are required for
new download/annotation adapters; they use request-owned artifact transport.
Agreement OAuth uses the authenticated caller, not a credential-free utility grant.
Document archive expansion and downloads are bounded. Public URL fetches accept
only public HTTPS destinations through the connector's checked DNS answers, deny
redirects and enforce body-size/time limits. Unknown commands remain denied.

### Personal Grain webhook setup

After connecting personal Grain OAuth, the authenticated user can call
`PUT /api/integrations/grain/webhook`. The response returns a relative webhook
path and a one-time Authorization header. Configure the automation sender to POST
`{"recording_id": "..."}` to that path with that header. This is an authenticated
custom automation endpoint, not automatic registration with Grain's vendor API.
The stored credential is a SHA-256 digest; it expires after 90 days. Repeating PUT
rotates it immediately; DELETE on the same API path revokes it. Never put the
credential in the URL or webhook body.

The webhook derives the owner from the stored subscription and fetches through
that owner's current personal OAuth connection. Account status, expiry, rotation
and disconnection are checked. Delivery retries are idempotent per owner/recording.
Transcripts go to `personal_grain_events`, **not** organization-wide changestreams.
`GET /api/integrations/grain/recordings` returns the current user's latest 50 events.
The legacy organization webhook remains disabled; it has no verified personal
owner. Dashboard setup controls and additional automated downstream consumers are
not included in this API implementation.

No preview deployment testing was performed for these additions, as requested.
Unit and local subprocess checks are not evidence of independent worker isolation
or real vendor subscription compatibility.


### Legacy credential directories and background utilities

Before proxy-backed Claude/Codex execution, account directories are made private
and reclaimed by the trusted backend identity. Non-root backends fail closed if
they cannot secure a legacy worker-owned directory; symlink directories are
rejected. Stop all workers from older deployments before upgrading: permission
changes cannot revoke already-open file descriptors. Background utility calls
also revoke their proxy and wait for process termination before deleting scratch
state, including cancellation/error paths. This closes a credential-directory
fallback, not the independent worker containment requirement.
