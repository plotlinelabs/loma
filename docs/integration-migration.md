# Personal integration migration

## Implemented on this branch

- Organization credential connect/disconnect require admin access.
- Custom connectors are owner-scoped, including legacy records. URL deduplication
  is per owner; new provider IDs are owner-namespaced. They no longer enter the
  shared runtime configuration. Custom OAuth authorization requires ownership.
- CLI broker calls and MCP gateway calls recheck authorization. Lookup errors
  deny access. Unconfigured integrations receive no tool grants.
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
| Google, Slack personal | Personal OAuth CLI | Replace general CLI broker invocation with operation-specific adapters |
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
- Operation-specific validation replacing general privileged CLI execution.
- Review authorization for direct CLI paths outside the broker.
- Validate simultaneous runs, cancellation, resume, artifacts and provider refresh
  in the deployed topology, not only mocked tests.
- Repair preview capacity without deleting other users' data. Local disk capacity
  is not evidence of capacity on the remote preview host.
- Browser OAuth/account linking/replay checks and visual regression checks.

Keep PR #157 draft until these gates are resolved or explicitly split into
separately reviewed releases. This document does not claim P0 completion.
