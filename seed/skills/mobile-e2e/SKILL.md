---
name: mobile-e2e
description: End-to-end test a mobile app build on a real Android emulator or iOS simulator through Loma Devices (a Loma Device Runner on a machine the user enrolled). Use when asked to test, verify or reproduce something on a device/emulator/simulator, to check a PR build on mobile, or to attach on-device evidence to a PR.
user-invocable: false
---

# Mobile end-to-end testing with Loma Devices

Loma can drive Android emulators and iOS simulators that run on a machine the user
enrolled under **Integrations → Devices** (the "Loma Device Runner"). Devices are only
available while that machine is awake and the runner is running.

## Which interface to use

| Runtime | How to call it |
|---|---|
| Isolated worker (you see `device.*` tools) | `device.list`, `device.lease`, `device.install`, `device.app`, `device.input`, `device.observe`, `device.run_flow`, `device.release` |
| Legacy runtime (you have Bash) | `python3 tools/device.py --user-email <E> --auth-token <T> --scope <conversation-id> <command> ...` |

Always pass the **conversation id** as `--scope` in the legacy CLI so the lease belongs to
this chat. Screenshots from the CLI are saved to a PNG path you can open with Read.
In isolated workers you cannot view screenshots: they are shown to the user as evidence,
and you assert on `ui_tree`.

In isolated workers a failed call returns `{"ok": false, "device_error": "..."}`: read the
message and act on it (see Troubleshooting). A long call (install, a Maestro flow) returns
`"pending": true` after about 90 seconds while it keeps running; call the **same tool with the
same `device_id`** again to wait for its result. Other calls on that device report it busy
until then.

## Cost rules (read first)

Every device call is a model turn, and every turn re-sends the whole conversation. The
cheapest test is the one with the fewest calls, so:

- **One call per intent, not per key press.** Use `set-text` (not `type` + many `key delete`),
  `tap-text` (not `ui-tree` + `tap`), `wait-for` (not sleep + screenshot), and
  `scroll-until-visible` (not repeated swipes). These poll on the runner machine.
- **Configure by launch arguments, not by typing.** If the app reads its settings from intent
  extras (Android) or UserDefaults (iOS), pass them with `app --action launch --extra KEY=VALUE`.
  Typing a URL into a settings screen is the slowest, least reliable step in any test.
- **Read the tree small, act by ref.** `ui-tree --compact` prints one line per element with a
  ref (`e3 Button 'Save' @540,1800 *`); add `--filter` or `--clickable-only`. Then
  `tap --ref e3` / `set-text --ref e2 --text ...`: never copy coordinates. Refs stay valid until
  an op that may change the screen (tap, type, key, set-text, launch, swipe, open-url, ...).
- **Act with `--settle` instead of re-reading the tree.** `tap --ref e3 --settle` waits until the
  screen stops changing and returns `screen_after`: `added` / `removed` lines with refs for the new
  screen (unchanged elements keep their refs). Act on those refs directly; read `ui-tree` only when
  `settled` is false or you need the full screen. Needs runner 1.3.0+.
- **Read failures by `code`.** `dispatched: no` means the input never reached the device (safe to
  retry); `unknown` means check `ui-tree` first. `ambiguous` lists `details.candidates`: retry
  with `--nth N` or a ref. `ui_not_idle`: turn animations off. `policy_denied`: do not retry.
  Use `screenshot --preview` and open the JPEG; keep the PNG for evidence.
- **Animations off on Android emulators** (`animations --off`) at the start of a session:
  `ui-tree` stops stalling on "UI not idle" and taps don't land mid-transition. Turn them back
  on (`--on`) before testing an animation or loader, and at the end of the session.
- **Time-sensitive states** (loaders, toasts, animations): use `burst` or `record`, which
  time the frames on the runner. Screenshots taken turn by turn arrive seconds late.
- **Repeat work goes in a Maestro flow.** Once a path works, run it with `run-flow` in one call.
- **Keep device work out of long threads.** For a large matrix, run the device loop in a
  subagent that returns only pass/fail per scenario and screenshot paths.
- **State file.** Write the device id, installed build SHA, app id and anything you started
  (servers, tunnels) to `state.json` in the conversation work dir (the literal path in `[Conversation Work Dir: ...]`; never `$LOMA_CONVERSATION_DIR`, which can be unset). Read it first when resuming.

## The loop

1. **Find a device**: `list` shows running devices and the **templates** each runner can boot.
   If nothing is online, the runner owner already got a Loma notification: either lease with
   `--wait-online 300` (isolated: `wait_online_s=300`) or stop and tell the user exactly what to do
   ("wake the machine / start the runner"). Do not retry in a loop.
2. **Lease it**: `lease --platform android|ios` (or a specific `device_id`). If no device is running,
   an online runner boots one from a template. For reproducible tests prefer
   `lease --template NAME --clean` (isolated: `template=..., clean=true`): a fresh device from the
   template's clean state, shut down again on release. Booting takes 1-3 minutes (`pending` in
   isolated runs: repeat the call). A lease lasts 15 idle minutes and renews on every call.
3. **Install the build**:
   - CI artifact: `install --repo OWNER/NAME --artifact-name NAME --pr N --app-id PKG --wait 1200`.
     `--wait` (max 1200 s) makes the backend wait for the CI run (no model turns); `--dispatch-workflow FILE.yml`
     starts it if the PR has no run. Only workflows an admin allowed can be dispatched (Integrations > Devices >
     Build sources, or `LOMA_DEVICE_BUILD_WORKFLOWS`). Report the returned `head_sha`.
   - The runner updates in place (keeps app data), reinstalls only on a signature mismatch,
     and skips an identical build. `--force` reinstalls anyway.
   - Pre-grant permissions in the same call: `--grant-appop SCHEDULE_EXACT_ALARM` (Android),
     `--grant-privacy photos` (iOS). Runtime permissions are granted on Android installs.
   - Local file (legacy only): `install --file /path/app.apk --app-id PKG`.
4. **Wake, clean and set conditions**: `key --key wakeup` and `animations --off` (Android), then
   `app --action reset_app` only if the test needs a fresh state (a clean lease already is), then `logs --clear`.
   Set test conditions in one `configure` call, then relaunch the app: `--locale ar-SA --app-id PKG` (RTL),
   `--dark-mode on`, `--font-scale 1.3`, `--location 25.2,55.27`, `--grant/--revoke PERMISSION --app-id PKG`,
   and on Android `--timezone Asia/Dubai` and `--clock-offset 86400` (move a day forward for streaks,
   milestones, expiry and frequency caps). Release restores all of it.
5. **Launch configured**: `app --action launch --app-id PKG --extra endpoint=... --bool-extra test_mode=true`.
   On iOS add `--console` if the app logs with `print`, then read it with `logs --source console`.
   Use deep links (`open-url`) to reach a screen instead of tapping through menus.
6. **Drive by element, not coordinates**: `tap-text --match "Got it"`, `set-text --match "User ID" --text u1`,
   `wait-for --match "Welcome" --timeout 15`, `scroll-until-visible --match "Offers"`.
   When the element has no useful text (icons, empty fields), use its ref from
   `ui-tree --compact`: `tap --ref e7`. Use `tap --x --y` only for things not in the tree
   (Flutter canvases, games, some WebViews), taking coordinates from a screenshot.
7. **Verify and collect evidence** (text for you, media for the user):
   - `ui-tree` for what is on screen; `visual-check --expect "..."` (isolated: `observe what=visual`)
     for what the tree cannot see: clipping, overlap, RTL layout, WebView/HTML templates, Lottie. It is
     supporting evidence only; never fail a test on it alone.
   - Network (Android): start with `configure capture_network=true` (CLI: `netcap --action start`) before
     launching, then `observe what=network filter=/sdk/` shows the SDK calls grouped by endpoint.
     HTTPS needs a debug build that trusts the capture CA; `tls_failures` means it does not.
   - SDK analytics: `sdk-events-check --product-id P --user-id U --flow-id F` (isolated:
     `observe what=sdk_events`) returns the user's events, campaign triggers and flow shows/clicks, with a
     triggered/shown/clicked verdict. Only test products an admin allowlisted work. Analytics lag up to
     a minute: check again before calling it a failure.
   - One screenshot at the key moment, or `record --duration 8` / `burst` for motion (2 recordings per run).
   A strong result says: rendered (ui_tree/visual) + SDK call made (network) + recorded (sdk_events).
8. **Make it repeatable**: write the scenario as a Maestro flow and run it with `run-flow`.
9. **Release the device** (`release`) when finished, including after failures.

## A person can take over

The device owner (or whoever holds the session) can open **Live** on the device in
Integrations → Devices and **Take over** to get past a login/OTP or show you something. While they
hold it, your calls fail with "... took over this device". Wait about a minute and retry the same
call; do not switch devices mid-test. Your refs are stale afterwards: read `ui_tree` again.

## Reproducing a reported SDK bug

Used by the `sdk-bug-intake` flow once a report is complete:
1. Lease a **clean** template device on the reported platform (closest OS version you have).
2. Install the SDK example/demo app build for the reported SDK version from CI (never a customer build).
3. `configure` the reported conditions (locale, dark mode, font scale, permissions; time on Android)
   and `capture_network=true` on Android.
4. Recreate the campaign type in an allowlisted **test product**, identify as a fresh test user, follow
   the reported steps.
5. Verify as in step 7 above, record a short clip of the result, release.
6. Report "reproduced / not reproduced on <device, OS, SDK version>" with the evidence. Not reproduced is
   a valid result: it points at client-specific factors, say so.

## Common blockers on a fresh install

| Blocker | Fix in one call |
|---|---|
| Screen off / lock screen | `key --key wakeup` |
| Runtime permission dialog (Android) | Already granted by `install` (`-g`); special ones need `--grant-appop` |
| Notification / photos prompt (iOS) | `--grant-privacy` where supported; otherwise `tap-text --match "Allow"` |
| Keyboard onboarding sheet | `tap-text --match "Got it"` or `key --key back`, then `set-text` |
| App settings screen on launch | Pass the settings as launch extras instead |
| Live in-app campaign or promo blocking the screen | Use a test user or test project with none live; else `tap-text` its close button |

## Reporting

Post a short result: device (name, OS), build (repo, PR, head SHA), steps, each
assertion with pass/fail, the key log lines, and the screenshot. If something could not
be verified on the device, say so plainly; never claim a pass you did not observe.

## Maestro flows (run_flow)

```yaml
appId: com.example.app
---
- launchApp
- openLink: myapp://track?event=test_event
- assertVisible: "Welcome back!"
- tapOn: "Got it"
- assertNotVisible: "Welcome back!"
- takeScreenshot: after_dismiss
```

By default runners block `runScript`, `evalScript`, `runFlow`, `addMedia`,
`startRecording` and inline JavaScript (`${...}`), because those can run code with
network access or touch files on the user's machine. If a flow is rejected for this
reason, rewrite it without them; don't ask the user to weaken the policy unless they
explicitly want that.

## Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| "No devices are registered for you" | The user has not enrolled a machine: point them to Integrations → Devices |
| "Runner is offline" / "No online … devices" | Machine asleep or runner stopped; ask the user to wake it or start the runner |
| "leased by another session" | Another chat holds it; pick another device or ask the owner to release it in Integrations → Devices |
| "not in this runner's allowed_app_ids" | The runner owner restricted apps; use an allowed app id |
| "uiautomator could not capture the screen" | UI not idle (animation/video); wait a second and retry |
| iOS "needs idb" | iOS taps, swipes, typing, keys and ui_tree need `idb` on the runner machine; screenshots, install, launch and deep links still work |
| `"pending": true` | Still running on the device; repeat the same tool call on the same device to wait |
| "Timed out on the runner" | The device did not finish in time; check `ui_tree`/`logs`, then retry once |
| "Builds from this repository are not allowed" | An admin adds `owner/name` under Integrations → Devices → Build sources (or the `LOMA_DEVICE_BUILD_REPOS` env var) |
| "Ref e3 is unknown or expired" (`ref_stale`) | The screen changed: `ui-tree --compact` again, or act with `--settle` to get the new refs |
| "... took over this device" | A person is driving it from the dashboard; wait a minute and retry the same call |
| "Runner too old for ..." | Runners from 1.2.0 update themselves; older ones need one manual `setup` by their owner |
| "No device template matches" | The runner owner has not defined that template; `list` shows the available ones |
| "product_id is not allowed" | Only test products in `LOMA_DEVICE_ANALYTICS_PRODUCTS` can be checked; use one of those |

## Isolated-worker tool mapping

The steps above use `tools/device.py` spellings. In isolated runs use the `device.*` tools:

| CLI | Isolated tool |
|---|---|
| `open-url` | `device.input action=open_url` |
| `tap-text` / `set-text` / `clear-text` / `wait-for` / `scroll-until-visible` | `device.input action=tap_text|set_text|clear_text|wait_for|scroll_until_visible` |
| `app --action launch --extra K=V` | `device.app action=launch extras={...} bool_extras={...}` |
| `tap` / `type` / `swipe` / `key` | `device.input action=tap|type|swipe|key` (`tap ref=e3` works too) |
| `--settle` / `--settle-ms MS` / `--nth N` | `settle=true` / `settle_ms=MS` / `nth=N` on the same `device.input` call |
| `animations --off` | `device.input action=animations enabled=false` |
| `ui-tree --compact` | `device.observe what=ui_tree compact=true` |
| `screenshot` | `device.observe what=screenshot` |
| `logs --clear` / `logs --filter X` | `device.observe what=logs clear=true` / `filter=X` |
| `record --duration 8` / `burst --count 6` | `device.observe what=record duration_s=8` / `what=burst count=6` |
| `configure ...` / `netcap --action start` | `device.configure ...` / `device.configure capture_network=true` |
| `netcap --action read` / `sdk-events-check` / `visual-check` | `device.observe what=network` / `what=sdk_events` / `what=visual` |
| `lease --template T --clean --wait-online 300` | `device.lease template=T clean=true wait_online_s=300` |
