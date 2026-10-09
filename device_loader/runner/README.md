# Loma Device Runner

Lets a Loma agent drive the Android emulators and iOS simulators on a machine you
control (your laptop, a Mac mini, a KVM Linux box). The agent can install a PR
build, launch it, open deep links, tap/type/swipe, read the UI tree, take
screenshots, read logs and run Maestro flows.

The runner makes **one outbound WebSocket** to Loma. It never listens on a port,
so there is no tunnel, DNS or firewall setup, and it works behind NAT/VPN.
When the machine sleeps or the runner stops, Loma shows the devices as offline
and agent calls fail fast with a clear message.

## Setup

In Loma, open **Integrations → Devices → Get setup commands** and run the two lines it
shows in a terminal on the machine:

```bash
curl -fsSL -o loma_device_runner.py https://<your-loma>/device-runner/download
python3 loma_device_runner.py setup --server https://<your-loma> --token <token> --name 'Work Mac'
```

`setup` does everything else:

1. Copies the runner and its dependencies (aiohttp, PyYAML) into `~/.loma-device-runner`
   (a private virtualenv, so your system Python is untouched).
2. Enrolls the machine with the one-time token. If Loma is behind Cloudflare Access and
   `cloudflared` is installed, it detects this and uses your `cloudflared` login.
3. On macOS with Xcode, installs `idb` for iOS taps, typing and the UI tree: the client
   into the private virtualenv, `idb_companion` from Meta's Homebrew tap (trusting only that
   one formula). Best effort; add `--skip-ios-tools` to skip it.
4. Checks tooling and lists usable devices (`doctor`), with a fix for anything missing.
5. Starts a login service (launchd on macOS, systemd `--user` on Linux) so the runner is
   online whenever the machine is. Add `--foreground` to run it in the terminal instead.

Then boot an emulator or simulator; it shows up in Loma within about 15 seconds.

| Task | Command |
|---|---|
| Upgrade / restart (download the new file first) | `python3 loma_device_runner.py setup` |
| Check tooling and devices | `~/.loma-device-runner/venv/bin/python3 ~/.loma-device-runner/loma_device_runner.py doctor` |
| Remove from this machine | `python3 ~/.loma-device-runner/loma_device_runner.py uninstall` |
| Logs | macOS: `~/.loma-device-runner/runner.log`, Linux: `journalctl --user -u loma-device-runner -f` |

Device tooling (install what you need):

- **Android:** Android Studio + an emulator (arm64 image on Apple Silicon). `adb` is found in
  the default SDK location (or `$ANDROID_HOME`) without PATH changes.
- **iOS:** Xcode + a booted simulator. `setup` installs `idb` (needs Homebrew).
- **Flows (optional):** Maestro, which needs Java 17:
  `brew install openjdk@17 && curl -fsSL "https://get.maestro.mobile.dev" | bash`, then re-run `setup`.

The service keeps the `PATH` of the shell you ran `setup` from, plus the Android SDK,
Maestro, Homebrew and the runner's own `idb`. On Linux, run `loginctl enable-linger $USER` if the
runner should stay up while you are logged out.

## What the agent can and cannot do

| Allowed (fixed list) | Never possible |
|---|---|
| install (build fetched by Loma, checksum-verified), uninstall, launch, stop, reset_app | Run shell commands on your machine |
| open_url (deep links), tap, swipe, type, key | Read or write files on your machine |
| screenshot, ui_tree, logs (tag filters, since-cursor) | See physical devices (unless you opt in) |
| scenario: the same inputs as one test case (in order, or at fixed times), with optional video, screenshots, screen-change sampling, logs and pass/fail rules | |
| scenario preflight: up to 4 HTTP GET/POST checks before a test, to public hosts only by default. The agent gets the status and a yes/no per expected text, never the response body | Read the response of an HTTP request made from your machine |
| run_flow (Maestro YAML, screened) | Run Maestro JavaScript (unless you opt in) |
| recover: restart a crashed / closed / hung emulator or simulator it has seen before (see `auto_recover`) | Restart physical devices, or start emulators it never saw running |
| boot / shutdown: start a device from a template **you** listed in `templates`, and shut down only devices it booted | Boot an arbitrary AVD / simulator, or pass emulator flags |
| installed: list the user apps on a device and the build checksum of the ones it installed | List system apps or read app data |

## Policy (`~/.loma-device-runner/config.json`)

```json
"policy": {
  "allow_physical_devices": false,
  "allowed_app_ids": ["com.example.demo"],
  "allow_maestro_scripts": false,
  "keep_awake": true,
  "preflight": "public",
  "auto_recover": true,
  "emulator_args": [],
  "net_probe_host": "connectivitycheck.gstatic.com",
  "dns_servers": "",
  "idle_shutdown_s": 1800,
  "freeze_animations_on_stall": false,
  "log_dedupe": false,
  "log_dedupe_markers": ["flutter: ", "] ", ") "]
},
"templates": [
  {"name": "pixel-clean", "platform": "android", "avd": "Pixel_7_API_34", "snapshot": "clean"},
  {"name": "iphone-clean", "platform": "ios", "simulator": "iPhone 15"}
]
```

- `allow_physical_devices`: expose USB/Wi-Fi phones, not just emulators/simulators. Keep `false` on a personal laptop.
- `allowed_app_ids`: if non-empty, only these app ids can be installed/launched/stopped/reset/uninstalled, and Maestro flows may only target them. `install` then needs an `app_id`, and the package that actually got installed is checked too.
- `allow_maestro_scripts`: permit Maestro commands outside the built-in allowlist (`runScript`, `evalScript`, `runFlow`, `addMedia`, `file:` sub-flows, ...), `${...}` and extra flow config keys. Maestro JavaScript can make HTTP requests from your machine and sub-flows can read files on it, so this is off by default.

- `keep_awake` (macOS, default `true`): while the agent is using a device, hold a `caffeinate -i -s` assertion so
  the Mac does not idle-sleep and drop the connection mid-test. It lapses 15 minutes after the last device call.
  A closed lid on battery still sleeps (macOS policy).

- `preflight` (default `"public"`): a scenario may first check the test environment over HTTP (for example "does the
  backend return this config flag"), so a broken environment is reported as *blocked* instead of as a failed test.
  `"public"` refuses hosts that are, or resolve to, loopback / private / link-local addresses, so the agent cannot
  probe your local network; `"any"` allows them (use it when the backend under test runs on this machine or your LAN);
  `"off"` refuses every preflight. Redirects are not followed and the response body is never sent to Loma.

- `auto_recover` (default `true`): when an emulator/simulator that Loma used in the last 30 minutes crashes, is
  closed or hangs, the runner starts it again so the test run can continue. It remembers each device's AVD name
  and port (`~/.loma-device-runner/known-devices.json`) and restarts it on the **same port**, so the Loma
  `device_id` does not change. The steps escalate:
  - Android: reconnect an `offline` emulator, restart a hung `adb` server, then kill the AVD's processes, clear
    stale `*.lock` files, and cold boot it (`-no-snapshot-load`). If that boot fails, it tries once more with the
    software GPU.
  - iOS: boot a shut-down simulator, or shut down and boot a hung one. It restarts `CoreSimulatorService` only when
    `simctl` itself is stuck.

  Restarting `adb` or `CoreSimulatorService` affects every device, so the runner only does it when no other device
  on this runner is in use. Physical devices and devices idle for over 30 minutes are never restarted
  automatically, so an emulator you closed on purpose stays closed. After 3 automatic restarts in 30 minutes the
  runner stops (a boot loop) and reports the emulator log. Apps and their data are kept, but the app is not running
  after a restart. Emulator output goes to `~/.loma-device-runner/logs/emulator-<AVD>.log`.
- `emulator_args` (default `[]`): extra flags used when the runner cold boots an emulator, e.g.
  `["-memory", "4096", "-cores", "4"]`. On Linux without a display, `-no-window` is added. Emulators the runner
  started keep running when the runner restarts (`KillMode=process` / `AbandonProcessGroup`).

- `net_probe_host` (1.5.0, default `connectivitycheck.gstatic.com`): `health` checks that the device can resolve
  this host. An emulator whose DNS broke still takes screenshots fine, but every SDK call hangs, so it is reported
  as unhealthy. Android checks with `ping` from the device (only "unknown host" fails it; blocked ICMP is fine),
  iOS with the Mac's resolver. `"off"` disables the check.
- `dns_servers` (1.5.0, default `""`, none): when `health` found broken DNS, `recover` cold boots the emulator
  with `-dns-server` set to these (unless `emulator_args` already sets one). Empty means a plain restart; set your
  own resolvers (public ones like `8.8.8.8` are often blocked on corporate networks). On iOS a broken DNS is the Mac's
  own network, so it is reported instead of restarting the simulator.
- `idle_shutdown_s` (1.5.0, default 1800): a device booted from a template is shut down after this long without
  calls, in case a lease was never released. A template's own `idle_shutdown_s` wins.
- `freeze_animations_on_stall` (1.5.0, default `false`): on Android, when a UI read fails with "UI not idle"
  (shimmer, looping Lottie), retry it with `animator_duration_scale` set to 0 for that one read, then restore it.
  Off by default because it changes a global device setting under the app under test.
- `log_dedupe` (1.5.0, default `false`): on iOS, merge the console capture with the unified log and drop duplicate
  copies of one print. `log_dedupe_markers` is the list of prefixes stripped before comparing lines (the defaults
  suit Flutter). Off by default because two real events with the same text within 500 ms are counted once.

### Device templates (1.5.0)

`templates` lists the devices the agent may **boot** when it needs one. You name them; the agent only picks a name.
- Android: `avd` is an existing AVD (`emulator -list-avds`). `snapshot` (optional) is a snapshot saved in that AVD
  (Extended controls > Snapshots). A **clean** boot loads it read-only and never saves (`-snapshot NAME
  -no-snapshot-save -read-only`), so every test starts from the same state and the AVD itself is never changed.
  Without `snapshot`, only ordinary (cold) boots are possible. `headless: true` adds `-no-window`.
- iOS: `simulator` is a simulator name or UDID. A clean boot clones it (the template must be shut down) and deletes
  the clone at shutdown, so keychain, permissions and installed apps start from the template's state.
- A lease with `template` (and `clean`) boots one; a lease without a device also boots a template when no device is
  running or all are taken. Releasing the lease shuts that device down. Booted devices are remembered in
  `~/.loma-device-runner/booted-devices.json`, so a restarted runner still shuts them down.

Run `setup` again after editing the policy to restart the runner.

## Tips for reliable agent testing

- Use a **dedicated** emulator/simulator for Loma, with no personal Google/Apple account signed in.
- Runner 1.2.0+ keeps the Mac awake while devices are in use (`keep_awake`); keep the lid open or plug in power.
- Runner 1.3.0+ re-encodes a recording that is over the 16 MB limit instead of dropping it. It uses `ffmpeg` when
  installed (`brew install ffmpeg`, best quality for the size) and otherwise the `avconvert` tool that ships with macOS.
- Emulators can run headless: `emulator -avd loma-test -no-window -no-snapshot-save`.
- Runner 1.4.0+ checks device speed (`health`) when a device is leased and before a suite: an emulator that takes
  over 5 s per screenshot or 10 s per UI tree is reported as *blocked (device_slow)* instead of timing out tests.
  Give the emulator more RAM/cores, close other emulators, or use a hardware-accelerated image.
- Runner 1.4.0+ can reset an iOS app (`reset_app`): it reinstalls the last build it installed (or empties the app's
  data container) and resets the simulator keychain. The keychain is shared by every app on that simulator, which
  is one more reason to use a dedicated simulator.
- Runner 1.5.0+ uploads large screenshots and videos to Loma over HTTPS (`/device-runner/media`) instead of inside
  the WebSocket message. A video inline used to block the connection long enough to drop it ("Runner reconnected" on
  the next test). If a suite still loses the connection, it waits up to 90 s for the runner and re-runs that case.
- With `freeze_animations_on_stall` on, runner 1.5.0+ reads the UI tree of an Android screen that never goes idle
  (shimmer / skeleton loaders, looping animations) by pausing animators for that one read and restoring them, so
  `scroll_until_visible` and `wait_for` no longer hang on such screens.
- Runner 1.5.0+ remembers which build it installed on which device across restarts
  (`~/.loma-device-runner/installed-builds.json`), so a lease can report whether the app under test is installed
  and current.
- Revoke the runner under **Integrations → Devices** at any time; it stops within seconds.
