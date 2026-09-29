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

## The loop

1. **Find a device**: `list`. If nothing is online, stop and tell the user exactly what
   to do ("wake the machine / start the runner / boot the emulator"). Do not retry in a loop.
2. **Lease it**: `lease --platform android|ios` (or a specific `device_id`). A lease lasts
   15 idle minutes and renews on every call. Another chat cannot use a leased device.
3. **Install the build**:
   - CI artifact: `install --repo OWNER/NAME --artifact-name NAME --pr N --app-id PKG`
     (or `--run-id`). The backend fetches it; the result includes the installed `head_sha`.
     Report that SHA, so reviewers know exactly what was tested.
   - Local file (legacy only): `install --file /path/app.apk --app-id PKG`.
   - Always pass `--app-id` so the old app is removed first (avoids signature mismatch).
4. **Clean state**: `app --action reset_app` (Android) or reinstall (iOS), then `logs --clear`.
5. **Launch and drive**: `app --action launch`, then prefer **deep links**
   (`open-url`) to reach a screen or fire a test event. Tapping through menus is slow
   and fragile.
6. **Observe with ui_tree, not pixels**: read `ui-tree`, find the element by `text` / `id` /
   `label`, tap its `center`. Assert on the text/ids that must be present after each step.
   Android coordinates are pixels; iOS are points. Always take coordinates from the
   latest ui_tree, never guess them.
7. **Collect evidence**: one screenshot at the key moment, `logs --filter <tag>` for
   the SDK/app log lines, plus any backend checks your team's skills describe.
8. **Make it repeatable**: once a scenario works interactively, write it as a Maestro flow
   and run it with `run-flow` / `device.run_flow`. The same YAML can be committed as a regression test.
9. **Release the device** (`release`) when finished, including after failures.

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
| "Runner is offline" / "No online devices" | Machine asleep or runner stopped; ask the user to wake it or start the runner |
| "leased by another session" | Another chat holds it; pick another device or ask the owner to release it in Integrations → Devices |
| "not in this runner's allowed_app_ids" | The runner owner restricted apps; use an allowed app id |
| "uiautomator could not capture the screen" | UI not idle (animation/video); wait a second and retry |
| iOS "needs idb" | iOS taps/ui_tree need `idb` on the runner machine; screenshots, install, launch and deep links still work |
| "Builds from this repository are not allowed" | An operator must add the repo to `LOMA_DEVICE_BUILD_REPOS` |
