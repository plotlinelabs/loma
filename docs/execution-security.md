# Execution isolation

## Status

**P0 remains incomplete. Do not deploy this branch as a security isolation boundary.**
The current implementation provides environment filtering, per-run capabilities,
private workspaces and broker admission, but it is not sufficient to contain
arbitrary agent code. Production worker separation, credential-free subscription
runtimes, operation-specific broker adapters and deployed adversarial validation
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
- **Non-root.** With `LOMA_WORKER_UID`/`LOMA_WORKER_GID` set (defaulted in
  the Docker image to the dedicated `loma-worker` user), every worker drops
  privileges (setuid/setgid via `preexec_fn`, `setpriv` in the CLI
  launcher). This does not establish separation between workers sharing a UID.
  Filesystem isolation must be enforced by the worker deployment, not assumed
  from directory modes.
- **Resource limits**: CPU time, address space, file size, open files,
  process count, no core dumps (`RLIMIT_*`), own session/process group,
  wall-clock watchdog, umask 077. Optional bubblewrap wrapping
  (`LOMA_WORKER_BWRAP=1`) adds mount/pid/ipc/uts namespace isolation where
  bubblewrap is installed.
- Runs are revoked and workers/workspaces torn down at run end; pool clients
  and Codex workers are single-use (one conversation per process).

### Broker (implemented)

- `tool.invoke` — first-party CLI tools (Gmail/Drive/Calendar/Sheets/
  Slides/Docs/Apps Script/Slack/Telegram/notify/loma_skills + integration
  tools) now execute **server-side**. Workers hold shims that forward argv
  (plus small workspace file uploads) to the broker; worker-supplied
  `--user-email`/`--auth-token` are stripped and replaced with server-minted
  identity for the run owner. OAuth tokens, integration keys, the encryption
  key and the DB URI never enter a worker.
- `model.request` — admission for the model gateway. Providers configured
  with server-side keys (Anthropic/OpenAI/OpenRouter/OpenCode Zen) are
  reached via `/model/<provider>/…` on the gateway; the worker authenticates
  with its run capability and the real key is injected server-side.
- `grain.transcript` — unchanged personal-OAuth read-only adapter.
- `mcp.request` binds each proxy to a run capability and rechecks account status,
  integration ownership/sharing and personal connection availability per call.
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

- Subscription-based CLIs own their auth material: the Claude CLI's pool
  account `CLAUDE_CONFIG_DIR`, the Codex `CODEX_HOME`, and the OpenCode
  `auth.json` copy are readable inside that worker's sandbox. These are
  pooled model-account credentials (deliberately shared across runs), not
  backend infrastructure secrets; fully brokering them requires a
  protocol-level auth proxy (future work).
- OpenCode/Codex warm-session and prewarm optimizations that shared server
  processes across runs are disabled/removed; each run pays a worker start.
  Conversation continuity is carried by the textual conversation context.
- Fixed-argv admin utilities (`claude auth status/logout`, `codex logout`
  in auth/usage routes) still run backend-side; they execute no
  model-driven code. Background `claude -p` utility calls (titles/topics)
  run with the scrubbed worker env and a scratch workspace.
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
