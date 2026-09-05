# Personal integration migration

## Implemented on this branch

- Organization credential connect/disconnect require admin access.
- Custom connectors are owner-scoped, including legacy records. URL deduplication
  is per owner; new provider IDs are owner-namespaced. They no longer enter the
  shared runtime configuration. Custom OAuth authorization requires ownership.
- CLI broker calls, direct organization CLI entrypoints and MCP gateway calls
  recheck authorization. Lookup errors deny access, including when credentials
  are supplied through environment variables. Direct CLI invocations now require
  `--user-email` and `--auth-token`. Unconfigured integrations receive no grants.
- Broker CLI commands have explicit command/flag contracts and request-scoped
  upload validation. Unreviewed commands are disabled; see the compatibility
  list in `execution-security.md`. Full adapter coverage is not complete.
- Skill CLI reads and writes enforce active-user identity and personal ownership;
  root/subcommand argument placement preserves the supplied identity.
- MCP proxies require a live run capability. Codex and OpenCode resolve caller
  configuration and sharing exclusions, as does Claude. Personal initialization
  errors do not start a shared fallback client.
- Grain CLI search/recent/transcript require authenticated personal OAuth. No
  environment or organization token fallback is used. Missing/expired/revoked
  connections fail explicitly; refresh uses the existing trusted OAuth service.
- Grain legacy webhook enrichment returns 503: it has no verified personal
  subscription owner. Re-enable only after owner-bound subscriptions exist.
- Skill folder moves enforce personal ownership and admin-only workspace edits.
  The skills picker returns owned personal plus workspace/system skills to all
  authenticated roles, including chatter. UI load failures display an error.

## Migration inventory

| Integration family | Current access path | Next action |
| --- | --- | --- |
| Google, Slack personal | Personal OAuth CLI | Reviewed CLI command schemas implemented; complete typed provider adapters and binary artifact transfer |
| Grain | Personal OAuth CLI; personal MCP when configured | Validate consent/refresh in browser; introduce owner-bound webhook subscriptions |
| HubSpot, Notion | Personal OAuth MCP overrides | Validate provider scopes, refresh, reconnect and runtime compatibility |
| Custom remote MCP | Owner-only configuration, optional personal OAuth | Validate destinations and MCP transport/session behavior in deployment |
| GitHub, Linear, Apollo, Ashby, Pylon, PostHog, Sentry, Grafana | Organization-managed connectors/tools where configured | Choose personal OAuth or explicitly administered service identities per provider |
| Billing, CDN and automation providers | Organization-managed credentials where configured | Preserve explicit admin ownership; assess least-privilege scopes before migration |
| Claude/Codex subscription pools and OpenCode auth | Runtime-specific account material | Credential-free runtime protocol migration remains a P0 blocker |

The inventory is based on repository configuration, not a live account census.
OAuth support and scopes must be verified with each provider before migration.
UI labels distinguish organization-managed connections and personal connectors;
organization sharing rules remain visible through the existing sharing dialog.

## Release gates still open

- Per-worker isolation, restricted network egress and server-side subscription auth.
- Complete reviewed adapter coverage for disabled commands and replace the
  remaining trusted CLI bridge with typed provider adapters where appropriate.
- Validate simultaneous runs, cancellation, resume, artifacts and provider refresh
  in the deployed topology, not only mocked tests.
- Repair preview capacity without deleting other users' data. Local disk capacity
  is not evidence of capacity on the remote preview host.
- Browser OAuth/account linking/replay checks and visual regression checks.

Keep PR #157 draft until these gates are resolved or explicitly split into
separately reviewed releases. This document does not claim P0 completion.
