# Independent worker runtime

## Status and rollout gate

The default execution path now creates a separate OCI/gVisor sandbox for each
worker process. This implementation has unit and transport tests, **not a passing
real-runtime validation yet**. Do not mark the PR merge-ready on unit results.
The local implementation environment refuses namespace creation. The GitHub
credential available to the implementer cannot create/update Actions workflows.
`deploy/worker-isolation-ci.example.yml` is a **non-executing template** for a
repository administrator to install after review. It runs
`tests/test_gvisor_execution.py` on a disposable CI host without production
credentials. Preview deployment testing remains waived, not passed.

## Boundary and configuration

- `LOMA_WORKER_SANDBOX=runsc` is the default and the shipping image setting.
  Missing runtime, image, per-run identity, sockets, namespace support or cgroup
  permissions causes execution failure. There is no unconfined fallback.
- A separate `worker-base` image stage contains runtimes, public dependencies and
  the renderer. It does not copy the application repository, account directories,
  `.env` or backend secrets. Its snapshot is the worker's read-only root filesystem.
- Each worker gets only its own writable workspace and the two broker/gateway
  Unix sockets. It does not mount the backend root, Docker socket, provider
  account directories, or neighboring workspaces.
- gVisor runs with `--network=none`, a private network namespace, empty workload
  capabilities, `noNewPrivileges`, process/memory/CPU limits and read-only rootfs.
  Private loopback bridges forward only to the mounted broker/gateway sockets.
  Raw shell networking, metadata endpoints and host services are not reachable.
- The trusted runner/bundle stays outside worker-writable directories. SDK command
  arguments remain container arguments and cannot add runsc options. SDK proxy
  references are validated; provider secrets are not inherited by runsc or workers.
- OpenCode ingress uses a per-workspace Unix socket. The backend pins and validates
  that socket inode before connecting, rejecting worker-controlled symlinks.
  External `OPENCODE_SERVER_URL` overrides are refused in isolated mode.
- Cleanup explicitly force-deletes the sandbox before workspace/UID release.
  Teardown failure retains the workspace and allocation rather than reusing it.
  Keeping workspace files for debugging does not bypass sandbox teardown.

Relevant settings: `LOMA_WORKER_ROOTFS`, `LOMA_SANDBOX_STATE`,
`LOMA_SANDBOX_SOCKETS`, `LOMA_WORKER_UID_RANGE`. Control/image paths must be
trusted, non-shared-writable directories. Broker/gateway URLs must use
`http://127.0.0.1:<port>` in this single-host implementation. Multi-host workers
and custom external endpoints are not supported by this transport.

Local unit tests explicitly set **both** `LOMA_ENV=development` and
`LOMA_WORKER_SANDBOX=development`. This retains the prior subprocess path solely
for unit testing and single-user development. Do not set these in production.

## Host prerequisites

The host must permit gVisor to create mount/PID/network namespaces and enforce OCI
cgroups. Installing runsc in an otherwise restricted container is insufficient.
The existing default Compose stack has not been validated for these privileges;
a platform owner must supply and review the outer-container policy or run the
trusted backend on an appropriately isolated worker host. Do not fix startup by
changing to development mode, host networking or mounting the host root.

Build the `worker-base` Docker stage, export it into a trusted rootfs directory,
and set `LOMA_WORKER_ROOTFS` when testing outside the shipping image. The supplied
CI template contains exact disposable-host commands. No production host was
modified during implementation.

The design uses the official [gVisor OCI runtime](https://gvisor.dev/docs/user_guide/quick_start/oci/)
and [network isolation](https://gvisor.dev/docs/user_guide/networking/). Runtime
flags and the `--force` teardown behavior were checked against upstream source.

## Rendering and feature compatibility

- `tools/pptx_creator.py` executes inside the independent worker, not the
  credential-bearing backend. Blank composition requires no proprietary master
  deck. Preset/master-deck composition also accepts a caller-uploaded asset pack via
  `python3 tools/pptx_creator.py --library-dir "$HOME/library" generate ...`.
  The library directory must resolve inside this worker's HOME, including symlink
  resolution. Supply `master/slide-index.json`, its referenced source decks and
  assets. A synthetic library generation test passes without any backend assets.
  The proprietary original pack is not supplied or synthesized by this branch. Outputs default to the
  worker's `HOME/artifacts` directory.
- Diagram rendering uses a fixed public HTTPS renderer with bounded, no-redirect
  downloads; parsing/rendering does not execute on the credential-bearing backend.
  Diagram uploads/embedding use the authenticated personal Google broker adapter.
- OpenCode now has personal `loma-claude` and `loma-codex` provider adapters,
  using the standard bundled AI SDK packages and run-bound gateway references.
  The model picker and configuration use only the authenticated owner's connected
  native CLI accounts; neither reads another user's or a pooled account as a
  fallback. This replaces credential-reading auth plugins for these two providers.
  Live protocol compatibility is still unverified; configuration tests are not
  vendor acceptance tests. Arbitrary credential-bearing plugins/stdio MCP remain
  denied and are not claimed as restored feature parity. Do not put pooled account files back in workers to
  enable them. Generic shell downloads/clone operations also require an approved
  broker adapter rather than re-enabling network access.

## Rejected-token recovery

A subscription inference request rejected with HTTP 401 **before streaming** may
refresh and retry once. Backend locking coalesces concurrent refreshes; persisted
cooldowns bound forced attempts across processes. Run/proxy admission is checked
before refresh and again before replay. No timeout, disconnect, 403, 429, 5xx or
partially streamed response is automatically replayed. Failed/non-refreshable
credentials require reconnecting, with no API-billing or organization fallback.

## Required review evidence

Before rollout: install/run the real-runtime CI test, exercise all three installed
CLI versions with mock provider transports, verify worker cancellation and reboot
cleanup on the target host, and perform authorized vendor compatibility checks.
The current evidence does not close these gates. A preview-testing waiver is not
a substitute for a supported execution host or an implemented plugin adapter.


## Standalone validation without workflow installation

On a disposable root-capable host, install runsc and export the `worker-base`
image using the commands in `deploy/worker-isolation-ci.example.yml`, then run:

```bash
sudo bash scripts/verify_worker_isolation.sh /opt/loma-ci-rootfs
```

The command refuses missing runtime/image/namespace support, removes inherited
credentials from the test controller environment, and opts into (not skips) the
real tests. Coverage includes host/sibling visibility, blocked network access,
broker transport, startup of the installed Claude/Codex/OpenCode executables,
and force deletion of detached worker processes. This is not preview deployment
and does not exercise live vendor credentials or claim inference compatibility.
No successful result is recorded yet. The regular unit suite continues to skip
these five tests explicitly. Failed runtime teardown also returns failure instead
of reporting a successful run with residual sandbox state.
