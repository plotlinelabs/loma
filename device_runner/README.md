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
| Upgrade / restart (download the new file first) | `python3 loma_device_runner.py setup` (only needed once to reach 1.2.0; see below) |
| Check tooling and devices | `~/.loma-device-runner/venv/bin/python3 ~/.loma-device-runner/loma_device_runner.py doctor` |
| Remove from this machine | `python3 ~/.loma-device-runner/loma_device_runner.py uninstall` |
| Logs | macOS: `~/.loma-device-runner/runner.log`, Linux: `journalctl --user -u loma-device-runner -f` |

Device tooling (install what you need):

- **Android:** Android Studio + an emulator (arm64 image on Apple Silicon). `adb` is found in
  the default SDK location (or `$ANDROID_HOME`) without PATH changes.
- **iOS:** Xcode + a booted simulator. `setup` installs `idb` (needs Homebrew).
- **Flows (optional):** Maestro, which needs Java 17:
  `brew install openjdk@17 && curl -fsSL "https://get.maestro.mobile.dev" | bash`, then re-run `setup`.

**Automatic updates (1.2.0+).** When the runner runs as the login service, Loma offers it the
newer runner it serves. The runner downloads it over its authenticated connection, checks it
against the SHA-256 the server announced, checks it parses and declares that version, waits for
running operations to finish (up to 10 minutes) and exits so launchd/systemd restart it on the
new version. The checksum protects against a truncated or altered download on the way; it comes
from the same Loma server, so it is not a code signature. Set `"auto_update": false` in the policy
to update by hand. Runners older than 1.2.0 need one manual `setup` with the new file.

The service keeps the `PATH` of the shell you ran `setup` from, plus the Android SDK,
Maestro, Homebrew and the runner's own `idb`. On Linux, run `loginctl enable-linger $USER` if the
runner should stay up while you are logged out.

## What the agent can and cannot do

| Allowed (fixed list) | Never possible |
|---|---|
| install (build fetched by Loma, checksum-verified), uninstall, launch, stop, reset_app | Run shell commands on your machine |
| open_url (deep links), tap, swipe, type, key | Read or write files on your machine |
| screenshot, ui_tree, logs, record, burst | See physical devices (unless you opt in) |
| run_flow (Maestro YAML, screened) | Run Maestro JavaScript (unless you opt in) |
| configure: per-app locale, time zone and clock offset (Android), location, dark mode, font scale, permissions | Change settings of apps outside `allowed_app_ids` |

`configure` remembers the original value of each setting it changes and restores them when the
agent releases the device (or on `configure reset=true`). Permissions and Android location are not
restored. Clock and time zone changes need Android 11+, per-app locale needs Android 13+; iOS
simulators follow the Mac's clock and time zone, so those two are reported as unsupported there.

## Policy (`~/.loma-device-runner/config.json`)

```json
"policy": {
  "allow_physical_devices": false,
  "allowed_app_ids": ["com.example.demo"],
  "allow_maestro_scripts": false,
  "auto_update": true
}
```

- `allow_physical_devices`: expose USB/Wi-Fi phones, not just emulators/simulators. Keep `false` on a personal laptop.
- `allowed_app_ids`: if non-empty, only these app ids can be installed/launched/stopped/reset/uninstalled, and Maestro flows may only target them. `install` then needs an `app_id`, and the package that actually got installed is checked too.
- `allow_maestro_scripts`: permit Maestro commands outside the built-in allowlist (`runScript`, `evalScript`, `runFlow`, `addMedia`, `file:` sub-flows, ...), `${...}` and extra flow config keys. Maestro JavaScript can make HTTP requests from your machine and sub-flows can read files on it, so this is off by default.

- `auto_update`: install newer runner versions offered by Loma automatically (default `true`).

## Device templates (boot on demand, clean state)

Add `templates` at the top level of `config.json` so the agent can boot devices itself instead of
needing one already running. The agent only picks a template by name; it can never pass an AVD
name, emulator flags or a simulator of its choice.

```json
"templates": [
  {"name": "pixel-34", "platform": "android", "avd": "Pixel_7_API_34", "snapshot": "clean", "headless": true},
  {"name": "iphone-15", "platform": "ios", "simulator": "Loma iPhone 15"}
]
```

- Android: `avd` from `emulator -list-avds`. `snapshot` is a snapshot you saved in that AVD
  (Extended controls > Snapshots) with the state every test should start from, e.g. the Plotline
  demo app installed and logged out. A clean boot loads it `-read-only` and never saves, so
  nothing a session does persists and several clean devices can run at once.
- iOS: `simulator` is the name or UDID of a simulator you set up once and keep shut down. A clean
  boot clones it and deletes the clone at shutdown; a normal boot starts it as is.
- `headless` (Android): no emulator window. `idle_shutdown_s` (default 1800): a device the runner
  booted is shut down after this long without calls, in case a session never released it.

Devices booted for a session shut down when it releases them. Run `setup` again after editing.

Run `setup` again after editing the policy to restart the runner.

## Tips for reliable agent testing

- Use a **dedicated** emulator/simulator for Loma, with no personal Google/Apple account signed in.
- Run `caffeinate -dimsu` (macOS) during long sessions so the machine does not sleep.
- Emulators can run headless: `emulator -avd loma-test -no-window -no-snapshot-save`.
- Revoke the runner under **Integrations → Devices** at any time; it stops within seconds.
