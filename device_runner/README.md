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
| screenshot, ui_tree, logs | See physical devices (unless you opt in) |
| run_flow (Maestro YAML, screened) | Run Maestro JavaScript (unless you opt in) |

## Policy (`~/.loma-device-runner/config.json`)

```json
"policy": {
  "allow_physical_devices": false,
  "allowed_app_ids": ["com.example.demo"],
  "allow_maestro_scripts": false
}
```

- `allow_physical_devices`: expose USB/Wi-Fi phones, not just emulators/simulators. Keep `false` on a personal laptop.
- `allowed_app_ids`: if non-empty, only these app ids can be installed/launched/stopped/reset/uninstalled, and Maestro flows may only target them. `install` then needs an `app_id`, and the package that actually got installed is checked too.
- `allow_maestro_scripts`: permit Maestro commands outside the built-in allowlist (`runScript`, `evalScript`, `runFlow`, `addMedia`, `file:` sub-flows, ...), `${...}` and extra flow config keys. Maestro JavaScript can make HTTP requests from your machine and sub-flows can read files on it, so this is off by default.

Run `setup` again after editing the policy to restart the runner.

## Tips for reliable agent testing

- Use a **dedicated** emulator/simulator for Loma, with no personal Google/Apple account signed in.
- Run `caffeinate -dimsu` (macOS) during long sessions so the machine does not sleep.
- Emulators can run headless: `emulator -avd loma-test -no-window -no-snapshot-save`.
- Revoke the runner under **Integrations → Devices** at any time; it stops within seconds.
