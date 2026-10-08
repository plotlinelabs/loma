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

## Policy (`~/.loma-device-runner/config.json`)

```json
"policy": {
  "allow_physical_devices": false,
  "allowed_app_ids": ["com.example.demo"],
  "allow_maestro_scripts": false,
  "keep_awake": true,
  "preflight": "public",
  "auto_recover": true,
  "emulator_args": []
}
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
- Revoke the runner under **Integrations → Devices** at any time; it stops within seconds.
