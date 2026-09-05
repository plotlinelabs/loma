# Execution isolation migration

## Status

The broker implementation is stage 1 infrastructure, **not a P0 fix by itself**.
It is intentionally not mounted on the public API or connected to current agent
runtimes. Existing runtimes still execute with backend filesystem/environment
access. Do not enable broker issuance to these runtimes or mark P0 resolved.

## Broker contract

- Trusted controller creates a new run and opaque capability using `Broker.issue`.
  Identity must come from authenticated controller state, and resource grants from
  controller policy/explicit approval, never prompts or model-generated arguments.
- A single deployment identity is the tenant boundary. Use a separate database and
  broker for every organization; this does not implement multi-tenant membership.
- Call `Broker.initialize` before serving to install the capability TTL index.
- `broker.http.create_app` exposes only `POST /v1/invoke`. Workers supply a bearer
  capability plus `operation` and `resource`. There are no public issuance APIs.
- Capabilities expire within 15 minutes and are stored only as SHA-256 digests.
  Revocation and remaining-call admission are atomic database operations. No token
  cache; expiry is checked independently of MongoDB's asynchronous TTL cleanup.
- Revocation/status changes prevent subsequent admission, not already-admitted
  requests. Run cancellation must also terminate the worker and in-flight requests.
- Audit records contain run/operation/time only. Admission audit failure blocks I/O.
  Outcome/denial metrics and retention controls remain rollout work.
- Initial adapter: `grain.transcript`, exact recording UUID allowlist, personal OAuth
  only. It never falls back to an organization API key. Tokens stay in the broker.
  Fixed HTTPS destination, no redirects or environment proxies, bounded responses.
  Expired OAuth tokens are denied; automatic refresh is intentionally not implemented.
- The broker's only supported permission policy currently is personal Grain access.
  Shared/organization connections and other operations remain disabled. Do not
  describe this as centralized authorization for existing tools/runtimes.

## Required follow-up stages

1. Trusted controller integration: authenticate execution owners (including scheduled
   flows), authorize requested resource grants, issue/revoke runs, and wire lifecycle
   cancellation. Add distributed rate limits and concurrent-request limits.
2. Deploy per-run hardened workers (gVisor/Kata/microVM boundary selected and verified
   by infrastructure owners), non-root and resource-limited. No host/socket mounts,
   backend environment, shared homes, credential stores, or cloud metadata access.
3. Private TLS/mTLS broker ingress; bind capability admission to worker identity.
   Bearer capabilities alone are transferable if stolen, not proof of worker identity.
   Enforce egress at the network layer; adapter destination pinning is not DNS/IP
   isolation. Only broker/model gateways may be reached. No public ingress.
4. Broker model calls and migrate all three runtimes, CLI/MCP operations, terminals,
   scheduled flows, artifacts and session resume. Disable unsupported operations;
   remove legacy execution only after replacements are validated.
5. Rotate exposed credentials, conduct adversarial deployed end-to-end tests, then
   staged rollout. Rollback disables execution, never restores unsafe execution.

## Release criteria

The current unit tests use synthetic credentials and mocked DB/provider responses.
They are not a deployment test or proof of sandbox containment. Before release,
verify MongoDB atomic budget/revocation behavior across processes, TLS identity and
network policy, cross-user filesystem isolation, metadata blocking, model-provider
brokering, cancellation, artifacts/resume, and every alternate execution path.
