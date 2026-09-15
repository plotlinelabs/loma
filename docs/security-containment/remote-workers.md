# Remote chat worker migration

## Status: transport foundation only, NOT a production cutover

The approved direction is to retain chat while moving its workers off the
backend. `isolation/` now contains the supervisor, backend transport and narrow
wire protocol. **Existing chat is not routed through them yet.** This is not
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
