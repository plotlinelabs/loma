# device_loader: Loma Devices

Everything that lets a Loma agent drive Android emulators and iOS simulators on a
machine someone enrolled lives here.

| Folder | What it is | Runs where |
|---|---|---|
| `runner/` | `loma_device_runner.py`, the single-file runner users download, plus its README and pinned deps | The enrolled machine (laptop, Mac mini, KVM box) |
| `backend/` | Policy layer: leases, ACLs, validation, audit (`service.py`), runner RPC hub, build blobs, store, verification, isolated-worker tool adapter (`gateway.py`) | Loma backend |
| `api/` | HTTP and WebSocket routes: runner socket, dashboard `/api/devices*`, legacy CLI `/internal/devices/*`, runner download | Loma backend |
| `cli/` | `device.py`, the legacy-runtime agent CLI. `tools/device.py` is a shim for it, so existing skills keep working | Loma backend (agent Bash) |
| `tests/` | Unit and integration tests (fake `adb`, real WebSocket runner) | CI |
| `docs/` | Design notes and plans | - |

Rules:
- The runner stays one file. It is served by `GET /device-runner/download` and self-updates from it, so do not split it without adding a bundling step.
- `backend/service.py` is the only place that validates and authorizes device operations. The runner validates again; never skip either side.
- New runner ops or arguments must be gated with `OP_MIN_RUNNER` / `NEW_RUNNER_ARGS` in `backend/service.py`, because older runners stay connected until they update.
