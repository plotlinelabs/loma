# Claude login and shared round-robin pool

## Standard deployment: one command

For an organization with permission to pool its Claude subscriptions:

```bash
docker compose up -d --build
```

That is the entire login infrastructure setup. Open **Integrations > Personal >
Claude Code > Login**, open the Anthropic sign-in link, and paste its authorization
code. Repeat for each account. Loma saves the connection and discovers it without
restarting. Users' tasks share the connected-account pool; Google/Slack tool
credentials remain those of the task's requester, not the Claude account owner.

The normal Compose stack now includes two small same-server components:
`loma-login` (fixed-command login sandbox) and `loma-login-proxy` (restricted
outbound HTTPS). They start automatically, publish no ports, and need no separate
server, Docker daemon, gVisor installation, certificate or service token. The
backend uses a private Unix socket. **Do deploy the full stack**, not just the
backend image. Linux Docker Engine with normal rootful container capabilities
is required. Rootless Docker is not supported by this sandbox.

No new login environment variables are required. Existing
`LOMA_CLAUDE_SHARED_LOGIN=off` deliberately disables login; remove it to use the
bundled default. Existing dedicated-host deployments must set
`LOMA_LOGIN_MODE=remote` to continue using their URL/token/mTLS configuration.
Do not set `LOMA_REMOTE_WORKERS=on` just for login: that flag selects a separate
TASK execution architecture and still requires its own worker infrastructure.
Login works with either the existing local task pool or remote task workers.

Credentials persist in the existing `loma-claude-users` volume. Keep the standard
`CLAUDE_USERS_DIR=/opt/claude-users` path unless mounting an alternative. Do not run
`docker compose down -v` unless intentionally deleting saved connections.
Permissions are 0700/0600, not encryption: encrypt the deployment's disks/backups
with your infrastructure controls. Never run local and remote token refreshers
against the same account store concurrently.

## What remains isolated

- Host terminal HTTP/WebSocket endpoints remain disabled for every role.
- Official, pinned Claude Code executes only `claude auth login`, never task
  prompts, browser commands, project hooks or backend code.
- Each login gets a unique unprivileged UID, a private 0700 temporary home and
  chroot. The runtime is read-only. No backend secrets, persistent accounts,
  control socket, Docker socket or host directories enter the chroot. Each login
  has a private PID/mount namespace and read-only `/proc` containing only its own
  processes. Claude's runtime needs `/proc/self/maps` to initialize its stack;
  hiding procfs entirely crashes it before the sign-in prompt.
- Login networking can reach only the bundled proxy. Startup installs IPv4/IPv6
  deny-by-default rules inside the login container; if this fails the service
  never becomes healthy. The proxy allows only exact Anthropic HTTPS hostnames,
  rejects private/metadata addresses and connects to the IP it validated.
- Resource/concurrency limits and a ten-minute login deadline bound abuse. On
  completion/cancel/failure/disconnect, all processes of the session's UID are
  killed and its temporary files deleted. Interrupted login sessions restart
  from scratch after a backend restart; saved connections survive.
- Only allowlisted identity/OAuth fields reach the backend. No raw terminal
  output or credential bundle reaches a browser. Disconnect removes local
  credentials; revoke at Anthropic separately if provider-side revocation is
  needed. Existing active tasks may finish; disconnect blocks future selection.

**Trade-off:** this shares the application host's kernel. A kernel/container
escape has a larger blast radius than the dedicated gVisor-host option below.
The trusted broker additionally needs `SYS_ADMIN` to create PID/mount namespaces
and mount procfs. Only this broker uses `apparmor:unconfined` because Docker's
default AppArmor profile denies mounts; default seccomp and no-new-privileges
remain enabled. This broadens the broker's security exposure and deserves review.
The CLI drops capabilities before exec and never receives a host procfs, Docker
socket, host PID mode or privileged container. Namespace/mount failures stop
login rather than falling back to a less-isolated process. This PR does not
upgrade the security boundary of legacy local TASK execution.

Login sessions are still process-local. Multi-replica backends need sticky login
routing and shared credential storage; the out-of-box stack is single-backend.

## Verification before first production use

Run `bash scripts/test-bundled-login.sh` on a Docker host to exercise bundled
image startup, the real official CLI authorization-link step, sandbox file/network
denials and cleanup without a paid model call or real credentials. It creates
an ephemeral project from committed sources and never loads operator secrets.
The existing preview deployment script runs this image-level check in a
separate throwaway project before starting the actual preview. It never uses
preview credentials and remains a mandatory pre-merge check. Unit/browser fixtures cover successful synthetic publication,
owner access, cancel/disconnect and pool rotation. A maintainer must still finish
one real Anthropic login, validate refresh/restart and connect a second test
account before calling provider authentication end-to-end verified.

If login is unavailable, run `docker compose ps` and inspect `docker compose logs
loma-login`. A failed firewall/image preflight is a hard failure, never a fallback
to a host process. Restart the login service after replacing the proxy container
with a different IP; the firewall intentionally pins that IP. A normal full-stack
recreation initializes it again.

## Optional dedicated-host mode (existing deployments)

1. Keep `LOMA_REMOTE_WORKERS=on` and deploy the existing remote-task architecture.
   Host terminal endpoints remain 403, including for admins. No local fallback.
2. Provision a **separate dedicated login daemon/host** with `runsc`, not `runc`.
   Do not share its Docker daemon with task workers: the supervisor startup
   cleanup deliberately removes containers with the worker label.
3. Build `deploy/worker/login.Dockerfile` and rebuild
   `deploy/worker/supervisor.Dockerfile`, push both images, and pin their digests.
   The login image contains official Claude Code 2.1.261 and the fixed wrapper,
   not the backend or task runner. Never use the native task image for login.
4. Provision a bridge named `loma-login-...`, disable IPv6, and enforce host-level
   INPUT/FORWARD (including Docker's forwarding path) egress rules **before**
   setting label `io.loma.login-egress=restricted`. Allow required public
   Anthropic HTTPS endpoints and DNS only. Deny host/bridge services, RFC1918,
   loopback, link-local/metadata (169.254.0.0/16), private IPv6 and internal VPCs.
   The label is an operator attestation, **not a firewall implementation**.
   Prove those denies from the actual gVisor container on the deployment host.
5. Use `deploy/worker/login-compose.yaml` on that host. Configure the required
   digest-pinned images, private bind IP, network and secret-file paths. Use a
   separate >=32-character login token and mTLS certificates. Restrict ingress
   to backend addresses only; never expose the supervisor directly to browsers.
6. Backend configuration:

   ```text
   LOMA_LOGIN_MODE=remote
   LOMA_CLAUDE_SHARED_LOGIN=on
   LOMA_LOGIN_URL=https://login-host.example:8443
   LOMA_LOGIN_TOKEN=<separate secret>
   LOMA_LOGIN_TLS_CA=/run/secrets/login-ca
   LOMA_LOGIN_TLS_CERT=/run/secrets/login-client-cert
   LOMA_LOGIN_TLS_KEY=/run/secrets/login-client-key
   CLAUDE_USERS_DIR=/encrypted/claude-users
   LOMA_REMOTE_ACCOUNT_CAPACITY=1
   ```

   All backend replicas must share that volume with flock/atomic-rename semantics.
   Do not run legacy CLI pools against these files. Explicit
   `LOMA_REMOTE_CLAUDE_ACCOUNTS` entries take precedence for duplicate owners;
   remove stale explicit entries when migrating an owner to managed login.
7. Login REST sessions are process-local and expire after 10 minutes. Route all
   `/api/claude-auth/login*` requests from one user to the same backend instance
   (sticky routing). Backend restart loses an unfinished login; start again.
   Durable generations prevent a late completion after disconnect/reconnect.
8. Before production enablement, perform a real login with a dedicated test
   subscription, confirm refresh, cancellation/expiry/container cleanup,
   reconnect/disconnect, two-account rotation and personal-tool isolation.
   This is mandatory even when unit tests and browser fixture tests pass.

Disable `LOMA_CLAUDE_SHARED_LOGIN` to stop new logins/discovery. Stop the login
supervisor to terminate its containers. Do not roll back by enabling host shells.
Existing active runs retain their pinned accounts until release; disabling the
flag alone is not emergency credential revocation.

## Verification scope

Tests use synthetic credentials and a throwaway `loma_local_*` Mongo database.
Browser evidence uses the actual backend/dashboard with an injected **synthetic
login transport**, not real Anthropic authentication. The live gVisor login image
and network firewall must be verified on the dedicated deployment host.

Official command reference: https://code.claude.com/docs/en/cli-reference


### Recovering login and pasted codes

The Personal integrations page recovers the current owner's active login after a
refresh. Clicking Login again resumes that session instead of returning a conflict.
Closing the dialog leaves it resumable until its 10-minute expiry. Use **Cancel
login** or **Cancel & restart** to terminate it immediately; cancelling a reconnect
does not remove an existing saved connection. Sessions still require sticky routing
and do not survive a backend restart.

Validation errors leave the code input editable. The backend removes a trailing
allowlisted Anthropic authorization URL, including the terminal BEL delimiter and
surrounding whitespace, while preserving the complete `code#state`. Unknown URLs,
embedded controls, extra prose and overlong input are rejected rather than guessed.
No pasted codes are logged or included in test fixtures.
