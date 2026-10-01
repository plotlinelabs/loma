# Agent configuration without environment-file edits

Human tasks need **no new manually configured environment variables**. Deploy both
services and click **Admin → Environment → Human task approvals → Set up approvals**.
This writes a dedicated gateway key to the existing Loma database, never to an env
file. It does not reuse the personal-tool signing key.

## Normal administration

| Capability | Normal configuration | Backward-compatible override |
| --- | --- | --- |
| Human tasks | Taskboard and the setup button above | Existing `LOMA_WORK_GATEWAY_SECRET` works until managed setup; managed key wins |
| Bounded agent work | Admin card checkbox, default off; applies on the next worker tick | `LOMA_BOUNDED_WORK_ENABLED`, if present, wins |
| Ashby permission | Admin card active-user list; empty means nobody | `ASHBY_ALLOWED_USERS`, if present, wins (including an empty deny-all value) |
| History recall | Enabled by default; built-in issuer/key discovery | Existing recall enable/key/issuer overrides remain supported |
| Docs/Sheets skill sync | Both enabled by default | Existing enable flags remain optional off-switches |
| Device builds | Integrations → Devices → Build sources | Existing repo/workflow overrides remain supported |
| Backend connection | Existing dashboard backend connection, unchanged | Existing `BACKEND_URL`, not a new requirement |
| Continuation and scheduled work | Existing backend worker, enabled by default | `LOMA_ENABLE_SCHEDULER=false` remains a deployment kill switch |

Only active **admins** can edit permissions/activation. Maintainers can set up the
gateway and inspect status but cannot grant Ashby access. No automatic access
migration or environment deletion occurs. Overrides are displayed and their
controls disabled rather than silently ignored. Remove legacy overrides only in
a separately authorized deployment change; this PR does not remove them.

Bounded activation does not provision an LLM or grant tool access. Its existing
model, budget, session, policy and provider checks still apply. Disabling blocks
new ticks/requests; it does not cancel a tool call already in progress.

## Remote workers: optional infrastructure, not normal app setup

`LOMA_WORKER_URL`, `LOMA_WORKER_CONTROL_TOKEN`, worker TLS certificate/key/CA,
and `LOMA_REMOTE_*` remain specific to a separately deployed isolated worker.
Normal chat and human tasks do not need these. They are not replaced by a weak
default, reused personal-tool secret, or an unsecured connection. Configure them
only if using that infrastructure. Existing worker auth/TLS checks are unchanged.

## Environment safety and deployment checks

- No tracked env file, env writer, deployment script, Dockerfile, or startup
  command is changed by this feature.
- Setup/preferences never invoke the environment editor or restart service.
- New config writes use `gateway_config` in the existing database.
- Transient human-task recovery failure is retried in a background worker,
  not allowed to prevent the API from starting.
- Test only against a disposable `loma_local_*` database, with Slack and the
  scheduler disabled. Test config files belong only to that isolated checkout.
- Check both backend/dashboard health and the signed setup status after
  deployment. A successful build alone does not prove deployment health.
- The separate production-env protection work in PR #232 is not duplicated.

This keeps the existing trusted-server boundary: deployment/DB administrators
have access to configuration. It is not a sandbox against an agent with arbitrary
production database or filesystem credentials.
