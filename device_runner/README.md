# Loma Device Runner

Lets a Loma agent drive the Android emulators and iOS simulators on a machine you
control (your laptop, a Mac mini, a KVM Linux box). The agent can install a PR
build, launch it, open deep links, tap/type/swipe, read the UI tree, take
screenshots, read logs and run Maestro flows.

The runner makes **one outbound WebSocket** to Loma. It never listens on a port,
so there is no tunnel, DNS or firewall setup, and it works behind NAT/VPN.
When the machine sleeps or the runner stops, Loma shows the devices as offline
and agent calls fail fast with a clear message.

## Quick start (macOS)

```bash
# 1. Tooling (install what you need)
#    Android: Android Studio + an emulator (arm64 image on Apple Silicon); make sure `adb devices` works
#    iOS:     Xcode + a booted simulator; for taps/UI tree also: brew install idb-companion && pip3 install fb-idb
#    Flows:   curl -fsSL "https://get.maestro.mobile.dev" | bash
python3 -m pip install --user 'aiohttp>=3.9,<4' 'pyyaml>=6,<7'

# 2. Download the runner from your Loma server and enroll
curl -fsSLo loma_device_runner.py https://<your-loma>/device-runner/download
python3 loma_device_runner.py enroll --server https://<your-loma> --token <token from Loma → Devices>

# 3. Check tooling and visible devices
python3 loma_device_runner.py doctor

# 4. Run it (foreground), or install it as a login service
python3 loma_device_runner.py run
python3 loma_device_runner.py install-service
```

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
- `allowed_app_ids`: if non-empty, only these app ids can be installed/launched/stopped/reset/uninstalled, and Maestro flows may only target them.
- `allow_maestro_scripts`: permit `runScript`, `evalScript`, `runFlow`, `addMedia` and `${...}` in flows. Maestro JavaScript can make HTTP requests from your machine, so this is off by default.

Restart the runner after editing the policy.

## Tips for reliable agent testing

- Use a **dedicated** emulator/simulator for Loma, with no personal Google/Apple account signed in.
- Run `caffeinate -dimsu` (macOS) during long sessions so the machine does not sleep.
- Emulators can run headless: `emulator -avd loma-test -no-window -no-snapshot-save`.
- Revoke the runner from **Loma → Devices** at any time; it stops within seconds.
