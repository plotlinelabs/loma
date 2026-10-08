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
| Isolated worker (you see `device.*` tools) | `device.list`, `device.lease`, `device.install`, `device.app`, `device.input`, `device.observe`, `device.scenario`, `device.suite`, `device.run_flow`, `device.release` |
| Legacy runtime (you have Bash) | `python3 tools/device.py --user-email <E> --auth-token <T> --scope <conversation-id> <command> ...` |

**Never write the auth token into a script or file** (no `dev.sh` wrappers with the token inside).
It expires after an hour, so a saved script breaks on the next run anyway, and the conversation
work dir is shared. Pass it per command, or as `LOMA_USER_EMAIL=... LOMA_AUTH_TOKEN=... python3 tools/device.py ...`.

Always pass the **conversation id** as `--scope` in the legacy CLI so the lease belongs to
this chat. Screenshots from the CLI are saved to a PNG path you can open with Read.
In isolated workers you cannot view screenshots: they are shown to the user as evidence,
and you assert on `ui_tree`.

In isolated workers a failed call returns `{"ok": false, "device_error": "..."}`: read the
message and act on it (see Troubleshooting). A long call (install, a Maestro flow) returns
`"pending": true` after about 90 seconds while it keeps running; call the **same tool with the
same `device_id`** again to wait for its result. Other calls on that device report it busy
until then.

## Before you touch a device (do this first)

Most failed device sessions fail on the environment, not the app. Check these before building,
installing or writing specs, and stop with a clear "blocked" message if one is missing:

1. **The backend/config the test needs is live.** Run a preflight-only scenario (no `app_id`, no steps):
   `{duration_s: 1, preflight: [{url: ".../sdk/init", contains: ['"flag":true']}], expect: {}}`.
   The runner checks it without touching the device. If it returns `blocked`, fix or report the
   environment now; do not install anything.
2. **The build can be fetched.** The repo must be in the build-source allowlist (Integrations > Devices >
   Build sources). Downloading and installing builds by hand with `--file` is a workaround, not the flow.
3. **Every app under test can be configured at launch** (user id, locale, endpoint, cache reset) through
   launch extras / UserDefaults. If one platform's test app has no such hook, say so before you start:
   that platform's fresh-state or locale cases cannot run, and that is a gap in the app, not a test result.
4. **The device is healthy.** `lease` returns `health`. If the check fails, the lease restarts the device once
   by itself (`recovery` in the result). If `health.ok` is still false, lease another device instead of running
   tests that will time out. Never restart an emulator by hand or ask the user to, before trying `recover`.

## Cost rules (read first)

Every device call is a model turn, and every turn re-sends the whole conversation. The
cheapest test is the one with the fewest calls, so:

- **One test case = one `scenario` call**, whatever is being tested (a login flow, a deep link,
  a purchase, a loader, a crash check). Do NOT chain tap + wait + logs + screenshot calls by hand.
  Write a spec for the case and run it once. The spec is generic: use the app id, element texts
  and log tags of the app under test (the names below are placeholders, never copy them).
- **Probe budget: at most 2 exploratory calls per platform** (a `ui-tree --compact`, one throwaway
  scenario) to learn element texts, ids and log tags. Then write the suite. Twenty probe specs for eight
  real cases is the failure mode to avoid; delete probe files once the suite exists.
- **No secrets in spec files.** Write `${API_KEY}` in the spec and pass `--var API_KEY=...` (or export
  `E2E_API_KEY`); only `--var` values and `E2E_*` variables are substituted. Spec files get committed and
  the conversation work dir is shared.
  ```yaml
  app_id: com.example.app          # optional: stopped, then launched at t0
  duration_s: 30                   # upper bound, 1-60 s
  end_after_steps: true            # return as soon as the steps are done
  stop_on_fail: true               # a failed step ends the case
  log_tags: [MyTag, OtherTag]      # optional: log lines containing any of these
  steps:                           # run in order; after_ms = pause after the previous step
    - {action: tap_text, match: "Sign in"}
    - {action: set_text, match: "Email", text: "a@b.co"}
    - {action: wait_for, match: "Welcome", timeout_s: 15}
    - {action: screenshot, name: home}       # evidence for the user / the PR
  expect:                          # the runner decides pass/fail
    app_running: true              # false at the end = the app died
    logs: [{match: "login_ok", by_ms: 8000}, {match: "ERROR", max: 0}]
  ```
  `scenario --device-id ID --spec case.yaml` (isolated: `device.scenario` with the same fields).
  Read `verdict` and `failed` first; they are usually all you need. Each step also reports
  `ran_ms` / `took_ms` / `ok`, and `app_running` tells you whether the app survived.
- **Every case has an `expect`.** The runner decides pass/fail; you report its `verdict` and `failed`
  reasons. A spec without `expect` returns raw data that you would have to judge yourself with scripts,
  which is slower, costs more and is how wrong passes get reported. If a rule you need does not exist,
  say so in the report instead of parsing logs or frames yourself.
- **Find elements by text or id, not by coordinates.** Use `tap_text`, `wait_for`, `set_text` and
  `scroll_until_visible` steps. Keep `tap` with `x`/`y` for things that are not in the UI tree.
  Use `after_ms` steps for ordinary flows; use `at_ms` only when the exact time is what you test.

  | Need | Add to the spec |
  |---|---|
  | Steps: `tap`, `tap_text`, `wait_for` (`gone: true`), `set_text`, `clear_text`, `scroll_until_visible`, `swipe`, `type`, `key`, `open_url`, `screenshot`, `launch_app`, `stop_app` | `steps` (max 40) |
  | Exact timing (animation, loader, toast, flicker, launch time) | `at_ms` on the steps instead of `after_ms`, no `end_after_steps`, and `sample_ms: 250` |
  | When did the screen change, and where | `sample_ms` returns `frames.changes` (periods with `from_ms`, `to_ms`, `box`) and `last_change_ms`; no images reach you |
  | Watch one area, or ignore a blinking cursor / clock | `sample_region: [x1, y1, x2, y2]`, `sample_min_change: 0.02` |
  | "Settled within N ms" | `expect: {settled_by_ms: N}` |
  | Log checks | `expect.logs` rules (`min`, `max`, `by_ms`, `after_ms`) and `expect.log_order: [A, B]` |
  | A number in the logs (a height, a count, a duration) | On a log rule: `number_after: "height="` (the text right before the number), then `value_min` / `value_max` (every value), `after_reaching: 100` (only check values from the first one at or above 100), `last_min` / `last_max`. Example, "never drops below 60 after reaching 100": `{match: "slot", number_after: "height=", after_reaching: 100, value_min: 60}`. `logs.values` reports count / min / max / first / last |
  | Timing must be real | `expect: {max_drift_ms: 300}` fails the case when an `at_ms` step ran later than that. Every `at_ms` step reports `drift_ms`, and the result has `max_drift_ms`. iOS swipes take about 1.2 s each, so space `at_ms` steps after a swipe by at least 1500 ms |
  | Is the test environment ready (backend up, a config flag on) | `preflight: [{url: "https://api.example.com/config", contains: ['"flag":true']}]` (also `method`, `headers`, `body`, `status`). The runner checks it before touching the device; a failed check returns `verdict: blocked`, which is an environment problem, not a test failure. Report it as blocked and stop, do not retry the test |
  | Background / foreground, kill and relaunch | `key: home`, `stop_app`, `launch_app` steps. `launch_app` takes `activity`, `extras` and `bool_extras`. With `restart` unset: Android brings the running app back with the new intent; iOS restarts it when extras are given (a running iOS app ignores new launch arguments) and otherwise brings it to the front. `restart: true` / `false` forces either. A step result with `ignored_extras` means iOS kept the old arguments |
  | iOS logs across a relaunch | Launch with `console: true` at t0; later `launch_app` steps for the same app keep capturing into the same console log (runner >= 1.4.0) |
  | "Is the right image / logo / locale on screen" | Never judge screenshots by eye. Add `fingerprint: true` (optional `region: [x1, y1, x2, y2]`) to a named `screenshot` step; the step returns a `fingerprint`. From a run you verified once, save it in the suite as `expect: {screens: [{shot: logo, like: "<fingerprint>"}]}` (`unlike:` a known-bad one, e.g. the English logo when Hindi is expected; `max_diff` default 0.1) |
  | Video evidence | `record: true` (`video_offset_ms` is where t0 sits in the video) |

  On Android, turn animations **on** for timing or animation cases, and off for ordinary flows
  (put this in the suite `setup`, below). If the video cannot be delivered you still get the rest (`video_error`).
  A recording over 16 MB is re-encoded smaller on the runner (ffmpeg if installed, else macOS
  `avconvert`), so long iOS recordings arrive at a lower resolution instead of being lost.
- **A matrix or a regression run = one `suite` call.** Put the cases in one file and run
  `suite --device-id ID --spec suite.yaml` (isolated: `device.suite`). You get one table
  (case, verdict, first reason), a JUnit XML file, and details only for the cases that did not pass.
  ```yaml
  defaults:                        # fields every case shares
    app_id: com.example.app
    log_tags: [MyTag]
    preflight: [{url: "https://api.example.com/config", headers: {X-Key: "${API_KEY}"}, contains: ['"flag":true']}]
    expect: {app_running: true}    # a case's expect is merged over this one
  platform_defaults:               # per-platform overrides (the iOS bundle id often differs)
    ios: {app_id: com.example.ios, console: true, log_source: console}
  setup:                           # once, before the cases (replaces the manual wake / animations / clear-logs calls)
    - {action: key, key: wakeup}
    - {action: animations, enabled: false}   # Android only; leave it out for an iOS-only suite
    - {action: logs, clear: true}
  teardown: [{action: animations, enabled: true}]
  reset: reset_app                 # fresh app state before each case (Android pm clear; iOS reinstall + keychain reset)
  retries: 1                       # re-run a failed case once; a pass on the retry is reported as flaky
  cases:                           # each case is a scenario spec with a name; every case needs expect
    - {name: login, duration_s: 30, end_after_steps: true, steps: [...], expect: {logs: [...]}}
    - {name: deep_link, duration_s: 20, end_after_steps: true, steps: [...]}
    - {name: back_button, only: [android], duration_s: 10, steps: [...], expect: {...}}
  ```
  Run it on every platform at once with repeated `--device-id` (2-4 devices; isolated: `device_ids`).
  The result adds a per-device table, and cases with `only:` are skipped where they do not apply.
  Verdicts: `pass`, `flaky` (passed only on a retry), `fail`, `blocked` (a preflight or health check
  failed), `error` (the case could not run). Cases that never started are listed in `not_run` with
  `not_run_reason` (stop_on_fail, `device_slow`, a failed setup step, the suite deadline). The suite
  verdict is `pass` only when every case passed; otherwise `fail`, `error`, `blocked`, then `flaky`.
  At most 20 cases and 900 s of `duration_s` in total (retries included), so one platform never needs two
  suite files. Videos are kept for cases that did not pass (`keep_video: all` keeps every one).
  Paste the `table` into the PR or the report. **The suite file is the replay format:** commit it next to
  the test app (for example `e2e/device/suite.yaml`) so the next PR replays it without exploring.
- **Logs: tags + cursor, never re-read.** `logs --tag A --tag B` keeps lines with any tag and
  returns `counts`. Every logs result has a `cursor`; pass `--since <cursor>` next time to get
  only newer lines instead of `--clear` + re-reading thousands of lines.

- **One call per intent, not per key press.** Use `set-text` (not `type` + many `key delete`),
  `tap-text` (not `ui-tree` + `tap`), `wait-for` (not sleep + screenshot), and
  `scroll-until-visible` (not repeated swipes). These poll on the runner machine.
- **Configure by launch arguments, not by typing.** If the app reads its settings from intent
  extras (Android) or UserDefaults (iOS), pass them with `app --action launch --extra KEY=VALUE`.
  Typing a URL into a settings screen is the slowest, least reliable step in any test.
- **Read the tree small, act by ref.** `ui-tree --compact` prints one line per element with a
  ref (`e3 Button 'Save' @540,1800 *`); add `--filter` or `--clickable-only`. Then
  `tap --ref e3` / `set-text --ref e2 --text ...`: never copy coordinates. Refs expire after
  120 s or any op that may change the screen (tap, type, key, set-text, launch, swipe, open-url, ...);
  read the tree again then.
  Use `screenshot --preview` and open the JPEG; keep the PNG for evidence.
- **Animations off on Android emulators** for ordinary flows (suite `setup`): `ui-tree` stops
  stalling on "UI not idle" and taps don't land mid-transition. Turn them back on for animation or
  loader cases, and in `teardown`.
- **Time-sensitive states** (anything that appears or changes within a second or two): use
  `scenario` with `at_ms` steps. `burst` and `record` still exist for a single capture, but
  cannot run steps while capturing.
- **Never build your own harness** (wrapper scripts, log parsers, frame-diff scripts, contact sheets): if
  `scenario` cannot express a case, say what is missing in your report.
- **Clean up at the end, every time.** `release --all` frees every device this conversation holds
  (isolated runs release them automatically when the run ends). `cleanup --keep <evidence files>`
  deletes the other screenshots, frames, videos and JUnit files under `device/`. Update `state.json`
  so it does not claim devices are still leased.
- **Keep device work out of long threads.** For a large matrix, run the device loop in a
  subagent that returns only pass/fail per scenario and file paths (one table, no raw logs).
- **State file.** Write the device id, installed build SHA, app id and anything you started
  (servers, tunnels, device_mock sessions) to `state.json` in the conversation work dir (the literal path in `[Conversation Work Dir: ...]`; never `$LOMA_CONVERSATION_DIR`, which can be unset). Read it first when resuming.

## The loop

1. **Find a device**: `list`. If nothing is online, stop and tell the user exactly what
   to do ("wake the machine / start the runner / boot the emulator"). Do not retry in a loop.
2. **Lease it**: `lease --platform android|ios` (or a specific `device_id`). A lease lasts
   15 idle minutes and renews on every call. Another chat cannot use a leased device.
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
4. **Wake and clean**: in a suite, the `setup` block does this in the same call. For one-off work:
   `key --key wakeup`, `animations --off` (Android), `app --action reset_app` only if the test needs a
   fresh state (works on iOS too: reinstall of the cached build or a data wipe, plus a keychain reset),
   then `logs --clear`.
5. **Launch configured**: `app --action launch --app-id PKG --extra endpoint=... --bool-extra test_mode=true`.
   On iOS add `--console` if the app logs with `print` (Flutter `debugPrint`, Swift `print`): without it
   those lines are not in the simulator log at all. Read them with `logs --source console`
   (in a scenario: `console: true` with `log_source: console`).
   Use deep links (`open-url`) to reach a screen instead of tapping through menus.
6. **Drive by element, not coordinates**: `tap-text --match "Got it"`, `set-text --match "User ID" --text u1`,
   `wait-for --match "Welcome" --timeout 15`, `scroll-until-visible --match "Offers"`.
   When the element has no useful text (icons, empty fields), use its ref from
   `ui-tree --compact`: `tap --ref e7`. Use `tap --x --y` only for things not in the tree
   (Flutter canvases, games, some WebViews), taking coordinates from a screenshot.
   **Flutter:** only widgets with Semantics show up (often a merged label like "Title\nSubtitle",
   so `--exact` fails). In test harness apps you own, wrap tappable widgets in
   `Semantics(identifier: 'submit_button', child: ...)` and match `--by id` (the iOS
   accessibilityIdentifier; recent Flutter versions also expose it as the Android resource-id).
   If `ui-tree` does not show it, give the widget a unique `Semantics(label: ...)` instead.
   Do this before the first device run, not after the third failed `tap-text`.
7. **Collect evidence**: one screenshot at the key moment (every capture gets a new file name),
   `logs --filter <tag>`, plus any backend checks your team's skills describe.
8. **Make it repeatable**: the suite file you ran is the regression test; commit it (no secrets in it).
   Use `run-flow` only for Maestro flows that already exist in the repo.
9. **Release and clean up** (`release --all`, `cleanup --keep ...`) when finished, including after failures.

## Control the backend response: device_mock

Use it when the case needs a Plotline API response you cannot get on demand: a field the server
does not send yet, an empty or failing `/sdk/init`, slow or broken images, an outage. Do not
start your own tunnel or proxy. Same auth flags as `tools/device.py`
(`D="python3 tools/device_mock.py --user-email <E> --auth-token <T> --scope <conversation-id>"`).

1. **Create**: `$D create --label <case>` returns `session_id` and `base_url`. `$D presets` lists the
   built-in scenarios (`passthrough`, `no_flows`, `slow_images`, `failing_images`, `init_error`, ...).
2. **Pick the scenario**: `$D set-scenario --session-id ID --preset slow_images`, optionally with
   `--patch-file p.json` (RFC 7396 merge patch on the `/init` body) or `--scenario-file s.json`
   (see `docs/device-mock.md`). Keep feature payloads in those files, not in presets.
3. **Point the app at it**: launch with the test app's API endpoint extra set to `base_url`
   (`app --action launch --app-id PKG --extra <endpoint-key>=<base_url>`). Start with `passthrough`
   and check the log shows `/sdk/init` before trusting any other scenario.
4. **Switch mid-session**: `set-scenario` again; the same `base_url` serves the new scenario from the
   next request. Relaunch the app if it only calls `/init` at start.
5. **Prove what the app got**: `$D log --session-id ID --path /sdk/init --limit 3`; `scenario`,
   `scenario_version` and `served` must match the case before you screenshot it. Filter with
   `--status 4xx`, `--applied asset_rule`, `--since <latest_at>` instead of reading the whole log.
6. **Delete** when done, including after failures: `$D delete --session-id ID`. Note the
   `session_id` in `state.json`. Never paste `base_url` publicly: it is a bearer URL until it expires.

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

Post a short result: device (name, OS), build (repo, PR, head SHA), the suite `table` (or the
matrix table), the key log lines, and the evidence files. Every case that did not run must be
in the report with its reason (`blocked`, `not run: device_slow`, "no cache-reset hook in the
Flutter app"), so a half-finished matrix is visible as such. Report `flaky` cases as flaky, not as
passes. If something could not be verified on the device, say so plainly; never claim a pass you
did not observe.

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
| "Runner is offline" / "No online … devices" | Machine asleep or runner stopped; ask the user to wake it or start the runner. Runner >= 1.2.0 keeps a Mac awake while devices are in use (`keep_awake` policy); a closed lid on battery still sleeps |
| "Runner too old for scenario" / "logs with tags" | Ask the user to update the runner: download the new `loma_device_runner.py` from Integrations → Devices and run `python3 loma_device_runner.py setup` |
| "Runner too old for scenario with preflight / expect.max_drift_ms / number_after / launch_app options" | Those need runner >= 1.3.0: same update as above. Everything else in `scenario` still works on 1.2.0 |
| "Runner too old for health / scenario with expect.screens / screenshot fingerprint" | Those need runner >= 1.4.0 (also iOS `reset_app` and iOS console across relaunch): same update as above. A suite on an older runner skips the health check |
| `health.ok: false` / `not run: device_slow` | The emulator is too slow (screenshots over 5 s, UI tree over 10 s) even after the automatic restart. If `health.hint` says the machine is overloaded, a restart will not help: lease another device or ask the user to close other emulators |
| "Device is not connected to this runner … the runner is restarting it" / "Device is restarting" / `state: recovering` | The emulator/simulator crashed or was closed, and runner >= 1.4.0 is restarting it (cold boot, same `device_id`). Run `recover --device-id ID` (isolated: `device.lease device_id=ID recover=true`) to wait for it, then re-launch the app: it is installed but not running. Do not re-install. A suite does this by itself and re-runs the case (`device_restarted: true`) |
| `state: down` in `list`, or "The last restart failed: …" | The automatic restart failed (or the device was idle for over 30 min, so it was not restarted). Run `recover` once. If it fails again, the error has the emulator log tail (e.g. low disk, no hypervisor): tell the user, and lease another device |
| "would cut off other devices" / "would disturb other simulators" | Restarting adb or CoreSimulatorService needs every other device on that runner to be idle; retry when your other device calls are done |
| Step result `ignored_extras: true` (iOS) | The app was running and `restart: false`, so iOS kept its old launch arguments. Leave `restart` unset or set it to true |
| `verdict: blocked` | A `preflight` check failed: the test environment is wrong (backend down, config flag off). Fix the environment or tell the user; the app was not tested |
| "host is on a private or local network" | Preflight only reaches public hosts by default. The runner owner can set `"preflight": "any"` in the runner policy |
| "leased by another session" | Another chat holds it; pick another device or ask the owner to release it in Integrations → Devices |
| "not in this runner's allowed_app_ids" | The runner owner restricted apps; use an allowed app id |
| "uiautomator could not capture the screen" | UI not idle (animation/video); wait a second and retry |
| iOS "needs idb" | iOS taps, swipes, typing, keys and ui_tree need `idb` on the runner machine; screenshots, install, launch and deep links still work |
| `"pending": true` | Still running on the device; repeat the same tool call on the same device to wait |
| "Timed out on the runner" | The device did not finish in time; check `ui_tree`/`logs`, then retry once |
| "Builds from this repository are not allowed" | An admin adds `owner/name` under Integrations → Devices → Build sources (or the `LOMA_DEVICE_BUILD_REPOS` env var) |
| "Ref e3 is unknown or expired" | The screen changed or 120 s passed: `ui-tree --compact` again and use the new ref |

## Isolated-worker tool mapping

The steps above use `tools/device.py` spellings. In isolated runs use the `device.*` tools:

| CLI | Isolated tool |
|---|---|
| `open-url` | `device.input action=open_url` |
| `tap-text` / `set-text` / `clear-text` / `wait-for` / `scroll-until-visible` | `device.input action=tap_text|set_text|clear_text|wait_for|scroll_until_visible` |
| `app --action launch --extra K=V` | `device.app action=launch extras={...} bool_extras={...}` |
| `tap` / `type` / `swipe` / `key` | `device.input action=tap|type|swipe|key` (`tap ref=e3` works too) |
| `scenario --spec case.yaml` | `device.scenario` with the spec fields as arguments (the video is delivered to the user) |
| `suite --spec suite.yaml` | `device.suite` with `cases`, `defaults`, `platform_defaults`, `setup`, `teardown`, `reset`, `retries`, `stop_on_fail`, `keep_video` |
| `suite --device-id A --device-id B` | `device.suite device_ids=[A, B]` |
| `release --all` | `device.release all=true` (also automatic when an isolated run ends) |
| `recover --device-id ID [--cold]` | `device.lease device_id=ID recover=true [cold=true]` (restart a crashed / closed / hung device and wait) |
| `logs --tag X --since C` | `device.observe what=logs tags=[X] since=C` |
| `animations --off` | `device.input action=animations enabled=false` |
| `ui-tree --compact` | `device.observe what=ui_tree compact=true` |
| `screenshot` | `device.observe what=screenshot` |
| `logs --clear` / `logs --filter X` | `device.observe what=logs clear=true` / `filter=X` |
