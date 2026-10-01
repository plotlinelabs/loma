# Loma Devices vs. callstack/agent-device, and the improvement plan

Reviewed against `callstack/agent-device` v0.21.18 (commit `f7d7f38`, MIT), 2026-10-01.
Other references checked: openatx/uiautomator2, mobile-next/mobile-mcp, takahirom/arbigent,
AXe, Google Android CLI, facebook/idb (README and release level only).

## Where each side is stronger

| Area | Loma | agent-device |
|---|---|---|
| Control plane | Outbound WebSocket, enrollment tokens, Mongo leases, audit log, app allowlist | Local daemon with a token; remote use needs a proxy |
| Build install | Backend fetches the GitHub artifact, runner verifies sha256, caches it | No checksum on downloads |
| Maestro safety | Hard command allowlist | Runs `runScript` in `node:vm` (not a sandbox) |
| Android UI tree | `uiautomator dump`; fails when the UI never goes idle | Helper APK on `UiAutomation`; waits at most 500 ms, then captures anyway |
| iOS | Simulators only, needs `idb` | Own accessibility bridge plus an XCUITest runner; physical devices |
| After an action | Agent must read the tree again | `--settle` returns what changed, with fresh refs |
| Errors | One free-text string | Code, reason, hint, retriable, whether input reached the device |

## Plan

**Phase 1: backend and runner only, no new binaries.** Fewer model turns per test, errors an agent can act on, no silent wrong taps.
1. Settle and diff after `tap`, `tap_text`, `set_text`, `swipe`, `key` (opt-in `settle`).
2. Structured errors: `code`, `retriable`, `dispatched`, `hint`.
3. Richer Android tree: enabled, checked, selected, focused, scrollable, password; flag when the element cap is hit.
4. Ambiguity: `tap_text` refuses (with candidates) when several unrelated elements match equally well.
5. Refs by generation, not a 120 s timer.
6. Scroll progress: `scroll_until_visible` stops at the end of the list.
7. iOS `reset_app`: empty the app's data container.
8. Screenshot `scale` for token economy.

**Phase 2: device plane.** Helper APK (no idle failure, real gestures, Unicode typing), iOS without `idb`, perf numbers, longer video.

**Phase 3: replay and scale.** Session to Maestro flow, batch op, output-size budgets in CI.

## Not taken
Their local daemon trust model, `node:vm` scripts, install-from-URL, the JVM-free Maestro engine, and log-parsed "network capture".
