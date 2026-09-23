# Isolated Claude login and shared subscription pool

This feature is **off by default**. Enable it only for an organization whose
Anthropic agreement permits shared subscription usage. No production deployment
or provider login is performed by this PR.

## Flow

Integrations > Personal > Claude Code > Login starts the official pinned
`claude auth login` in a fresh gVisor container on a **separate login host**.
The UI shows only the provider's allowlisted authorization URL and accepts a
single authorization code, not arbitrary terminal input. The CLI performs OAuth;
Loma does not implement a second OAuth authorization-code exchange.

After the CLI exits successfully, the existing authenticated supervisor protocol
returns an allowlisted credential bundle to the backend. Credentials are never
returned to the browser, logged, or mounted into a task worker. The login
container is removed on completion, error, deadline or backend disconnect.

Backend files are private (directory 0700, files 0600) on an **encrypted volume**.
This PR does not implement disk encryption: configuring and verifying encrypted
storage, backups and access controls is a deployment prerequisite. Only OAuth
identity and credential fields persist; no CLI history, hooks or MCP settings.

Successfully connected accounts join the existing isolated-task selector without
a restart. New tasks rotate through eligible accounts regardless of the task
owner. Inactive/deleted users, admin-disabled pool accounts, disconnected
credentials, full capacity and cooldowns are skipped. Selection order is
process-local; capacity leases and cooldowns are Mongo-backed across processes.
An account is pinned for a run: no unsafe mid-run replay on another account.
The existing backend refresh adapter renews access tokens; ambiguous/revoked
refreshes require reconnect. Personal tools still use the run owner's identity.

Disconnect fences pending logins and removes the local credentials. Already-sent
provider calls cannot be undone; subsequent calls fail revalidation. Disconnect
is local removal, not a guarantee of provider-side token revocation; revoke the
session in Anthropic's account settings if needed.

## Deployment prerequisites and rollout gate

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
