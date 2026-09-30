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
  `tap --ref e3` / `set-text --ref e2 --text ...`: never copy coordinates. Refs expire after
  120 s or any op that may change the screen (tap, type, key, set-text, launch, swipe, open-url, ...);
  read the tree again then.
  Use `screenshot --preview` and open the JPEG; keep the PNG for evidence.
- **Animations off on Android emulators** (`animations --off`) at the start of a session:
  `ui-tree` stops stalling on "UI not idle" and taps don't land mid-transition. Turn them back
  on (`--on`) before testing an animation or loader, and at the end of the session.
- **Time-sensitive states** (loaders, toasts, animations): use `burst` or `record`, which
  time the frames on the runner. Screenshots taken turn by turn arrive seconds late.
- **Repeat work goes in a Maestro flow.** Once a path works, run it with `run-flow` in one call.
- **Hand the tap/check loop to the `device-tester` sub-agent** (legacy runtime on a Claude
  model: call the Agent tool with `subagent_type: "device-tester"`). It runs on a cheaper model
  (`LOMA_DEVICE_TESTER_MODEL`, default `haiku`) and returns only PASS/FAIL/BLOCKED per scenario
  with the deciding tool output. Split the work like this:
  - **You** find and lease the device, install the build, plan the scenarios and write each
    assertion as something a tool can check (`wait-for` text, a `ui-tree` id, a Maestro flow).
  - **Give it** the full CLI prefix (`python3 tools/device.py --user-email <E> --auth-token <T>
    --scope <conversation-id>`), the device id, the app id, and the scenarios with assertions.
    It cannot see this conversation, so leave nothing implicit.
  - **You** judge every FAIL/BLOCKED (real bug, flaky step or setup issue), re-check anything
    that decides the verdict yourself, write the report and release the device.
  - Skip the hand-off for one or two quick checks, and on OpenCode/Codex models or isolated
    workers (no `device-tester` there); run the loop yourself instead.
- **State file.** Write the device id, installed build SHA, app id and anything you started
  (servers, tunnels) to `state.json` in the conversation work dir (the literal path in `[Conversation Work Dir: ...]`; never `$LOMA_CONVERSATION_DIR`, which can be unset). Read it first when resuming.

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
4. **Wake and clean**: `key --key wakeup` and `animations --off` (Android), then `app --action reset_app` only if the
   test needs a fresh state, then `logs --clear`.
5. **Launch configured**: `app --action launch --app-id PKG --extra endpoint=... --bool-extra test_mode=true`.
   On iOS add `--console` if the app logs with `print`, then read it with `logs --source console`.
   Use deep links (`open-url`) to reach a screen instead of tapping through menus.
6. **Drive by element, not coordinates**: `tap-text --match "Got it"`, `set-text --match "User ID" --text u1`,
   `wait-for --match "Welcome" --timeout 15`, `scroll-until-visible --match "Offers"`.
   When the element has no useful text (icons, empty fields), use its ref from
   `ui-tree --compact`: `tap --ref e7`. Use `tap --x --y` only for things not in the tree
   (Flutter canvases, games, some WebViews), taking coordinates from a screenshot.
7. **Collect evidence**: one screenshot at the key moment (every capture gets a new file name),
   `logs --filter <tag>`, plus any backend checks your team's skills describe.
8. **Make it repeatable**: write the scenario as a Maestro flow and run it with `run-flow`.
9. **Release the device** (`release`) when finished, including after failures.

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
| "Ref e3 is unknown or expired" | The screen changed or 120 s passed: `ui-tree --compact` again and use the new ref |

## Isolated-worker tool mapping

The steps above use `tools/device.py` spellings. In isolated runs use the `device.*` tools:

| CLI | Isolated tool |
|---|---|
| `open-url` | `device.input action=open_url` |
| `tap-text` / `set-text` / `clear-text` / `wait-for` / `scroll-until-visible` | `device.input action=tap_text|set_text|clear_text|wait_for|scroll_until_visible` |
| `app --action launch --extra K=V` | `device.app action=launch extras={...} bool_extras={...}` |
| `tap` / `type` / `swipe` / `key` | `device.input action=tap|type|swipe|key` (`tap ref=e3` works too) |
| `animations --off` | `device.input action=animations enabled=false` |
| `ui-tree --compact` | `device.observe what=ui_tree compact=true` |
| `screenshot` | `device.observe what=screenshot` |
| `logs --clear` / `logs --filter X` | `device.observe what=logs clear=true` / `filter=X` |
